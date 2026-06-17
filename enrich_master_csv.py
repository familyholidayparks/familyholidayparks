"""
enrich_master_csv.py
====================
Upgrades holiday_parks_master.csv with new columns and backfills data
from the Google Places API.

Usage:
    # Upgrade schema only (add new columns, fill brand logos) — no API calls
    python enrich_master_csv.py --schema-only

    # Enrich all parks missing a Place ID (costs API quota)
    python enrich_master_csv.py --all

    # Enrich one state only
    python enrich_master_csv.py --state QLD

    # Enrich a specific park by name
    python enrich_master_csv.py --park "BIG4 Gold Coast Holiday Park"

    # Dry run — show what would be searched, no API calls
    python enrich_master_csv.py --all --dry-run

    # Limit to N parks (useful for testing)
    python enrich_master_csv.py --all --limit 10

Output: holiday_parks_master.csv (updated in place)
        enrich_report.md (summary of what changed)

Requires:
    GOOGLE_MAPS_API_KEY env var  (or hardcode below)
"""

import csv
import json
import os
import re
import sys
import time
import argparse
import requests
from pathlib import Path
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────

GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "AIzaSyD-wXTs4SJ1MoUCXNS-smGcDaQBxukAtKU")
CSV_PATH = Path("holiday_parks_master.csv")
REPORT_PATH = Path("enrich_report.md")

# Rate limiting — Places API Text Search: 10 QPS
REQUEST_DELAY = 0.15  # seconds between requests

# ── Brand logos (one per brand, propagates to all parks) ──────────────────────

BRAND_LOGOS = {
    "BIG4":                       "https://www.big4.com.au/themes/custom/big4/images/logo.svg",
    "Discovery Parks":            "https://cdn.discoverycampings.com.au/assets/img/logos/discovery-parks-logo.svg",
    "NRMA Parks and Resorts":     "https://www.nrmaparksandresorts.com.au/globalassets/nrma/logos/nrma-parks-resorts-logo.svg",
    "NRMA (managed)":             "https://www.nrmaparksandresorts.com.au/globalassets/nrma/logos/nrma-parks-resorts-logo.svg",
    "G'Day Parks":                "https://www.gdayparks.com.au/assets/img/logo.svg",
    "Kui Parks":                  "https://kuiparks.com.au/wp-content/uploads/2020/07/kui-parks-logo.png",
    "Hampshire Holidays":         "https://hampshireholidays.com.au/wp-content/uploads/2022/01/Hampshire-Holidays-Logo.png",
    "Reflections Holiday Parks":  "https://reflectionsholidayparks.com.au/wp-content/themes/rhp/img/logo.svg",
    "Ingenia Holidays":           "https://www.ingeniaholidays.com.au/Assets/logos/ingenia-holidays-logo.svg",
    "Tasman Holiday Parks":       "https://tasmanholidayparks.com.au/wp-content/uploads/2021/06/Tasman-Holiday-Parks-Logo.png",
    "Summerstar Tourist Parks":   "https://summerstartouristparks.com.au/wp-content/uploads/2020/11/summerstar-logo.png",
    "Family Parks Australia":     "https://familyparks.com.au/wp-content/uploads/2020/06/family-parks-australia-logo.png",
    "RAC Parks":                  "https://www.rac.com.au/globalassets/rac/images/rac-logo.svg",
    "Lake Mac Holiday Parks":     "https://www.lakemacholidayparks.com.au/wp-content/uploads/2021/08/lake-mac-logo.png",
    "Holiday Haven (Shoalhaven)": "https://www.holidayhaven.com.au/wp-content/uploads/2020/01/holiday-haven-logo.png",
    "Gold Coast Tourist Parks":   "https://www.goldcoasttouristparks.com.au/wp-content/uploads/2020/07/gctp-logo.png",
    "Aspen Holiday Parks":        "https://aspengroup.com.au/wp-content/uploads/2021/03/aspen-logo.svg",
    "Sunshine Coast Holiday Parks": "https://www.sunshinecoastholidayparks.com.au/wp-content/uploads/logo.png",
    "Fraser Coast Holiday Parks": "https://www.frasercoastholidayparks.com.au/wp-content/uploads/logo.png",
    "Kiama Coast Holiday Parks":  "https://www.kiamacoastholidayparks.com.au/wp-content/uploads/logo.png",
    "West Beach Parks":           "https://www.westbeachparks.com.au/wp-content/uploads/logo.png",
    "Noosa River Holiday Parks":  "https://www.noosariverholidayparks.com.au/wp-content/uploads/logo.png",
}

