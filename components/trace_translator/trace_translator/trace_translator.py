"""
TraceTranslator - Unified UI Detection System

This module provides a complete UI element detection system that works with both:
1. Web pages (HTML detection) - 100% accurate via DOM extraction
2. Screenshots (Computer Vision) - Works with any image

MAIN CLASS:
- TraceTranslator: Main orchestrator for converting UIs to structured traces

DETECTION METHODS:
- HTMLDetector: Extracts UI elements from web pages using Playwright
- CVDetector: Analyzes screenshots using computer vision (YOLO + OCR)
- UIElementExtractor: Converts CV results into structured elements

TWO-STATE TRACE FORMAT:
    Each trace step holds TWO states so the Trace Translator can tell what
    changed between frames of a screen recording:
      - state_before : the UI *before* an action
      - state_after  : the UI *after* the action

USAGE:
    # Single-state trace (legacy / snapshot)
    translator = TraceTranslator(use_cv=True)
    state = translator.image_to_state('screenshot.png')
    trace = translator.state_to_trace(state, trace_id='demo')
    translator.save_trace(trace, 'output.json')

    # Two-state trace (screen recording step)
    state_before = translator.image_to_state('frame_01.png')
    state_after  = translator.image_to_state('frame_02.png')
    trace = translator.states_to_trace(
        state_before, state_after,
        action={'type': 'click', 'element_id': 'button_0'},
        trace_id='step_01'
    )
    translator.save_trace(trace, 'output.json')
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional
from PIL import Image
import numpy as np
from datetime import datetime
from collections import defaultdict
import io

# MouseInput and KeyboardInput have been moved to:
#   components/trace_translator/recorder/recorder.py


# ============================================================================
# HTML DETECTOR - Web UI Detection (100% Accurate)
# ============================================================================

class HTMLDetector:
    """
    Detect UI elements from web pages using HTML/DOM extraction.
    
    Provides 100% accurate detection for web UIs by directly accessing
    the DOM structure through browser automation (Playwright).
    
    Detects: buttons, inputs, textareas, links, dropdowns, checkboxes, images
    """
    
    def __init__(self, headless: bool = True, timeout: int = 30000):
        """
        Initialize HTML detector.
        
        Args:
            headless: Run browser in headless mode (no visible window)
            timeout: Page load timeout in milliseconds
        """
        self.headless = headless
        self.timeout = timeout
    
    def extract_ui_elements(self, url: str) -> Dict[str, Any]:
        """
        Extract all UI elements from a web page.
        
        Args:
            url: URL of the web page to analyze
            
        Returns:
            Dictionary containing:
                - elements: List of detected UI elements
                - screenshot: PIL Image of the page
                - page_info: Metadata about the page
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise ImportError(
                "Playwright is required for HTML detection. "
                "Install with: pip install playwright && playwright install chromium"
            )
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            page = browser.new_page()
            
            try:
                # Navigate to URL
                print(f"  Loading page: {url}")
                page.goto(url, timeout=self.timeout, wait_until='networkidle')
                page.wait_for_timeout(1000)
                
                # Extract all UI elements
                print("  Extracting UI elements...")
                elements = self._extract_all_elements(page)
                
                # Take screenshot
                print("  Capturing screenshot...")
                screenshot_bytes = page.screenshot(full_page=False)
                screenshot = Image.open(io.BytesIO(screenshot_bytes))
                
                # Get page info
                page_info = {
                    'title': page.title(),
                    'url': page.url,
                    'viewport': {
                        'width': page.viewport_size['width'],
                        'height': page.viewport_size['height']
                    }
                }
                
                print(f"  Detected {len(elements)} UI elements")
                
                return {
                    'elements': elements,
                    'screenshot': screenshot,
                    'page_info': page_info
                }
            
            finally:
                browser.close()
    
    def _extract_all_elements(self, page) -> List[Dict[str, Any]]:
        """
        Extract all interactive UI elements from the page using JavaScript.
        
        Args:
            page: Playwright page object
            
        Returns:
            List of element dictionaries with type, bbox, text, metadata
        """
        # Execute JavaScript to extract all elements with their positions
        elements = page.evaluate("""
            () => {
                const elements = [];
                let elementCounter = 0;
                
                // Helper function to get element info
                const getElementInfo = (el, type, customId = null) => {
                    const rect = el.getBoundingClientRect();
                    
                    // Skip invisible elements
                    if (rect.width === 0 || rect.height === 0) {
                        return null;
                    }
                    
                    const id = customId || `${type}_${elementCounter++}`;
                    
                    return {
                        element_id: id,
                        type: type,
                        bbox: [
                            Math.round(rect.left),
                            Math.round(rect.top),
                            Math.round(rect.right),
                            Math.round(rect.bottom)
                        ],
                        text: (el.innerText || el.textContent || '').trim().substring(0, 200),
                        label: el.getAttribute('aria-label') || (el.innerText || '').trim().substring(0, 100),
                        enabled: !el.disabled && !el.hasAttribute('disabled'),
                        visible: true,
                        metadata: {
                            tag: el.tagName.toLowerCase(),
                            class: el.className,
                            id: el.id,
                            type: el.type || null,
                            placeholder: el.placeholder || null,
                            href: el.href || null,
                            value: el.value || null,
                            role: el.getAttribute('role'),
                            name: el.name || null
                        }
                    };
                };
                
                // Extract buttons
                document.querySelectorAll('button').forEach(el => {
                    const info = getElementInfo(el, 'button');
                    if (info) elements.push(info);
                });
                
                // Extract inputs
                document.querySelectorAll('input').forEach(el => {
                    const inputType = el.type || 'text';
                    const info = getElementInfo(el, 'input');
                    if (info) {
                        info.metadata.input_type = inputType;
                        elements.push(info);
                    }
                });
                
                // Extract textareas
                document.querySelectorAll('textarea').forEach(el => {
                    const info = getElementInfo(el, 'textarea');
                    if (info) {
                        info.metadata.char_count = el.value.length;
                        elements.push(info);
                    }
                });
                
                // Extract links
                document.querySelectorAll('a').forEach(el => {
                    const info = getElementInfo(el, 'link');
                    if (info) elements.push(info);
                });
                
                // Extract select dropdowns
                document.querySelectorAll('select').forEach(el => {
                    const info = getElementInfo(el, 'dropdown');
                    if (info) {
                        info.metadata.options = Array.from(el.options).map(opt => opt.text);
                        elements.push(info);
                    }
                });
                
                // Extract checkboxes and radio buttons
                document.querySelectorAll('input[type="checkbox"]').forEach(el => {
                    const info = getElementInfo(el, 'checkbox');
                    if (info) {
                        info.metadata.checked = el.checked;
                        elements.push(info);
                    }
                });
                
                document.querySelectorAll('input[type="radio"]').forEach(el => {
                    const info = getElementInfo(el, 'radio');
                    if (info) {
                        info.metadata.checked = el.checked;
                        elements.push(info);
                    }
                });
                
                // Extract images
                document.querySelectorAll('img').forEach(el => {
                    const info = getElementInfo(el, 'image');
                    if (info) {
                        info.metadata.src = el.src;
                        info.metadata.alt = el.alt;
                        elements.push(info);
                    }
                });
                
                return elements;
            }
        """)
        
        # Add confidence score and window_role (HTML = single active window)
        for element in elements:
            element['confidence'] = 1.0
            element['window_role'] = 'active'

        return elements


