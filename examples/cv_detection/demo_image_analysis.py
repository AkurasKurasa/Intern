"""
Test CV detection on test.png
"""

import sys
sys.path.insert(0, 'components/trace_translator')

from trace_translator import TraceTranslator
from collections import Counter
import json


def analyze_test_image():
    """Analyze test.png using CV detection."""
    
    print("="*80)
    print("CV DETECTION ON test.png")
    print("="*80)
    
    # Initialize with CV detection
    print("\n1. Initializing TraceTranslator with CV detection...")
    translator = TraceTranslator(use_cv=True)
    print("   ✓ CV detector ready")
    
    # Analyze image
    image_path = r"C:\Users\paula\OneDrive\Desktop\test.png"
    print(f"\n2. Analyzing image: {image_path}")
    
    state = translator.image_to_state(image_path)
    
    # Display results
    print(f"\n3. Results:")
    print(f"   Application: {state['application']}")
    print(f"   Resolution: {state['screen_resolution'][0]}x{state['screen_resolution'][1]}")
    print(f"   Total elements: {len(state['elements'])}")
    
    # Count by type
    types = Counter(e['type'] for e in state['elements'])
    print(f"\n4. Elements by type:")
    for element_type, count in sorted(types.items()):
        print(f"   {element_type}: {count}")
    
    # Show sample elements
    print(f"\n5. Sample detected elements (first 10):")
    for i, el in enumerate(state['elements'][:10], 1):
        text_preview = el.get('text', '')[:40] + "..." if len(el.get('text', '')) > 40 else el.get('text', '')
        print(f"   {i}. {el['type']}: '{text_preview}'")
        print(f"      Position: {el['bbox']}")
        print(f"      Confidence: {el.get('confidence', 'N/A')}")
    
    # Create trace
    print(f"\n6. Creating trace...")
    trace = translator.state_to_trace(state, trace_id='test_png_analysis')
    
    # Save trace
    output_path = 'test_data/output/test_png_trace.json'
    translator.save_trace(trace, output_path)
    print(f"   ✓ Trace saved to {output_path}")
    
    # Generate visualization
    print(f"\n7. Generating visualization...")
    viz_path = 'test_data/output/test_png_visualization.png'
    translator.visualize_trace(trace, viz_path)
    print(f"   ✓ Visualization saved to {viz_path}")
    
    print(f"\n{'='*80}")
    print("ANALYSIS COMPLETE!")
    print(f"{'='*80}")
    
    return state


if __name__ == "__main__":
    import os
    os.makedirs('test_data/output', exist_ok=True)
    
    try:
        state = analyze_test_image()
        print(f"\n✓ Analysis completed successfully!")
        
        # Summary
        types = Counter(e['type'] for e in state['elements'])
        print(f"\nSummary:")
        print(f"  Total elements: {len(state['elements'])}")
        print(f"  Cells: {types.get('cell', 0)}")
        print(f"  Labels: {types.get('label', 0)}")
        print(f"  Other: {sum(v for k, v in types.items() if k not in ['cell', 'label'])}")
        
    except Exception as e:
        print(f"\n✗ Analysis failed: {e}")
        import traceback
        traceback.print_exc()
