# HTML Detection Guide

This guide explains how to use the HTML detection pipeline in Intern. By the end you should be able to extract UI elements from any web page, generate traces, and understand exactly what the code is doing at each step.

---

## What is HTML Detection?

HTML detection uses **Playwright** (a browser automation library) to open a real Chromium browser, navigate to a URL, and extract all interactive UI elements directly from the **DOM** — the live structure of the page.

Because the data comes straight from the page's HTML rather than from visual inference, it is **100% accurate** — every element type, bounding box, text, and attribute is exact.

**Compare with CV detection:**

| | HTML Detection | CV Detection |
|---|---|---|
| Works on | Web pages (URLs) | Any screenshot/image |
| Accuracy | 100% | Medium (OCR-dependent) |
| Element types | `button`, `input`, `link`, `dropdown`, etc. | `label` (text regions only) |
| Requires browser | Yes (Playwright + Chromium) | No |
| Use case | Web automation | Desktop apps, PDFs, images |

---

## Prerequisites

### 1. Install dependencies

```bash
cd components/trace_translator
pip install -r requirements.txt
```

### 2. Install the Playwright browser

```bash
playwright install chromium
```

### 3. Verify installation

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('https://example.com')
    print(page.title())  # Should print: "Example Domain"
    browser.close()
```

---

## Quick Start

All examples below assume you run them from the **project root** (`c:\Users\Ralph\Documents\Intern\Intern`).

### Extract elements from a web page

```python
import sys
sys.path.insert(0, 'components/trace_translator')

from trace_translator import TraceTranslator

translator = TraceTranslator(use_html=True, use_cv=False)
state = translator.url_to_state('https://example.com')

print(f"Page:     {state['window_title']}")
print(f"Elements: {len(state['elements'])}")

for el in state['elements']:
    print(f"  {el['element_id']}: {el['type']} — '{el['text'][:50]}'")
```

### Save a trace to JSON

```python
import sys
sys.path.insert(0, 'components/trace_translator')

import os
from trace_translator import TraceTranslator

translator = TraceTranslator(use_html=True, use_cv=False)
state = translator.url_to_state('https://example.com')

trace = translator.state_to_trace(state, trace_id='example_snapshot')

os.makedirs('data/output/traces', exist_ok=True)
translator.save_trace(trace, 'data/output/traces/example_trace.json')
print("Saved to data/output/traces/example_trace.json")
```

### Compare two pages (before/after)

```python
import sys
sys.path.insert(0, 'components/trace_translator')

from trace_translator import TraceTranslator

translator = TraceTranslator(use_html=True, use_cv=False)

state_before = translator.url_to_state('https://example.com')
state_after  = translator.url_to_state('https://example.com/page2')

trace = translator.states_to_trace(
    state_before,
    state_after,
    action={'type': 'click', 'description': 'navigated to page 2'},
    trace_id='navigation_trace'
)

diff = trace['diff']
print(f"Added:   {diff['summary']['added_count']}")
print(f"Removed: {diff['summary']['removed_count']}")
print(f"Changed: {diff['summary']['changed_count']}")
```

---

## How the Pipeline Works

Here is the full data flow from a URL to a saved trace:

```
URL (string)
      │
      ▼
 HTMLDetector.extract_ui_elements(url)
      │
      ├── Playwright: p.chromium.launch(headless=True)
      ├── page.goto(url, wait_until='networkidle')
      ├── page.wait_for_timeout(1000)   ← let JS finish rendering
      │
      ├── page.evaluate(JS)  ← runs in the browser context
      │       Queries: button, input, textarea, a, select,
      │                input[type=checkbox], input[type=radio], img
      │       For each: getBoundingClientRect() + attributes
      │       Skips: elements with width=0 or height=0
      │
      ├── page.screenshot()  ← captures the visible viewport
      │
      └── Returns: { elements, screenshot, page_info }
      │
      ▼
 TraceTranslator.url_to_state()
      │
      └── Wraps result into the standard state dict:
          { application, window_title, screen_resolution,
            focused_element_id, elements, metadata }
      │
      ▼
 TraceTranslator.state_to_trace()       ← single state snapshot
     OR
 TraceTranslator.states_to_trace()      ← two states with diff
      │
      └── states_to_trace calls _diff_states():
              IoU matching (≥ 0.3) + center proximity (< 30 px)
              Produces: added / removed / changed lists
      │
      ▼
 TraceTranslator.save_trace()  ──►  trace.json
