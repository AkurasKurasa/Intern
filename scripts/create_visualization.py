"""
Trace Visualization Tool
Creates comprehensive visualizations of detected UI elements with bounding boxes and text labels.

This is the main visualization script for the trace translator.
It creates professional visualizations showing:
- Color-coded bounding boxes by element type
- Text labels showing element content
- Summary panel with element counts
- Legend for element types

Usage:
    python scripts/create_visualization.py

Output:
    - data/output/visualizations/trace_visualization.png (with labels)
    - data/output/visualizations/trace_visualization_summary.png (with summary panel)
"""

from PIL import Image, ImageDraw, ImageFont
import json


def create_enhanced_visualization():
    """Create visualization with element boxes AND text labels."""
    
    print("="*80)
    print("CREATING ENHANCED VISUALIZATION")
    print("="*80)
    
    # Load state (use most recent trace file or specify one)
    import glob
    import os
    
    # Find most recent trace file
    trace_files = glob.glob('data/output/**/*state.json', recursive=True) + \
                  glob.glob('data/output/**/*trace.json', recursive=True)
    
    if not trace_files:
        print("Error: No trace files found in data/output/")
        print("Please run an example first (e.g., python examples/html_detection/demo_notepad.py)")
        return None, None
    
    # Use most recent
    trace_file = max(trace_files, key=os.path.getmtime)
    print(f"Using trace file: {trace_file}")
    
    with open(trace_file, 'r', encoding='utf-8') as f:
        state = json.load(f)
    
    # Find corresponding screenshot
    trace_dir = os.path.dirname(trace_file)
    screenshot_files = glob.glob(os.path.join(trace_dir, '*screenshot.png'))
    
    if not screenshot_files:
        # Try parent directory
        screenshot_files = glob.glob('data/output/**/*screenshot.png', recursive=True)
    
    if not screenshot_files:
        print("Error: No screenshot found")
        return None, None
    
    screenshot_file = max(screenshot_files, key=os.path.getmtime)
    print(f"Using screenshot: {screenshot_file}")
    
    screenshot = Image.open(screenshot_file)
    draw = ImageDraw.Draw(screenshot)
    
    # Colors for different element types
    colors = {
        'button': '#FF0000',      # Red
        'input': '#00FF00',       # Green
        'textarea': '#0000FF',    # Blue
        'link': '#FF00FF',        # Magenta
        'image': '#FFFF00',       # Yellow
    }
    
    # Load fonts
    try:
        label_font = ImageFont.truetype("arial.ttf", 14)
        text_font = ImageFont.truetype("arial.ttf", 12)
    except:
        label_font = ImageFont.load_default()
        text_font = ImageFont.load_default()
    
    elements = state['elements']
    
    print(f"\nDrawing {len(elements)} elements with text labels...")
    
    # Draw each element
    for elem in elements:
        bbox = elem['bbox']
        elem_type = elem['type']
        elem_text = elem.get('text', '')
        elem_id = elem['element_id']
        
        color = colors.get(elem_type, '#FFFFFF')
        
        # Draw bounding box (thicker for visibility)
        draw.rectangle(bbox, outline=color, width=3)
        
        # Create label with element type and text
        if elem_text and len(elem_text) > 0:
            # Show element type + text preview
            text_preview = elem_text[:30] + "..." if len(elem_text) > 30 else elem_text
            # Remove newlines for label
            text_preview = text_preview.replace('\n', ' ')
            label = f"{elem_type}: {text_preview}"
        else:
            # Just show element type and ID
            label = f"{elem_type} ({elem_id})"
        
        # Calculate label position (above the box)
        label_y = bbox[1] - 22
        if label_y < 0:
            label_y = bbox[1] + 2  # Put inside if no room above
        
        # Get text bounding box
        text_bbox = draw.textbbox((bbox[0], label_y), label, font=label_font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        
        # Draw semi-transparent background for label
        padding = 4
        bg_bbox = [
            bbox[0] - padding,
            label_y - padding,
            bbox[0] + text_width + padding,
            label_y + text_height + padding
        ]
        
        # Draw background rectangle
        draw.rectangle(bg_bbox, fill=color)
        
        # Draw text in black
        draw.text((bbox[0], label_y), label, fill='#000000', font=label_font)
        
        # For textarea, also show character count
        if elem_type == 'textarea' and elem['metadata'].get('char_count'):
            char_label = f"{elem['metadata']['char_count']} chars"
            char_y = bbox[3] + 5
            char_bbox = draw.textbbox((bbox[0], char_y), char_label, font=text_font)
            char_width = char_bbox[2] - char_bbox[0]
            
            # Background
            draw.rectangle([
                bbox[0] - 2,
                char_y - 2,
                bbox[0] + char_width + 2,
                char_y + 15
            ], fill='#0000FF')
            
            # Text
            draw.text((bbox[0], char_y), char_label, fill='#FFFFFF', font=text_font)
    
    # Save enhanced visualization
    output_path = 'data/output/visualizations/trace_visualization.png'
    screenshot.save(output_path)
    print(f"\n✓ Visualization saved to: {output_path}")
    
    # Create a summary overlay in the corner
    summary_img = screenshot.copy()
    summary_draw = ImageDraw.Draw(summary_img)
    
    # Count elements by type
    from collections import Counter
    types = Counter(e['type'] for e in elements)
    
    # Draw summary box in top-right corner
    summary_x = screenshot.width - 300
    summary_y = 10
    summary_width = 290
    summary_height = 30 + len(types) * 25
    
    # Background
    summary_draw.rectangle([
        summary_x, summary_y,
        summary_x + summary_width, summary_y + summary_height
    ], fill='#FFFFFF', outline='#000000', width=2)
    
    # Title
    summary_draw.text((summary_x + 10, summary_y + 5), 
                     "Detected Elements", fill='#000000', font=label_font)
    
    # Element counts
    y_offset = summary_y + 30
    for elem_type, count in sorted(types.items(), key=lambda x: -x[1]):
        color = colors.get(elem_type, '#FFFFFF')
        
        # Color box
        summary_draw.rectangle([
            summary_x + 10, y_offset,
            summary_x + 30, y_offset + 15
        ], fill=color, outline='#000000')
        
        # Label
        summary_draw.text((summary_x + 40, y_offset), 
                         f"{elem_type}: {count}", fill='#000000', font=text_font)
        
        y_offset += 25
    
    # Save version with summary
    summary_path = 'data/output/visualizations/trace_visualization_summary.png'
    summary_img.save(summary_path)
    print(f"✓ Version with summary panel saved to: {summary_path}")
    
    print(f"\n{'='*80}")
    print("VISUALIZATION COMPLETE!")
    print(f"{'='*80}")
    
    print(f"\nCreated 2 versions:")
    print(f"1. {output_path}")
    print(f"   - Element boxes with text labels")
    print(f"2. {summary_path}")
    print(f"   - Same + summary overlay")
    
    # Show what was detected
    print(f"\nDetected elements:")
    for elem_type, count in sorted(types.items(), key=lambda x: -x[1]):
        with_text = len([e for e in elements if e['type'] == elem_type and e.get('text')])
        print(f"  {elem_type}: {count} total ({with_text} with text)")
    
    return output_path, summary_path


if __name__ == "__main__":
    import os
    os.makedirs('data/output/visualizations', exist_ok=True)
    
    try:
        result = create_enhanced_visualization()
        
        if result is None:
            print("\n✗ Visualization failed - no trace files found")
            print("\nRun an example first:")
            print("  python examples/html_detection/demo_notepad.py")
            print("  python examples/html_detection/demo_with_text.py")
        else:
            viz_path, summary_path = result
            
            print(f"\n🎉 Visualization created successfully!")
            print(f"\nOpening images...")
            
            # Open the images
            os.system(f'start {viz_path}')
            os.system(f'start {summary_path}')
        
    except Exception as e:
        print(f"\n✗ Failed: {e}")
        import traceback
        traceback.print_exc()
