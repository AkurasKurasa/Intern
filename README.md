# Intern Project

A comprehensive UI detection and trace generation system for training imitation learning models.

## Overview

This project provides tools for detecting UI elements from screenshots and web pages, generating structured traces for training AI models. It supports both:

- **HTML-based detection** - Perfect accuracy for web applications
- **Computer Vision detection** - Works with any screenshot/image

## Project Structure

```
Intern/
├── components/          # Core components
│   └── trace_translator/  # Main trace translation package
├── examples/            # Example scripts
│   ├── html_detection/  # HTML detection demos
│   └── cv_detection/    # CV detection demos
├── scripts/             # Utility scripts
├── tests/               # Test files
├── data/                # Data directory
│   ├── input/          # Input images
│   ├── output/         # Generated outputs
│   └── models/         # Model files
└── docs/                # Documentation
```

## Quick Start

### Installation

```bash
# Install dependencies
cd components/trace_translator
pip install -r requirements.txt

# Install Playwright browsers (for HTML detection)
playwright install chromium
```

### Run Examples

```bash
# HTML detection demo
python examples/html_detection/demo_basic.py

# CV detection demo
python examples/cv_detection/demo_image_analysis.py
```

## Features

### HTML Detection
- ✅ 100% accurate element detection
- ✅ Exact element types (button, input, link, etc.)
- ✅ Perfect bounding boxes
- ✅ Text content extraction
- ✅ Link destinations and attributes
- ✅ No training required

### CV Detection
- ✅ Works with any image/screenshot
- ✅ OCR text extraction
- ✅ Object detection with YOLO
- ✅ Layout analysis
- ✅ Grid detection for spreadsheets

## Documentation

- [HTML Detection Guide](docs/html_detection_guide.md)
- [CV Detection Guide](docs/cv_detection_guide.md)
- [API Reference](docs/api_reference.md)
- [Examples README](examples/README.md)

## Directory Guide

- **examples/** - Ready-to-run demonstration scripts
- **scripts/** - Utility tools for visualization and analysis
- **tests/** - Test suite
- **data/** - Input/output data and models
- **docs/** - Comprehensive documentation
- **components/** - Core trace translator package

## Output Files

All generated files are organized in `data/output/`:
- `traces/` - Trace JSON files
- `screenshots/` - Captured screenshots
- `visualizations/` - Element detection visualizations
- `reports/` - Text reports

## gstack

Use the `/browse` skill from gstack for web browsing and testing. 

Quick commands:
- `/office-hours` — Describe what you're building and get strategic feedback
- `/plan-ceo-review` — Rethink feature ideas before building
- `/review` — Code review for any branch with changes
- `/qa <url>` — Test your app and find bugs

If gstack skills aren't working, run: `cd .claude/skills/gstack && ./setup`

## License

MIT