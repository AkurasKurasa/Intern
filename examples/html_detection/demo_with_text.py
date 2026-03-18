"""
HTML Detection with Text Input
Demonstrates detecting text content in textarea after typing
"""

import sys
sys.path.insert(0, 'components/trace_translator')

from playwright.sync_api import sync_playwright
from trace_translator import TraceTranslator
from collections import Counter
import json
from PIL import Image
import io


def analyze_notepad_with_text():
    """Analyze notepad after typing text."""
    
    print("="*80)
    print("HTML DETECTION: Notepad with Text Input")
    print("="*80)
    
    url = 'https://www.rapidtables.com/tools/notepad.html'
    
    print("\n1. Launching browser...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # Show browser
        page = browser.new_page()
        
        print(f"2. Loading page: {url}")
        page.goto(url, wait_until='networkidle')
        page.wait_for_timeout(2000)
        
        # Find and click the textarea
        print("\n3. Finding textarea...")
        textarea = page.query_selector('textarea')
        
        if textarea:
            print("   ✓ Textarea found!")
            
            # Type some text
            sample_text = """Hello! This is a test of HTML-based UI detection.

The HTML detector can:
1. Detect all UI elements (buttons, inputs, links, etc.)
2. Extract exact element types
3. Get perfect bounding boxes
4. Capture element content (like this text!)

This is much better than CV detection because:
- 100% accurate element types
- No training required
- Complete element attributes
- Perfect for web automation

Try clicking the buttons above to see more features!"""
            
            print(f"\n4. Typing text into textarea...")
            textarea.fill(sample_text)
            print(f"   ✓ Typed {len(sample_text)} characters")
            
            # Wait for text to appear
            page.wait_for_timeout(1000)
            
            # Now extract elements
            print("\n5. Extracting UI elements...")
            
            # Get all elements
            elements = []
            element_id = 0
            
            # Buttons
            buttons = page.query_selector_all('button')
            for btn in buttons:
                bbox = btn.bounding_box()
                if bbox:
                    elements.append({
                        'element_id': f'button_{element_id}',
                        'type': 'button',
                        'text': btn.inner_text(),
                        'bbox': [int(bbox['x']), int(bbox['y']), 
                                int(bbox['x'] + bbox['width']), 
                                int(bbox['y'] + bbox['height'])],
                        'enabled': btn.is_enabled(),
                        'visible': btn.is_visible(),
                        'metadata': {
                            'tag': 'button',
                            'class': btn.get_attribute('class') or '',
                            'id': btn.get_attribute('id') or ''
                        },
                        'confidence': 1.0
                    })
                    element_id += 1
            
            # Inputs
            inputs = page.query_selector_all('input:not([type="hidden"])')
            for inp in inputs:
                bbox = inp.bounding_box()
                if bbox:
                    elements.append({
                        'element_id': f'input_{element_id}',
                        'type': 'input',
                        'text': inp.input_value(),
                        'bbox': [int(bbox['x']), int(bbox['y']), 
                                int(bbox['x'] + bbox['width']), 
                                int(bbox['y'] + bbox['height'])],
                        'enabled': inp.is_enabled(),
                        'visible': inp.is_visible(),
                        'metadata': {
                            'tag': 'input',
                            'input_type': inp.get_attribute('type') or 'text',
                            'placeholder': inp.get_attribute('placeholder') or None
                        },
                        'confidence': 1.0
                    })
                    element_id += 1
            
            # Textareas (with content!)
            textareas = page.query_selector_all('textarea')
            for ta in textareas:
                bbox = ta.bounding_box()
                if bbox:
                    content = ta.input_value()  # Get the text content!
                    elements.append({
                        'element_id': f'textarea_{element_id}',
                        'type': 'textarea',
                        'text': content,  # ← This will have our typed text!
                        'bbox': [int(bbox['x']), int(bbox['y']), 
                                int(bbox['x'] + bbox['width']), 
                                int(bbox['y'] + bbox['height'])],
                        'enabled': ta.is_enabled(),
                        'visible': ta.is_visible(),
                        'metadata': {
                            'tag': 'textarea',
                            'placeholder': ta.get_attribute('placeholder') or None,
                            'char_count': len(content)
                        },
                        'confidence': 1.0
                    })
                    element_id += 1
            
            # Links
            links = page.query_selector_all('a[href]')
            for link in links:
                bbox = link.bounding_box()
                if bbox:
                    elements.append({
                        'element_id': f'link_{element_id}',
                        'type': 'link',
                        'text': link.inner_text(),
                        'bbox': [int(bbox['x']), int(bbox['y']), 
                                int(bbox['x'] + bbox['width']), 
                                int(bbox['y'] + bbox['height'])],
                        'enabled': True,
                        'visible': link.is_visible(),
                        'metadata': {
                            'tag': 'a',
                            'href': link.get_attribute('href')
                        },
                        'confidence': 1.0
                    })
                    element_id += 1
            
            # Take screenshot
            print("\n6. Capturing screenshot...")
            screenshot_bytes = page.screenshot(full_page=True)
            screenshot = Image.open(io.BytesIO(screenshot_bytes))
            
            # Get page info
            title = page.title()
            viewport = page.viewport_size
            
            print(f"   ✓ Captured screenshot ({viewport['width']}x{viewport['height']})")
            
        browser.close()
    
    # Create state
    state = {
        'application': 'Chrome',
        'window_title': title,
        'screen_resolution': [viewport['width'], viewport['height']],
        'focused_element_id': None,
        'elements': elements,
        'metadata': {
            'url': url,
            'detection_method': 'html',
            'num_elements_detected': len(elements)
        }
    }
    
    # Display results
    print(f"\n7. Results:")
    print(f"   Total elements: {len(elements)}")
    
    types = Counter(e['type'] for e in elements)
    print(f"\n8. Elements by type:")
    for elem_type, count in sorted(types.items(), key=lambda x: -x[1]):
        print(f"   {elem_type}: {count}")
    
    # Show textarea content
    textareas = [e for e in elements if e['type'] == 'textarea']
    if textareas:
        print(f"\n9. Textarea content detected:")
        for ta in textareas:
            print(f"   Element ID: {ta['element_id']}")
            print(f"   Character count: {ta['metadata']['char_count']}")
            print(f"   Text preview (first 100 chars):")
            print(f"   '{ta['text'][:100]}...'")
    
    # Save results
    print(f"\n10. Saving results...")
    
    # Save screenshot
    screenshot_path = 'test_data/output/notepad_with_text_screenshot.png'
    screenshot.save(screenshot_path)
    print(f"   ✓ Screenshot: {screenshot_path}")
    
    # Save state as JSON
    state_path = 'test_data/output/notepad_with_text_state.json'
    with open(state_path, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    print(f"   ✓ State: {state_path}")
    
    # Save detailed report
    report_path = 'test_data/output/notepad_with_text_report.txt'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("="*80 + "\n")
        f.write("HTML DETECTION REPORT - Notepad with Text\n")
        f.write("="*80 + "\n\n")
        f.write(f"URL: {url}\n")
        f.write(f"Total Elements: {len(elements)}\n\n")
        
        f.write("Elements by Type:\n")
        for elem_type, count in sorted(types.items(), key=lambda x: -x[1]):
            f.write(f"  {elem_type}: {count}\n")
        
        f.write("\n" + "-"*80 + "\n")
        f.write("Textarea Content:\n")
        f.write("-"*80 + "\n\n")
        
        for ta in textareas:
            f.write(f"Element: {ta['element_id']}\n")
            f.write(f"Position: {ta['bbox']}\n")
            f.write(f"Character count: {ta['metadata']['char_count']}\n")
            f.write(f"\nFull text:\n")
            f.write(ta['text'])
            f.write("\n\n")
    
    print(f"   ✓ Report: {report_path}")
    
    print(f"\n{'='*80}")
    print("DETECTION COMPLETE!")
    print(f"{'='*80}")
    
    print(f"\n✓ Successfully:")
    print(f"  - Typed text into textarea")
    print(f"  - Detected {len(elements)} UI elements")
    print(f"  - Extracted textarea content ({textareas[0]['metadata']['char_count']} characters)")
    print(f"  - Captured screenshot with text visible")
    
    return state, screenshot


if __name__ == "__main__":
    import os
    os.makedirs('test_data/output', exist_ok=True)
    
    try:
        state, screenshot = analyze_notepad_with_text()
        print(f"\n🎉 Analysis completed successfully!")
        print(f"\nThe HTML detector can extract text content from form fields!")
        
    except Exception as e:
        print(f"\n✗ Analysis failed: {e}")
        import traceback
        traceback.print_exc()
