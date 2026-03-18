# Utility Scripts

This directory contains utility scripts for working with trace data and visualizations.

## Available Scripts

### `create_visualization.py`
**The main visualization tool** - Creates comprehensive visualizations of detected UI elements
- Color-coded bounding boxes by element type (red=buttons, blue=textarea, magenta=links, etc.)
- Text labels showing element content
- Summary panel with element counts
- Automatically finds most recent trace file
- Generates two versions: with labels and with summary panel

**Usage:**
```bash
python scripts/create_visualization.py
```

**Output:**
- `data/output/visualizations/trace_visualization.png` - With text labels
- `data/output/visualizations/trace_visualization_summary.png` - With summary panel

### `show_trace_elements.py`
Displays all elements from a trace with their text content
- Organized by element type
- Shows metadata
- Summary table

**Usage:**
```bash
python scripts/show_trace_elements.py
```

### `summarize_trace.py`
Generates quick summary of trace contents
- Element counts by type
- Basic statistics
- Quick overview

**Usage:**
```bash
python scripts/summarize_trace.py
```

## Common Workflows

### Visualize Detection Results
```bash
# Run detection (creates trace + screenshot)
python examples/html_detection/demo_notepad.py

# Create visualization (automatically finds latest trace)
python scripts/create_visualization.py

# Output automatically opens in your default image viewer
# Files saved to: data/output/visualizations/
```

### Analyze Trace Data
```bash
# Get quick summary
python scripts/summarize_trace.py

# See all elements with text
python scripts/show_trace_elements.py
```

## Output Location

All scripts save output to `data/output/visualizations/`

## Requirements

These scripts require the trace translator to be installed:
```bash
cd components/trace_translator
pip install -r requirements.txt
```