# ============================================================================
# CV DETECTOR - Screenshot Analysis (Computer Vision)
# ============================================================================

class CVDetector:
    """
    Computer Vision detector for analyzing screenshots.
    
    Uses OCR (Tesseract) to extract text from images.
    Works with any image but less accurate than HTML detection.
    
    For production use, this would integrate YOLO for object detection,
    but for now focuses on OCR text extraction.
    """
    
    def __init__(self):
        """Initialize CV detector with OCR."""
        self._tesseract_available = None
    
    def detect_ui_elements(
        self,
        image: Image.Image,
        use_ocr: bool = True
    ) -> Dict[str, Any]:
        """
        Detect UI elements from screenshot using computer vision.
        
        Args:
            image: PIL Image to analyze
            use_ocr: Whether to use OCR for text extraction
            
        Returns:
            Dictionary with detection results
        """
        results = {
            "ocr_results": []
        }
        
        # OCR text extraction
        if use_ocr:
            print("Running Tesseract OCR...")
            results["ocr_results"] = self._extract_text_with_ocr(image)
            print(f"  Extracted {len(results['ocr_results'])} text regions")
        
        return results
    
    def _extract_text_with_ocr(self, image: Image.Image) -> List[Dict[str, Any]]:
        """
        Extract text regions using Tesseract OCR.
        
        Args:
            image: PIL Image
            
        Returns:
            List of text regions with bbox, text, confidence
        """
        try:
            import pytesseract
        except ImportError:
            print("Warning: pytesseract not installed. Skipping OCR.")
            return []
        
        try:
            # Get detailed OCR data
            ocr_data = pytesseract.image_to_data(
                image,
                output_type=pytesseract.Output.DICT,
                config='--psm 11'  # Sparse text mode for UI screenshots
            )
            
            text_regions = []
            n_boxes = len(ocr_data['text'])
            
            for i in range(n_boxes):
                text = ocr_data['text'][i].strip()
                conf = int(ocr_data['conf'][i])
                
                # Skip empty text or low confidence
                if not text or conf < 30:
                    continue
                
                # Get bounding box
                x, y, w, h = (
                    ocr_data['left'][i],
                    ocr_data['top'][i],
                    ocr_data['width'][i],
                    ocr_data['height'][i]
                )
                
                text_regions.append({
                    'text': text,
                    'bbox': [x, y, x + w, y + h],
                    'confidence': conf / 100.0  # Normalize to 0-1
                })
            
            return text_regions
            
        except Exception as e:
            print(f"Warning: OCR failed: {e}")
            return []


