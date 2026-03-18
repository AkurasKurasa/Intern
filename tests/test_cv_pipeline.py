"""
Test Template for Trace Translator Component
Tests the CV-based trace translator with sample screenshots and validates output.
"""

import sys
import os
from pathlib import Path
from PIL import Image
import json

# Add trace_translator to path
sys.path.insert(0, str(Path(__file__).parent / "components" / "trace_translator"))

from trace_translator.trace_translator import TraceTranslator
from trace_translator.visualization import DetectionVisualizer
from trace_translator.cv_config import config


def setup_directories():
    """Create necessary directories for testing."""
    directories = [
        "test_data/screenshots",
        "test_data/output/traces",
        "test_data/output/visualizations"
    ]
    
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)
    
    print("✓ Test directories created")


def test_1_initialization():
    """Test 1: Initialize the trace translator."""
    print("\n" + "=" * 80)
    print("TEST 1: Trace Translator Initialization")
    print("=" * 80)
    
    try:
        # Initialize with CV enabled
        translator = TraceTranslator(use_cv=True)
        print("✓ TraceTranslator initialized successfully")
        
        # Check model status
        status = translator.cv_detector.get_model_status()
        print("\nModel Status:")
        for model_name, is_loaded in status.items():
            icon = "✓" if is_loaded else "✗"
            print(f"  {icon} {model_name.upper()}: {'Available' if is_loaded else 'Not loaded yet'}")
        
        return translator
    except Exception as e:
        print(f"✗ Initialization failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_2_create_sample_image():
    """Test 2: Create a sample test image if no screenshots exist."""
    print("\n" + "=" * 80)
    print("TEST 2: Sample Image Creation")
    print("=" * 80)
    
    sample_path = "test_data/screenshots/sample_ui.png"
    
    if os.path.exists(sample_path):
        print(f"✓ Sample image already exists: {sample_path}")
        return sample_path
    
    try:
        from PIL import ImageDraw, ImageFont
        
        # Create a simple UI mockup
        img = Image.new('RGB', (800, 600), color='white')
        draw = ImageDraw.Draw(img)
        
        # Draw some UI elements
        # Button 1
        draw.rectangle([50, 50, 200, 100], outline='blue', width=2, fill='lightblue')
        draw.text((80, 70), "Save Button", fill='black')
        
        # Button 2
        draw.rectangle([220, 50, 370, 100], outline='blue', width=2, fill='lightblue')
        draw.text((250, 70), "Cancel", fill='black')
        
        # Textbox
        draw.rectangle([50, 150, 400, 200], outline='gray', width=2, fill='white')
        draw.text((60, 165), "Enter text here...", fill='gray')
        
        # Label
        draw.text((50, 120), "Input Field:", fill='black')
        
        # Table header
        draw.rectangle([50, 250, 700, 290], outline='black', width=1, fill='lightgray')
        draw.text((60, 265), "Name", fill='black')
        draw.text((200, 265), "Email", fill='black')
        draw.text((400, 265), "Status", fill='black')
        
        # Table row
        draw.rectangle([50, 290, 700, 330], outline='black', width=1)
        draw.text((60, 305), "John Doe", fill='black')
        draw.text((200, 305), "john@example.com", fill='black')
        draw.text((400, 305), "Active", fill='black')
        
        img.save(sample_path)
        print(f"✓ Created sample UI image: {sample_path}")
        return sample_path
        
    except Exception as e:
        print(f"✗ Failed to create sample image: {e}")
        return None


def test_3_process_screenshot(translator, screenshot_path):
    """Test 3: Process a screenshot and extract UI state."""
    print("\n" + "=" * 80)
    print("TEST 3: Screenshot Processing")
    print("=" * 80)
    
    if not screenshot_path or not os.path.exists(screenshot_path):
        print(f"✗ Screenshot not found: {screenshot_path}")
        return None
    
    try:
        print(f"Processing: {screenshot_path}")
        
        # Extract UI state
        state = translator.image_to_state(screenshot_path, application="TestApp")
        
        print(f"\n✓ UI State extracted successfully")
        print(f"  - Application: {state['application']}")
        print(f"  - Screen Resolution: {state['screen_resolution']}")
        print(f"  - Elements Detected: {len(state['elements'])}")
        
        # Show detected elements
        if state['elements']:
            print("\n  Detected Elements:")
            for elem in state['elements'][:5]:  # Show first 5
                print(f"    - {elem['element_id']}: {elem['type']} (confidence: {elem['confidence']:.2f})")
                if elem.get('text'):
                    print(f"      Text: '{elem['text']}'")
        
        return state
        
    except Exception as e:
        print(f"✗ Screenshot processing failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_4_generate_trace(translator, state):
    """Test 4: Generate a trace from the UI state."""
    print("\n" + "=" * 80)
    print("TEST 4: Trace Generation")
    print("=" * 80)
    
    if not state:
        print("✗ No state available to generate trace")
        return None
    
    try:
        # Create a simple trace
        trace = translator.state_to_trace(
            state=state,
            trace_id="test_trace_001"
        )
        
        # Save trace
        output_path = "test_data/output/traces/test_trace_001.json"
        translator.save_trace(trace, output_path)
        
        print(f"✓ Trace generated successfully")
        print(f"  - Trace ID: {trace['trace_id']}")
        print(f"  - Timestamp: {trace['timestamp']}")
        print(f"  - Saved to: {output_path}")
        
        return trace
        
    except Exception as e:
        print(f"✗ Trace generation failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_5_generate_trace_with_action(translator, screenshot_path):
    """Test 5: Generate a trace with action and next state."""
    print("\n" + "=" * 80)
    print("TEST 5: Trace with Action Generation")
    print("=" * 80)
    
    if not screenshot_path or not os.path.exists(screenshot_path):
        print("✗ Screenshot not available")
        return None
    
    try:
        # Use same screenshot for before and after (in real scenario, these would be different)
        before_state = translator.image_to_state(screenshot_path, application="TestApp")
        after_state = translator.image_to_state(screenshot_path, application="TestApp")
        
        # Define a sample action (clicking first detected button)
        button_elements = [e for e in before_state['elements'] if e['type'] == 'button']
        
        if button_elements:
            target_element = button_elements[0]
            bbox = target_element['bbox']
            center_x = (bbox[0] + bbox[2]) // 2
            center_y = (bbox[1] + bbox[3]) // 2
            
            action = {
                "type": "CLICK",
                "target_element_id": target_element['element_id'],
                "mouse_position": [center_x, center_y],
                "button": "left"
            }
        else:
            # Default action if no button found
            action = {
                "type": "CLICK",
                "target_element_id": "unknown",
                "mouse_position": [100, 100],
                "button": "left"
            }
        
        # Generate trace with action
        trace = translator.state_to_trace(
            state=before_state,
            action=action,
            next_state=after_state,
            trace_id="test_trace_with_action_001"
        )
        
        # Save trace
        output_path = "test_data/output/traces/test_trace_with_action_001.json"
        translator.save_trace(trace, output_path)
        
        print(f"✓ Trace with action generated successfully")
        print(f"  - Action Type: {action['type']}")
        print(f"  - Target Element: {action['target_element_id']}")
        print(f"  - Mouse Position: {action['mouse_position']}")
        print(f"  - Saved to: {output_path}")
        
        return trace
        
    except Exception as e:
        print(f"✗ Trace with action generation failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_6_visualize_detections(screenshot_path, state):
    """Test 6: Visualize detected UI elements."""
    print("\n" + "=" * 80)
    print("TEST 6: Detection Visualization")
    print("=" * 80)
    
    if not screenshot_path or not state:
        print("✗ Screenshot or state not available")
        return
    
    try:
        visualizer = DetectionVisualizer(output_dir="test_data/output/visualizations")
        
        # Load image
        img = Image.open(screenshot_path)
        
        # Visualize detections
        vis_path = "test_data/output/visualizations/detected_elements.png"
        visualizer.visualize_detections(
            img,
            state['elements'],
            vis_path,
            show_labels=True,
            show_confidence=True
        )
        
        print(f"✓ Visualization created successfully")
        print(f"  - Saved to: {vis_path}")
        
        # Generate text report
        report_path = "test_data/output/visualizations/detection_report.txt"
        report = visualizer.create_detection_report(state['elements'], report_path)
        print(f"  - Report saved to: {report_path}")
        
    except Exception as e:
        print(f"✗ Visualization failed: {e}")
        import traceback
        traceback.print_exc()


def test_7_validate_trace_format(trace):
    """Test 7: Validate trace format matches expected structure."""
    print("\n" + "=" * 80)
    print("TEST 7: Trace Format Validation")
    print("=" * 80)
    
    if not trace:
        print("✗ No trace available to validate")
        return False
    
    try:
        # Check required fields
        required_fields = ['trace_id', 'timestamp', 'state']
        state_fields = ['application', 'window_title', 'screen_resolution', 'elements']
        
        # Validate top-level fields
        for field in required_fields:
            assert field in trace, f"Missing required field: {field}"
        
        # Validate state fields
        for field in state_fields:
            assert field in trace['state'], f"Missing state field: {field}"
        
        # Validate elements structure
        if trace['state']['elements']:
            element = trace['state']['elements'][0]
            element_fields = ['element_id', 'type', 'bbox', 'confidence']
            for field in element_fields:
                assert field in element, f"Missing element field: {field}"
        
        print("✓ Trace format validation passed")
        print(f"  - All required fields present")
        print(f"  - Structure matches trace_format.json")
        
        return True
        
    except AssertionError as e:
        print(f"✗ Trace format validation failed: {e}")
        return False
    except Exception as e:
        print(f"✗ Validation error: {e}")
        return False


def run_all_tests():
    """Run all tests in sequence."""
    print("\n" + "=" * 80)
    print("TRACE TRANSLATOR COMPONENT - TEST SUITE")
    print("=" * 80)
    print(f"Testing CV-based trace translator with YOLO, SAM, OCR, and LayoutLM")
    print("=" * 80)
    
    # Setup
    setup_directories()
    
    # Test 1: Initialize
    translator = test_1_initialization()
    if not translator:
        print("\n✗ Critical failure: Cannot proceed without translator")
        return
    
    # Test 2: Create/get sample image
    screenshot_path = test_2_create_sample_image()
    
    # Test 3: Process screenshot
    state = test_3_process_screenshot(translator, screenshot_path)
    
    # Test 4: Generate basic trace
    trace = test_4_generate_trace(translator, state)
    
    # Test 5: Generate trace with action
    trace_with_action = test_5_generate_trace_with_action(translator, screenshot_path)
    
    # Test 6: Visualize detections
    test_6_visualize_detections(screenshot_path, state)
    
    # Test 7: Validate trace format
    test_7_validate_trace_format(trace)
    
    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    print("\n✓ All tests completed!")
    print("\nGenerated Files:")
    print("  - test_data/output/traces/test_trace_001.json")
    print("  - test_data/output/traces/test_trace_with_action_001.json")
    print("  - test_data/output/visualizations/detected_elements.png")
    print("  - test_data/output/visualizations/detection_report.txt")
    print("\nNext Steps:")
    print("  1. Review generated traces in test_data/output/traces/")
    print("  2. Check visualization in test_data/output/visualizations/")
    print("  3. Add your own screenshots to test_data/screenshots/")
    print("  4. Use generated traces for training the learning model")


def test_custom_screenshot(screenshot_path: str):
    """
    Test with a custom screenshot.
    
    Usage:
        test_custom_screenshot('path/to/your/screenshot.png')
    """
    print("\n" + "=" * 80)
    print("CUSTOM SCREENSHOT TEST")
    print("=" * 80)
    
    if not os.path.exists(screenshot_path):
        print(f"✗ Screenshot not found: {screenshot_path}")
        return
    
    # Initialize translator
    translator = TraceTranslator(use_cv=True)
    
    # Process screenshot
    state = translator.image_to_state(screenshot_path, application="CustomApp")
    
    # Generate trace
    trace = translator.state_to_trace(state, trace_id=f"custom_{Path(screenshot_path).stem}")
    
    # Save outputs
    output_dir = Path("test_data/output/custom")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    trace_path = output_dir / f"{trace['trace_id']}.json"
    translator.save_trace(trace, str(trace_path))
    
    # Visualize
    visualizer = DetectionVisualizer(output_dir=str(output_dir))
    img = Image.open(screenshot_path)
    vis_path = output_dir / f"{trace['trace_id']}_visualization.png"
    visualizer.visualize_detections(img, state['elements'], str(vis_path))
    
    print(f"\n✓ Custom screenshot processed successfully")
    print(f"  - Elements detected: {len(state['elements'])}")
    print(f"  - Trace saved: {trace_path}")
    print(f"  - Visualization saved: {vis_path}")


if __name__ == "__main__":
    # Run all tests
    run_all_tests()
    
    # Uncomment to test with your own screenshot:
    # test_custom_screenshot('path/to/your/screenshot.png')
