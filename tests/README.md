# Tests

This directory contains test files for the trace translator components.

## Test Files

### `test_html_detector.py`
Tests for HTML-based UI detection
- Element detection accuracy
- Text extraction
- Attribute capture
- Screenshot generation

### `test_cv_pipeline.py`
Tests for computer vision detection pipeline
- OCR functionality
- Object detection
- Grid detection
- Layout analysis

## Running Tests

```bash
# Run all tests
python -m pytest tests/

# Run specific test
python tests/test_html_detector.py
python tests/test_cv_pipeline.py
```

## Test Coverage

- ✅ HTML element detection
- ✅ CV detection pipeline
- ✅ Text extraction (OCR)
- ✅ Trace generation
- ✅ Screenshot capture

## Adding New Tests

1. Create test file: `test_<feature>.py`
2. Import necessary modules
3. Write test functions
4. Run with pytest

## Requirements

```bash
cd components/trace_translator
pip install -r requirements.txt
pip install pytest  # If not already installed
```
