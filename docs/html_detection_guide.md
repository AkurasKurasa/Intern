# HTML Detection Guide

Complete guide to using HTML-based UI detection for web applications.

## Overview

HTML detection uses Playwright to directly access the DOM structure of web pages, providing 100% accurate element detection with complete metadata.

## Key Features

✅ **Perfect Accuracy** - 100% element detection rate  
✅ **Exact Element Types** - Knows buttons, inputs, links, textareas, etc.  
✅ **Complete Attributes** - href, placeholder, class, id, etc.  
✅ **Text Content** - Extracts text from all elements  
✅ **No Training Required** - Works immediately  
✅ **Fast** - Direct DOM access  

## Quick Start

```python
from trace_translator import TraceTranslator

# Initialize with HTML detection
translator = TraceTranslator(use_html=True, use_cv=False)

# Detect elements from URL
state = translator.url_to_state('https://example.com')

# Access detected elements
for element in state['elements']:
    print(f"{element['type']}: {element['text']}")
```

## Supported Element Types

- **button** - All button elements
- **input** - Text inputs, checkboxes, radio buttons, file inputs
- **textarea** - Multi-line text areas
- **link** - Anchor tags with href
- **select** - Dropdown menus
- **image** - Image elements

## Element Structure

Each detected element includes:

```python
{
    "element_id": "button_0",
    "type": "button",
    "text": "Click Me",
    "bbox": [x1, y1, x2, y2],
    "enabled": true,
    "visible": true,
    "metadata": {
        "tag": "button",
        "class": "btn btn-primary",
        "id": "submit-btn",
        # ... other attributes
    },
    "confidence": 1.0
}
```

## Advanced Usage

### Authentication

For pages requiring login:

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    
    # Login
    page.goto('https://example.com/login')
    page.fill('#username', 'user')
    page.fill('#password', 'pass')
    page.click('#login-button')
    
    # Now extract elements
    # ... use HTMLDetector methods
```

### Text Extraction

HTML detection automatically extracts text content:

```python
state = translator.url_to_state('https://example.com')

# Find textarea
textarea = [e for e in state['elements'] if e['type'] == 'textarea'][0]
print(f"Text content: {textarea['text']}")
print(f"Character count: {textarea['metadata']['char_count']}")
```

### Link Destinations

All links include their href attribute:

```python
links = [e for e in state['elements'] if e['type'] == 'link']
for link in links:
    print(f"{link['text']} -> {link['metadata']['href']}")
```

## Best Practices

1. **Use for web applications** - HTML detection is perfect for web UIs
2. **Handle authentication** - Use Playwright to log in before detection
3. **Wait for page load** - Ensure page is fully loaded before detection
4. **Check visibility** - Filter by `visible: true` for interactive elements

## Limitations

- ❌ **Web only** - Requires a URL (doesn't work with desktop apps)
- ❌ **Requires source** - Can't analyze screenshots without URL
- ❌ **Authentication** - May need login credentials for protected pages

## Use Cases

### Training Data Generation
Perfect for creating high-quality training data for imitation learning:

```python
# Record user workflow
before_state = translator.url_to_state(url)
# User performs action
after_state = translator.url_to_state(url)

training_example = {
    "before": before_state,
    "action": user_action,
    "after": after_state
}
```

### Web Automation
Automate web interactions with perfect element identification:

```python
# Find and click button
state = translator.url_to_state(url)
submit_btn = [e for e in state['elements'] 
              if e['type'] == 'button' and 'submit' in e['text'].lower()][0]
# Use bbox to click at correct position
```

### UI Testing
Verify UI structure and content:

```python
state = translator.url_to_state(url)

# Verify expected elements exist
assert len([e for e in state['elements'] if e['type'] == 'button']) == 5
assert any('Login' in e['text'] for e in state['elements'])
```

## Examples

See `examples/html_detection/` for complete examples:
- `demo_basic.py` - Basic detection
- `demo_notepad.py` - Complex UI
- `demo_with_text.py` - Text extraction
- `demo_learning_platform.py` - Real-world application

## API Reference

See [API Reference](api_reference.md) for complete API documentation.
