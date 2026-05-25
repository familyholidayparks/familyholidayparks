#!/usr/bin/env python3
"""
Pre-approval script — scrape and approve parks before overnight scoring.

Usage:
  python preapprove.py "Noosa QLD" "Cairns QLD"
  python preapprove.py --file tonight.txt
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))
from score_parks import (
    get_location_dir,
    init_location_dir,
    scrape_parks_with_apify,
    load_park_whitelist,
    log,
    log_err,
)

ALLOWED_TYPES = {"campground", "rv_park", "caravan_park"}

NON_CARAVAN_TERMS = [
    "hotel", "motel", "resort", "hostel", "backpacker",
    "apartment", "villa", "suites", "inn", "lodge",
    "museum", "lookout", "picnic", "national park",
    "scuba", "transmission", "auto", "complex", "sports",
]

def passes_name_filter(name: str) -> bool:
    name_lower = name.lower()
    return not any(term in name_lower for term in NON_CARAVAN_TERMS)

def passes_type_check(name: str, types: list, whitelist: set) -> bool:
    if name.strip().lower() in whitelist:
        return True
    types_lower = {str(t).lower() for t in (types or [])}
    return bool(ALLOWED_TYPES & types_lower)

def preapprove_location(location: str, project_dir: Path, apify_token: str) -> None:
    print(f"\n{'='*60}")
    print(f"LOCATION: {location}")
    print(f"{'='*60}")

    loc_dir = get_location_dir(project_dir, location)
    init_location_dir(loc_dir)
    whitelist_path = loc_dir / "whitelist.json"
    whitelist = set()
    if whitelist_path.exists():
        try:
            raw = json.loads(whitelist_path.read_text(encoding="utf-8"))
            whitelist = {str(k).strip().lower() for k, v in raw.items() if v}
        except Exception:
            pass

    approved_path = loc_dir / "approved-parks.json"
    if approved_path.exists():
        print(f"Already approved — skipping. Delete approved-parks.json to redo.")
        return

    # Load or scrape
    raw_cache = loc_dir / "raw-parks.json"
    if raw_cache.exists():
        print(f"Loading cached parks...")
        parks = json.loads(raw_cache.read_text(encoding="utf-8"))
    else:
        print(f"Scraping Apify...")
        try:
            parks = scrape_parks_with_apify(apify_token, location)
            raw_cache.write_text(json.dumps(parks, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"Scraped {len(parks)} raw results.")
        except Exception as e:
            print(f"ERROR: {e}")
            return

    # Filter
    valid = []
    for park in parks:
        name = str(park.get("title") or park.get("name") or "").strip()
        if not name:
            continue
        if not passes_name_filter(name):
            continue
        reviews = int(park.get("reviewsCount") or 0)
        rating = float(park.get("totalScore") or 0)
        if reviews < 25:
            continue
        valid.append({"name": name, "rating": rating, "reviews": reviews})

    # Dedupe
    seen = set()
    deduped = []
    for p in valid:
        if p["name"].lower() not in seen:
            seen.add(p["name"].lower())
            deduped.append(p)

    # Sort by reviews
    deduped.sort(key=lambda x: x["reviews"], reverse=True)

    if not deduped:
        print(f"No valid parks found.")
        return

    # Show list
    print(f"\nFound {len(deduped)} parks:")
    for i, p in enumerate(deduped, 1):
        print(f"  {i:2}. {p['name']:<45} rating={p['rating']:.1f}  reviews={p['reviews']}")

    # Exclude
    print(f"\nEnter numbers to EXCLUDE (comma separated) or press Enter to approve all:")
    try:
        exclude_input = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("Skipped.")
        return

    excluded = set()
    if exclude_input:
        for x in exclude_input.split(","):
            try:
                excluded.add(int(x.strip()) - 1)
            except ValueError:
                pass

    # Add extra parks
    print(f"Enter park names to ADD (comma separated) or press Enter to skip:")
    try:
        add_input = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        add_input = ""

    extra = []
    if add_input:
        for name in add_input.split(","):
            name = name.strip()
            if name:
                extra.append(name)
                # Auto-add to whitelist
                wl = {}
                if whitelist_path.exists():
                    try:
                        wl = json.loads(whitelist_path.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                wl[name] = True
                whitelist_path.write_text(json.dumps(wl, indent=2, ensure_ascii=False), encoding="utf-8")
                print(f"Added '{name}' to whitelist.")

    approved = [p["name"] for i, p in enumerate(deduped) if i not in excluded] + extra

    print(f"\nApproved {len(approved)} parks:")
    for p in approved:
        print(f"  ✓ {p}")

    approved_data = {
        "approved_parks": approved,
        "date": str(date.today()),
        "location": location,
    }
    approved_path.write_text(json.dumps(approved_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved to {approved_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-approve parks for overnight scoring.")
    parser.add_argument("locations", nargs="*", help="Location names e.g. 'Noosa QLD'")
    parser.add_argument("--file", help="Text file with one location per line")
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parent
    apify_token = os.environ.get("APIFY_TOKEN", "").strip()
    if not apify_token:
        print("ERROR: APIFY_TOKEN not set")
        return

    locations = list(args.locations)
    if args.file:
        file_path = Path(args.file)
        if file_path.exists():
            locations += [l.strip() for l in file_path.read_text().splitlines() if l.strip()]

    if not locations:
        print("No locations provided. Use: python preapprove.py 'Noosa QLD' or --file tonight.txt")
        return

    for location in locations:
        preapprove_location(location, project_dir, apify_token)

    print("\n✅ Pre-approval complete. Run: python build_all.py")


if __name__ == "__main__":
    main()