# ============================================================================
# UI ELEMENT EXTRACTOR - Converts CV Results to Structured Elements
# ============================================================================

class UIElementExtractor:
    """
    Converts CV detection results into structured UI element format.
    
    Takes raw OCR results and formats them into the standard element structure
    used by the trace system.
    """
    
    def __init__(self):
        """Initialize element extractor."""
        self.element_counter = 0
    
    # Common button labels for type inference
    _BUTTON_KEYWORDS = {
        "ok", "cancel", "submit", "save", "delete", "close", "open", "yes", "no",
        "apply", "back", "next", "continue", "confirm", "add", "remove", "edit",
        "update", "create", "new", "browse", "upload", "download", "refresh",
        "search", "find", "login", "logout", "sign in", "sign out",
    }

    def _infer_type(self, text: str, bbox: list) -> str:
        """Infer element type from text and bounding-box geometry."""
        w = max(1, bbox[2] - bbox[0])
        h = max(1, bbox[3] - bbox[1])
        t = (text or "").strip()
        tl = t.lower()

        if not t:
            # Wide thin box with no text → input field
            return "input" if w > 3 * h else "label"

        if tl in self._BUTTON_KEYWORDS:
            return "button"

        # Short title-case text in a roughly square-ish box → button
        words = t.split()
        if len(words) <= 3 and t[0].isupper() and w / h < 8 and h >= 16:
            return "button"

        # Ends with colon → label for a field
        if t.endswith(":"):
            return "label"

        return "label"

    def extract_elements(
        self,
        image: Image.Image,
        ocr_results: List[Dict]
    ) -> List[Dict[str, Any]]:
        """
        Extract UI elements from OCR results.

        Infers element type from text content and bounding-box geometry so the
        transformer receives richer type signals than a flat "label" for everything.
        All OCR elements are tagged window_role="active" (single-window source).
        """
        elements = []
        self.element_counter = 0

        for ocr_result in ocr_results:
            bbox = ocr_result['bbox']
            text = ocr_result['text']
            elem_type = self._infer_type(text, bbox)
            element = {
                "element_id": f"{elem_type}_{self.element_counter}",
                "type": elem_type,
                "bbox": bbox,
                "text": text,
                "label": text,
                "enabled": True,
                "visible": True,
                "confidence": ocr_result['confidence'],
                "window_role": "active",
                "metadata": {
                    "source": "ocr"
                }
            }
            elements.append(element)
            self.element_counter += 1

        return elements

    def merge_overlapping_elements(self, elements: List[Dict]) -> List[Dict]:
        """
        Deduplicate elements whose bounding boxes overlap significantly (IoU >= 0.5).

        OCR often produces multiple fragments for the same text region.  This pass
        keeps the highest-confidence representative from each overlapping cluster.
        """
        if len(elements) <= 1:
            return elements

        def iou(a, b):
            ax1, ay1, ax2, ay2 = a
            bx1, by1, bx2, by2 = b
            ix1 = max(ax1, bx1); iy1 = max(ay1, by1)
            ix2 = min(ax2, bx2); iy2 = min(ay2, by2)
            inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            if inter == 0:
                return 0.0
            area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
            area_b = max(1, (bx2 - bx1) * (by2 - by1))
            return inter / (area_a + area_b - inter)

        kept = []
        suppressed = set()
        # Sort by confidence descending so we keep the best representative
        sorted_elems = sorted(
            enumerate(elements),
            key=lambda x: x[1].get("confidence", 0.0),
            reverse=True,
        )
        for i, elem in sorted_elems:
            if i in suppressed:
                continue
            kept.append(elem)
            for j, other in sorted_elems:
                if j <= i or j in suppressed:
                    continue
                if iou(elem["bbox"], other["bbox"]) >= 0.5:
                    suppressed.add(j)

        # Restore original order
        kept_ids = {id(e) for e in kept}
        return [e for e in elements if id(e) in kept_ids]


