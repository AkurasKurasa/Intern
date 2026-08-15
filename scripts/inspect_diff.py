import json, os

DIR = r'C:\Users\paula\OneDrive\Desktop\Intern\data\output\traces\video_sample'

interesting = {
    'step_0002 (+8 added)':   'video_state_sample_step_0002.json',
    'step_0003 (-4 removed)': 'video_state_sample_step_0003.json',
    'step_0008 (most active)':'video_state_sample_step_0008.json',
    'step_0013 (-7 removed)': 'video_state_sample_step_0013.json',
}

for name, fname in interesting.items():
    with open(os.path.join(DIR, fname)) as f:
        t = json.load(f)
    d = t['diff']
    print(f'=== {name} ===')
    if d['added']:
        print(f'  ADDED ({len(d["added"])}):')
        for el in d['added']:
            print(f'    + "{el["text"]}"  bbox={el["bbox"]}  conf={el["confidence"]:.2f}')
    if d['removed']:
        print(f'  REMOVED ({len(d["removed"])}):')
        for el in d['removed']:
            print(f'    - "{el["text"]}"  bbox={el["bbox"]}  conf={el["confidence"]:.2f}')
    if not d['added'] and not d['removed']:
        print('  (only OCR-level changes, no structural adds/removes)')
    print()
