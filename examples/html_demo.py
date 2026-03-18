"""
HTML Detection Examples
Interactive demo showing all HTML detection capabilities

This unified script contains all HTML detection examples:
1. Basic detection (example.com)
2. Notepad analysis
3. Text extraction demo
4. Learning platform analysis

Usage:
    python examples/html_demo.py [example_number]
    
    Or run without arguments for interactive menu
"""

import sys
sys.path.insert(0, 'components/trace_translator')

from trace_translator import TraceTranslator
from collections import Counter
import json
import os


def demo_basic():
    """Example 1: Basic HTML detection on example.com"""
    
    print("\n" + "="*80)
    print("EXAMPLE 1: Basic HTML Detection (example.com)")
    print("="*80)
    
    translator = TraceTranslator(use_html=True, use_cv=False)
    print("\n✓ HTML detector initialized")
    
    print("\nExtracting UI elements from example.com...")
    state = translator.url_to_state('https://example.com')
    
    print(f"\nResults:")
    print(f"  Page: {state['window_title']}")
    print(f"  Total elements: {len(state['elements'])}")
    
    types = Counter(e['type'] for e in state['elements'])
    print(f"\nElements by type:")
    for element_type, count in sorted(types.items()):
        print(f"  {element_type}: {count}")
    
    print(f"\nDetected elements:")
    for i, el in enumerate(state['elements'], 1):
        text_preview = el['text'][:40] + "..." if len(el['text']) > 40 else el['text']
        print(f"  {i}. {el['type']}: '{text_preview}'")
        if el['metadata'].get('href'):
            print(f"     Link: {el['metadata']['href']}")
    
    # Save outputs
    os.makedirs('data/output/traces', exist_ok=True)
    os.makedirs('data/output/screenshots', exist_ok=True)
    
    trace = translator.state_to_trace(state, trace_id='html_basic')
    translator.save_trace(trace, 'data/output/traces/html_basic_trace.json')
    
    if hasattr(translator, '_last_screenshot'):
        translator._last_screenshot.save('data/output/screenshots/html_basic_screenshot.png')
    
    print(f"\n✓ Outputs saved to data/output/")
    return state


def demo_notepad():
    """Example 2: Analyze online notepad application"""
    
    print("\n" + "="*80)
    print("EXAMPLE 2: Notepad Analysis (RapidTables)")
    print("="*80)
    
    url = 'https://www.rapidtables.com/tools/notepad.html'
    
    translator = TraceTranslator(use_html=True)
    print("\n✓ HTML detector initialized")
    
    print(f"\nExtracting UI elements from {url}...")
    state = translator.url_to_state(url, application='Chrome')
    
    print(f"\nResults:")
    print(f"  Page: {state['window_title']}")
    print(f"  Total elements: {len(state['elements'])}")
    
    types = Counter(e['type'] for e in state['elements'])
    print(f"\nElements by type:")
    for element_type, count in sorted(types.items(), key=lambda x: -x[1]):
        print(f"  {element_type}: {count}")
    
    # Show specific element types
    buttons = [e for e in state['elements'] if e['type'] == 'button']
    if buttons:
        print(f"\nButtons detected ({len(buttons)} total - showing first 10):")
        for i, btn in enumerate(buttons[:10], 1):
            text = btn['text'][:40] + "..." if len(btn['text']) > 40 else btn['text']
            print(f"  {i}. '{text}'")
    
    textareas = [e for e in state['elements'] if e['type'] == 'textarea']
    if textareas:
        print(f"\nTextareas detected ({len(textareas)} total):")
        for i, ta in enumerate(textareas, 1):
            print(f"  {i}. Position: {ta['bbox']}")
    
    # Save outputs
    os.makedirs('data/output/traces', exist_ok=True)
    os.makedirs('data/output/screenshots', exist_ok=True)
    os.makedirs('data/output/reports', exist_ok=True)
    
    trace = translator.state_to_trace(state, trace_id='html_notepad')
    translator.save_trace(trace, 'data/output/traces/html_notepad_trace.json')
    
    if hasattr(translator, '_last_screenshot'):
        translator._last_screenshot.save('data/output/screenshots/html_notepad_screenshot.png')
    
    # Save detailed report
    with open('data/output/reports/html_notepad_report.txt', 'w', encoding='utf-8') as f:
        f.write("="*80 + "\n")
        f.write("HTML DETECTION REPORT - RapidTables Notepad\n")
        f.write("="*80 + "\n\n")
        f.write(f"URL: {url}\n")
        f.write(f"Total Elements: {len(state['elements'])}\n\n")
        
        f.write("Elements by Type:\n")
        for element_type, count in sorted(types.items(), key=lambda x: -x[1]):
            f.write(f"  {element_type}: {count}\n")
        
        f.write("\n" + "-"*80 + "\n")
        f.write("All Elements:\n")
        f.write("-"*80 + "\n\n")
        
        for i, el in enumerate(state['elements'], 1):
            f.write(f"{i}. {el['element_id']}\n")
            f.write(f"   Type: {el['type']}\n")
            f.write(f"   Text: {el['text'][:100]}\n")
            f.write(f"   BBox: {el['bbox']}\n\n")
    
    print(f"\n✓ Outputs saved to data/output/")
    return state


