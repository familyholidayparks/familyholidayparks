#!/usr/bin/env python3
"""
update_hero_images.py
Reads hero_images.csv and updates config.json hero_image for each location.

CSV format (two columns):
  slug,image_url
  gold-coast,https://lh3.googleusercontent.com/...
  noosa,https://lh3.googleusercontent.com/...
  byron-bay,https://lh3.googleusercontent.com/...

Usage:
  python update_hero_images.py              # update only
  python update_hero_images.py --publish    # update + regenerate homepage + push
  python update_hero_images.py --all        # also regenerate all location pages
"""
import argparse, csv, json, subprocess, sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
LOCATIONS_DIR = PROJECT / "locations"
LOCATIONS_CSV = PROJECT / "locations.csv"
HERO_CSV = PROJECT / "hero_images.csv"

STATE_NAMES = {
    "qld": "queensland", "nsw": "new-south-wales", "vic": "victoria",
    "wa": "western-australia", "sa": "south-australia", "tas": "tasmania",
    "nt": "northern-territory", "act": "act",
}

def find_location_dir(slug):
    """Find the location directory for a given slug across all states."""
    for state_dir in LOCATIONS_DIR.iterdir():
        if not state_dir.is_dir():
            continue
        loc_dir = state_dir / slug
        if loc_dir.is_dir():
            return loc_dir, state_dir.name
    return None, None

def get_location_name(slug):
    """Get display name from locations.csv."""
    if not LOCATIONS_CSV.exists():
        return slug
    with open(LOCATIONS_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("slug","").strip() == slug:
                state = row.get("state","").strip()
                return f"{row.get('location','').strip()} {state}"
    return slug

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true", help="Regenerate homepage and push")
    parser.add_argument("--all", action="store_true", help="Also regenerate all updated location pages")
    args = parser.parse_args()

    if not HERO_CSV.exists():
        print(f"ERROR: {HERO_CSV} not found.")
        print("Create hero_images.csv with columns: slug,image_url")
        print("Example row: gold-coast,https://lh3.googleusercontent.com/...")
        sys.exit(1)

    updated = []
    skipped = []

    with open(HERO_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"\n📸 Processing {len(rows)} locations...\n")

    for row in rows:
        slug = row.get("slug", "").strip()
        url = row.get("image_url", "").strip()

        if not slug or not url:
            print(f"  ⚠️  Skipping empty row")
            continue

        loc_dir, state = find_location_dir(slug)
        if not loc_dir:
            print(f"  ❌ Not found: {slug}")
            skipped.append(slug)
            continue

        config_file = loc_dir / "config.json"
        config = {}
        if config_file.exists():
            try:
                config = json.loads(config_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        config["hero_image"] = url
        config_file.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
        loc_name = get_location_name(slug)
        print(f"  ✓ {loc_name}")
        updated.append((slug, state, loc_name))

    print(f"\n  💾 Updated {len(updated)} locations")

    # Regenerate location pages if --all
    if args.all and updated:
        print(f"\n  🔨 Regenerating {len(updated)} location pages...")
        for slug, state, loc_name in updated:
            result = subprocess.run(
                [sys.executable, str(PROJECT / "generate_page.py"), loc_name],
                cwd=PROJECT, capture_output=True, text=True
            )
            if result.returncode == 0:
                print(f"    ✓ {loc_name}")
            else:
                print(f"    ❌ {loc_name}: {result.stderr[:80]}")

    # Regenerate homepage
    if args.publish or args.all:
        print(f"\n  🏠 Regenerating homepage...")
        subprocess.run([sys.executable, str(PROJECT / "generate_homepage.py")], cwd=PROJECT)

    # Push
    if args.publish:
        print("\n  📤 Pushing to GitHub...")
        subprocess.run(["git", "add", "-A"], cwd=PROJECT)
        r = subprocess.run(
            ["git", "commit", "-m", f"Update hero images for {len(updated)} locations"],
            cwd=PROJECT, capture_output=True, text=True
        )
        out = r.stdout.strip()
        print(out or "Nothing to commit.")
        if "nothing to commit" not in out.lower():
            subprocess.run(["git", "push"], cwd=PROJECT)
            print("  ✅ Pushed!")

    print("\nDone!")

if __name__ == "__main__":
    main()
