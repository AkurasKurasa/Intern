import json, os

DIR = r'C:\Users\paula\OneDrive\Desktop\Intern\data\output\traces\video_sample'

files = sorted(f for f in os.listdir(DIR) if f.endswith('.json'))

# Collect all unique text seen across ALL state_after elements, per step
# Then show progression of actual editor content
print("=" * 60)
print("TEXT CONTENT PROGRESSION ACROSS ALL FRAMES")
print("=" * 60)

all_step_texts = []
for fname in files:
    with open(os.path.join(DIR, fname)) as f:
        t = json.load(f)

    # Get all text from state_after, sorted by bbox top-to-bottom
    after_els = sorted(t['state_after']['elements'], key=lambda e: (e['bbox'][1], e['bbox'][0]))
    texts = [e['text'].strip() for e in after_els if e['text'].strip() and e['confidence'] >= 0.7]
    all_step_texts.append((fname, texts))

# Show "changed" text across steps — look at diff['changed'] text deltas
print("\n--- Changed text (before -> after) per step ---\n")
for fname in files:
    with open(os.path.join(DIR, fname)) as f:
        t = json.load(f)
    d = t['diff']
    # Only show changed entries where text actually differs significantly
    real_changes = []
    for ch in d['changed']:
        if 'text' in ch['changes']:
            before_txt = ch['changes']['text']['before'] or ''
            after_txt  = ch['changes']['text']['after']  or ''
            # Skip trivial/noisy changes (very short, garbled chars, same length swaps)
            if (len(after_txt) > 3 or len(before_txt) > 3) and before_txt != after_txt:
                real_changes.append((before_txt, after_txt, ch['changes'].get('bbox', {})))

    if real_changes:
        step = fname.replace('video_state_sample_', '').replace('.json', '')
        print(f"[{step}] ({len(real_changes)} real text changes)")
        for b, a, bbox in real_changes[:15]:
            print(f"  \"{b}\"  ->  \"{a}\"")
        if len(real_changes) > 15:
            print(f"  ... and {len(real_changes)-15} more")
        print()
