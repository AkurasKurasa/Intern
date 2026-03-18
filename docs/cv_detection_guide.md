# Computer Vision Detection Guide

Guide to using CV-based UI detection for screenshots and images.

## Overview

CV detection uses computer vision techniques (OCR, object detection, layout analysis) to detect UI elements from screenshots and images.

## Key Features

✅ **Works with any image** - Screenshots, photos, etc.  
✅ **No URL required** - Analyze images directly  
✅ **Text extraction** - OCR with Tesseract  
✅ **Object detection** - YOLO for UI elements  
✅ **Grid detection** - Special handling for spreadsheets  

## Quick Start

```python
from trace_translator import TraceTranslator

# Initialize with CV detection
translator = TraceTranslator(use_cv=True)

# Detect elements from image
state = translator.image_to_state('screenshot.png')

# Access detected elements
for element in state['elements']:
    print(f"{element['type']}: {element['text']}")
```

## Detection Methods

### OCR (Tesseract)
Extracts text from images:
- Detects text regions
- Recognizes characters
- Provides bounding boxes

### Object Detection (YOLO)
Detects UI objects:
- Buttons
- Input fields
- Icons
- Generic UI elements

### Grid Detection
Special handling for spreadsheets:
- Detects grid lines
- Identifies cells
- Extracts cell content

## Element Structure

```python
{
    "element_id": "label_0",
    "type": "label",  # or "unknown"
    "text": "Detected text",
    "bbox": [x1, y1, x2, y2],
    "enabled": true,
    "visible": true,
    "metadata": {
        "detection_method": "ocr",
        "confidence": 0.95
    },
    "confidence": 0.95
}
```

## Configuration

Configure detection in `cv_config.py`:

```python
OCR_CONFIG = {
    'tesseract_cmd': r'C:\Program Files\Tesseract-OCR\tesseract.exe',
    'lang': 'eng',
    'config': '--psm 11'
}

YOLO_CONFIG = {
    'model_path': 'data/models/yolov8n.pt',
    'confidence': 0.25
}
```

## Best Practices

1. **Use high-quality images** - Better resolution = better detection
2. **Preprocess images** - Enhance contrast, remove noise
3. **Configure Tesseract** - Adjust PSM mode for your use case
4. **Filter by confidence** - Remove low-confidence detections

## Limitations

- ⚠️ **Approximate accuracy** - Not 100% like HTML detection
- ⚠️ **Text-focused** - Mainly detects text labels
- ⚠️ **No element types** - Can't distinguish button from label
- ⚠️ **Requires training** - Object detection needs trained models

## When to Use

**Use CV detection when:**
- Analyzing desktop applications
- Working with screenshots only
- No URL available
- Analyzing mobile app screenshots

**Use HTML detection when:**
- Analyzing web applications
- URL is available
- Need perfect accuracy
- Need element types and attributes

## Examples

See `examples/cv_detection/` for examples:
- `demo_image_analysis.py` - Image analysis
- `demo_ocr.py` - OCR demo
- `demo_tesseract.py` - Tesseract configuration

## API Reference

See [API Reference](api_reference.md) for complete API documentation.