```

---

## DOM Traversal — What Gets Extracted

The JavaScript injected into the page queries these selectors in order:

| Selector | Element type | Extra metadata |
|----------|-------------|----------------|
| `button` | `button` | — |
| `input` | `input` | `input_type` (text, email, password, etc.) |
| `textarea` | `textarea` | `char_count` |
| `a` | `link` | `href` |
| `select` | `dropdown` | `options` (list of option texts) |
| `input[type="checkbox"]` | `checkbox` | `checked` (bool) |
| `input[type="radio"]` | `radio` | `checked` (bool) |
| `img` | `image` | `src`, `alt` |

**Elements are skipped** if their bounding rect has `width = 0` or `height = 0` — this filters out hidden and `display: none` elements automatically.

---

## Bounding Box Calculation

Bounding boxes are computed using the browser's built-in [`getBoundingClientRect()`](https://developer.mozilla.org/en-US/docs/Web/API/Element/getBoundingClientRect), which returns the element's position relative to the **viewport**.

```js
const rect = el.getBoundingClientRect();
bbox: [
    Math.round(rect.left),   // x1
    Math.round(rect.top),    // y1
    Math.round(rect.right),  // x2
    Math.round(rect.bottom)  // y2
]
```

All coordinates are in **CSS pixels** and are rounded to integers. For pages with `devicePixelRatio > 1` (Retina/HiDPI screens), the screenshot pixels and bbox coordinates will match the viewport CSS pixels, not physical pixels.

---

## Attribute Mapping

For each element, the following attributes are extracted into the `metadata` field:

| Field | Source | Notes |
|-------|--------|-------|
| `tag` | `el.tagName.toLowerCase()` | e.g. `"button"`, `"a"` |
| `class` | `el.className` | Full class string |
| `id` | `el.id` | HTML `id` attribute |
| `type` | `el.type` | Input type (`text`, `email`, etc.) |
| `placeholder` | `el.placeholder` | Input placeholder text |
| `href` | `el.href` | Link destination (full URL) |
| `value` | `el.value` | Current value of inputs/textareas |
| `role` | `el.getAttribute('role')` | ARIA role |
| `name` | `el.name` | Form field name |

The top-level `label` field prefers `aria-label` if present, falling back to `innerText`.

---

## Classes and Methods

### `HTMLDetector`

Handles the browser and DOM extraction.

```python
HTMLDetector(
    headless=True,    # Hide the browser window (set False to watch it run)
    timeout=30000     # Page load timeout in milliseconds (default: 30s)
)
```

**`extract_ui_elements(url)`**

Opens the URL in Chromium, extracts all interactive elements, and takes a screenshot.

Returns:
```python
{
    'elements':   List[Dict],   # Structured element list
    'screenshot': PIL.Image,    # Screenshot of the visible viewport
    'page_info':  {
        'title':    str,
        'url':      str,
        'viewport': { 'width': int, 'height': int }
    }
}
```

**`_extract_all_elements(page)`** (internal)

Runs the JavaScript extraction and appends `confidence: 1.0` to every element. Called internally by `extract_ui_elements()` — you do not call this directly.

---

### `TraceTranslator`

Main entry point. Wraps `HTMLDetector` and handles trace generation.

```python
TraceTranslator(
    trace_format_path=None,   # Path to trace_format.json template (auto-found if None)
    use_cv=False,             # Disable CV for HTML-only workflows
    use_html=True             # Enable HTML detection
)
```

**`url_to_state(url, application='Chrome')`**

Calls `HTMLDetector.extract_ui_elements()` and packages the result into a state dict.

- `url` — the page to open
- `application` — label stored in the state (default: `'Chrome'`)

Returns a **UI state dict** (see Output Format below).

Also stores the screenshot at `translator._last_screenshot` (a `PIL.Image`) for you to save separately.

```python
state = translator.url_to_state('https://example.com', application='Chrome')
translator._last_screenshot.save('data/output/screenshots/page.png')
```

---

**`state_to_trace(state, trace_id=None, action=None)`**

Wraps a single state into a trace dict. Use this for snapshots when you only have one state.

- `state` — the state dict from `url_to_state()`
- `trace_id` — optional string identifier (auto-generated if `None`)
- `action` — optional dict describing the action that produced this state

```python
trace = translator.state_to_trace(
    state,
    trace_id='login_page_snapshot',
    action={'type': 'navigate', 'url': 'https://example.com/login'}
)
```

---

**`states_to_trace(state_before, state_after, action=None, trace_id=None)`**

Compares two states and returns a trace with a full diff. **Preferred method** for recording workflows.

```python
trace = translator.states_to_trace(
    state_before,
    state_after,
    action={'type': 'click', 'element_id': 'button_0'},
    trace_id='step_001'
)
```

The diff uses **IoU-based element matching** — elements are matched by bounding box overlap, not by `element_id`. This is important because IDs are sequential and will differ between pages.

---

**`save_trace(trace, output_path)`**

Writes a trace dict to a JSON file. Creates parent directories if needed.

```python
translator.save_trace(trace, 'data/output/traces/step_001.json')
```

**`save_traces(traces, output_dir)`**

Writes a list of traces to a directory, one file per trace.

```python
translator.save_traces(traces, 'data/output/traces/')
```

---

## Output Format

### UI State

```json
{
    "application": "Chrome",
    "window_title": "Example Domain",
    "screen_resolution": [1280, 720],
    "focused_element_id": null,
    "elements": [
        {
            "element_id": "link_0",
            "type": "link",
            "bbox": [100, 200, 350, 220],
            "text": "More information...",
            "label": "More information...",
            "enabled": true,
            "visible": true,
            "confidence": 1.0,
            "metadata": {
                "tag": "a",
                "class": "",
                "id": "",
                "type": null,
                "placeholder": null,
                "href": "https://www.iana.org/domains/reserved",
                "value": null,
                "role": null,
                "name": null
            }
        }
    ],
    "metadata": {
        "url": "https://example.com",
        "detection_method": "html",
        "detection_timestamp": "2026-03-28T10:00:00.000000",
        "num_elements_detected": 1
    }
}
```

### Trace (single state snapshot)

```json
{
    "trace_id": "example_snapshot",
    "timestamp": "2026-03-28T10:00:00.000000",
    "state_before": { "...": "see UI State above" },
    "state_after": null,
    "action": null,
    "metadata": {
        "trace_type": "snapshot",
        "detection_method": "html",
        "num_elements": 1
    }
}
```

### Trace (two-state transition)

```json
{
    "trace_id": "step_001",
    "timestamp": "2026-03-28T10:00:00.000000",
    "state_before": { "...": "..." },
    "state_after":  { "...": "..." },
    "action": { "type": "click", "element_id": "button_0" },
    "diff": {
        "added":   [],
        "removed": [],
        "changed": [
            {
                "element_id":       "input_1",
                "element_id_after": "input_1",
                "match_score":      1.0,
                "changes": {
                    "text": { "before": "", "after": "john@example.com" }
                }
            }
        ],
        "summary": {
            "total_before":   8,
            "total_after":    8,
            "matched":        8,
            "added_count":    0,
            "removed_count":  0,
            "changed_count":  1,
            "diff_method":    "position_iou"
        }
    },
    "metadata": {
        "trace_type":               "transition",
        "detection_method_before":  "html",
        "detection_method_after":   "html",
        "num_elements_before":      8,
        "num_elements_after":       8
    }
}
```

---

## Working Examples

### Example 1 — Inspect all elements on a page

```python
import sys, json
sys.path.insert(0, 'components/trace_translator')
from trace_translator import TraceTranslator
from collections import Counter

