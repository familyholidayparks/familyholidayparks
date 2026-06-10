#!/usr/bin/env python3
"""
enrich_locations.py — Overnight AI copy generator for Family Holiday Parks

Creates or updates reviews/[slug].txt using approved park data and Claude API.
Gold Coast is the quality benchmark. Never overwrite gold-coast-qld.txt.

Usage:
    python enrich_locations.py --slug byron-bay-nsw
    python enrich_locations.py --state NSW
    python enrich_locations.py --missing-only
    python enrich_locations.py --force
    python enrich_locations.py --publish
"""

import anthropic
import csv
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

PROJECT_DIR = Path(__file__).parent
LOCATIONS_CSV = PROJECT_DIR / "locations.csv"
REVIEWS_DIR = PROJECT_DIR / "reviews"
REVIEWS_DIR.mkdir(exist_ok=True)

PROTECTED_SLUGS = {"gold-coast-qld"}

# Use Haiku for speed and cost efficiency on batch copy generation
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 3000

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))


# ── Data loaders ─────────────────────────────────────────────────────────────

def load_approved_parks(slug: str, state: str) -> list[dict]:
    """Load approved-parks.json if it exists, else fall back to scores.json."""
    state_lower = state.lower()
    approved_path = PROJECT_DIR / "locations" / state_lower / slug / "approved-parks.json"
    scores_path = PROJECT_DIR / "locations" / state_lower / slug / "scores.json"

    for path in [approved_path, scores_path]:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list) and data:
                    source = "approved-parks.json" if path == approved_path else "scores.json"
                    print(f"  [approved parks] Loaded {len(data)} from {source}")
                    return data
            except Exception:
                continue
    print(f"  [approved parks] None found")
    return []


def load_supplementary(slug: str, state: str, filename: str) -> dict:
    """Load a supplementary JSON file (photos, prices, websites)."""
    path = PROJECT_DIR / "locations" / state.lower() / slug / filename
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def read_review_file(slug: str) -> dict:
    """Parse existing review file into sections dict."""
    path = REVIEWS_DIR / f"{slug}.txt"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    sections = {}
    current = None
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.endswith(":") and stripped.rstrip(":").upper() == stripped.rstrip(":"):
            if current:
                sections[current] = "\n".join(lines).strip()
            current = stripped.rstrip(":")
            lines = []
        elif current:
            lines.append(line)
    if current:
        sections[current] = "\n".join(lines).strip()
    return sections


def write_review_file(slug: str, sections: dict):
    """Write sections to review file in Gold Coast format order."""
    path = REVIEWS_DIR / f"{slug}.txt"
    order = [
        "LOCATION", "HEADING", "HERO INTRO", "WHY FAMILIES LOVE",
        "LOCAL KNOWLEDGE", "PARK CARDS", "TAGS", "PHOTOS",
        "ADDRESSES", "COORDS", "WEBSITES", "PRICES", "ACTIVITIES", "FAQ",
    ]
    written = set()
    lines = []
    for key in order:
        if key in sections and sections[key].strip():
            lines.append(f"{key}:")
            lines.append(sections[key])
            lines.append("")
            written.add(key)
    for key, val in sections.items():
        if key not in written and val.strip():
            lines.append(f"{key}:")
            lines.append(val)
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  [saved] reviews/{slug}.txt")


# ── Claude calls ──────────────────────────────────────────────────────────────

def call_claude(prompt: str) -> str:
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text.strip()


def generate_heading(location: str, state: str) -> str:
    return call_claude(
        f"Write an SEO page heading for a family holiday park comparison page. "
        f"Location: {location}, {state}, Australia. "
        f"Format: 'Best Family Holiday Parks in/on the {location}' or natural variation. "
        f"Return ONLY the heading text. No quotes. No explanation."
    )


