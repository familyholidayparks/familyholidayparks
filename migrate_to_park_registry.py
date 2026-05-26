#!/usr/bin/env python3
"""
Migrates parks/ flat JSON files to parks/{slug}/ folder structure.
Moves reviews and executive summaries from locations/ into parks/{slug}/.
Merges and deduplicates reviews for parks appearing in multiple locations.
Cleans up redundant files from locations/ folders.

Usage: python migrate_to_park_registry.py
       python migrate_to_park_registry.py --dry-run
"""
import argparse
import json
import re
import shutil
from pathlib import Path
from collections import defaultdict

project_dir = Path(__file__).resolve().parent
parks_dir = project_dir / "parks"

def slugify(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r'[^a-z0-9\s-]', '', name)
    name = re.sub(r'[\s]+', '-', name)
    name = re.sub(r'-+', '-', name)
    return name.strip('-')

def get_review_id(review: dict) -> str:
    """Generate a unique ID for a review to deduplicate."""
    author = review.get('author_name', '') or review.get('author', '')
    text = (review.get('text', '') or review.get('review_text', ''))[:50]
    rating = str(review.get('rating', ''))
    return f"{author}|{rating}|{text}"

def merge_reviews(review_lists: list) -> list:
    """Merge multiple review lists, deduplicate by review ID."""
    seen = set()
    merged = []
    for reviews in review_lists:
        if not isinstance(reviews, list):
            continue
        for r in reviews:
            rid = get_review_id(r)
            if rid not in seen:
                seen.add(rid)
                merged.append(r)
    return merged

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='Show what would happen without making changes')
    args = parser.parse_args()
    dry = args.dry_run

    if dry:
        print("DRY RUN — no changes will be made\n")

    # Step 1 — Build map of park_name → slug → locations
    print("Step 1: Building park location map...")
    park_location_map = defaultdict(list)  # slug → [location_dirs]

    for scores_file in sorted(project_dir.glob('locations/*/*/scores.json')):
        loc_dir = scores_file.parent
        try:
            parks = json.loads(scores_file.read_text(encoding='utf-8'))
            for p in parks:
                name = p.get('park_name', '').strip()
                if name:
                    slug = slugify(name)
                    park_location_map[slug].append((name, loc_dir))
        except Exception as e:
            print(f"  Error reading {scores_file}: {e}")

    print(f"  Found {len(park_location_map)} unique parks\n")

    # Step 2 — Convert flat parks/*.json to parks/{slug}/master.json
    print("Step 2: Converting flat registry to folder structure...")
    converted = 0
    skipped = 0

    for flat_file in sorted(parks_dir.glob("*.json")):
        slug = flat_file.stem
        park_folder = parks_dir / slug

        if park_folder.exists():
            skipped += 1
            continue

        try:
            data = json.loads(flat_file.read_text(encoding='utf-8'))
        except:
            continue

        if not dry:
            park_folder.mkdir(exist_ok=True)
            master_file = park_folder / "master.json"
            master_file.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding='utf-8'
            )
            flat_file.unlink()
        else:
            print(f"  Would create: parks/{slug}/master.json")

        converted += 1

    print(f"  Converted {converted} flat files, skipped {skipped} existing folders\n")

    # Step 3 — Move reviews and executive summaries
    print("Step 3: Moving reviews and executive summaries...")
    reviews_moved = 0
    summaries_moved = 0

    for slug, location_entries in park_location_map.items():
        park_folder = parks_dir / slug
        if not dry:
            park_folder.mkdir(exist_ok=True)
            reviews_dir = park_folder / "reviews"
            reviews_dir.mkdir(exist_ok=True)

        # Collect all review files for this park across all locations
        all_review_data = []
        review_file_name = None

        for park_name, loc_dir in location_entries:
            loc_reviews_dir = loc_dir / "reviews"
            if not loc_reviews_dir.exists():
                continue

            # Find review file matching this park slug
            park_slug = slugify(park_name)
            review_file = loc_reviews_dir / f"{park_slug}.json"

            if not review_file.exists():
                # Try to find by partial name match
                candidates = list(loc_reviews_dir.glob(f"{park_slug[:20]}*.json"))
                if candidates:
                    review_file = candidates[0]

            if review_file.exists():
                try:
                    data = json.loads(review_file.read_text(encoding='utf-8'))
                    if isinstance(data, list):
                        all_review_data.append(data)
                    elif isinstance(data, dict) and 'reviews' in data:
                        all_review_data.append(data['reviews'])
                    review_file_name = review_file.name
                    reviews_moved += 1
                except:
                    pass

        # Merge and save reviews
        if all_review_data and review_file_name:
            merged = merge_reviews(all_review_data)
            if not dry:
                dest = park_folder / "reviews" / review_file_name
                dest.write_text(
                    json.dumps(merged, indent=2, ensure_ascii=False),
                    encoding='utf-8'
                )
            else:
                total = sum(len(r) for r in all_review_data)
                print(f"  Would merge {total} reviews → parks/{slug}/reviews/{review_file_name}")

        # Move executive summary
        for park_name, loc_dir in location_entries:
            park_slug = slugify(park_name)
            exec_dir = loc_dir / "executive-summaries"
            if not exec_dir.exists():
                continue

            exec_file = exec_dir / f"{park_slug}.txt"
            if not exec_file.exists():
                candidates = list(exec_dir.glob(f"{park_slug[:20]}*.txt"))
                if candidates:
                    exec_file = candidates[0]

            if exec_file.exists():
                dest = park_folder / "executive-summary.txt"
                if not dest.exists():
                    if not dry:
                        shutil.copy2(exec_file, dest)
                    else:
                        print(f"  Would copy: {exec_file} → parks/{slug}/executive-summary.txt")
                    summaries_moved += 1
                break  # Only need one copy

    print(f"  Reviews processed: {reviews_moved}")
    print(f"  Executive summaries moved: {summaries_moved}\n")

    # Step 4 — Clean up redundant files from locations/
    print("Step 4: Cleaning up redundant files from locations/...")
    REDUNDANT_FILES = [
        'prices.json',
        'websites.json',
        'whitelist.json',
        'photos.json',
        'raw-parks.json',
    ]
    cleaned = 0

    for loc_dir in sorted(project_dir.glob('locations/*/*')):
        if not loc_dir.is_dir():
            continue
        for filename in REDUNDANT_FILES:
            f = loc_dir / filename
            if f.exists():
                if not dry:
                    f.unlink()
                else:
                    print(f"  Would delete: {f.relative_to(project_dir)}")
                cleaned += 1

    print(f"  Cleaned {cleaned} redundant files\n")

    # Step 5 — Summary
    print("="*50)
    if dry:
        print("DRY RUN COMPLETE — run without --dry-run to apply changes")
    else:
        print("MIGRATION COMPLETE")
        print(f"  Park folders created: {converted}")
        print(f"  Reviews processed: {reviews_moved}")
        print(f"  Executive summaries moved: {summaries_moved}")
        print(f"  Redundant files cleaned: {cleaned}")
        print()
        print("Next steps:")
        print("  1. python build_registry.py  (rebuild from new structure)")
        print("  2. git add -A && git commit -m 'Migrate to park registry structure'")

if __name__ == '__main__':
    main()
