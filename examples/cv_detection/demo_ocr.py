"""
Alternative UI Detection Approach
Uses OCR-based detection as fallback when YOLO doesn't detect UI elements.
This provides immediate results without requiring model training.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "components" / "trace_translator"))

from trace_translator.trace_translator import TraceTranslator
from trace_translator.visualization import DetectionVisualizer
from PIL import Image
import json


def detect_with_ocr_fallback(screenshot_path: str):
    """
    Detect UI elements using OCR as primary method.
    This works immediately without training.
    """
    print("\n" + "=" * 80)
    print("OCR-BASED UI DETECTION (No Training Required)")
    print("=" * 80)
    print(f"\nProcessing: {screenshot_path}\n")
    
    # Initialize translator
    translator = TraceTranslator(use_cv=True)
    
    # Process with OCR + LayoutLM enabled
    print("Running detection with OCR + LayoutLM...")
    state = translator.image_to_state(screenshot_path, application="DetectedApp")
    
    print(f"\n✓ Detection complete!")
    print(f"  - Elements detected: {len(state['elements'])}")
    
    if state['elements']:
        print("\n  Detected Elements:")
        for i, elem in enumerate(state['elements'][:10], 1):  # Show first 10
            print(f"    {i}. {elem['element_id']}: {elem['type']}")
            if elem.get('text'):
                print(f"       Text: '{elem['text'][:50]}'")
            if elem.get('label'):
                print(f"       Label: '{elem['label'][:50]}'")
            print(f"       BBox: {elem['bbox']}")
            print(f"       Confidence: {elem['confidence']:.2f}")
    else:
        print("\n  ⚠️  No elements detected")
        print("  Possible reasons:")
        print("    - Tesseract OCR not installed")
        print("    - Image has no text")
        print("    - Text is too small or unclear")
    
    # Generate trace
    trace = translator.state_to_trace(state, trace_id="ocr_detection_test")
    
    # Save outputs
    output_dir = Path("test_data/output/ocr_detection")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    trace_path = output_dir / "trace.json"
    translator.save_trace(trace, str(trace_path))
    print(f"\n✓ Trace saved: {trace_path}")
    
    # Visualize
    visualizer = DetectionVisualizer(output_dir=str(output_dir))
    img = Image.open(screenshot_path)
    vis_path = output_dir / "visualization.png"
    visualizer.visualize_detections(img, state['elements'], str(vis_path))
    print(f"✓ Visualization saved: {vis_path}")
    
    # Report
    report_path = output_dir / "report.txt"
    visualizer.create_detection_report(state['elements'], str(report_path))
    print(f"✓ Report saved: {report_path}")
    
    return state, trace


def check_tesseract():
    """Check if Tesseract is installed."""
    print("\n" + "=" * 80)
    print("CHECKING TESSERACT OCR")
    print("=" * 80)
    
    try:
        import pytesseract
        version = pytesseract.get_tesseract_version()
        print(f"\n✓ Tesseract OCR is installed: v{version}")
        return True
    except Exception as e:
        print(f"\n✗ Tesseract OCR not found")
        print(f"\nTo install Tesseract:")
        print("  1. Download: https://github.com/UB-Mannheim/tesseract/wiki")
        print("  2. Run installer")
        print("  3. Add to PATH")
        print("  4. Restart terminal")
        print(f"\nError: {e}")
        return False


def explain_options():
    """Explain why detection is limited and what to do."""
    print("\n" + "=" * 80)
    print("WHY UI ELEMENTS AREN'T DETECTED")
    print("=" * 80)
    
    print("\n📌 The Problem:")
    print("  - YOLO is pre-trained on everyday objects (cars, people, animals)")
    print("  - YOLO was NOT trained on UI elements (buttons, textboxes, etc.)")
    print("  - Result: YOLO can't recognize UI components")
    
    print("\n✅ Solution 1: Use OCR (Easiest)")
    print("  - Install Tesseract OCR")
    print("  - Detects text regions (most UI elements have text)")
    print("  - Works immediately, no training needed")
    
    print("\n✅ Solution 2: Enable LayoutLM (Already Done)")
    print("  - LayoutLM understands document/UI layouts")
    print("  - Already integrated in your code")
    print("  - Now enabled by default")
    
    print("\n✅ Solution 3: Train Custom YOLO (Best Long-term)")
    print("  - Collect 500-1000 screenshots")
    print("  - Label UI elements")
    print("  - Train YOLOv8 on your data")
    print("  - Achieves 90%+ accuracy")
    
    print("\n" + "=" * 80)


if __name__ == "__main__":
    # Explain the situation
    explain_options()
    
    # Check Tesseract
    has_tesseract = check_tesseract()
    
    if not has_tesseract:
        print("\n⚠️  Install Tesseract OCR for better results!")
    
    # Test with your screenshot
    screenshot = r"C:\Users\paula\OneDrive\Desktop\Screenshot 2026-02-07 163312.png"
    
    print("\n" + "=" * 80)
    print("TESTING WITH YOUR SCREENSHOT")
    print("=" * 80)
    
    try:
        state, trace = detect_with_ocr_fallback(screenshot)
        
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"\n✓ Processing complete!")
        print(f"  - Elements detected: {len(state['elements'])}")
        print(f"  - Output directory: test_data/output/ocr_detection/")
        
        if len(state['elements']) == 0:
            print("\n💡 To improve detection:")
            print("  1. Install Tesseract OCR (most important)")
            print("  2. Use screenshots with clear text")
            print("  3. Consider training custom YOLO model")
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
