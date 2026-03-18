# Trace Translator Component

Computer vision-based trace translator for the Intern automation system. Converts screen state images into structured trace JSON for training imitation learning models.

## Features

- **YOLO Detection**: Detects UI elements (buttons, textboxes, tables, etc.)
- **SAM Segmentation**: Segments UI regions for precise element boundaries
- **Tesseract OCR**: Extracts text from UI elements
- **LayoutLM Parsing**: Layout-aware document understanding
- **Trace Generation**: Creates structured training traces matching `trace_format.json`

## Installation

### 1. Install Python Dependencies

```bash
cd components/trace_translator
pip install -r requirements.txt
```

### 2. Install Tesseract OCR

**Windows:**
Download and install from: https://github.com/UB-Mannheim/tesseract/wiki

**Linux:**
```bash
sudo apt-get install tesseract-ocr
```

**macOS:**
```bash
brew install tesseract
```

### 3. Download Models (Optional)

Models will be downloaded automatically on first use. For manual download:

- **SAM Model**: Download from [Segment Anything](https://github.com/facebookresearch/segment-anything)
- **YOLO Model**: Pre-trained YOLOv8 will be downloaded automatically

## Quick Start

```python
from trace_translator import TraceTranslator

# Initialize with CV pipeline
translator = TraceTranslator(use_cv=True)

# Process a screenshot
trace = translator.translate('screenshot.png', 'output/trace.json')

# View results
print(f"Detected {len(trace['state']['elements'])} UI elements")
```

## Usage Examples

### Single Screenshot Processing

```python
from trace_translator import TraceTranslator
from visualization import DetectionVisualizer
from PIL import Image

# Initialize
translator = TraceTranslator(use_cv=True)
visualizer = DetectionVisualizer()

# Process screenshot
trace = translator.translate('screenshot.png', 'trace.json')

# Visualize detections
img = Image.open('screenshot.png')
visualizer.visualize_detections(
    img, 
    trace['state']['elements'],
    'output/visualization.png'
)
```

### Creating Training Traces with Actions

```python
# Extract before and after states
before_state = translator.image_to_state('before.png', application="Excel")
after_state = translator.image_to_state('after.png', application="Excel")

# Define action
action = {
    "type": "CLICK",
    "target_element_id": "btn_save",
    "mouse_position": [1540, 70],
    "button": "left"
}

# Create trace
trace = translator.state_to_trace(
    state=before_state,
    action=action,
    next_state=after_state
)

translator.save_trace(trace, 'trace_with_action.json')
```

### Batch Processing

```python
# Process all screenshots in a directory
traces = translator.batch_translate(
    'screenshots/',
    'output/traces/'
)
```

## Configuration

Edit `cv_config.py` to customize:

- Model paths and URLs
- Detection confidence thresholds
- Device selection (CPU/GPU)
- Element type mappings
- OCR language settings

```python
from cv_config import config

# Adjust YOLO confidence threshold
config.yolo_confidence_threshold = 0.3

# Enable GPU
config.device = "cuda"
```

## Output Format

Traces follow the format defined in `trace_format.json`:

```json
{
  "trace_id": "trace_0001",
  "timestamp": "2026-02-07T16:00:00.000Z",
  "state": {
    "application": "Excel",
    "window_title": "Workbook.xlsx",
    "screen_resolution": [1920, 1080],
    "focused_element_id": "cell_A1",
    "elements": [
      {
        "element_id": "button_1",
        "type": "button",
        "bbox": [100, 50, 200, 90],
        "label": "Save",
        "text": "Save",
        "confidence": 0.95,
        "enabled": true
      }
    ]
  },
  "action": {
    "type": "CLICK",
    "target_element_id": "button_1",
    "mouse_position": [150, 70]
  },
  "next_state": { ... }
}
```

## Architecture

```
trace_translator/
├── cv_config.py              # Configuration management
├── model_manager.py          # Model loading and caching
├── cv_detector.py            # Main CV pipeline orchestrator
├── ui_element_extractor.py  # Element extraction and merging
├── trace_translator.py       # Main translator class
├── visualization.py          # Debug visualization utilities
├── examples.py               # Usage examples
└── models/                   # Downloaded models (auto-created)
```

## GPU Acceleration

For best performance, use a CUDA-capable GPU:

```python
import torch
print(f"CUDA available: {torch.cuda.is_available()}")
```

If CUDA is available, models will automatically use GPU.

## Troubleshooting

**Issue: Tesseract not found**
- Install Tesseract OCR and add to PATH
- On Windows, set `pytesseract.pytesseract.tesseract_cmd` to installation path

**Issue: Models not downloading**
- Check internet connection
- Manually download models and place in `models/` directory

**Issue: Low detection accuracy**
- Adjust confidence thresholds in `cv_config.py`
- Fine-tune YOLO on your specific UI dataset
- Ensure screenshots are high resolution

## Next Steps

1. Collect demonstration data (screenshots + actions)
2. Generate training traces using this component
3. Feed traces to the Learning Model component
4. Train imitation learning policy
5. Execute learned tasks with Control Execution component

## License

Part of the Intern automation system thesis project.
