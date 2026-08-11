import json
import re
import sys
from collections import Counter

sys.path.insert(0, 'c:/Projects/Genshin Artifact Scorer')

from config import load_configs
from inventory import classify_inventory_artifact
from render_html import sort_inventory_for_display
from strongbox_cache import confirm_cache, load_cache

good = json.load(open('data/genshin_data.json', encoding='utf-8'))
_, rules, _ = load_configs()
# Cache is loaded + confirmed below, once positional ids are assigned.
for i, a in enumerate(good.get('artifacts', [])):
    a['id'] = i
cache = confirm_cache(load_cache(), good.get('artifacts', []))

rows = []
for a in good.get('artifacts', []):
    if a.get('location'):
        continue
    c = classify_inventory_artifact(a, [], prob_cache=cache, inventory_config=rules.get('inventory', {}))
    c['artifact'] = a
    c['slot'] = {'flower': 'Flower', 'plume': 'Feather', 'sands': 'Sands', 'goblet': 'Goblet', 'circlet': 'Circlet'}.get(a.get('slotKey'))
    c['ceiling'] = 0
    c['fits'] = []
    rows.append(c)

sorted_rows = sort_inventory_for_display(rows)
levels = Counter(r['artifact'].get('level', 0) for r in sorted_rows)
print('strongbox rows:', len(sorted_rows), '| levels present:', dict(levels))
subs = Counter(len(r['artifact'].get('substats', [])) for r in sorted_rows)
print('visible substat counts:', dict(subs))
print('first 6:', [(r['artifact']['setKey'], r['slot'], r['artifact'].get('level')) for r in sorted_rows[:6]])
print('OK')