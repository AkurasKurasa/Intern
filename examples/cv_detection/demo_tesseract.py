"""
Quick test to verify Tesseract OCR is working with text detection.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "components" / "trace_translator"))

# This will auto-configure Tesseract
from trace_translator import tesseract_config

from trace_translator.trace_translator import TraceTranslator
from trace_translator.visualization import DetectionVisualizer
from PIL import Image

def test_ocr_with_screenshot(screenshot_path):
    """Test OCR detection with your screenshot."""
    print("\n" + "=" * 80)
    print("TESTING TESSERACT OCR DETECTION")
    print("=" * 80)
    
    # Initialize translator
    print("\nInitializing trace translator with OCR enabled...")
    translator = TraceTranslator(use_cv=True)
    
    # Process screenshot
    print(f"Processing: {screenshot_path}")
    state = translator.image_to_state(screenshot_path, application="TestApp")
    
    # Show results
    print(f"\n{'='*80}")
    print("DETECTION RESULTS")
    print("=" * 80)
    print(f"\nElements detected: {len(state['elements'])}")
    
    if state['elements']:
        print("\nDetected Elements:")
        for i, elem in enumerate(state['elements'], 1):
            print(f"\n{i}. {elem['element_id']}")
            print(f"   Type: {elem['type']}")
            print(f"   BBox: {elem['bbox']}")
            print(f"   Confidence: {elem['confidence']:.2f}")
            if elem.get('text'):
                print(f"   Text: '{elem['text']}'")
            if elem.get('label'):
                print(f"   Label: '{elem['label']}'")
    else:
        print("\n⚠️  No elements detected")
        print("This could mean:")
        print("  - Screenshot has no text")
        print("  - Text is too small")
        print("  - OCR couldn't read the text")
    
    # Save outputs
    output_dir = Path("test_data/output/tesseract_test")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Save trace
    trace = translator.state_to_trace(state, trace_id="tesseract_test")
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
    
    print("\n" + "=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)
    
    return state

if __name__ == "__main__":
    screenshot = r"C:\Users\paula\OneDrive\Desktop\test.png"
    state = test_ocr_with_screenshot(screenshot)
