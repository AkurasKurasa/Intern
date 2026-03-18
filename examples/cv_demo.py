"""
Computer Vision Detection Examples
Interactive demo showing CV detection capabilities

This unified script contains all CV detection examples:
1. Image analysis with OCR
2. OCR demonstration
3. Tesseract configuration

Usage:
    python examples/cv_demo.py [example_number]
    
    Or run without arguments for interactive menu
"""

import sys
sys.path.insert(0, 'components/trace_translator')

from trace_translator import TraceTranslator
from collections import Counter
import json
import os


def demo_image_analysis():
    """Example 1: Analyze images using computer vision"""
    
    print("\n" + "="*80)
    print("EXAMPLE 1: Image Analysis with CV Detection")
    print("="*80)
    
    # Check for test image
    image_path = 'data/input/samples/test.png'
    if not os.path.exists(image_path):
        print(f"\n✗ Error: Test image not found at {image_path}")
        print("Please add a test image to data/input/samples/")
        return None
    
    translator = TraceTranslator(use_cv=True)
    print("\n✓ CV detector initialized")
    
    print(f"\nAnalyzing image: {image_path}")
    state = translator.image_to_state(image_path)
    
    print(f"\nResults:")
    print(f"  Total elements: {len(state['elements'])}")
    
    types = Counter(e['type'] for e in state['elements'])
    print(f"\nElements by type:")
    for element_type, count in sorted(types.items(), key=lambda x: -x[1]):
        print(f"  {element_type}: {count}")
    
    # Show detected text
    text_elements = [e for e in state['elements'] if e.get('text')]
    if text_elements:
        print(f"\nText detected ({len(text_elements)} elements - showing first 10):")
        for i, el in enumerate(text_elements[:10], 1):
            text = el['text'][:50] + "..." if len(el['text']) > 50 else el['text']
            print(f"  {i}. '{text}' (confidence: {el['confidence']:.2f})")
    
    # Save outputs
    os.makedirs('data/output/traces', exist_ok=True)
    
    trace = translator.state_to_trace(state, trace_id='cv_image_analysis')
    translator.save_trace(trace, 'data/output/traces/cv_image_analysis_trace.json')
    
    print(f"\n✓ Outputs saved to data/output/")
    return state


def demo_ocr():
    """Example 2: OCR demonstration"""
    
    print("\n" + "="*80)
    print("EXAMPLE 2: OCR Demonstration")
    print("="*80)
    
    # Check for test image
    image_path = 'data/input/samples/test.png'
    if not os.path.exists(image_path):
        print(f"\n✗ Error: Test image not found at {image_path}")
        print("Please add a test image to data/input/samples/")
        return None
    
    print("\n✓ Initializing OCR detector...")
    translator = TraceTranslator(use_cv=True)
    
    print(f"\nRunning OCR on: {image_path}")
    
    # Use CV detector directly for OCR
    from PIL import Image
    img = Image.open(image_path)
    
    # Extract text using OCR
    state = translator.image_to_state(image_path)
    
    # Filter for text elements
    text_elements = [e for e in state['elements'] if e.get('text') and e['text'].strip()]
    
    print(f"\nOCR Results:")
    print(f"  Total text regions: {len(text_elements)}")
    
    if text_elements:
        print(f"\nDetected text:")
        for i, el in enumerate(text_elements, 1):
            print(f"\n  {i}. Text: '{el['text']}'")
            print(f"     Position: {el['bbox']}")
            print(f"     Confidence: {el['confidence']:.2f}")
    else:
        print("\n  No text detected")
    
    # Save outputs
    os.makedirs('data/output/traces', exist_ok=True)
    
    trace = translator.state_to_trace(state, trace_id='cv_ocr_demo')
    translator.save_trace(trace, 'data/output/traces/cv_ocr_demo_trace.json')
    
    print(f"\n✓ Outputs saved to data/output/")
    print(f"\nNote: OCR accuracy depends on image quality and text clarity.")
    
    return state


