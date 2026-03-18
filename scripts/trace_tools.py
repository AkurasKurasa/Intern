"""
Trace Analysis Tools
All-in-one utility for working with trace files

Commands:
    visualize - Create visual representation with bounding boxes and labels
    summarize - Show quick statistics
    show      - List all elements with details

Usage:
    python scripts/trace_tools.py visualize [--trace FILE]
    python scripts/trace_tools.py summarize [--trace FILE]
    python scripts/trace_tools.py show [--trace FILE]
    
    If no trace file specified, uses most recent trace from data/output/
"""

from PIL import Image, ImageDraw, ImageFont
import json
import glob
import os
import sys
from collections import Counter, defaultdict
import argparse


def find_latest_trace():
    """Find the most recent trace file"""
    trace_files = glob.glob('data/output/**/*state.json', recursive=True) + \
                  glob.glob('data/output/**/*trace.json', recursive=True)
    
    if not trace_files:
        return None, None
    
    trace_file = max(trace_files, key=os.path.getmtime)
    
    # Find corresponding screenshot
    trace_dir = os.path.dirname(trace_file)
    screenshot_files = glob.glob(os.path.join(trace_dir, '*screenshot.png'))
    
    if not screenshot_files:
        screenshot_files = glob.glob('data/output/**/*screenshot.png', recursive=True)
    
    screenshot_file = max(screenshot_files, key=os.path.getmtime) if screenshot_files else None
    
    return trace_file, screenshot_file


def cmd_visualize(trace_file=None):
    """Create visualization with bounding boxes and text labels"""
    
    print("="*80)
    print("TRACE VISUALIZATION")
    print("="*80)
    
    # Find trace file
    if not trace_file:
        trace_file, screenshot_file = find_latest_trace()
        if not trace_file:
            print("\n✗ Error: No trace files found in data/output/")
            print("Run an example first: python examples/html_demo.py")
            return
    else:
        # Find screenshot for specified trace
        trace_dir = os.path.dirname(trace_file)
        screenshot_files = glob.glob(os.path.join(trace_dir, '*screenshot.png'))
        screenshot_file = screenshot_files[0] if screenshot_files else None
    
    print(f"\nUsing trace: {trace_file}")
    print(f"Using screenshot: {screenshot_file}")
    
    # Load trace
    with open(trace_file, 'r', encoding='utf-8') as f:
        state = json.load(f)
    
    if not screenshot_file or not os.path.exists(screenshot_file):
        print("\n✗ Error: Screenshot not found")
        return
    
    screenshot = Image.open(screenshot_file)
    draw = ImageDraw.Draw(screenshot)
    
    # Colors for element types
    colors = {
        'button': '#FF0000',
        'input': '#00FF00',
        'textarea': '#0000FF',
        'link': '#FF00FF',
        'image': '#FFFF00',
        'label': '#00FFFF',
        'unknown': '#FFFFFF'
    }
    
    elements = state.get('elements', [])
    print(f"\nDrawing {len(elements)} elements...")
    
    # Count by type
    types = Counter(e['type'] for e in elements)
    
    # Load fonts
    try:
        text_font = ImageFont.truetype("arial.ttf", 11)
        label_font = ImageFont.truetype("arial.ttf", 10)
    except:
        text_font = ImageFont.load_default()
        label_font = ImageFont.load_default()
    
    # Draw each element
    for element in elements:
        bbox = element['bbox']
        elem_type = element['type']
        color = colors.get(elem_type, '#FFFFFF')
        
        # Draw bounding box
        draw.rectangle(bbox, outline=color, width=3)
        
        # Draw label with type and text
        text_preview = element.get('text', '')[:30]
        if text_preview:
            label = f"{elem_type}: {text_preview}"
        else:
            label = elem_type
        
        # Position label above box
        label_y = max(bbox[1] - 15, 5)
        
        # Draw label background
        text_bbox = draw.textbbox((bbox[0], label_y), label, font=label_font)
        draw.rectangle(
            [text_bbox[0]-2, text_bbox[1]-2, text_bbox[2]+2, text_bbox[3]+2],
            fill='#000000'
        )
        
        # Draw label text
        draw.text((bbox[0], label_y), label, fill=color, font=label_font)
        
        # For textareas, show character count
        if elem_type == 'textarea' and element['metadata'].get('char_count'):
            char_count = element['metadata']['char_count']
            char_label = f"{char_count} chars"
            char_y = bbox[3] + 5
            draw.text((bbox[0], char_y), char_label, fill='#FFFFFF', font=text_font)
    
    # Save visualization
    os.makedirs('data/output/visualizations', exist_ok=True)
    output_path = 'data/output/visualizations/trace_visualization.png'
    screenshot.save(output_path)
    print(f"\n✓ Visualization saved to: {output_path}")
    
    # Create version with summary panel
    summary_img = screenshot.copy()
    summary_draw = ImageDraw.Draw(summary_img)
    
    # Draw summary panel
    panel_x = summary_img.width - 200
    panel_y = 10
    panel_width = 190
    panel_height = 30 + len(types) * 25
    
    # Panel background
    summary_draw.rectangle(
        [panel_x, panel_y, panel_x + panel_width, panel_y + panel_height],
        fill='#000000',
        outline='#FFFFFF',
        width=2
    )
    
    # Panel title
    summary_draw.text((panel_x + 10, panel_y + 5), "Detected Elements", fill='#FFFFFF', font=text_font)
    
    # Element counts
    y_offset = panel_y + 30
    for elem_type, count in sorted(types.items(), key=lambda x: -x[1]):
        color = colors.get(elem_type, '#FFFFFF')
        summary_draw.text((panel_x + 10, y_offset), f"{elem_type}: {count}", fill=color, font=label_font)
        y_offset += 25
    
    # Save with summary
    summary_path = 'data/output/visualizations/trace_visualization_summary.png'
    summary_img.save(summary_path)
    print(f"✓ Version with summary panel saved to: {summary_path}")
    
    print(f"\n{'='*80}")
    print("VISUALIZATION COMPLETE!")
    print(f"{'='*80}")
    print(f"\nElement counts:")
    for elem_type, count in sorted(types.items(), key=lambda x: -x[1]):
        print(f"  {elem_type}: {count}")
    
    # Open images
    print(f"\nOpening visualizations...")
    os.system(f'start {output_path}')
    os.system(f'start {summary_path}')


