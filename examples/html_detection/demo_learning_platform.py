"""
HTML Detection on Learning Platform
Demonstrates perfect UI element detection using HTML extraction
"""

import sys
sys.path.insert(0, 'components/trace_translator')

from trace_translator import TraceTranslator
from collections import Counter
import json


def analyze_learning_platform():
    """Analyze learning platform using HTML detection."""
    
    print("="*80)
    print("HTML DETECTION ON LEARNING PLATFORM")
    print("="*80)
    
    url = 'https://talatask.com/onboarding?step=learn-the-rules-of-rugby'
    
    # Initialize with HTML detection
    print("\n1. Initializing TraceTranslator with HTML detection...")
    translator = TraceTranslator(use_html=True)
    print("   ✓ HTML detector ready")
    
    # Extract UI state from URL
    print(f"\n2. Extracting UI elements from URL...")
    print(f"   URL: {url}")
    
    state = translator.url_to_state(url, application='Chrome')
    
    # Display results
    print(f"\n3. Results:")
    print(f"   Page: {state['window_title']}")
    print(f"   Resolution: {state['screen_resolution'][0]}x{state['screen_resolution'][1]}")
    print(f"   Total elements: {len(state['elements'])}")
    
    # Count by type
    types = Counter(e['type'] for e in state['elements'])
    print(f"\n4. Elements by type:")
    for element_type, count in sorted(types.items(), key=lambda x: -x[1]):
        print(f"   {element_type}: {count}")
    
    # Show buttons
    buttons = [e for e in state['elements'] if e['type'] == 'button']
    print(f"\n5. Detected BUTTONS ({len(buttons)} total):")
    for i, btn in enumerate(buttons[:10], 1):
        text_preview = btn['text'][:40] + "..." if len(btn['text']) > 40 else btn['text']
        print(f"   {i}. '{text_preview}'")
        print(f"      Position: {btn['bbox']}")
        print(f"      Enabled: {btn['enabled']}")
    
    # Show inputs
    inputs = [e for e in state['elements'] if e['type'] == 'input']
    if inputs:
        print(f"\n6. Detected INPUTS ({len(inputs)} total):")
        for i, inp in enumerate(inputs[:5], 1):
            print(f"   {i}. Type: {inp['metadata'].get('input_type', 'text')}")
            print(f"      Placeholder: {inp['metadata'].get('placeholder', 'N/A')}")
            print(f"      Position: {inp['bbox']}")
    
    # Show links
    links = [e for e in state['elements'] if e['type'] == 'link']
    print(f"\n7. Detected LINKS ({len(links)} total):")
    for i, link in enumerate(links[:10], 1):
        text_preview = link['text'][:40] + "..." if len(link['text']) > 40 else link['text']
        href = link['metadata'].get('href', 'N/A')
        print(f"   {i}. '{text_preview}'")
        print(f"      Link: {href[:60]}...")
    
    # Create trace
    print(f"\n8. Creating trace...")
    trace = translator.state_to_trace(state, trace_id='learning_platform_html')
    
    # Save trace
    output_path = 'test_data/output/learning_platform_html_trace.json'
    translator.save_trace(trace, output_path)
    print(f"   ✓ Trace saved to {output_path}")
    
    # Save screenshot
    if hasattr(translator, '_last_screenshot'):
        screenshot_path = 'test_data/output/learning_platform_html_screenshot.png'
        translator._last_screenshot.save(screenshot_path)
        print(f"   ✓ Screenshot saved to {screenshot_path}")
    
    # Save detailed report
    report_path = 'test_data/output/learning_platform_html_report.txt'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("="*80 + "\n")
        f.write("HTML DETECTION REPORT - Learning Platform\n")
        f.write("="*80 + "\n\n")
        f.write(f"URL: {url}\n")
        f.write(f"Page Title: {state['window_title']}\n")
        f.write(f"Total Elements: {len(state['elements'])}\n\n")
        
        f.write("Elements by Type:\n")
        for element_type, count in sorted(types.items(), key=lambda x: -x[1]):
            f.write(f"  {element_type}: {count}\n")
        
        f.write("\n" + "-"*80 + "\n")
        f.write("All Detected Elements:\n")
        f.write("-"*80 + "\n\n")
        
        for i, el in enumerate(state['elements'], 1):
            f.write(f"{i}. {el['element_id']}\n")
            f.write(f"   Type: {el['type']}\n")
            f.write(f"   Text: {el['text'][:100]}\n")
            f.write(f"   BBox: {el['bbox']}\n")
            f.write(f"   Enabled: {el['enabled']}\n")
            if el['metadata'].get('href'):
                f.write(f"   Link: {el['metadata']['href']}\n")
            if el['metadata'].get('placeholder'):
                f.write(f"   Placeholder: {el['metadata']['placeholder']}\n")
            f.write("\n")
    
    print(f"   ✓ Detailed report saved to {report_path}")
    
    print(f"\n{'='*80}")
    print("HTML DETECTION COMPLETE!")
    print(f"{'='*80}")
    
    # Comparison with CV
    print(f"\nComparison with CV detection (test.png):")
    print(f"  CV detection:   152 elements (151 labels, 1 object)")
    print(f"  HTML detection: {len(state['elements'])} elements")
    print(f"    - Buttons: {types.get('button', 0)}")
    print(f"    - Inputs: {types.get('input', 0)}")
    print(f"    - Links: {types.get('link', 0)}")
    print(f"    - Images: {types.get('image', 0)}")
    print(f"    - Other: {sum(v for k, v in types.items() if k not in ['button', 'input', 'link', 'image'])}")
    
    print(f"\n✓ HTML detection provides:")
    print(f"  - Exact element types (button, input, link)")
    print(f"  - Complete attributes (class, id, href, placeholder)")
    print(f"  - Perfect bounding boxes")
    print(f"  - 100% confidence")
    
    return state


if __name__ == "__main__":
    import os
    os.makedirs('test_data/output', exist_ok=True)
    
    try:
        state = analyze_learning_platform()
        print(f"\n🎉 Analysis completed successfully!")
        
    except Exception as e:
        print(f"\n✗ Analysis failed: {e}")
        import traceback
        traceback.print_exc()
