# Examples

This directory contains ready-to-run example scripts demonstrating the capabilities of the trace translator.

## HTML Detection Examples

Located in `html_detection/`:

### `demo_basic.py`
Basic HTML detection on example.com
- Shows fundamental HTML detection
- Detects links and text
- Generates trace and screenshot

### `demo_notepad.py`
Analyzes online notepad application
- Detects buttons, inputs, textareas, links
- Shows complex UI detection
- Generates detailed reports

### `demo_with_text.py`
Demonstrates text extraction from form fields
- Types text into textarea
- Extracts typed content
- Shows 100% accurate text capture

### `demo_learning_platform.py`
Analyzes learning platform URL
- Handles authentication pages
- Detects login forms
- Shows real-world application

## CV Detection Examples

Located in `cv_detection/`:

### `demo_image_analysis.py`
Analyzes static images using computer vision
- OCR text extraction
- Object detection
- Works with any screenshot

### `demo_ocr.py`
Demonstrates OCR capabilities
- Tesseract integration
- Text detection and extraction
- Bounding box visualization

### `demo_tesseract.py`
Basic Tesseract OCR demo
- Simple text extraction
- Configuration examples
- Performance testing

## Running Examples

```bash
# HTML detection
python examples/html_detection/demo_basic.py
python examples/html_detection/demo_notepad.py
python examples/html_detection/demo_with_text.py

# CV detection
python examples/cv_detection/demo_image_analysis.py
python examples/cv_detection/demo_ocr.py
```

## Output

All examples save their output to `data/output/`:
- Trace JSON files
- Screenshots
- Reports

## Requirements

Make sure you've installed dependencies:
```bash
cd components/trace_translator
pip install -r requirements.txt
playwright install chromium  # For HTML detection
```

## Next Steps

- Check `scripts/` for visualization tools
- Read `docs/` for comprehensive guides
- Run `tests/` to verify installation
