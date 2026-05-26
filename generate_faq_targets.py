#!/usr/bin/env python3
"""
Generates faq_targets.json for each location using Apify Keyword Discovery actor.
Usage: python generate_faq_targets.py
       python generate_faq_targets.py --file tonight.txt
       python generate_faq_targets.py "Noosa QLD" "Byron Bay NSW"
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))
from score_parks import get_location_dir, log

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "").strip()

FAMILY_KEYWORDS = [
    "kids", "family", "families", "toddler", "toddlers", "children",
    "holiday park", "caravan park", "rainy day", "things to do",
    "school holidays", "where to stay", "dog friendly", "pool",
    "playground", "kid friendly", "kidfriendly", "baby", "babies",
    "teenager", "teenagers", "camping", "cabin", "powered site",
    "pet friendly", "wifi", "waterpark", "water park", "jumping pillow",
    "best park", "best holiday", "best caravan",
    "school holiday", "school holidays", "holiday activities", "holiday programs",
    "2026",
]

JUNK_TERMS = [
    "instagram", "tiktok", "twitter", "facebook", "reddit", "youtube",
    "near me", "jobs", "for sale", "buy", "sell", "cheap flights",
    "cheap hotels", "airbnb", "real estate", "property",
    "restaurant", "restaurants", "dining", "dinner", "lunch", "breakfast",
    "cafe", "coffee", "food", "eat free", "triathlon", "tri", "shop",
    "store", "clothes", "clothing", "dental", "chiro", "chiropractor",
    "medical", "law", "legal", "practice", "practise", "centre", "center", "clinic", "physio",
    "yogurt", "yoghurt", "temperature", "weather", "warm in", "asics",
    "shoes", "civic", "fair", "september", "easter", "2025", "dates",
    "programs", "schedule",
]

def is_family_travel_intent(phrase: str) -> bool:
    phrase_lower = phrase.lower()
    if any(junk in phrase_lower for junk in JUNK_TERMS):
        return False
    return any(kw in phrase_lower for kw in FAMILY_KEYWORDS)

def get_seed_queries(location_name: str) -> list[str]:
    bare = re.sub(r'\s+(QLD|NSW|VIC|SA|WA|TAS|NT|ACT)$', '', location_name, flags=re.IGNORECASE).strip()
    return [
        f"{bare} kids",
        f"{bare} with kids",
        f"{bare} family",
        f"{bare} toddler",
        f"best holiday park {bare}",
        f"{bare} caravan park kids",
        f"{bare} rainy day kids",
        f"things to do {bare} with kids",
        f"where to stay {bare} family",
        f"{bare} school holidays",
    ]

def run_keyword_discovery(seeds: list[str]) -> dict:
    import urllib.request
    import urllib.error

    actor_id = "accurate_pouch~keyword-discovery"
    url = f"https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items?token={APIFY_TOKEN}&timeout=120"

    payload = json.dumps({
        "keywords": seeds,
        "expandAlphabet": False,
        "dryRun": False
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log(f"[faq] Apify error: {e}")
        return []

def classify_phrases(phrases: list[str]) -> dict:
    high = []
    medium = []
    long_tail = []

    seen = set()

    ACCOMMODATION_TERMS = [
        "holiday park", "caravan park", "accommodation", "where to stay",
        "best park", "stay", "camping", "cabin"
    ]

    ACTIVITY_TERMS = [
        "things to do", "what to do", "activities", "rainy day",
        "school holiday", "school holidays", "things to see", "weekend",
        "free", "walks", "walk", "hike", "swim", "beach"
    ]

    HIGH_PRIORITY_QUESTIONS = [
        "is ", "are ", "what is", "where to", "where should",
        "best ", "which ", "how "
    ]

    for phrase in phrases:
        phrase = phrase.strip()
        phrase_lower = phrase.lower()

        if phrase_lower in seen:
            continue
        seen.add(phrase_lower)

        if not is_family_travel_intent(phrase):
            continue

        word_count = len(phrase.split())

        if any(phrase_lower.startswith(q) for q in HIGH_PRIORITY_QUESTIONS):
            if any(t in phrase_lower for t in ACCOMMODATION_TERMS + ["family", "kids", "children"]):
                high.append(phrase)
                continue

        # High priority — accommodation + family intent
        if any(t in phrase_lower for t in ACCOMMODATION_TERMS):
            high.append(phrase)
        # Long tail — 6+ words with activity intent
        elif word_count >= 6 and any(t in phrase_lower for t in ACTIVITY_TERMS):
            long_tail.append(phrase)
        # Medium — everything else that passes family filter
        elif word_count <= 6:
            medium.append(phrase)
        else:
            long_tail.append(phrase)

    return {
        "high_priority": list(dict.fromkeys(high))[:10],
        "medium_priority": list(dict.fromkeys(medium))[:12],
        "long_tail": list(dict.fromkeys(long_tail))[:15]
    }

def generate_faq_targets(location: str, project_dir: Path) -> None:
    log(f"\n{'='*50}")
    log(f"FAQ TARGETS: {location}")

    loc_dir = get_location_dir(project_dir, location)
    faq_targets_path = loc_dir / "faq_targets.json"

    if faq_targets_path.exists():
        log(f"Already exists — skipping. Delete faq_targets.json to redo.")
        return

    seeds = get_seed_queries(location)
    log(f"Running {len(seeds)} seed queries via Apify...")

    results = run_keyword_discovery(seeds)

    # Extract all phrases
    all_phrases = []
    for item in results:
        all_phrases.extend(item.get("autocomplete", []))
        all_phrases.extend(item.get("peopleAlsoAsk", []))
        all_phrases.extend(item.get("relatedSearches", []))

    # Save raw keywords
    raw_path = loc_dir / "faq_raw_keywords.json"
    raw_path.write_text(json.dumps(all_phrases, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"Saved {len(all_phrases)} raw keywords.")

    # Classify
    classified = classify_phrases(all_phrases)
    total = sum(len(v) for v in classified.values())
    log(f"Classified: {len(classified['high_priority'])} high, {len(classified['medium_priority'])} medium, {len(classified['long_tail'])} long-tail")

    faq_targets_path.write_text(json.dumps(classified, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"Saved faq_targets.json")

    # Rate limit
    time.sleep(3)

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("locations", nargs="*")
    parser.add_argument("--file", help="Text file with one location per line")
    args = parser.parse_args()

    if not APIFY_TOKEN:
        print("ERROR: APIFY_TOKEN not set")
        return

    project_dir = Path(__file__).resolve().parent

    locations = list(args.locations)
    if args.file:
        file_path = Path(args.file)
        if file_path.exists():
            locations += [l.strip() for l in file_path.read_text().splitlines() if l.strip()]

    if not locations:
        import csv
        csv_path = project_dir / "locations.csv"
        if csv_path.exists():
            with open(csv_path, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    loc_name = row["location"].strip()
                    state = row["state"].strip()
                    location_str = f"{loc_name} {state}"
                    loc_dir = get_location_dir(project_dir, location_str)
                    approved_path = loc_dir / "approved-parks.json"
                    if approved_path.exists():
                        locations.append(location_str)

    for location in locations:
        generate_faq_targets(location, project_dir)

    print("\n✅ FAQ targets complete. Run: python build_all.py to regenerate pages.")

if __name__ == "__main__":
    main()