# ── Column schema (final column order) ────────────────────────────────────────

COLUMNS = [
    "Park Name",
    "Town/Suburb",
    "State",
    "Chain/Brand",
    "Source",
    "Slug",
    "Website",
    "Logo URL",
    "Photo URL",
    "Google Place ID",
    "Latitude",
    "Longitude",
    "Google Rating",
    "Review Count",
    "Family Score",
    "Pool",
    "Waterpark",
    "Jumping Pillow",
    "Playground",
    "Beach",
    "Pet Friendly",
    "Location Page",
]

# ── Slug generation ────────────────────────────────────────────────────────────

def make_slug(name: str) -> str:
    """BIG4 Gold Coast Holiday Park → big4-gold-coast-holiday-park"""
    s = name.lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s.strip())
    s = re.sub(r"-+", "-", s)
    return s

# ── Google Places API ──────────────────────────────────────────────────────────

def places_text_search(query: str) -> dict | None:
    """
    Text Search to find a place and return Place ID, coords, rating, reviews.
    Returns None if no result found.
    """
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": query,
        "type": "campground|rv_park|tourist_attraction",
        "key": GOOGLE_MAPS_API_KEY,
        "region": "au",
        "language": "en-AU",
    }
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") != "OK" or not data.get("results"):
        return None

    result = data["results"][0]
    return {
        "place_id":     result.get("place_id", ""),
        "lat":          str(result["geometry"]["location"]["lat"]),
        "lng":          str(result["geometry"]["location"]["lng"]),
        "rating":       str(result.get("rating", "")),
        "review_count": str(result.get("user_ratings_total", "")),
    }


def places_details(place_id: str) -> dict | None:
    """
    Place Details to get photo reference and website.
    """
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "photos,website",
        "key": GOOGLE_MAPS_API_KEY,
    }
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") != "OK":
        return None

    result = data.get("result", {})
    photo_url = ""
    photos = result.get("photos", [])
    if photos:
        ref = photos[0].get("photo_reference", "")
        if ref:
            photo_url = (
                f"https://maps.googleapis.com/maps/api/place/photo"
                f"?maxwidth=800&photo_reference={ref}&key={GOOGLE_MAPS_API_KEY}"
            )

    return {
        "photo_url": photo_url,
        "website":   result.get("website", ""),
    }

# ── CSV helpers ────────────────────────────────────────────────────────────────

def load_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_csv(path: Path, rows: list[dict]):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def upgrade_row(row: dict) -> dict:
    """Ensure row has all new columns, preserving existing values."""
    new = {col: "" for col in COLUMNS}
    for col in COLUMNS:
        if col in row:
            new[col] = row[col]

    # Auto-fill slug if missing
    if not new["Slug"] and new["Park Name"]:
        new["Slug"] = make_slug(new["Park Name"])

    # Auto-fill brand logo if missing
    if not new["Logo URL"]:
        brand = new.get("Chain/Brand", "").strip()
        new["Logo URL"] = BRAND_LOGOS.get(brand, "")

    return new

# ── Main enrichment ────────────────────────────────────────────────────────────

def enrich_row(row: dict, dry_run: bool = False) -> tuple[dict, str]:
    """
    Look up a park via Google Places and fill:
    - Google Place ID
    - Latitude / Longitude
    - Google Rating
    - Review Count
    - Photo URL
    - Website (if missing)

    Returns (updated_row, status_message)
    """
    name  = row["Park Name"]
    town  = row["Town/Suburb"]
    state = row["State"]

    query = f"{name} {town} {state} holiday park Australia"

    if dry_run:
        return row, f"DRY RUN: would search '{query}'"

    try:
        result = places_text_search(query)
        time.sleep(REQUEST_DELAY)
    except Exception as e:
        return row, f"ERROR (text search): {e}"

    if not result:
        return row, f"NOT FOUND: '{query}'"

    row["Google Place ID"] = result["place_id"]
    row["Latitude"]        = result["lat"]
    row["Longitude"]       = result["lng"]
    row["Google Rating"]   = result["rating"]
    row["Review Count"]    = result["review_count"]

    # Get photo + website from Place Details
    if result["place_id"]:
        try:
            details = places_details(result["place_id"])
            time.sleep(REQUEST_DELAY)
            if details:
                if details["photo_url"] and not row["Photo URL"]:
                    row["Photo URL"] = details["photo_url"]
                if details["website"] and not row["Website"]:
                    row["Website"] = details["website"]
        except Exception as e:
            pass  # Non-fatal — text search data is the priority

    return row, f"OK: {name} → {result['place_id']} ({result['rating']}★, {result['review_count']} reviews)"


