# Trace Translator Configuration Guide

This guide explains how to configure and customize the CV Trace Translator system. By understanding these concepts, you can adapt the system for different applications and use cases.

---

## Table of Contents

1. [System Architecture](#system-architecture)
2. [Configuration Files](#configuration-files)
3. [Configuring Detection Models](#configuring-detection-models)
4. [Grid Detection Configuration](#grid-detection-configuration)
5. [OCR Configuration](#ocr-configuration)
6. [Creating Custom Detectors](#creating-custom-detectors)
7. [Usage Examples](#usage-examples)
8. [Troubleshooting](#troubleshooting)

---

## System Architecture

### Component Overview

```
TraceTranslator (Main Interface)
    ├── CVDetector (Orchestrates all CV models)
    │   ├── YOLO (Object detection)
    │   ├── SAM (Segmentation)
    │   ├── Tesseract OCR (Text detection)
    │   ├── LayoutLM (Layout understanding)
    │   └── GridDetector (Cell/table detection)
    ├── UIElementExtractor (Structures results)
    └── DetectionVisualizer (Creates visualizations)
```

### Data Flow

```
Screenshot → CVDetector → UIElementExtractor → Trace JSON
                ↓
         Visualization
```

---

## Configuration Files

### 1. `cv_config.py` - Main Configuration

**Location**: `components/trace_translator/trace_translator/cv_config.py`

This is the central configuration file for all CV models.

#### Key Sections:

```python
class CVConfig:
    def __init__(self):
        # Model paths
        self.models_dir = Path.home() / ".cache" / "trace_translator" / "models"
        
        # YOLO configuration
        self.yolo_model_name = "yolov8n.pt"  # Options: yolov8n, yolov8s, yolov8m, yolov8l, yolov8x
        self.yolo_conf_threshold = 0.25      # Confidence threshold (0.0-1.0)
        self.yolo_iou_threshold = 0.45       # IoU threshold for NMS
        
        # SAM configuration
        self.sam_model_type = "vit_b"        # Options: vit_b, vit_l, vit_h
        
        # Tesseract configuration
        self.tesseract_lang = "eng"          # Language code
        self.tesseract_config = "--psm 11"   # Page segmentation mode
        
        # LayoutLM configuration
        self.layoutlm_model = "microsoft/layoutlmv3-base"
        self.layoutlm_conf_threshold = 0.5
        
        # Device selection
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
```

#### How to Modify:

**Example 1: Use a larger YOLO model for better accuracy**
```python
# In cv_config.py, change:
self.yolo_model_name = "yolov8m.pt"  # Medium model (more accurate, slower)
```

**Example 2: Adjust confidence threshold**
```python
# Lower threshold to detect more objects (may include false positives)
self.yolo_conf_threshold = 0.15

# Higher threshold for fewer, more confident detections
self.yolo_conf_threshold = 0.50
```

**Example 3: Change Tesseract language**
```python
# For Chinese text
self.tesseract_lang = "chi_sim"

# For multiple languages
self.tesseract_lang = "eng+chi_sim"
```

---

## Configuring Detection Models

### YOLO (Object Detection)

**Purpose**: Detects UI elements like buttons, text boxes, images, etc.

**Configuration Options**:

```python
# In cv_config.py
self.yolo_model_name = "yolov8n.pt"  # Model size
self.yolo_conf_threshold = 0.25      # Minimum confidence
self.yolo_iou_threshold = 0.45       # Overlap threshold
```

**Model Size Options**:
- `yolov8n.pt` - Nano (fastest, least accurate)
- `yolov8s.pt` - Small
- `yolov8m.pt` - Medium (balanced)
- `yolov8l.pt` - Large
- `yolov8x.pt` - Extra large (slowest, most accurate)

**When to adjust**:
- **Lower confidence**: Detect more elements (may get false positives)
- **Higher confidence**: Detect fewer, more certain elements
- **Larger model**: Better accuracy, slower processing
- **Smaller model**: Faster processing, less accurate

### Tesseract OCR (Text Detection)

**Purpose**: Extracts text from images.

**Configuration Options**:

```python
# In cv_config.py
self.tesseract_lang = "eng"              # Language
self.tesseract_config = "--psm 11"       # Page segmentation mode
self.tesseract_min_confidence = 30       # Minimum confidence (0-100)
```

**Page Segmentation Modes (PSM)**:
- `--psm 3` - Fully automatic (default)
- `--psm 6` - Uniform block of text
- `--psm 11` - Sparse text (best for UI elements)
- `--psm 12` - Sparse text with OSD

**Example: Configure for dense text**
```python
self.tesseract_config = "--psm 6"  # Better for paragraphs
```

**Example: Configure for single words**
```python
self.tesseract_config = "--psm 8"  # Single word mode
```

### LayoutLM (Layout Understanding)

**Purpose**: Understands document structure and layout.

**Configuration Options**:

```python
# In cv_config.py
self.layoutlm_model = "microsoft/layoutlmv3-base"
self.layoutlm_conf_threshold = 0.5
```

**Model Options**:
- `microsoft/layoutlmv3-base` - Base model (faster)
- `microsoft/layoutlmv3-large` - Large model (more accurate)

---

## Grid Detection Configuration

### GridDetector Settings

**Location**: `components/trace_translator/trace_translator/grid_detector.py`

**Purpose**: Detects cells in spreadsheets and tables.

#### Basic Configuration:

```python
# When initializing GridDetector
grid_detector = GridDetector(
    min_line_length=50,    # Minimum line length to detect (pixels)
    line_thickness=1       # Expected line thickness (pixels)
)
```

#### Advanced Configuration:

**1. Worksheet Detection**

```python
def _find_worksheet_start(self, image):
    # Scan range for worksheet boundary
    for y in range(50, min(200, height)):  # Adjust range as needed
        # ...
```

**Adjust for different applications**:
- **Excel**: `range(50, 200)` - Ribbon is ~100px
- **Google Sheets**: `range(30, 150)` - Smaller toolbar
- **Custom app**: Measure your app's header height

**2. Cell Size Estimation**

```python
def _estimate_cell_size_from_worksheet(self, image, start_x, start_y):
    # Extract worksheet region
    worksheet_region = gray[start_y:start_y+400, start_x:start_x+800]
    
    # Adjust region size based on your needs
```

**Customize for your use case**:
```python
# For larger cells (zoomed in)
worksheet_region = gray[start_y:start_y+600, start_x:start_x+1000]

# For smaller cells (zoomed out)
worksheet_region = gray[start_y:start_y+200, start_x:start_x+400]
```

**3. Grid Generation Limits**

```python
def _detect_excel_grid(self, image):
    # Limit columns to reasonable number
    if col_idx >= 50:  # Change this limit
        break
    
    # Limit rows to reasonable number
    if row_idx >= 50:  # Change this limit
        break
```

**Adjust for your needs**:
```python
# For larger spreadsheets
if col_idx >= 100:  # Detect up to 100 columns
    break
if row_idx >= 200:  # Detect up to 200 rows
    break
```

**4. Edge Detection Parameters**

```python
def _detect_cells_with_edges(self, gray_image, image_size):
    # Edge detection thresholds
    edges = cv2.Canny(blurred, 30, 100)  # (low, high)
    
    # Hough line detection
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi/180,
        threshold=50,        # Adjust for sensitivity
        minLineLength=50,    # Minimum line length
        maxLineGap=20        # Maximum gap in line
    )
```

**For faint gridlines**:
```python
edges = cv2.Canny(blurred, 20, 80)  # Lower thresholds
threshold=30  # More sensitive
```

**For strong gridlines**:
```python
edges = cv2.Canny(blurred, 50, 150)  # Higher thresholds
threshold=100  # Less sensitive
```

---

## OCR Configuration

### Tesseract Setup

**1. Auto-configuration** (already done):

The system automatically finds Tesseract via `tesseract_config.py`:

```python
# tesseract_config.py
TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    # Add custom paths here
]
```

**2. Manual configuration**:

```python
import pytesseract

# Set custom path
pytesseract.pytesseract.tesseract_cmd = r"C:\Your\Custom\Path\tesseract.exe"
```

### OCR Parameters in CVDetector

```python
def _extract_text_with_ocr(self, image):
    # Get OCR data
    ocr_data = pytesseract.image_to_data(
        image,
        lang=self.config.tesseract_lang,
        config=self.config.tesseract_config,
        output_type=pytesseract.Output.DICT
    )
    
    # Filter by confidence
    min_conf = self.config.tesseract_min_confidence  # Adjust this
```

**Customize confidence filtering**:
```python
# In cv_config.py
self.tesseract_min_confidence = 50  # Higher = fewer, more confident results
self.tesseract_min_confidence = 20  # Lower = more results, may include noise
```

---

## Creating Custom Detectors

### Example: Custom Application Detector

Let's create a detector for a custom application:

**1. Create new detector file**:

```python
# custom_app_detector.py
import cv2
import numpy as np
from PIL import Image
from typing import List, Dict, Any

class CustomAppDetector:
    """Detector for YourCustomApp."""
    
    def __init__(self, toolbar_height: int = 80):
        self.toolbar_height = toolbar_height
    
    def detect_elements(self, image: Image.Image) -> List[Dict[str, Any]]:
        """Detect UI elements specific to your app."""
        elements = []
        
        # Convert to OpenCV format
        img_cv = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
        
        # Skip toolbar area
        content_area = gray[self.toolbar_height:, :]
        
        # Your custom detection logic here
        # Example: Detect buttons by color
        elements.extend(self._detect_buttons(img_cv))
        
        # Example: Detect input fields
        elements.extend(self._detect_input_fields(content_area))
        
        return elements
    
    def _detect_buttons(self, image):
        """Detect buttons by color/shape."""
        # Your implementation
        pass
    
    def _detect_input_fields(self, image):
        """Detect input fields."""
        # Your implementation
        pass
```

**2. Integrate with CVDetector**:

```python
# In cv_detector.py
from .custom_app_detector import CustomAppDetector

class CVDetector:
    def __init__(self, cv_config=None):
        # ... existing code ...
        self.custom_detector = CustomAppDetector()
    
    def detect_ui_elements(self, image, use_custom=False, **kwargs):
        results = {
            "objects": [],
            "masks": [],
            "text_regions": [],
            "layout_elements": [],
            "grid_cells": [],
            "custom_elements": []  # New
        }
        
        # ... existing detection code ...
        
        if use_custom:
            print("Running custom app detection...")
            results["custom_elements"] = self.custom_detector.detect_elements(image)
        
        return results
```

---

## Usage Examples

### Example 1: Basic Usage

```python
from trace_translator import TraceTranslator

# Initialize with default settings
translator = TraceTranslator(use_cv=True)

# Process screenshot
state = translator.image_to_state('screenshot.png', application='Excel')

# Generate trace
trace = translator.state_to_trace(state, trace_id='my_trace')

# Save
translator.save_trace(trace, 'output/trace.json')
```

### Example 2: Custom Configuration

```python
from trace_translator import TraceTranslator, CVConfig

# Create custom config
config = CVConfig()
config.yolo_conf_threshold = 0.35  # Higher confidence
config.tesseract_min_confidence = 40  # More confident text
config.yolo_model_name = "yolov8m.pt"  # Larger model

# Initialize with custom config
translator = TraceTranslator(use_cv=True, cv_config=config)

# Use as normal
state = translator.image_to_state('screenshot.png')
```

### Example 3: Selective Detection

```python
from trace_translator.cv_detector import CVDetector
from trace_translator import CVConfig

# Initialize detector
config = CVConfig()
detector = CVDetector(config)

# Load image
from PIL import Image
img = Image.open('screenshot.png')

# Run only specific detections
results = detector.detect_ui_elements(
    img,
    use_yolo=False,      # Skip object detection
    use_sam=False,       # Skip segmentation
    use_ocr=True,        # Use OCR
    use_layoutlm=False,  # Skip layout
    use_grid=True        # Use grid detection
)

# Results contain only OCR and grid cells
print(f"Text regions: {len(results['text_regions'])}")
print(f"Grid cells: {len(results['grid_cells'])}")
```

### Example 4: Batch Processing

```python
from trace_translator import TraceTranslator
from pathlib import Path

translator = TraceTranslator(use_cv=True)

# Process all screenshots in a folder
screenshot_dir = Path('screenshots')
output_dir = Path('traces')
output_dir.mkdir(exist_ok=True)

for screenshot in screenshot_dir.glob('*.png'):
    print(f"Processing {screenshot.name}...")
    
    # Generate trace
    state = translator.image_to_state(str(screenshot), application='Excel')
    trace = translator.state_to_trace(state, trace_id=screenshot.stem)
    
    # Save
    output_path = output_dir / f"{screenshot.stem}.json"
    translator.save_trace(trace, str(output_path))
    
    print(f"  Detected {len(state['elements'])} elements")
```

---

## Troubleshooting

### Issue: Too many false positives

**Solution**: Increase confidence thresholds

```python
# In cv_config.py
self.yolo_conf_threshold = 0.40  # Higher threshold
self.tesseract_min_confidence = 50  # More confident text
```

### Issue: Missing elements

**Solution**: Lower confidence thresholds or use larger model

```python
# Lower thresholds
self.yolo_conf_threshold = 0.15
self.tesseract_min_confidence = 20

# Or use larger model
self.yolo_model_name = "yolov8m.pt"
```

### Issue: Grid detection not finding cells

**Solution**: Adjust edge detection parameters

```python
# In grid_detector.py, _detect_cells_with_edges()
edges = cv2.Canny(blurred, 20, 80)  # Lower thresholds for faint lines

# Adjust Hough parameters
threshold=30,        # More sensitive
minLineLength=30,    # Shorter lines
maxLineGap=30        # Larger gaps allowed
```

### Issue: Cells detected in wrong area

**Solution**: Adjust worksheet start detection

```python
# In grid_detector.py, _find_worksheet_start()
for y in range(50, min(200, height)):  # Adjust range
    # ...

# Or manually set worksheet start
def _find_worksheet_start(self, image):
    return 120  # Fixed value for your app
```

### Issue: Slow processing

**Solution**: Use smaller models or disable unused detections

```python
# Use smaller YOLO model
self.yolo_model_name = "yolov8n.pt"

# Disable unused detections
results = detector.detect_ui_elements(
    img,
    use_yolo=False,      # Skip if not needed
    use_sam=False,       # Skip if not needed
    use_layoutlm=False   # Skip if not needed
)
```

---

## Summary

**Key Configuration Files**:
1. `cv_config.py` - Model settings and thresholds
2. `grid_detector.py` - Grid detection parameters
3. `tesseract_config.py` - OCR path configuration

**Common Adjustments**:
- **Accuracy vs Speed**: Model size (`yolov8n` vs `yolov8m`)
- **Sensitivity**: Confidence thresholds (0.15 - 0.50)
- **Grid Detection**: Edge detection parameters, worksheet boundaries
- **OCR**: Language, PSM mode, confidence threshold

**Best Practices**:
1. Start with default settings
2. Test on your specific screenshots
3. Adjust one parameter at a time
4. Document your changes
5. Create custom configs for different use cases

Now you can configure the system yourself! Experiment with different settings to find what works best for your specific application.
