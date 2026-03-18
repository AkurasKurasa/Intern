import json
from collections import Counter

# Load trace
with open('test_data/output/test_png_trace.json', 'r') as f:
    data = json.load(f)

# Count elements
total = len(data['state']['elements'])
types = Counter(e['type'] for e in data['state']['elements'])

print(f"Total elements detected: {total}")
print(f"\nBreakdown by type:")
for elem_type, count in sorted(types.items()):
    print(f"  {elem_type}: {count}")

# Show some interesting elements
print(f"\nSample detected text:")
labels = [e for e in data['state']['elements'] if e['type'] == 'label']
for i, label in enumerate(labels[:15], 1):
    print(f"  {i}. '{label['text']}'")