def generate_hero_intro(location: str, state: str, parks: list) -> str:
    names = ", ".join(p.get("park_name", "") for p in parks[:5] if p.get("park_name"))
    return call_claude(
        f"Write 2 short paragraphs introducing {location}, {state} Australia as a family holiday destination. "
        f"Focus on: beaches, nature, key attractions, what makes it great for families with children. "
        f"Parks in this area include: {names}. "
        f"Tone: warm, practical, Australian. No marketing fluff. No emojis. "
        f"Return ONLY the two paragraphs."
    )


def generate_why_families(location: str, state: str) -> str:
    result = call_claude(
        f"List 5 specific reasons why families love {location}, {state} Australia for a holiday. "
        f"Each should be a short punchy phrase (5-10 words), specific to this location. "
        f"Return ONLY 5 lines, each starting with '- '. No numbering."
    )
    lines = [l.strip().lstrip("- ").strip() for l in result.splitlines() if l.strip()]
    return "\n".join(f"- {l}" for l in lines[:5])


def generate_local_knowledge(location: str, state: str) -> str:
    return call_claude(
        f"Write 2-3 paragraphs of practical local knowledge for families visiting {location}, {state} Australia. "
        f"Cover: best times to visit, local tips, what families love, what to watch out for, insider advice. "
        f"Tone: direct, useful, Australian. Written like a local parent who knows the area well. "
        f"Return ONLY the paragraphs."
    )


def generate_park_cards(parks: list, location: str) -> str:
    lines = []
    for park in parks:
        name = park.get("park_name", "")
        if not name:
            continue
        best_for = park.get("best_for", "").strip()
        if not best_for:
            best_for = call_claude(
                f"Write a single sentence starting with 'Best for families wanting...' "
                f"describing {name} in {location} Australia as a family holiday park. "
                f"Maximum 20 words. Return ONLY the sentence."
            )
            time.sleep(0.3)
        lines.append(f"{name} | {best_for}")
    return "\n".join(lines)


def generate_tags(parks: list, location: str) -> str:
    lines = []
    TAG_OPTIONS = [
        "Beach Access", "Heated Pool", "Pool", "Waterpark", "Splash Pad",
        "Creek Access", "River Access", "Jumping Pillow", "Playground",
        "Bike Hire", "Games Room", "Skate Park", "Nature Setting",
        "Waterfront Location", "Surf Beach", "Family Camping", "Resort Style",
        "Town Location", "Quiet Location", "Pet Friendly", "Big Rig Friendly",
        "Walk Everywhere", "Headland Walks", "Wildlife Spotting"
    ]
    for park in parks:
        name = park.get("park_name", "")
        if not name:
            continue
        existing = park.get("top_scoring_criteria") or []
        if isinstance(existing, list) and existing:
            tags = [t.strip() for t in existing[:4]]
        else:
            result = call_claude(
                f"Choose 4 tags for {name} in {location} Australia from this list: {', '.join(TAG_OPTIONS)}. "
                f"Return ONLY 4 tags separated by commas. No explanation."
            )
            tags = [t.strip() for t in result.split(",")][:4]
            time.sleep(0.3)
        lines.append(f"{name} | {', '.join(tags)}")
    return "\n".join(lines)


def generate_photos(parks: list, photos_data: dict) -> str:
    lines = []
    available = 0
    for park in parks:
        name = park.get("park_name", "")
        if not name:
            continue
        url = (
            park.get("photo_url_override")
            or park.get("photo_url_cached")
            or photos_data.get(name, "")
        )
        if url and str(url).startswith("http"):
            lines.append(f"{name} | {url}")
            available += 1
        else:
            lines.append(f"{name} | ")
    print(f"  [photos] {available}/{len(parks)} available")
    return "\n".join(lines)


def generate_addresses(parks: list) -> str:
    lines = []
    for park in parks:
        name = park.get("park_name", "")
        addr = park.get("address", "")
        if name:
            lines.append(f"{name} | {addr}")
    return "\n".join(lines)


def generate_coords(parks: list) -> str:
    lines = []
    for park in parks:
        name = park.get("park_name", "")
        lat = park.get("lat", "")
        lng = park.get("lng", "")
        if name and lat and lng:
            lines.append(f"{name} | {lat}, {lng}")
    return "\n".join(lines)