def demo_tesseract():
    """Example 3: Tesseract configuration demo"""
    
    print("\n" + "="*80)
    print("EXAMPLE 3: Tesseract Configuration")
    print("="*80)
    
    print("\nChecking Tesseract installation...")
    
    try:
        import pytesseract
        from PIL import Image
        
        # Check Tesseract version
        version = pytesseract.get_tesseract_version()
        print(f"✓ Tesseract version: {version}")
        
        # Check for test image
        image_path = 'data/input/samples/test.png'
        if not os.path.exists(image_path):
            print(f"\n✗ Error: Test image not found at {image_path}")
            print("Please add a test image to data/input/samples/")
            return None
        
        print(f"\nTesting Tesseract on: {image_path}")
        img = Image.open(image_path)
        
        # Test different PSM modes
        psm_modes = {
            3: "Fully automatic page segmentation",
            6: "Uniform block of text",
            11: "Sparse text (find as much text as possible)"
        }
        
        print(f"\nTesting different PSM modes:")
        for psm, description in psm_modes.items():
            print(f"\n  PSM {psm}: {description}")
            try:
                config = f'--psm {psm}'
                text = pytesseract.image_to_string(img, config=config)
                word_count = len(text.split())
                print(f"    Words detected: {word_count}")
                if text.strip():
                    preview = text[:100].replace('\n', ' ')
                    print(f"    Preview: {preview}...")
            except Exception as e:
                print(f"    Error: {e}")
        
        print(f"\n✓ Tesseract configuration test complete")
        print(f"\nRecommended PSM modes:")
        print(f"  - PSM 3: General documents")
        print(f"  - PSM 6: Clean, uniform text blocks")
        print(f"  - PSM 11: UI screenshots with scattered text")
        
    except ImportError:
        print("✗ Error: pytesseract not installed")
        print("Install with: pip install pytesseract")
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
    
    return None


def show_menu():
    """Display interactive menu"""
    print("\n" + "="*80)
    print("CV DETECTION EXAMPLES")
    print("="*80)
    print("\nChoose an example:")
    print("  1. Image analysis with OCR")
    print("  2. OCR demonstration")
    print("  3. Tesseract configuration")
    print("  0. Run all examples")
    print("\n" + "="*80)


def main():
    """Main entry point"""
    
    # Check for command-line argument
    if len(sys.argv) > 1:
        try:
            choice = int(sys.argv[1])
        except ValueError:
            print("Error: Please provide a number (1-3, or 0 for all)")
            return
    else:
        # Interactive menu
        show_menu()
        try:
            choice = int(input("\nEnter choice (0-3): "))
        except (ValueError, KeyboardInterrupt):
            print("\nCancelled.")
            return
    
    # Run selected example(s)
    examples = {
        1: ("Image Analysis", demo_image_analysis),
        2: ("OCR Demo", demo_ocr),
        3: ("Tesseract Config", demo_tesseract)
    }
    
    try:
        if choice == 0:
            # Run all
            print("\nRunning all examples...\n")
            for num, (name, func) in examples.items():
                print(f"\n{'#'*80}")
                print(f"Running Example {num}: {name}")
                print(f"{'#'*80}")
                func()
        elif choice in examples:
            name, func = examples[choice]
            func()
        else:
            print(f"Invalid choice: {choice}")
            return
        
        print("\n" + "="*80)
        print("✓ DEMO COMPLETE!")
        print("="*80)
        print("\nOutputs saved to:")
        print("  - data/output/traces/")
        print("\nNext steps:")
        print("  1. Visualize results: python scripts/trace_tools.py visualize")
        print("  2. View summary: python scripts/trace_tools.py summarize")
        print("\nNote: CV detection works with any image but is less accurate than HTML detection.")
        print("      For web applications, use html_demo.py instead for 100% accuracy.")
        
    except Exception as e:
        print(f"\n✗ Demo failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # Create sample data directory
    os.makedirs('data/input/samples', exist_ok=True)
    main()
