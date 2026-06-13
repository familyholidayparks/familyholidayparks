import json
from pathlib import Path
total = 0
parks = 0
for f in Path('locations').rglob('scores.json'):
    try:
        data = json.loads(f.read_text(encoding='utf-8'))
        if isinstance(data, list):
            for p in data:
                try:
                    total += int(float(p.get('review_count') or 0))
                    parks += 1
                except:
                    pass
    except:
        pass
print(f'Parks: {parks}')
print(f'Total Google reviews analysed: {total:,}')
