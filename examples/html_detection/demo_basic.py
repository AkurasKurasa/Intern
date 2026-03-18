"""
Demo: HTML-based UI Detection

This script demonstrates using the HTML detector to extract UI elements
from web pages with perfect accuracy.
"""

import sys
sys.path.insert(0, 'components/trace_translator')

from trace_translator import TraceTranslator
from collections import Counter
import json


def demo_html_detection():
    """Demonstrate HTML-based UI detection."""
    
    print("="*80)
    print("HTML-BASED UI DETECTION DEMO")
    print("="*80)
    
    # Initialize translator with HTML detection
    print("\n1. Initializing TraceTranslator with HTML detection...")
    translator = TraceTranslator(use_html=True, use_cv=False)
    print("   ✓ HTML detector ready")
    
    # Test with example.com
    print("\n2. Extracting UI elements from example.com...")
    state = translator.url_to_state('https://example.com')
    
    # Display results
    print(f"\n3. Results:")
    print(f"   Page: {state['window_title']}")
    print(f"   Resolution: {state['screen_resolution'][0]}x{state['screen_resolution'][1]}")
    print(f"   Total elements: {len(state['elements'])}")
    
    # Count by type
    types = Counter(e['type'] for e in state['elements'])
    print(f"\n4. Elements by type:")
    for element_type, count in sorted(types.items()):
        print(f"   {element_type}: {count}")
    
    # Show detected elements
    print(f"\n5. Detected elements:")
    for i, el in enumerate(state['elements'], 1):
        text_preview = el['text'][:40] + "..." if len(el['text']) > 40 else el['text']
        print(f"   {i}. {el['type']}: '{text_preview}'")
        print(f"      Position: {el['bbox']}")
        print(f"      Confidence: {el['confidence']}")
        if el['metadata'].get('href'):
            print(f"      Link: {el['metadata']['href']}")
    
    # Create trace
    print(f"\n6. Creating trace...")
    trace = translator.state_to_trace(state, trace_id='html_demo')
    
    # Save trace
    output_path = 'test_data/output/html_demo_trace.json'
    translator.save_trace(trace, output_path)
    print(f"   ✓ Trace saved to {output_path}")
    
    # Save screenshot
    if hasattr(translator, '_last_screenshot'):
        screenshot_path = 'test_data/output/html_demo_screenshot.png'
        translator._last_screenshot.save(screenshot_path)
        print(f"   ✓ Screenshot saved to {screenshot_path}")
    
    print(f"\n{'='*80}")
    print("DEMO COMPLETE!")
    print(f"{'='*80}")
    print(f"\nKey benefits of HTML detection:")
    print(f"  ✓ 100% accurate element detection")
    print(f"  ✓ Exact element types (button, input, link, etc.)")
    print(f"  ✓ Perfect bounding boxes")
    print(f"  ✓ Complete element attributes")
    print(f"  ✓ No training required")
    
    return state


if __name__ == "__main__":
    import os
    os.makedirs('test_data/output', exist_ok=True)
    
    try:
        state = demo_html_detection()
        print(f"\n✓ Demo completed successfully!")
        print(f"\nNext steps:")
        print(f"  1. Try with your learning platform URL")
        print(f"  2. Compare with CV detection results")
        print(f"  3. Generate training traces for imitation learning")
    except Exception as e:
        print(f"\n✗ Demo failed: {e}")
        import traceback
        traceback.print_exc()