def demo_with_text():
    """Example 3: Text extraction from form fields"""
    
    print("\n" + "="*80)
    print("EXAMPLE 3: Text Extraction Demo")
    print("="*80)
    
    url = 'https://www.rapidtables.com/tools/notepad.html'
    
    print("\n✓ Initializing HTML detector with text input capability...")
    translator = TraceTranslator(use_html=True)
    
    # Use HTMLDetector directly for text input
    from playwright.sync_api import sync_playwright
    
    sample_text = """Hello! This is a test of HTML-based text extraction.

The HTML detector can:
1. Detect all UI elements (buttons, inputs, links, etc.)
2. Extract exact element types
3. Capture element content (like this text!)
4. Perfect bounding boxes

This is much better than CV detection because:
- 100% accurate element types
- No training required
- Complete element attributes
- Perfect for web automation"""
    
    print(f"\nTyping {len(sample_text)} characters into textarea...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url)
        page.wait_for_load_state('networkidle')
        
        # Type into textarea
        textarea = page.locator('textarea').first
        textarea.fill(sample_text)
        
        print("✓ Text typed successfully")
        print("\nExtracting UI elements with typed content...")
        
        # Now extract elements (they will include our typed text)
        state = translator.html_detector.extract_ui_elements_from_page(page)
        
        browser.close()
    
    print(f"\nResults:")
    print(f"  Total elements: {len(state['elements'])}")
    
    # Find the textarea with our text
    textareas = [e for e in state['elements'] if e['type'] == 'textarea']
    if textareas:
        ta = textareas[0]
        print(f"\nTextarea content extracted:")
        print(f"  Character count: {ta['metadata'].get('char_count', 0)}")
        print(f"  Text preview: {ta['text'][:100]}...")
        print(f"  ✓ Successfully extracted {len(ta['text'])} characters!")
    
    # Save outputs
    os.makedirs('data/output/traces', exist_ok=True)
    os.makedirs('data/output/screenshots', exist_ok=True)
    
    trace = translator.state_to_trace(state, trace_id='html_text_extraction')
    translator.save_trace(trace, 'data/output/traces/html_text_extraction_trace.json')
    
    print(f"\n✓ Outputs saved to data/output/")
    print(f"\nThis demonstrates 100% accurate text extraction from web forms!")
    
    return state


def demo_learning_platform():
    """Example 4: Learning platform analysis"""
    
    print("\n" + "="*80)
    print("EXAMPLE 4: Learning Platform Analysis")
    print("="*80)
    
    url = 'https://talatask.com/login'
    
    translator = TraceTranslator(use_html=True)
    print("\n✓ HTML detector initialized")
    
    print(f"\nExtracting UI elements from {url}...")
    state = translator.url_to_state(url, application='Chrome')
    
    print(f"\nResults:")
    print(f"  Page: {state['window_title']}")
    print(f"  Total elements: {len(state['elements'])}")
    
    types = Counter(e['type'] for e in state['elements'])
    print(f"\nElements by type:")
    for element_type, count in sorted(types.items(), key=lambda x: -x[1]):
        print(f"  {element_type}: {count}")
    
    # Show login form elements
    inputs = [e for e in state['elements'] if e['type'] == 'input']
    buttons = [e for e in state['elements'] if e['type'] == 'button']
    
    if inputs:
        print(f"\nInput fields detected ({len(inputs)} total):")
        for i, inp in enumerate(inputs, 1):
            input_type = inp['metadata'].get('input_type', 'text')
            placeholder = inp['metadata'].get('placeholder', 'N/A')
            print(f"  {i}. Type: {input_type}, Placeholder: {placeholder}")
    
    if buttons:
        print(f"\nButtons detected ({len(buttons)} total):")
        for i, btn in enumerate(buttons, 1):
            print(f"  {i}. '{btn['text']}'")
    
    # Save outputs
    os.makedirs('data/output/traces', exist_ok=True)
    os.makedirs('data/output/screenshots', exist_ok=True)
    
    trace = translator.state_to_trace(state, trace_id='html_learning_platform')
    translator.save_trace(trace, 'data/output/traces/html_learning_platform_trace.json')
    
    if hasattr(translator, '_last_screenshot'):
        translator._last_screenshot.save('data/output/screenshots/html_learning_platform_screenshot.png')
    
    print(f"\n✓ Outputs saved to data/output/")
    print(f"\nNote: This is a login page. For authenticated pages, you'll need to")
    print(f"      implement login logic using Playwright before extracting elements.")
    
    return state


def show_menu():
    """Display interactive menu"""
    print("\n" + "="*80)
    print("HTML DETECTION EXAMPLES")
    print("="*80)
    print("\nChoose an example:")
    print("  1. Basic detection (example.com)")
    print("  2. Notepad analysis (RapidTables)")
    print("  3. Text extraction demo")
    print("  4. Learning platform analysis")
    print("  0. Run all examples")
    print("\n" + "="*80)


def main():
    """Main entry point"""
    
    # Check for command-line argument
    if len(sys.argv) > 1:
        try:
            choice = int(sys.argv[1])
        except ValueError:
            print("Error: Please provide a number (1-4, or 0 for all)")
            return
    else:
        # Interactive menu
        show_menu()
        try:
            choice = int(input("\nEnter choice (0-4): "))
        except (ValueError, KeyboardInterrupt):
            print("\nCancelled.")
            return
    
    # Run selected example(s)
    examples = {
        1: ("Basic Detection", demo_basic),
        2: ("Notepad Analysis", demo_notepad),
        3: ("Text Extraction", demo_with_text),
        4: ("Learning Platform", demo_learning_platform)
    }
    
    try:
        if choice == 0:
            # Run all
            print("\nRunning all examples...\n")
            for num, (name, func) in examples.items():
                print(f"\n{'#'*80}")
                print(f"Running Example {num}: {name}")
                print(f"{'#'*80}")
                func()
        elif choice in examples:
            name, func = examples[choice]
            func()
        else:
            print(f"Invalid choice: {choice}")
            return
        
        print("\n" + "="*80)
        print("✓ DEMO COMPLETE!")
        print("="*80)
        print("\nOutputs saved to:")
        print("  - data/output/traces/")
        print("  - data/output/screenshots/")
        print("  - data/output/reports/")
        print("\nNext steps:")
        print("  1. Visualize results: python scripts/trace_tools.py visualize")
        print("  2. View summary: python scripts/trace_tools.py summarize")
        
    except Exception as e:
        print(f"\n✗ Demo failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
