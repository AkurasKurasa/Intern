"""
Trace Translator Package
Unified UI detection system for web pages and screenshots.

All core functionality and detection classes live in trace_translator.py.
MouseInput and KeyboardInput live in recorder/recorder.py.
"""

from .trace_translator import (
    TraceTranslator,
    HTMLDetector,
    CVDetector,
    UIElementExtractor,
)

__version__ = "1.0.0"
__all__ = [
    "TraceTranslator",
    "HTMLDetector",
    "CVDetector",
    "UIElementExtractor",
]