def generate_websites(parks: list, websites_data: dict) -> str:
    lines = []
    for park in parks:
        name = park.get("park_name", "")
        url = park.get("website") or websites_data.get(name, "")
        if name:
            lines.append(f"{name} | {url or ''}")
    return "\n".join(lines)


def generate_prices(parks: list, prices_data: dict) -> str:
    lines = []
    available = 0
    for park in parks:
        name = park.get("park_name", "")
        pw = (
            park.get("powered_weekday")
            or (park.get("prices") or {}).get("powered_weekday")
            or prices_data.get(name, "")
        )
        if name:
            if pw and pw != "—":
                lines.append(f"{name} | ${pw}/night" if not str(pw).startswith("$") else f"{name} | {pw}")
                available += 1
            else:
                lines.append(f"{name} | — | Price unavailable online")
    print(f"  [prices] {available}/{len(parks)} available")
    return "\n".join(lines)


def generate_faq(location: str, state: str, parks: list) -> str:
    names = ", ".join(p.get("park_name", "") for p in parks[:4] if p.get("park_name"))
    result = call_claude(
        f"""Write 10 SEO-focused FAQ questions and answers for a family holiday park page about {location}, {state} Australia.
Parks include: {names}

Target these question types:
- best family holiday parks in {location}
- best caravan park in {location} for kids
- holiday parks with pools near {location}
- holiday parks near beach in {location}
- powered sites in {location}
- cabins in {location}
- pet friendly holiday parks {location}
- best value holiday park {location}
- best area to stay in {location} for families
- things to do with kids in {location}
- rainy day activities {location}
- best time to visit {location} with kids

Answer rules:
- 40-90 words each
- specific to {location}, not generic
- practical, written for parents choosing where to stay
- no marketing fluff

Format exactly:
Q: question
A: answer

Return ONLY the 10 Q&A pairs."""
    )
    print(f"  [faq] {len(re.findall(r'^Q:', result, re.MULTILINE))} generated")
    return result


def generate_activities(location: str, state: str, parks: list) -> str:
    park_names = [p.get("park_name", "") for p in parks[:4] if p.get("park_name")]
    parks_str = ", ".join(park_names)
    result = call_claude(
        f"""List 10 family-friendly activities near {location}, {state} Australia.

Parks in this area: {parks_str}

For each activity use this exact pipe-separated format:
Activity Name | One sentence description written for families. | Category | Distance note referencing nearest park where relevant | | Badge

Distance note examples:
- 2 mins from BIG4 Apollo Bay
- In town, walkable from most parks
- 25km from Apollo Bay — worth the drive
- At Tallebudgera Creek Tourist Park

Category must be one of: Theme Park, Water Activity, Nature, Wildlife, Free, Rainy Day, Beach, Cultural
Badge must be one of: Must Do, Family Favourite, Free, Nature, Rainy Day, Wildlife, Water Fun
Leave Badge blank if not a standout.
Leave photo field (5th column) blank — do not invent URLs.

Return ONLY the 10 pipe-separated lines. No numbering, no headers."""
    )
    lines = [l.strip() for l in result.splitlines() if "|" in l and l.strip()]
    formatted = []
    for line in lines[:10]:
        parts = [p.strip() for p in line.split("|")]
        while len(parts) < 6:
            parts.append("")
        parts[4] = ""
        formatted.append(" | ".join(parts[:6]))
    print(f"  [activities] {len(formatted)} generated")
    return "\n".join(formatted)


# ── Main enrichment ───────────────────────────────────────────────────────────

