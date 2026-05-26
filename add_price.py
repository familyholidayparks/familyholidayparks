#!/usr/bin/env python3
"""
Add powered site price and deals to a park in the master registry.

Usage:
  python add_price.py "BIG4 Apollo Bay"
  python add_price.py "BIG4 Apollo Bay" --powered "$55/night"
  python add_price.py "BIG4 Apollo Bay" --deals "10% off direct booking"
  python add_price.py --search "apollo"
"""
import argparse
import json
import re
from pathlib import Path

project_dir = Path(__file__).resolve().parent
parks_dir = project_dir / "parks"

def slugify(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r'[^a-z0-9\s-]', '', name)
    name = re.sub(r'[\s]+', '-', name)
    name = re.sub(r'-+', '-', name)
    return name.strip('-')

def find_park(query: str):
    slug = slugify(query)
    exact = parks_dir / slug / "master.json"
    if exact.exists():
        return exact

    query_lower = query.lower()
    matches = [
        f for f in [f / "master.json" for f in sorted(parks_dir.iterdir()) if f.is_dir() and (f / "master.json").exists()]
        if query_lower in f.parent.name.replace('-', ' ')
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"\nMultiple matches for '{query}':")
        for i, m in enumerate(matches):
            data = json.loads(m.read_text(encoding='utf-8'))
            print(f"  {i+1}. {data['park_name']} ({', '.join(data['locations'])})")
        choice = input("Enter number: ").strip()
        try:
            return matches[int(choice) - 1]
        except:
            return None
    return None

def show_park(data: dict) -> None:
    powered = data.get('prices', {}).get('powered_weekday') or '—'
    print(f"\n{'='*55}")
    print(f"  {data['park_name']}")
    print(f"  Locations : {', '.join(data['locations'])}")
    print(f"  Score     : {data.get('total_score')}/100  |  Google: {data.get('google_rating')}★ ({data.get('review_count')} reviews)")
    print(f"  Powered   : {powered}")
    print(f"  Deals     : {data.get('deals') or '—'}")
    print(f"  Notes     : {data.get('notes') or '—'}")
    print(f"{'='*55}\n")

def main():
    parser = argparse.ArgumentParser(description='Add prices to a park in the master registry')
    parser.add_argument('park', nargs='?', help='Park name (partial match ok)')
    parser.add_argument('--powered', help='Powered site weekday price e.g. "$55/night"')
    parser.add_argument('--deals', help='Deals or discount text')
    parser.add_argument('--notes', help='Internal notes')
    parser.add_argument('--search', help='Search parks by partial name')
    parser.add_argument('--clear', action='store_true', help='Clear all prices for this park')
    args = parser.parse_args()

    if args.search:
        query_lower = args.search.lower()
        matches = [
            f for f in [f / "master.json" for f in sorted(parks_dir.iterdir()) if f.is_dir() and (f / "master.json").exists()]
            if query_lower in f.parent.name.replace('-', ' ')
        ]
        print(f"\nResults for '{args.search}':")
        for m in matches:
            data = json.loads(m.read_text(encoding='utf-8'))
            powered = data.get('prices', {}).get('powered_weekday') or '—'
            print(f"  {data['park_name']}  |  powered: {powered}  |  {', '.join(data['locations'])}")
        return

    if not args.park:
        parser.print_help()
        return

    park_file = find_park(args.park)
    if not park_file:
        print(f"\n❌ Park not found: '{args.park}'")
        print("Try: python add_price.py --search \"partial name\"")
        return

    data = json.loads(park_file.read_text(encoding='utf-8'))

    no_updates = not any([args.powered, args.deals, args.notes, args.clear])
    if no_updates:
        show_park(data)
        return

    prices = data.get('prices', {})

    if args.clear:
        prices = {'powered_weekday': None}

    if args.powered:
        prices['powered_weekday'] = args.powered

    data['prices'] = prices

    if args.deals is not None:
        data['deals'] = args.deals
    if args.notes is not None:
        data['notes'] = args.notes

    park_file.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding='utf-8'
    )

    show_park(data)
    print(f"✅ Saved — {park_file.name}")
    if len(data['locations']) > 1:
        print(f"ℹ️  This park appears in {len(data['locations'])} locations — price updated everywhere automatically.")

if __name__ == '__main__':
    main()
