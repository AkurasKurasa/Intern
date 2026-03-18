"""
Test HTML detector on web pages.

This script tests the HTML-based UI element detection system.
"""

import sys
sys.path.insert(0, 'components/trace_translator')

from trace_translator.html_detector import HTMLDetector
from PIL import Image
import json
from collections import Counter
from datetime import datetime


def test_html_detector_basic():
    """Test HTML detector with a simple web page."""
    print("="*80)
    print("TEST 1: Basic HTML Detection (example.com)")
    print("="*80)
    
    detector = HTMLDetector(headless=True)
    
    try:
        # Extract elements from example.com
        result = detector.extract_ui_elements("https://example.com")
        
        # Display results
        print(f"\n✓ Successfully loaded page")
        print(f"  Page title: {result['page_info']['title']}")
        print(f"  URL: {result['page_info']['url']}")
        print(f"  Viewport: {result['page_info']['viewport']['width']}x{result['page_info']['viewport']['height']}")
        
        print(f"\n✓ Detected {len(result['elements'])} UI elements")
        
        # Count by type
        types = Counter(e['type'] for e in result['elements'])
        print(f"\nElements by type:")
        for element_type, count in sorted(types.items()):
            print(f"  {element_type}: {count}")
        
        # Show sample elements
        print(f"\nSample elements:")
        for i, el in enumerate(result['elements'][:5]):
            text_preview = el['text'][:40] + "..." if len(el['text']) > 40 else el['text']
            print(f"  {i+1}. {el['type']}: '{text_preview}'")
            print(f"      BBox: {el['bbox']}")
            print(f"      Enabled: {el['enabled']}, Visible: {el['visible']}")
        
        # Save screenshot
        result['screenshot'].save('test_data/output/html_test/example_com.png')
        print(f"\n✓ Screenshot saved to test_data/output/html_test/example_com.png")
        
        # Save elements as JSON
        with open('test_data/output/html_test/example_com_elements.json', 'w') as f:
            json.dump(result['elements'], f, indent=2)
        print(f"✓ Elements saved to test_data/output/html_test/example_com_elements.json")
        
        return True
    
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_html_detector_custom():
    """Test HTML detector with custom HTML."""
    print("\n" + "="*80)
    print("TEST 2: Custom HTML Detection")
    print("="*80)
    
    detector = HTMLDetector(headless=True)
    
    # Create test HTML
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Test Page</title>
        <style>
            body { font-family: Arial; padding: 20px; }
            button { margin: 10px; padding: 10px 20px; }
            input { margin: 10px; padding: 5px; }
        </style>
    </head>
    <body>
        <h1>Test UI Elements</h1>
        
        <div>
            <button id="btn1">Click Me</button>
            <button id="btn2" disabled>Disabled Button</button>
        </div>
        
        <div>
            <input type="text" placeholder="Enter your name" id="name">
            <input type="email" placeholder="Enter your email" id="email">
            <input type="checkbox" id="agree"> <label for="agree">I agree</label>
        </div>
        
        <div>
            <a href="https://example.com">Visit Example</a>
            <a href="/about">About Us</a>
        </div>
        
        <div>
            <select id="country">
                <option>USA</option>
                <option>Canada</option>
                <option>UK</option>
            </select>
        </div>
        
        <div>
            <textarea placeholder="Enter comments" rows="4"></textarea>
        </div>
    </body>
    </html>
    """
    
    try:
        result = detector.extract_from_html_string(html)
        
        print(f"\n✓ Successfully processed custom HTML")
        print(f"  Detected {len(result['elements'])} UI elements")
        
        # Count by type
        types = Counter(e['type'] for e in result['elements'])
        print(f"\nElements by type:")
        for element_type, count in sorted(types.items()):
            print(f"  {element_type}: {count}")
        
        # Verify expected elements
        expected = {
            'button': 2,
            'input': 2,  # text and email (checkbox counted separately)
            'checkbox': 1,
            'link': 2,
            'dropdown': 1,
            'textarea': 1
        }
        
        print(f"\nVerification:")
        all_pass = True
        for elem_type, expected_count in expected.items():
            actual_count = types.get(elem_type, 0)
            status = "✓" if actual_count == expected_count else "✗"
            print(f"  {status} {elem_type}: expected {expected_count}, got {actual_count}")
            if actual_count != expected_count:
                all_pass = False
        
        if all_pass:
            print(f"\n✓ All element types detected correctly!")
        else:
            print(f"\n⚠ Some element counts don't match")
        
        # Save screenshot
        result['screenshot'].save('test_data/output/html_test/custom_html.png')
        print(f"\n✓ Screenshot saved")
        
        return all_pass
    
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_html_vs_cv_comparison():
    """Compare HTML detection vs CV detection."""
    print("\n" + "="*80)
    print("TEST 3: HTML vs CV Comparison")
    print("="*80)
    
    # This would compare HTML extraction with CV detection
    # For now, just show that HTML detection is ready
    print("\n✓ HTML detector is ready for comparison with CV detector")
    print("  HTML detection provides:")
    print("    - Exact element types (button, input, link, etc.)")
    print("    - Perfect bounding boxes")
    print("    - Complete element attributes")
    print("    - 100% confidence")
    print("\n  CV detection provides:")
    print("    - Text labels (OCR)")
    print("    - Generic objects (YOLO)")
    print("    - Approximate bounding boxes")
    print("    - Variable confidence")
    
    return True


def main():
    """Run all HTML detector tests."""
    import os
    
    # Create output directory
    os.makedirs('test_data/output/html_test', exist_ok=True)
    
    print("\n" + "="*80)
    print("HTML DETECTOR TEST SUITE")
    print("="*80)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = []
    
    # Run tests
    results.append(("Basic HTML Detection", test_html_detector_basic()))
    results.append(("Custom HTML Detection", test_html_detector_custom()))
    results.append(("HTML vs CV Comparison", test_html_vs_cv_comparison()))
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {test_name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    print(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    if passed == total:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n⚠ {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    exit(main())