def main():
    parser = argparse.ArgumentParser(description="Enrich holiday_parks_master.csv")
    parser.add_argument("--schema-only", action="store_true",
                        help="Upgrade column schema and fill brand logos only, no API calls")
    parser.add_argument("--all", action="store_true",
                        help="Enrich all parks missing a Place ID")
    parser.add_argument("--state", type=str,
                        help="Enrich parks in a specific state (e.g. QLD)")
    parser.add_argument("--park", type=str,
                        help="Enrich a specific park by name")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be searched without making API calls")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max parks to enrich in this run")
    parser.add_argument("--force", action="store_true",
                        help="Re-enrich even if Place ID already exists")
    parser.add_argument("--input", type=str, default=str(CSV_PATH),
                        help="Input CSV path (default: holiday_parks_master.csv)")
    parser.add_argument("--output", type=str, default=str(CSV_PATH),
                        help="Output CSV path (default: same as input)")
    args = parser.parse_args()

    input_path  = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"ERROR: {input_path} not found")
        sys.exit(1)

    print(f"Loading {input_path}...")
    raw_rows = load_csv(input_path)
    rows = [upgrade_row(r) for r in raw_rows]
    print(f"Loaded {len(rows)} parks. Schema upgraded.")

    # Schema-only — just save the upgraded CSV
    if args.schema_only:
        save_csv(output_path, rows)
        logo_count = sum(1 for r in rows if r["Logo URL"])
        slug_count  = sum(1 for r in rows if r["Slug"])
        print(f"Schema upgraded and saved to {output_path}")
        print(f"  Slugs:      {slug_count}/{len(rows)}")
        print(f"  Brand logos: {logo_count}/{len(rows)}")
        return

    # Select parks to enrich
    targets = []

    if args.park:
        targets = [r for r in rows if args.park.lower() in r["Park Name"].lower()]
        if not targets:
            print(f"No park matching '{args.park}'")
            sys.exit(1)

    elif args.state:
        state = args.state.upper()
        targets = [r for r in rows if r["State"].upper() == state]
        if not args.force:
            targets = [r for r in targets if not r["Google Place ID"]]

    elif args.all:
        targets = rows if args.force else [r for r in rows if not r["Google Place ID"]]

    else:
        parser.print_help()
        sys.exit(0)

    if args.limit:
        targets = targets[:args.limit]

    print(f"\nEnriching {len(targets)} parks{'(dry run)' if args.dry_run else ''}...")

    report_lines = [
        f"# Enrich Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Parks targeted: {len(targets)}",
        "",
    ]

    found = 0
    not_found = 0
    errors = 0

    # Build index for in-place update
    row_index = {r["Park Name"]: i for i, r in enumerate(rows)}

    for i, target in enumerate(targets):
        updated, msg = enrich_row(target, dry_run=args.dry_run)

        if "OK:" in msg:
            found += 1
        elif "NOT FOUND" in msg:
            not_found += 1
        else:
            errors += 1

        print(f"  [{i+1}/{len(targets)}] {msg}")
        report_lines.append(f"- {msg}")

        # Update in main rows list
        if target["Park Name"] in row_index:
            rows[row_index[target["Park Name"]]] = updated

    # Save
    if not args.dry_run:
        save_csv(output_path, rows)
        print(f"\nSaved to {output_path}")

    # Write report
    report_lines += [
        "",
        f"## Summary",
        f"- Found:     {found}",
        f"- Not found: {not_found}",
        f"- Errors:    {errors}",
        f"- Total rows in CSV: {len(rows)}",
        f"- Rows with Place ID: {sum(1 for r in rows if r['Google Place ID'])}",
        f"- Rows with coords:   {sum(1 for r in rows if r['Latitude'])}",
        f"- Rows with photo:    {sum(1 for r in rows if r['Photo URL'])}",
        f"- Rows with logo:     {sum(1 for r in rows if r['Logo URL'])}",
    ]

    REPORT_PATH.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"Report written to {REPORT_PATH}")
    print(f"\nDone. Found: {found} | Not found: {not_found} | Errors: {errors}")


if __name__ == "__main__":
    main()