translator = TraceTranslator(use_html=True, use_cv=False)
state = translator.url_to_state('https://example.com')

# Count elements by type
counts = Counter(e['type'] for e in state['elements'])
for t, n in sorted(counts.items()):
    print(f"  {t}: {n}")

# Print all elements
for el in state['elements']:
    print(f"  [{el['element_id']}] {el['type']}: '{el['text'][:60]}'")
    if el['metadata'].get('href'):
        print(f"    href: {el['metadata']['href']}")
```

### Example 2 — Extract and filter input fields

```python
import sys
sys.path.insert(0, 'components/trace_translator')
from trace_translator import TraceTranslator

translator = TraceTranslator(use_html=True, use_cv=False)
state = translator.url_to_state('https://httpbin.org/forms/post')

inputs = [e for e in state['elements'] if e['type'] == 'input']
print(f"Found {len(inputs)} input fields:\n")
for inp in inputs:
    print(f"  id:          {inp['element_id']}")
    print(f"  input type:  {inp['metadata'].get('type', 'text')}")
    print(f"  placeholder: {inp['metadata'].get('placeholder', '')}")
    print(f"  bbox:        {inp['bbox']}")
    print()
```

### Example 3 — Save screenshot alongside the trace

```python
import sys, os
sys.path.insert(0, 'components/trace_translator')
from trace_translator import TraceTranslator

os.makedirs('data/output/traces',      exist_ok=True)
os.makedirs('data/output/screenshots', exist_ok=True)

translator = TraceTranslator(use_html=True, use_cv=False)
state = translator.url_to_state('https://example.com')

# Save trace
trace = translator.state_to_trace(state, trace_id='example_with_screenshot')
translator.save_trace(trace, 'data/output/traces/example_with_screenshot.json')

