#!/usr/bin/env python3
"""
Builds the parks/ master registry from all scores.json files.
Usage: python build_registry.py
Run this once to create the registry, then use add_price.py to add prices.
"""
import json
import re
from pathlib import Path
from collections import defaultdict

project_dir = Path(__file__).resolve().parent
parks_dir = project_dir / "parks"
parks_dir.mkdir(exist_ok=True)

def slugify(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r'[^a-z0-9\s-]', '', name)
    name = re.sub(r'[\s]+', '-', name)
    name = re.sub(r'-+', '-', name)
    return name.strip('-')

# Collect all park records across all locations
park_records = defaultdict(list)

for scores_file in sorted(project_dir.glob('locations/*/*/scores.json')):
    state = scores_file.parent.parent.name
    loc_slug = scores_file.parent.name
    location_key = f"{state}/{loc_slug}"

    try:
        parks = json.loads(scores_file.read_text(encoding='utf-8'))
        for p in parks:
            name = p.get('park_name', '').strip()
            if not name:
                continue
            park_records[name].append({
                'location': location_key,
                'data': p
            })
    except Exception as e:
        print(f"Error reading {scores_file}: {e}")

print(f"Found {len(park_records)} unique park names across all locations")

created = 0
updated = 0

for park_name, records in park_records.items():
    park_slug = slugify(park_name)
    park_folder = parks_dir / park_slug
    park_folder.mkdir(exist_ok=True)
    park_file = park_folder / "master.json"

    # Pick best record — most reviews wins
    best = max(records, key=lambda r: r['data'].get('review_count') or 0)
    best_data = best['data']

    locations = sorted(set(r['location'] for r in records))

    # Check if master record already exists (preserve prices/deals)
    existing_prices = {}
    existing_deals = ""
    existing_notes = ""
    if park_file.exists():
        try:
            existing = json.loads(park_file.read_text(encoding='utf-8'))
            existing_prices = existing.get('prices', {})
            existing_deals = existing.get('deals', '')
            existing_notes = existing.get('notes', '')
            updated += 1
        except:
            pass
    else:
        created += 1

    master = {
        "park_name": park_name,
        "slug": park_slug,
        "locations": locations,
        "total_score": best_data.get('total_score'),
        "classification": best_data.get('classification'),
        "google_rating": best_data.get('google_rating'),
        "review_count": best_data.get('review_count'),
        "website": best_data.get('website'),
        "lat": best_data.get('lat'),
        "lng": best_data.get('lng'),
        "wifi_available": best_data.get('wifi_available'),
        "pet_friendly": best_data.get('pet_friendly'),
        "executive_summary": best_data.get('executive_summary', ''),
        "rationale_top3": best_data.get('rationale_top3', ''),
        "rationale_honourable": best_data.get('rationale_honourable', ''),
        "water_fun": best_data.get('water_fun', ''),
        "kids_play": best_data.get('kids_play', ''),
        "pet_detail": best_data.get('pet_detail', ''),
        "best_for": best_data.get('best_for', ''),
        "photo_url_cached": best_data.get('photo_url_cached', ''),
        "nearest_beach_cached": best_data.get('nearest_beach_cached', ''),
        "nearest_supermarket_cached": best_data.get('nearest_supermarket_cached', ''),
        "prices": existing_prices,
        "deals": existing_deals,
        "notes": existing_notes
    }

    park_file.write_text(
        json.dumps(master, indent=2, ensure_ascii=False),
        encoding='utf-8'
    )

print(f"Registry complete: {created} created, {updated} updated")
print(f"Parks with multiple locations: {sum(1 for r in park_records.values() if len(r) > 1)}")
print(f"Master records saved to: {parks_dir}")