def cmd_summarize(trace_file=None):
    """Show quick summary of trace contents"""
    
    print("="*80)
    print("TRACE SUMMARY")
    print("="*80)
    
    # Find trace file
    if not trace_file:
        trace_file, _ = find_latest_trace()
        if not trace_file:
            print("\n✗ Error: No trace files found in data/output/")
            print("Run an example first: python examples/html_demo.py")
            return
    
    print(f"\nTrace file: {trace_file}")
    
    # Load trace
    with open(trace_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Handle both trace format and state format
    if 'state' in data:
        state = data['state']
    else:
        state = data
    
    elements = state.get('elements', [])
    
    print(f"\nTotal elements: {len(elements)}")
    
    # Count by type
    types = Counter(e['type'] for e in elements)
    
    print(f"\nElements by type:")
    for elem_type, count in sorted(types.items(), key=lambda x: -x[1]):
        print(f"  {elem_type}: {count}")
    
    # Show elements with text
    text_elements = [e for e in elements if e.get('text') and e['text'].strip()]
    print(f"\nElements with text: {len(text_elements)}")
    
    print(f"\n{'='*80}")


def cmd_show(trace_file=None):
    """List all elements with details"""
    
    print("="*80)
    print("TRACE ELEMENTS")
    print("="*80)
    
    # Find trace file
    if not trace_file:
        trace_file, _ = find_latest_trace()
        if not trace_file:
            print("\n✗ Error: No trace files found in data/output/")
            print("Run an example first: python examples/html_demo.py")
            return
    
    print(f"\nTrace file: {trace_file}")
    
    # Load trace
    with open(trace_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Handle both formats
    if 'state' in data:
        state = data['state']
    else:
        state = data
    
    elements = state.get('elements', [])
    
    print(f"\nTotal elements: {len(elements)}")
    
    # Group by type
    by_type = defaultdict(list)
    for el in elements:
        by_type[el['type']].append(el)
    
    # Show each type
    for elem_type, elems in sorted(by_type.items(), key=lambda x: -len(x[1])):
        print(f"\n{'='*80}")
        print(f"{elem_type.upper()} ({len(elems)} total)")
        print(f"{'='*80}")
        
        for i, el in enumerate(elems, 1):
            print(f"\n{i}. {el['element_id']}")
            print(f"   Position: {el['bbox']}")
            
            if el.get('text'):
                text = el['text'][:100] + "..." if len(el['text']) > 100 else el['text']
                print(f"   Text: '{text}'")
            
            if el['metadata'].get('href'):
                print(f"   Link: {el['metadata']['href']}")
            
            if el['metadata'].get('char_count'):
                print(f"   Characters: {el['metadata']['char_count']}")
            
            if el['metadata'].get('input_type'):
                print(f"   Input type: {el['metadata']['input_type']}")
            
            print(f"   Confidence: {el['confidence']}")
    
    print(f"\n{'='*80}")


def main():
    """Main entry point"""
    
    parser = argparse.ArgumentParser(
        description='Trace Analysis Tools',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/trace_tools.py visualize
  python scripts/trace_tools.py summarize --trace data/output/traces/my_trace.json
  python scripts/trace_tools.py show
        """
    )
    
    parser.add_argument('command', 
                       choices=['visualize', 'summarize', 'show'],
                       help='Command to run')
    parser.add_argument('--trace', 
                       help='Path to trace file (uses latest if not specified)')
    
    args = parser.parse_args()
    
    # Run command
    try:
        if args.command == 'visualize':
            cmd_visualize(args.trace)
        elif args.command == 'summarize':
            cmd_summarize(args.trace)
        elif args.command == 'show':
            cmd_show(args.trace)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
