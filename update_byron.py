import json, re
from pathlib import Path

def slugify(name):
    name = name.lower().strip()
    name = re.sub(r'[^a-z0-9\s-]', '', name)
    name = re.sub(r'\s+', '-', name)
    return re.sub(r'-+', '-', name).strip('-')

data = {
    'Suffolk Beachfront Holiday Park': {
        'photo': 'https://lh3.googleusercontent.com/gps-proxy/ALd4DhFFGcGS19pSXDcqJjJKy0YacTo9E649jCHqClrqUrevWQr5XurrAXPDsXvd3AWD0UFUJpsN1z8r4uDFP8xvlrLWt6MU4IABtLdZRyMkW2E31LEmj5RNw9yFRP4xSrE8YaNmp5kBenCsS31tebGQzsQtzqAaN8Cf7RQuXEoiHWL40RbFThmOo-cl=w800-h600-k-no',
        'address': '143 Alcorn St, Suffolk Park NSW 2481',
        'lat': -28.6904268, 'lng': 153.612943,
    },
    'Discovery Parks - Byron Bay': {
        'photo': 'https://lh3.googleusercontent.com/p/AF1QipN5WHSyBXcHGd_VGB24rCMv2xSXUfR1un1po8DD=w800-h600-k-no',
        'address': '1/399 Ewingsdale Rd, Byron Bay NSW 2481',
        'lat': -28.6362377, 'lng': 153.5932207,
    },
    'Broken Head Holiday Park': {
        'photo': 'https://lh3.googleusercontent.com/gps-cs-s/APNQkAFR8iyALnc9fq4gwWFGPRAIBaZYzEL4Lb5QZXNFIW-bOSTkdduZcVGgZf64F-aLxLy94VQtiBLo4rbM82UtGkxxj5_OQ_7ZqjtTDcKoj_93EKonWVyLG9Da7N0RT2G22xNSdhmQ=w800-h600-k-no',
        'address': '184 Broken Head Reserve Rd, Broken Head NSW 2481',
        'lat': -28.7054523, 'lng': 153.6142978,
    },
    'First Sun Holiday Park': {
        'photo': 'https://lh3.googleusercontent.com/p/AF1QipPfVAXizGaL5trha01T6SAOi2Uyi4GyICiFDt6r=w800-h600-k-no',
        'address': 'Lawson St, Byron Bay NSW 2481',
        'lat': -28.6410983, 'lng': 153.611705,
    },
    'Reflections Byron Bay - Holiday Park': {
        'photo': 'https://lh3.googleusercontent.com/p/AF1QipOxDTm-qSTb9YM6CWR9onEBD7X-b29vhb7qzFPr=w800-h600-k-no',
        'address': '1 Lighthouse Rd, Byron Bay NSW 2481',
        'lat': -28.6426456, 'lng': 153.6235542,
    },
    'Ingenia Holidays Byron Bay': {
        'photo': 'https://lh3.googleusercontent.com/p/AF1QipNA-BPlgYKuRDn_eVImjYnJHN6StQ82oddiZiNj=w800-h600-k-no',
        'address': '37 Broken Head Rd, Byron Bay NSW 2481',
        'lat': -28.6736918, 'lng': 153.611705,
    },
}

scores_path = Path('locations/nsw/byron-bay/scores.json')
scores = json.loads(scores_path.read_text(encoding='utf-8'))
for park in scores:
    name = park.get('park_name', '')
    if name in data:
        park['photo_url_override'] = data[name]['photo']
        park['photo_url_cached'] = data[name]['photo']
        park['address'] = data[name]['address']
        park['lat'] = data[name]['lat']
        park['lng'] = data[name]['lng']
        print(f'scores.json: {name}')
scores_path.write_text(json.dumps(scores, indent=2, ensure_ascii=False), encoding='utf-8')

parks_dir = Path('parks')
for name, fields in data.items():
    slug = slugify(name)
    master_file = parks_dir / slug / 'master.json'
    if master_file.exists():
        master = json.loads(master_file.read_text(encoding='utf-8'))
        master['photo_url_override'] = fields['photo']
        master['photo_url_cached'] = fields['photo']
        master['address'] = fields['address']
        master['lat'] = fields['lat']
        master['lng'] = fields['lng']
        master_file.write_text(json.dumps(master, indent=2, ensure_ascii=False), encoding='utf-8')
        print(f'master.json: {slug}')
    else:
        print(f'NOT FOUND: {slug}')

print('Done')