# Save the screenshot captured during detection
translator._last_screenshot.save('data/output/screenshots/example.png')

print("Saved trace and screenshot to data/output/")
```

### Example 4 — Record a two-state workflow step

```python
import sys, os
sys.path.insert(0, 'components/trace_translator')
from trace_translator import TraceTranslator

os.makedirs('data/output/traces', exist_ok=True)

translator = TraceTranslator(use_html=True, use_cv=False)

# Capture state before an action
state_before = translator.url_to_state(
    'https://example.com',
    application='Chrome'
)

# (In a real recording, the user would do something here)
# Capture state after the action
state_after = translator.url_to_state(
    'https://www.iana.org/domains/reserved',
    application='Chrome'
)

trace = translator.states_to_trace(
    state_before,
    state_after,
    action={'type': 'click', 'description': 'clicked More information link'},
    trace_id='example_to_iana'
)

translator.save_trace(trace, 'data/output/traces/example_to_iana.json')

diff = trace['diff']
print(f"Elements before: {diff['summary']['total_before']}")
print(f"Elements after:  {diff['summary']['total_after']}")
print(f"Added:           {diff['summary']['added_count']}")
print(f"Removed:         {diff['summary']['removed_count']}")
print(f"Changed:         {diff['summary']['changed_count']}")
```

### Example 5 — Use HTMLDetector directly (lower level)

```python
import sys
sys.path.insert(0, 'components/trace_translator')
from trace_translator import HTMLDetector

# Use HTMLDetector directly without TraceTranslator
detector = HTMLDetector(headless=True, timeout=30000)
result = detector.extract_ui_elements('https://example.com')

print(f"Title:    {result['page_info']['title']}")
print(f"URL:      {result['page_info']['url']}")
print(f"Viewport: {result['page_info']['viewport']}")
print(f"Elements: {len(result['elements'])}")

# Screenshot is a PIL Image
result['screenshot'].save('page_screenshot.png')
```

### Example 6 — Run the bundled demo

A ready-to-run demo is included with four examples:

```bash
# Interactive menu
python examples/html_demo.py

# Run a specific example
python examples/html_demo.py 1    # example.com
python examples/html_demo.py 2    # RapidTables notepad
python examples/html_demo.py 4    # Login page analysis
```

---

## Tunable Parameters

### `headless` (default: `True`)

When `False`, the browser window is visible. Useful for debugging — you can watch the page load and see exactly what Playwright is seeing.

```python
translator = TraceTranslator(use_html=True)
translator.html_detector = HTMLDetector(headless=False)  # Watch it run
```

### `timeout` (default: `30000` ms)

How long to wait for the page to reach `networkidle` before extracting elements. Increase for slow pages.

```python
detector = HTMLDetector(timeout=60000)  # Wait up to 60s
```

### `wait_until='networkidle'`

Playwright's page load strategy. Currently hardcoded. Options and when to use each:

| Strategy | When to use |
|----------|------------|
| `'load'` | Page fires the `load` event — fast but may miss lazy-loaded elements |
| `'domcontentloaded'` | DOM is parsed — fastest, misses most JS-rendered content |
| `'networkidle'` | No network requests for 500ms — **current setting**, best for SPAs |
| `'commit'` | Navigation committed — rarely useful |

To change it, edit `trace_translator.py` line 107:
```python
page.goto(url, timeout=self.timeout, wait_until='networkidle')
```

### `page.wait_for_timeout(1000)` (1 second extra wait)

After `networkidle`, the code waits an additional 1000ms to allow JavaScript animations and deferred rendering to finish. Increase if elements are still missing on dynamic pages.

---

## Troubleshooting

**`playwright._impl._errors.TimeoutError`**
- The page took longer than `timeout` ms to load
- Increase `timeout`: `HTMLDetector(timeout=60000)`
- Or switch to `wait_until='load'` for faster but less complete extraction

**Missing elements on JavaScript-heavy pages**
- The page renders content after `networkidle` fires
- Increase the extra wait: change `page.wait_for_timeout(1000)` to `page.wait_for_timeout(3000)`
- Set `headless=False` to watch the page and see when elements appear

**`playwright install` not run / browser not found**
```bash
playwright install chromium
```

**`ImportError: playwright not installed`**
```bash
pip install playwright
playwright install chromium
```

**Elements have `bbox: [0, 0, 0, 0]`**
- This should not happen (zero-size elements are filtered out)
- If it does, the element is technically in the DOM but not rendered — this is expected behaviour

**`_last_screenshot` is `None`**
- `_last_screenshot` is only set after `url_to_state()` is called
- It holds only the **most recent** screenshot — call it before calling `url_to_state()` again