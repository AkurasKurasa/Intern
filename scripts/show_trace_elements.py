"""
Show All Elements with Their Text Content
Displays every detected element along with its text
"""

import json
from collections import defaultdict


def show_elements_with_text():
    """Display all elements with their text content."""
    
    print("="*80)
    print("ALL ELEMENTS WITH TEXT CONTENT")
    print("="*80)
    
    # Load the state with text
    with open('test_data/output/notepad_with_text_state.json', 'r', encoding='utf-8') as f:
        state = json.load(f)
    
    elements = state['elements']
    
    print(f"\nTotal elements detected: {len(elements)}")
    print(f"Page: {state['window_title']}")
    print(f"\n{'='*80}\n")
    
    # Group by type
    by_type = defaultdict(list)
    for elem in elements:
        by_type[elem['type']].append(elem)
    
    # Display each type
    for elem_type in sorted(by_type.keys()):
        elems = by_type[elem_type]
        print(f"\n{'='*80}")
        print(f"{elem_type.upper()}S ({len(elems)} total)")
        print(f"{'='*80}\n")
        
        for i, elem in enumerate(elems, 1):
            print(f"{i}. {elem['element_id']}")
            print(f"   Type: {elem['type']}")
            print(f"   Position: {elem['bbox']}")
            
            # Show text (with preview for long text)
            text = elem.get('text', '')
            if text:
                if len(text) > 100:
                    print(f"   Text: '{text[:100]}...'")
                    print(f"   Full length: {len(text)} characters")
                else:
                    print(f"   Text: '{text}'")
            else:
                print(f"   Text: (empty)")
            
            # Show metadata
            if elem['type'] == 'link' and elem['metadata'].get('href'):
                print(f"   Link: {elem['metadata']['href']}")
            
            if elem['type'] == 'textarea' and elem['metadata'].get('char_count'):
                print(f"   Character count: {elem['metadata']['char_count']}")
            
            if elem['type'] == 'input' and elem['metadata'].get('placeholder'):
                print(f"   Placeholder: {elem['metadata']['placeholder']}")
            
            print(f"   Enabled: {elem['enabled']}")
            print()
    
    # Show full textarea content
    print(f"\n{'='*80}")
    print("FULL TEXTAREA CONTENT")
    print(f"{'='*80}\n")
    
    textareas = [e for e in elements if e['type'] == 'textarea']
    for ta in textareas:
        print(f"Element: {ta['element_id']}")
        print(f"Position: {ta['bbox']}")
        print(f"Character count: {ta['metadata']['char_count']}")
        print(f"\nFull text:\n")
        print("-" * 80)
        print(ta['text'])
        print("-" * 80)
    
    # Summary table
    print(f"\n{'='*80}")
    print("SUMMARY TABLE")
    print(f"{'='*80}\n")
    
    print(f"{'Element Type':<15} {'Count':<10} {'With Text':<15} {'Empty':<10}")
    print("-" * 80)
    
    for elem_type in sorted(by_type.keys()):
        elems = by_type[elem_type]
        with_text = len([e for e in elems if e.get('text')])
        empty = len(elems) - with_text
        print(f"{elem_type:<15} {len(elems):<10} {with_text:<15} {empty:<10}")
    
    print("-" * 80)
    print(f"{'TOTAL':<15} {len(elements):<10} {len([e for e in elements if e.get('text')]):<15} {len([e for e in elements if not e.get('text')]):<10}")
    
    print(f"\n{'='*80}")
    print("COMPLETE!")
    print(f"{'='*80}")


if __name__ == "__main__":
    try:
        show_elements_with_text()
        print("\n✓ All elements displayed with their text content!")
        
    except Exception as e:
        print(f"\n✗ Failed: {e}")
        import traceback
        traceback.print_exc()