def enrich_location(
    slug: str,
    location: str,
    state: str,
    publish: bool = False,
    force: bool = False,
    missing_only: bool = False,
):
    print(f"\n{'='*58}")
    print(f"  {location}, {state}  ({slug})")
    print(f"{'='*58}")

    if slug in PROTECTED_SLUGS:
        print(f"  [protected] Skipping {slug}")
        return

    review_path = REVIEWS_DIR / f"{slug}.txt"
    review_exists = review_path.exists()

    if missing_only and review_exists and not force:
        print(f"  [skip] Review file exists (use --force to overwrite)")
        return

    if review_exists and not force:
        print(f"  [skip] Review file exists (use --force to overwrite)")
        return

    # Load data
    parks = load_approved_parks(slug, state)
    if not parks:
        print(f"  [skip] No park data found")
        return

    photos_data = load_supplementary(slug, state, "photos.json")
    prices_data = load_supplementary(slug, state, "prices.json")
    websites_data = load_supplementary(slug, state, "websites.json")

    existing = read_review_file(slug)
    sections = dict(existing)

    # Always set LOCATION
    sections["LOCATION"] = f"{location} {state}"

    # Generate all sections
    print(f"  [generating] Heading...")
    sections["HEADING"] = generate_heading(location, state)

    print(f"  [generating] Hero intro...")
    sections["HERO INTRO"] = generate_hero_intro(location, state, parks)

    print(f"  [generating] Why families love...")
    sections["WHY FAMILIES LOVE"] = generate_why_families(location, state)

    print(f"  [generating] Local knowledge...")
    sections["LOCAL KNOWLEDGE"] = generate_local_knowledge(location, state)

    print(f"  [generating] Park cards...")
    sections["PARK CARDS"] = generate_park_cards(parks, location)

    print(f"  [generating] Tags...")
    sections["TAGS"] = generate_tags(parks, location)

    print(f"  [generating] Photos...")
    sections["PHOTOS"] = generate_photos(parks, photos_data)

    print(f"  [generating] Addresses...")
    sections["ADDRESSES"] = generate_addresses(parks)

    print(f"  [generating] Coords...")
    sections["COORDS"] = generate_coords(parks)

    print(f"  [generating] Websites...")
    sections["WEBSITES"] = generate_websites(parks, websites_data)

    print(f"  [generating] Prices...")
    sections["PRICES"] = generate_prices(parks, prices_data)

    print(f"  [generating] FAQ...")
    sections["FAQ"] = generate_faq(location, state, parks)

    print(f"  [generating] Activities...")
    sections["ACTIVITIES"] = generate_activities(location, state, parks)

    # Save
    action = "updated" if review_exists else "created"
    write_review_file(slug, sections)
    print(f"  [{action}] reviews/{slug}.txt")

    # Build page
    if publish:
        print(f"  [build] Running update_location.py...")
        cmd = ["python", "update_location.py", slug, "--publish"]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_DIR))
        if result.returncode == 0:
            print(f"  [build] Success")
        else:
            print(f"  [build] Failed")
            if result.stderr:
                print(result.stderr[-300:])


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    publish = "--publish" in args
    force = "--force" in args
    missing_only = "--missing-only" in args

    slug_filter = next(
        (args[i+1] for i, a in enumerate(args) if a == "--slug" and i+1 < len(args)), None
    )
    state_filter = next(
        (args[i+1].upper() for i, a in enumerate(args) if a == "--state" and i+1 < len(args)), None
    )

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[error] ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)

    with open(LOCATIONS_CSV, newline="", encoding="utf-8") as f:
        locations = list(csv.DictReader(f))

    if slug_filter:
        locations = [l for l in locations if l["slug"] == slug_filter]
    if state_filter:
        locations = [l for l in locations if l["state"].upper() == state_filter]

    print(f"\n[enrich_locations] Processing {len(locations)} locations")
    print(f"  publish={publish}  force={force}  missing_only={missing_only}\n")

    for loc in locations:
        try:
            enrich_location(
                slug=loc["slug"],
                location=loc["location"],
                state=loc["state"],
                publish=publish,
                force=force,
                missing_only=missing_only,
            )
            time.sleep(1)
        except Exception as e:
            print(f"  [error] {loc['slug']}: {e}")
            continue

    print(f"\n[enrich_locations] Complete.")


if __name__ == "__main__":
    main()