# ============================================================================
# TRACE TRANSLATOR - Main Orchestrator
# ============================================================================

class TraceTranslator:
    """
    Main class for converting UIs into structured trace data.
    
    Supports two detection methods:
    1. HTML Detection (use_html=True) - For web pages, 100% accurate
    2. CV Detection (use_cv=True) - For screenshots, works with any image
    
    WORKFLOW:
    1. Detect UI elements (HTML or CV)
    2. Convert to intermediate "state" format
    3. Convert state to final "trace" JSON format
    4. Save trace for training imitation learning models
    
    EXAMPLE:
        # HTML detection
        translator = TraceTranslator(use_html=True)
        state = translator.url_to_state('https://example.com')
        trace = translator.state_to_trace(state, trace_id='demo')
        translator.save_trace(trace, 'output.json')
        
        # CV detection
        translator = TraceTranslator(use_cv=True)
        state = translator.image_to_state('screenshot.png')
        trace = translator.state_to_trace(state, trace_id='demo')
        translator.save_trace(trace, 'output.json')
    """
    
    def __init__(
        self,
        trace_format_path: str = None,
        use_cv: bool = True,
        use_html: bool = False
    ):
        """
        Initialize TraceTranslator.
        
        Args:
            trace_format_path: Path to trace format JSON template
            use_cv: Enable computer vision for screenshot analysis
            use_html: Enable HTML detection for web pages
        """
        # Load trace format template
        if trace_format_path is None:
            trace_format_path = os.path.join(
                os.path.dirname(__file__),
                'trace_format.json'
            )
        
        self.trace_format_path = trace_format_path
        self.trace_template = self._load_trace_template()
        self.use_cv = use_cv
        self.use_html = use_html
        
        # Initialize detectors
        if self.use_cv:
            self.cv_detector = CVDetector()
            self.ui_extractor = UIElementExtractor()
        else:
            self.cv_detector = None
            self.ui_extractor = None
        
        if self.use_html:
            self.html_detector = HTMLDetector()
            self._last_screenshot = None
        else:
            self.html_detector = None
    
    def _load_trace_template(self) -> Dict[str, Any]:
        """Load trace format template from JSON file."""
        try:
            with open(self.trace_format_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"Warning: Trace format template not found at {self.trace_format_path}")
            return {}
    
    # ------------------------------------------------------------------------
    # HTML DETECTION (Web Pages)
    # ------------------------------------------------------------------------
    
    def url_to_state(self, url: str, application: str = 'Chrome') -> Dict[str, Any]:
        """
        Extract UI state from a URL using HTML detection.
        
        This provides 100% accurate detection for web pages by extracting
        elements directly from the DOM.
        
        Args:
            url: URL of the web page to analyze
            application: Application name (default: 'Chrome')
            
        Returns:
            UI state dictionary with detected elements
        """
        if not self.use_html:
            raise ValueError(
                "HTML detection not enabled. Initialize with use_html=True"
            )
        
        print(f"\nExtracting UI state from URL: {url}")
        
        # Extract elements using HTML detector
        result = self.html_detector.extract_ui_elements(url)
        
        # Store screenshot for later use
        self._last_screenshot = result['screenshot']
        
        # Build state dictionary
        state = {
            'application': application,
            'window_title': result['page_info']['title'],
            'screen_resolution': [
                result['page_info']['viewport']['width'],
                result['page_info']['viewport']['height']
            ],
            'focused_element_id': None,
            'elements': result['elements'],
            'metadata': {
                'url': url,
                'detection_method': 'html',
                'detection_timestamp': datetime.now().isoformat(),
                'num_elements_detected': len(result['elements'])
            }
        }
        
        return state
    
    # ------------------------------------------------------------------------
    # CV DETECTION (Screenshots)
    # ------------------------------------------------------------------------
    
    def image_to_state(self, image_path: str, application: str = None) -> Dict[str, Any]:
        """
        Convert a screenshot file into UI state using computer vision.

        Args:
            image_path:  Path to the screenshot image (.png, .jpg, etc.)
            application: Application name (optional)

        Returns:
            UI state dictionary with detected elements
        """
        if not self.use_cv:
            raise ValueError(
                "CV detection not enabled. Initialize with use_cv=True"
            )

        print(f"\nAnalyzing screenshot: {image_path}")
        img = Image.open(image_path)
        return self._state_from_pil(
            img,
            source_label=os.path.basename(image_path),
            application=application
        )

    def _state_from_pil(
        self,
        img: Image.Image,
        source_label: str = 'frame',
        application: str = None
    ) -> Dict[str, Any]:
        """
        Internal helper: convert a PIL Image into a UI state dict.

        Shared by both image_to_state() (file path) and video_to_traces()
        (in-memory frames) so detection logic lives in one place.

        Args:
            img:          PIL Image to analyse
            source_label: Human-readable label for the source (filename / frame id)
            application:  Application name (optional)

        Returns:
            UI state dictionary with detected elements
        """
        # Run OCR
        cv_results = self.cv_detector.detect_ui_elements(img, use_ocr=True)

        # Build structured elements
        elements = self.ui_extractor.extract_elements(img, cv_results['ocr_results'])
        elements = self.ui_extractor.merge_overlapping_elements(elements)

        return {
            'application': application or 'Unknown',
            'window_title': source_label,
            'screen_resolution': [img.width, img.height],
            'focused_element_id': None,
            'elements': elements,
            'metadata': {
                'source': source_label,
                'detection_method': 'cv',
                'detection_timestamp': datetime.now().isoformat(),
                'num_elements_detected': len(elements)
            }
        }
    
    # ------------------------------------------------------------------------
    # VIDEO PROCESSING
    # ------------------------------------------------------------------------

    def video_to_traces(
        self,
        video_path: str,
        interval_sec: float = 3.0,
        application: str = None,
        output_dir: str = None,
        verbose: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Extract frames from an MP4 (or any OpenCV-compatible video) and
        generate a sequential list of two-state trace steps.

        Each trace step pairs consecutive sampled frames:
            frame_N  (state_before)  +  frame_N+1 (state_after)  =  trace step

        Args:
            video_path:   Path to the video file (.mp4, .avi, etc.)
            interval_sec: Seconds between sampled frames (default: 3.0)
            application:  Application name to tag on every state
            output_dir:   If set, each trace JSON is saved here automatically
            verbose:      Print progress to console

        Returns:
            List of trace dictionaries (one per consecutive frame pair)
        """
        if not self.use_cv:
            raise ValueError(
                "CV detection not enabled. Initialize with use_cv=True"
            )

        try:
            import cv2
        except ImportError:
            raise ImportError(
                "opencv-python is required for video processing. "
                "Install with: pip install opencv-python"
            )

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"Could not open video file: {video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration_sec = total_frames / fps
        frame_interval = max(1, int(fps * interval_sec))

        if verbose:
            print(f"\nVideo : {video_path}")
            print(f"  FPS            : {fps:.1f}")
            print(f"  Duration       : {duration_sec:.1f}s")
            print(f"  Total frames   : {total_frames}")
            print(f"  Sample interval: every {interval_sec}s ({frame_interval} frames)")
            expected = max(0, int(total_frames / frame_interval) - 1)
            print(f"  Expected traces: ~{expected}")
            print()

        # ── Extract and process frames ────────────────────────────────────
        states: List[Dict[str, Any]] = []
        frame_num = 0
        sampled = 0

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            if frame_num % frame_interval == 0:
                # Convert BGR (OpenCV) → RGB → PIL
                rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img   = Image.fromarray(rgb)
                label = f"{os.path.basename(video_path)}::frame_{frame_num:06d}"

                if verbose:
                    ts = frame_num / fps
                    print(f"  [{sampled:04d}] t={ts:.1f}s  frame={frame_num}  ", end='', flush=True)

                state = self._state_from_pil(img, source_label=label, application=application)

                if verbose:
                    print(f"-> {len(state['elements'])} elements")

                states.append(state)
                sampled += 1

            frame_num += 1

        cap.release()

        if len(states) < 2:
            print("Warning: fewer than 2 frames sampled — cannot build trace steps.")
            return []

        # ── Build consecutive trace steps ──────────────────────────────────
        traces: List[Dict[str, Any]] = []
        for i in range(len(states) - 1):
            trace = self.states_to_trace(
                states[i],
                states[i + 1],
                trace_id=f"{os.path.splitext(os.path.basename(video_path))[0]}_step_{i:04d}"
            )
            traces.append(trace)

            if output_dir:
                out_path = os.path.join(
                    output_dir,
                    f"{os.path.splitext(os.path.basename(video_path))[0]}_step_{i:04d}.json"
                )
                self.save_trace(trace, out_path)

        if verbose:
            print(f"\nGenerated {len(traces)} trace steps from {sampled} sampled frames.")
            if output_dir:
                print(f"Traces saved to: {output_dir}")

        return traces

    # ------------------------------------------------------------------------
    # TRACE CONVERSION
    # ------------------------------------------------------------------------
    
    def state_to_trace(
        self,
        state: Dict[str, Any],
        trace_id: str = None,
        action: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Convert a *single* UI state to trace format (snapshot / legacy use).
        
        For screen recordings where you need to distinguish BEFORE vs AFTER,
        use ``states_to_trace`` instead.
        
        Args:
            state: UI state dictionary from url_to_state() or image_to_state()
            trace_id: Unique identifier for this trace
            action: Optional action that was performed
            
        Returns:
            Trace dictionary in standard format
        """
        if trace_id is None:
            trace_id = f"trace_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        trace = {
            'trace_id': trace_id,
            'timestamp': datetime.now().isoformat(),
            'state': state,           # transformer reads this key
            'state_before': state,    # kept for compatibility
            'state_after': None,
            'mouse': action.get('mouse', {}) if action else {},
            'keyboard': action.get('keyboard', {}) if action else {},
            'action': action,
            'metadata': {
                'trace_type': 'snapshot',
                'detection_method': state['metadata'].get('detection_method', 'unknown'),
                'num_elements': len(state['elements'])
            }
        }

        return trace

    def states_to_trace(
        self,
        state_before: Dict[str, Any],
        state_after: Dict[str, Any],
        action: Dict[str, Any] = None,
        trace_id: str = None
    ) -> Dict[str, Any]:
        """
        Convert TWO UI states into a single trace step.

        This is the preferred method when processing screen recordings because
        it lets the Trace Translator detect exactly what changed between frames.

        Args:
            state_before: UI state captured *before* the action
                          (from url_to_state() or image_to_state())
            state_after:  UI state captured *after* the action
            action:       Optional description of the action that caused the
                          transition (e.g. {'type': 'click', 'element_id': 'btn_0'})
            trace_id:     Unique identifier for this trace step

        Returns:
            Trace dictionary with both states and a diff summary
        """
        if trace_id is None:
            trace_id = f"trace_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # --- compute a lightweight diff between the two states -----------
        diff = self._diff_states(state_before, state_after)

        # Infer mouse/keyboard from diff when no explicit action is provided
        if action is None:
            mouse, keyboard = self._infer_action_from_diff(
                diff, state_before, state_after
            )
        else:
            mouse    = action.get('mouse', {})
            keyboard = action.get('keyboard', {})

        trace = {
            'trace_id': trace_id,
            'timestamp': datetime.now().isoformat(),
            'state': state_before,    # transformer reads this key
            'state_before': state_before,
            'state_after': state_after,
            'mouse': mouse,
            'keyboard': keyboard,
            'action': action,
            'diff': diff,
            'metadata': {
                'trace_type': 'transition',
                'detection_method_before': state_before['metadata'].get('detection_method', 'unknown'),
                'detection_method_after':  state_after['metadata'].get('detection_method', 'unknown'),
                'num_elements_before': len(state_before['elements']),
                'num_elements_after':  len(state_after['elements'])
            }
        }

        return trace

    # ------------------------------------------------------------------------
    # STATE DIFF HELPER
    # ------------------------------------------------------------------------

    def _diff_states(
        self,
        state_before: Dict[str, Any],
        state_after: Dict[str, Any],
        iou_threshold: float = 0.3,
        center_threshold: int = 30
    ) -> Dict[str, Any]:
        """
        Produce an accurate diff between two UI states using position-based matching.

        Elements are matched by bounding-box overlap (IoU) rather than element_id,
        which makes the diff robust against OCR ID shuffling between frames.

        Matching strategy (in priority order):
          1. High IoU (>= iou_threshold)     → same element, check for text change
          2. Center proximity (< center_threshold px) → same element, different size
          3. Unmatched before element        → removed
          4. Unmatched after element         → added

        Args:
            state_before:      UI state before the action
            state_after:       UI state after the action
            iou_threshold:     Minimum IoU to consider two elements the same (0.0-1.0)
            center_threshold:  Max centre-point distance (px) for proximity matching

        Returns:
            Dict with keys:
                'added'   : list of elements new in state_after
                'removed' : list of elements gone from state_before
                'changed' : list of {element_id, before, after, changes} dicts
        """

        before_els: List[Dict] = state_before.get('elements', [])
        after_els:  List[Dict] = state_after.get('elements', [])

        # ── helpers ──────────────────────────────────────────────────────────

        def bbox_center(bbox):
            x1, y1, x2, y2 = bbox
            return ((x1 + x2) / 2, (y1 + y2) / 2)

        def iou(a, b):
            """Intersection over Union for two [x1,y1,x2,y2] boxes."""
            ax1, ay1, ax2, ay2 = a
            bx1, by1, bx2, by2 = b
            ix1 = max(ax1, bx1); iy1 = max(ay1, by1)
            ix2 = min(ax2, bx2); iy2 = min(ay2, by2)
            inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            if inter == 0:
                return 0.0
            area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
            area_b = max(1, (bx2 - bx1) * (by2 - by1))
            return inter / (area_a + area_b - inter)

        def center_dist(a_bbox, b_bbox):
            cx1, cy1 = bbox_center(a_bbox)
            cx2, cy2 = bbox_center(b_bbox)
            return ((cx1 - cx2) ** 2 + (cy1 - cy2) ** 2) ** 0.5

        # ── build match matrix ────────────────────────────────────────────────

        matched_before: set = set()   # indices into before_els
        matched_after:  set = set()   # indices into after_els

        # (before_idx, after_idx, score)
        matches: List[Tuple[int, int, float]] = []

        for bi, bel in enumerate(before_els):
            best_score = -1.0
            best_ai    = -1
            for ai, ael in enumerate(after_els):
                if ai in matched_after:
                    continue
                score = iou(bel['bbox'], ael['bbox'])
                if score >= iou_threshold and score > best_score:
                    best_score = score
                    best_ai    = ai

            # Fallback: centre distance if IoU didn't find a match
            if best_ai == -1:
                for ai, ael in enumerate(after_els):
                    if ai in matched_after:
                        continue
                    dist = center_dist(bel['bbox'], ael['bbox'])
                    if dist < center_threshold:
                        # Represent as a negative score so lower distance = higher priority
                        score = 1.0 - (dist / center_threshold)
                        if score > best_score:
                            best_score = score
                            best_ai    = ai

            if best_ai != -1:
                matches.append((bi, best_ai, best_score))
                matched_before.add(bi)
                matched_after.add(best_ai)

        # ── classify matches → changed ────────────────────────────────────────

        changed: List[Dict] = []
        for bi, ai, score in matches:
            bel = before_els[bi]
            ael = after_els[ai]
            delta: Dict = {}

            for field in ('type', 'text', 'label', 'enabled', 'visible'):
                bv = (bel.get(field) or '').strip() if isinstance(bel.get(field), str) else bel.get(field)
                av = (ael.get(field) or '').strip() if isinstance(ael.get(field), str) else ael.get(field)
                if bv != av:
                    delta[field] = {'before': bel.get(field), 'after': ael.get(field)}

            # Only report bbox change if it shifted meaningfully (> 5px)
            bb, ab = bel.get('bbox', [0,0,0,0]), ael.get('bbox', [0,0,0,0])
            if any(abs(bb[i] - ab[i]) > 5 for i in range(4)):
                delta['bbox'] = {'before': bb, 'after': ab}

            if delta:
                changed.append({
                    'element_id': bel['element_id'],       # before id (for reference)
                    'element_id_after': ael['element_id'], # after id
                    'match_score': round(score, 3),
                    'before': bel,
                    'after': ael,
                    'changes': delta
                })

        # ── unmatched → added / removed ───────────────────────────────────────

        removed = [before_els[bi] for bi in range(len(before_els)) if bi not in matched_before]
        added   = [after_els[ai]  for ai in range(len(after_els))  if ai not in matched_after]

        return {
            'added':   added,
            'removed': removed,
            'changed': changed,
            'summary': {
                'total_before':  len(before_els),
                'total_after':   len(after_els),
                'matched':       len(matches),
                'added_count':   len(added),
                'removed_count': len(removed),
                'changed_count': len(changed),
                'diff_method':   'position_iou'
            }
        }

    
    def _infer_action_from_diff(
        self,
        diff: Dict[str, Any],
        state_before: Dict[str, Any],
        state_after: Dict[str, Any],
    ) -> Tuple[Dict, Dict]:
        """
        Heuristically infer mouse/keyboard actions from a state diff.

        Used by states_to_trace() when no explicit action is supplied (e.g. video mode).

        Priority:
          1. Text grew in an element → keyboard action (type the new characters)
          2. Element was removed and was a button/link → click at its centre
          3. No distinguishable change → no_op (empty dicts)

        Returns
        -------
        (mouse_dict, keyboard_dict) compatible with transformer.train() input format.
        """
        res = state_before.get('screen_resolution', [1920, 1080])
        W   = float(res[0]) or 1920.0
        H   = float(res[1]) or 1080.0

        # --- 1. Text grew in a changed element → keyboard ----------------
        for change in diff.get('changed', []):
            text_delta = change.get('changes', {}).get('text')
            if text_delta is None:
                continue
            before_text = text_delta.get('before') or ''
            after_text  = text_delta.get('after')  or ''
            if len(after_text) > len(before_text):
                typed = after_text[len(before_text):]
                elem  = change['after']
                bbox  = elem.get('bbox', [0, 0, 0, 0])
                cx    = (bbox[0] + bbox[2]) / 2.0
                cy    = (bbox[1] + bbox[3]) / 2.0
                return (
                    {'action': 'click', 'x': cx, 'y': cy},
                    {'text': typed, 'key_count': len(typed)},
                )

        # --- 2. Button/link disappeared → click --------------------------
        for elem in diff.get('removed', []):
            if elem.get('type') in ('button', 'link'):
                bbox = elem.get('bbox', [0, 0, 0, 0])
                cx   = (bbox[0] + bbox[2]) / 2.0
                cy   = (bbox[1] + bbox[3]) / 2.0
                return ({'action': 'click', 'x': cx, 'y': cy}, {})

        # --- 3. Any added element whose bbox centre is new → click --------
        for elem in diff.get('added', []):
            if elem.get('type') in ('button', 'link', 'menu_item'):
                bbox = elem.get('bbox', [0, 0, 0, 0])
                cx   = (bbox[0] + bbox[2]) / 2.0
                cy   = (bbox[1] + bbox[3]) / 2.0
                return ({'action': 'click', 'x': cx, 'y': cy}, {})

        # --- 4. No distinguishable change --------------------------------
        return ({}, {})

    def save_trace(self, trace: Dict[str, Any], output_path: str):
        """
        Save a single trace to a JSON file.

        Args:
            trace:       Trace dictionary from state_to_trace() or states_to_trace()
            output_path: Destination .json file path
        """
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(trace, f, indent=2, ensure_ascii=False)

        print(f"Trace saved to: {output_path}")

    def save_traces(self, traces: List[Dict[str, Any]], output_dir: str):
        """
        Save a list of traces to a directory, one JSON file per trace.

        Useful after video_to_traces() when output_dir was not set.

        Args:
            traces:     List of trace dictionaries
            output_dir: Directory to save trace JSON files into

        Example:
            traces = translator.video_to_traces('recording.mp4')
            translator.save_traces(traces, 'data/output/traces/')
        """
        os.makedirs(output_dir, exist_ok=True)

        for trace in traces:
            filename = f"{trace['trace_id']}.json"
            out_path = os.path.join(output_dir, filename)
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(trace, f, indent=2, ensure_ascii=False)

        print(f"Saved {len(traces)} traces to: {output_dir}")


# ============================================================================
# EXPORTS
# ============================================================================

# MouseInput and KeyboardInput live in:
#   components/trace_translator/recorder/recorder.py

__all__ = [
    'TraceTranslator',
    'HTMLDetector',
    'CVDetector',
    'UIElementExtractor',
]
