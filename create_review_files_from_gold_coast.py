#!/usr/bin/env python3
"""
Phase 3 — Review-file enrichment and QA system.

Creates or updates structured review .txt files using gold-coast-qld.txt as the
format standard. Runs photo, price, maps, FAQ and activity QA before assembly.

Usage:
  python create_review_files_from_gold_coast.py
  python create_review_files_from_gold_coast.py --slug byron-bay-nsw
  python create_review_files_from_gold_coast.py --state QLD
  python create_review_files_from_gold_coast.py --force
  python create_review_files_from_gold_coast.py --publish
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

from generate_page import (  # noqa: E402
    get_google_maps_url,
    google_text_search_place_id,
)

REVIEWS_DIR = PROJECT_DIR / "reviews"
MASTER_REVIEW = REVIEWS_DIR / "gold-coast-qld.txt"
LOCATIONS_CSV = PROJECT_DIR / "locations.csv"
PARKS_DIR = PROJECT_DIR / "parks"
SKIP_REVIEW_SLUG = "gold-coast-qld"
CLAUDE_MODEL = "claude-sonnet-4-5"

STATE_MAP = {
    "QLD": "qld",
    "NSW": "nsw",
    "VIC": "vic",
    "SA": "sa",
    "WA": "wa",
    "TAS": "tas",
    "NT": "nt",
    "ACT": "act",
}

BANNED_WORDS = [
    "nestled",
    "boasts",
    "world-class",
    "unforgettable",
    "ultimate destination",
    "perfect for everyone",
    "hidden gem",
    "breathtaking",
]

ACTIVITY_BADGES = [
    "Must Do",
    "Family Favourite",
    "Free",
    "Nature",
    "Rainy Day",
    "Wildlife",
    "Water Fun",
]

SEO_FAQ_TOPICS = [
    "best family holiday parks in {location}",
    "best caravan park in {location} for kids",
    "holiday parks in {location} with pools",
    "holiday parks near the beach in {location}",
    "powered sites at holiday parks in {location}",
    "family cabins at holiday parks in {location}",
    "pet friendly holiday parks in {location}",
    "best value holiday parks in {location}",
    "best area to stay in {location} with kids",
    "things to do with kids in {location}",
    "rainy day activities in {location} for families",
    "how far in advance to book a holiday park in {location}",
]

STABLE_PHOTO_HOSTS = (
    "lh3.googleusercontent.com",
    "lh4.googleusercontent.com",
    "lh5.googleusercontent.com",
    "lh6.googleusercontent.com",
)

KIDS_CATEGORIES = [
    "Playground",
    "Waterpark",
    "Jumping Pillow",
    "Beach Play",
    "Nature Play",
    "Games Room",
    "Kids Club",
    "Bike Hire",
    "Skate Park",
    "Pool",
    "None Known",
]

WATER_CATEGORIES = [
    "Waterpark",
    "Pool",
    "Heated Pool",
    "Splash Pad",
    "Beach Access",
    "Creek Access",
    "River Access",
    "None Known",
]

KIDS_KEYWORD_MAP = [
    ("Waterpark", ("waterpark", "water park")),
    ("Jumping Pillow", ("jumping pillow",)),
    ("Playground", ("playground",)),
    ("Skate Park", ("skate park", "skatepark")),
    ("Bike Hire", ("bike hire", "bike rental", "bikes for hire", "bike")),
    ("Kids Club", ("kids club", "kids' club")),
    ("Games Room", ("games room", "game room")),
    ("Beach Play", ("beach", "surf", "ocean")),
    ("Nature Play", ("nature", "bush", "wildlife", "open space", "national park")),
    ("Pool", ("pool",)),
]

WATER_KEYWORD_MAP = [
    ("Waterpark", ("waterpark", "water park")),
    ("Heated Pool", ("heated pool",)),
    ("Splash Pad", ("splash pad", "splash zone", "splash feature", "splash")),
    ("Pool", ("pool",)),
    ("Beach Access", ("beach", "ocean swim", "surf")),
    ("Creek Access", ("creek",)),
    ("River Access", ("river",)),
]

BOOKABLE_CATEGORY_KEYWORDS = (
    "holiday park",
    "caravan park",
    "campground",
    "rv park",
    "tourist park",
    "camping",
    "mobile home park",
)

TAG_FALLBACK_POOL = [
    "Beach Access",
    "Nature Setting",
    "Quiet Location",
    "Family Camping",
    "Town Location",
    "Pet Friendly",
    "Budget Friendly",
    "Walk Everywhere",
    "Family Friendly",
    "Resort Style",
]

EXTRA_FAQ_TOPICS = [
    "best beach for young children in {location}",
    "free family activities in {location}",
    "best time to visit {location} with kids",
]


@dataclass
class ParkRecord:
    park_name: str
    total_score: Any = None
    classification: str = ""
    google_rating: Any = None
    review_count: Any = None
    best_for: str = ""
    top_scoring_criteria: list[str] = field(default_factory=list)
    water_fun: str = ""
    kids_play: str = ""
    pet_detail: str = ""
    pet_friendly: str = ""
    address: str = ""
    website: str = ""
    photo_url: str = ""
    photo_approved: bool = False
    powered_price: str = ""
    price_note: str = ""
    price_found: bool = False
    place_id: str = ""
    maps_url: str = ""
    maps_valid: bool = False
    park_lat: Any = None
    park_lng: Any = None
    nearest_beach: dict[str, Any] = field(default_factory=dict)
    nearest_supermarket: dict[str, Any] = field(default_factory=dict)
    rationale_top3: str = ""
    from_approved: bool = False
    non_park: bool = False
    kids_categories: str = ""
    water_categories: str = ""
    approved_item: dict[str, Any] = field(default_factory=dict)


@dataclass
class LocationStats:
    review_slug: str
    created: bool = False
    updated: bool = False
    photos_approved: int = 0
    photos_total: int = 0
    activities_count: int = 0
    faq_count: int = 0
    prices_found: int = 0
    prices_total: int = 0
    maps_valid: int = 0
    maps_total: int = 0
    build_ok: bool = False


def slugify(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9\s-]", "", name)
    name = re.sub(r"[\s]+", "-", name)
    return re.sub(r"-+", "-", name).strip("-")


def review_slug_for_row(row: dict[str, str]) -> str:
    return f"{row['slug'].strip()}-{row['state'].strip().lower()}"


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def read_locations(
    *,
    slug_filter: str | None = None,
    state_filter: str | None = None,
) -> list[dict[str, str]]:
    if not LOCATIONS_CSV.exists():
        print("ERROR: locations.csv not found.")
        sys.exit(1)

    rows: list[dict[str, str]] = []
    with open(LOCATIONS_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            review_slug = review_slug_for_row(row)
            if review_slug == SKIP_REVIEW_SLUG:
                continue
            if slug_filter and review_slug != slug_filter:
                continue
            if state_filter and row.get("state", "").strip().upper() != state_filter.upper():
                continue
            rows.append(row)
    return rows


def normalize_photo_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    url = re.sub(r"w\d+-h\d+", "w800-h600", url)
    return url


def is_stable_photo_url(url: str) -> bool:
    url = (url or "").strip()
    if not url:
        return False
    if "maps.googleapis.com/maps/api/place/photo" in url:
        return False
    if "photo_reference=" in url:
        return False
    return any(host in url for host in STABLE_PHOTO_HOSTS)


def fetch_image_base64(url: str, max_bytes: int = 800_000) -> str | None:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "FamilyHolidayParks/1.0"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                return None
            media = resp.headers.get_content_type() or "image/jpeg"
            if not media.startswith("image/"):
                media = "image/jpeg"
            return base64.standard_b64encode(data).decode("ascii")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def load_park_master(park_name: str) -> dict[str, Any]:
    master_path = PARKS_DIR / slugify(park_name) / "master.json"
    data = load_json_file(master_path, {})
    return data if isinstance(data, dict) else {}


def _combined_park_text(park: ParkRecord) -> str:
    parts = [
        park.kids_play,
        park.water_fun,
        park.rationale_top3,
        " ".join(park.top_scoring_criteria),
    ]
    return " ".join(str(p).lower() for p in parts if p)


def _keyword_in_text(keyword: str, text: str) -> bool:
    if keyword not in text:
        return False
    for match in re.finditer(re.escape(keyword), text):
        window = text[max(0, match.start() - 20):match.start()]
        if re.search(r"\b(no|without|not)\s+[\w\s]{0,12}$", window):
            continue
        return True
    return False


def _match_categories(text: str, keyword_map: list[tuple[str, tuple[str, ...]]], allowed: list[str]) -> list[str]:
    found: list[str] = []
    for category, keywords in keyword_map:
        if category not in allowed:
            continue
        if any(_keyword_in_text(kw, text) for kw in keywords):
            if category not in found:
                found.append(category)
        if len(found) >= 3:
            break
    return found[:3]


def _indicates_none_known(text: str, none_for: str) -> bool:
    patterns = (
        f"no {none_for}",
        f"no dedicated {none_for}",
        f"no on-site {none_for}",
        "not mentioned",
        "absence of",
        "without",
        "none known",
    )
    return any(p in text for p in patterns)


def categorize_kids_play(park: ParkRecord) -> str:
    text = f"{park.kids_play} {' '.join(park.top_scoring_criteria)}".lower()
    categories = _match_categories(text, KIDS_KEYWORD_MAP, KIDS_CATEGORIES)
    if "Pool" in categories:
        categories = [c for c in categories if c != "Pool"]
    if not categories and _indicates_none_known(text, "playground"):
        if _keyword_in_text("beach", text):
            categories = ["Beach Play"]
        elif any(w in text for w in ("nature", "bush", "wildlife")):
            categories = ["Nature Play"]
        else:
            categories = ["None Known"]
    elif not categories and _keyword_in_text("beach", text):
        categories = ["Beach Play"]
    return ", ".join(categories[:3]) if categories else "None Known"


def categorize_water_fun(park: ParkRecord) -> str:
    text = f"{park.water_fun} {' '.join(park.top_scoring_criteria)}".lower()
    categories = _match_categories(text, WATER_KEYWORD_MAP, WATER_CATEGORIES)
    if "Heated Pool" in categories and "Pool" in categories:
        categories = [c for c in categories if c != "Pool"]
    if not categories and _indicates_none_known(text, "pool"):
        if _keyword_in_text("beach", text):
            categories = ["Beach Access"]
        else:
            categories = ["None Known"]
    elif not categories and _keyword_in_text("beach", text):
        categories = ["Beach Access"]
    return ", ".join(categories[:3]) if categories else "None Known"


def dedupe_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        cleaned = tag.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def enrich_tags(tags: list[str], park: ParkRecord) -> list[str]:
    tags = dedupe_tags(tags)
    if len(tags) >= 4:
        return tags[:4]

    text = _combined_park_text(park)
    contextual: list[str] = []
    if "beach" in text or any("beach" in t.lower() for t in tags):
        contextual.append("Beach Access")
    if any(w in text for w in ("quiet", "peaceful")) or any("quiet" in t.lower() for t in tags):
        contextual.append("Quiet Location")
    if any(w in text for w in ("nature", "bush", "rainforest")) or any("nature" in t.lower() for t in tags):
        contextual.append("Nature Setting")
    if str(park.pet_friendly).lower() == "yes" or any("pet" in t.lower() for t in tags):
        contextual.append("Pet Friendly")
    if any(w in text for w in ("town", "walk everywhere", "central")):
        contextual.append("Town Location")
    if "budget" in text:
        contextual.append("Budget Friendly")

    for candidate in contextual + TAG_FALLBACK_POOL:
        if len(tags) >= 4:
            break
        if candidate.lower() not in {t.lower() for t in tags}:
            tags.append(candidate)
    return tags[:4]


def has_accommodation_category(item: dict[str, Any] | None) -> bool:
    if not item:
        return False
    names = [str(item.get("categoryName") or "").lower()]
    names.extend(str(c).lower() for c in (item.get("categories") or []))
    return any(
        any(kw in name for kw in BOOKABLE_CATEGORY_KEYWORDS)
        for name in names
        if name
    )


def mark_non_park_if_needed(park: ParkRecord) -> None:
    has_website = bool(park.website.strip())
    has_price = park.price_found
    has_accommodation = has_accommodation_category(park.approved_item)
    if not has_website and not has_price and not has_accommodation:
        park.non_park = True
        print(f"[non_park] {park.park_name}")


def finalize_comparison_data(park: ParkRecord) -> None:
    mark_non_park_if_needed(park)
    park.kids_categories = categorize_kids_play(park)
    park.water_categories = categorize_water_fun(park)
    park.top_scoring_criteria = enrich_tags(park.top_scoring_criteria, park)
    park.kids_play = park.kids_categories
    park.water_fun = park.water_categories


def bookable_parks(parks: list[ParkRecord]) -> list[ParkRecord]:
    return [p for p in parks if not p.non_park]


def load_park_master_price(park_name: str) -> tuple[str, str]:
    data = load_park_master(park_name)
    prices = data.get("prices") or {}
    powered = ""
    if isinstance(prices, dict):
        powered = str(prices.get("powered_weekday") or "").strip()
    deals = str(data.get("deals") or "").strip()
    return powered, deals


def load_approved_parks(loc_dir: Path) -> list[dict[str, Any]] | None:
    path = loc_dir / "approved-parks.json"
    if not path.exists():
        return None
    data = load_json_file(path, {})
    if isinstance(data, dict):
        parks = data.get("parks")
        if isinstance(parks, list):
            return parks
    if isinstance(data, list):
        return data
    return None


def index_scores_by_name(scores: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for park in scores:
        name = str(park.get("park_name") or "").strip()
        if name:
            indexed[name] = park
    return indexed


def merge_score_enrichment(record: ParkRecord, score: dict[str, Any] | None) -> None:
    """Merge family-scoring enrichment from scores.json without overwriting approved core fields."""
    master = load_park_master(record.park_name)
    src = score or {}
    if src.get("total_score") is not None:
        record.total_score = src.get("total_score")
    if src.get("classification"):
        record.classification = str(src.get("classification") or "")
    if not record.best_for:
        record.best_for = str(src.get("best_for") or master.get("best_for") or "").strip()

    tags = src.get("top_scoring_criteria") or master.get("top_scoring_criteria") or []
    if isinstance(tags, list):
        record.top_scoring_criteria = [str(t).strip() for t in tags if str(t).strip()]
    elif tags:
        record.top_scoring_criteria = [t.strip() for t in str(tags).split(",") if t.strip()]

    if not record.water_fun:
        record.water_fun = str(src.get("water_fun") or "")
    if not record.kids_play:
        record.kids_play = str(src.get("kids_play") or "")
    if not record.pet_detail:
        record.pet_detail = str(src.get("pet_detail") or "")
    if not record.pet_friendly:
        record.pet_friendly = str(src.get("pet_friendly") or "")
    if not record.rationale_top3:
        record.rationale_top3 = str(src.get("rationale_top3") or "")
    if not record.nearest_beach:
        record.nearest_beach = src.get("nearest_beach_cached") or {}
    if not record.nearest_supermarket:
        record.nearest_supermarket = src.get("nearest_supermarket_cached") or {}


def load_price_for_park(
    park_name: str,
    prices_data: dict[str, Any],
) -> tuple[str, str]:
    powered_price = ""
    price_note = ""
    from generate_page import _parse_price

    raw = prices_data.get(park_name)
    if isinstance(raw, dict):
        powered_price = _parse_price(raw).strip()
        price_note = str(raw.get("deals") or raw.get("note") or "").strip()
    elif isinstance(raw, str):
        powered_price = _parse_price(raw).strip()
    if not powered_price:
        powered_price, price_note = load_park_master_price(park_name)
    return powered_price, price_note


def build_record_from_approved(
    item: dict[str, Any],
    score: dict[str, Any] | None,
    prices_data: dict[str, Any],
) -> ParkRecord:
    name = str(item.get("title") or "").strip()
    location = item.get("location") if isinstance(item.get("location"), dict) else {}
    place_id = str(item.get("placeId") or "").strip().replace("places/", "")
    maps_url = str(item.get("url") or "").strip()

    powered_price, price_note = load_price_for_park(name, prices_data)

    record = ParkRecord(
        park_name=name,
        google_rating=item.get("totalScore"),
        review_count=item.get("reviewsCount"),
        address=str(item.get("address") or "").strip(),
        website=str(item.get("website") or "").strip(),
        photo_url=normalize_photo_url(str(item.get("imageUrl") or "")),
        place_id=place_id,
        maps_url=maps_url,
        park_lat=location.get("lat"),
        park_lng=location.get("lng"),
        powered_price=powered_price,
        price_note=price_note,
        from_approved=True,
        approved_item=item,
    )
    merge_score_enrichment(record, score)
    return record


def build_record_from_scores(
    park: dict[str, Any],
    photos_data: dict[str, Any],
    websites_data: dict[str, Any],
    prices_data: dict[str, Any],
) -> ParkRecord:
    name = str(park.get("park_name") or "").strip()
    master = load_park_master(name)
    candidates = collect_photo_candidates(park, photos_data, master)
    photo_url = ""
    for candidate in candidates:
        if is_stable_photo_url(candidate):
            photo_url = candidate
            break

    website = str(
        websites_data.get(name) or park.get("website") or master.get("website") or ""
    ).strip()
    address = str(park.get("address") or master.get("address") or "").strip()
    powered_price, price_note = load_price_for_park(name, prices_data)

    tags = park.get("top_scoring_criteria") or master.get("top_scoring_criteria") or []
    if isinstance(tags, list):
        tags = [str(t).strip() for t in tags if str(t).strip()]
    else:
        tags = [t.strip() for t in str(tags).split(",") if t.strip()]

    return ParkRecord(
        park_name=name,
        total_score=park.get("total_score"),
        classification=str(park.get("classification") or ""),
        google_rating=park.get("google_rating"),
        review_count=park.get("review_count"),
        best_for=str(park.get("best_for") or master.get("best_for") or "").strip(),
        top_scoring_criteria=tags,
        water_fun=str(park.get("water_fun") or ""),
        kids_play=str(park.get("kids_play") or ""),
        pet_detail=str(park.get("pet_detail") or ""),
        pet_friendly=str(park.get("pet_friendly") or ""),
        address=address,
        website=website,
        photo_url=photo_url,
        powered_price=powered_price,
        price_note=price_note,
        nearest_beach=park.get("nearest_beach_cached") or {},
        nearest_supermarket=park.get("nearest_supermarket_cached") or {},
        rationale_top3=str(park.get("rationale_top3") or ""),
        from_approved=False,
    )


def collect_photo_candidates(
    park: dict[str, Any],
    photos_data: dict[str, Any],
    master: dict[str, Any],
) -> list[str]:
    candidates: list[str] = []
    for source in (
        photos_data.get(park.get("park_name", "")),
        park.get("photo_url_override"),
        park.get("photo_url_cached"),
        master.get("photo_url_override"),
        master.get("photo_url_cached"),
    ):
        url = normalize_photo_url(str(source or ""))
        if url and url not in candidates:
            candidates.append(url)
    return candidates


def qa_photos_batch(api_key: str, parks: list[ParkRecord]) -> None:
    to_check = [p for p in parks if p.photo_url and is_stable_photo_url(p.photo_url)]
    if not to_check:
        return

    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Review each holiday park photo. Approve only family-relevant visuals: "
                "pools, playgrounds, beaches, waterparks, jumping pillows, cabins/sites, "
                "park grounds with family feel, wildlife/attraction action shots.\n"
                "Reject: signs only, reception desks, blurry images, maps, logos, food photos, "
                "empty roads, amenity blocks, car parks with no context.\n"
                "Reply with JSON only: {\"Park Name\": true/false, ...}"
            ),
        }
    ]

    included: list[ParkRecord] = []
    for park in to_check:
        b64 = fetch_image_base64(park.photo_url)
        if not b64:
            continue
        included.append(park)
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": b64,
                },
            }
        )
        content.append({"type": "text", "text": f"Park: {park.park_name}"})

    if not included:
        return

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": content}],
        )
        text = "".join(
            block.text for block in message.content if getattr(block, "type", None) == "text"
        )
        parsed = parse_json_from_text(text)
        if isinstance(parsed, dict):
            for park in included:
                approved = parsed.get(park.park_name)
                if approved is True:
                    park.photo_approved = True
                elif approved is False:
                    park.photo_approved = False
                    print(f"[photo rejected] {park.park_name}")
    except Exception as exc:
        print(f"[warn] photo vision QA failed, using stable-url fallback: {exc}")
        for park in to_check:
            park.photo_approved = True


def resolve_place_id(
    google_key: str,
    park_name: str,
    address: str,
) -> str:
    for key in ("place_id", "placeId", "google_place_id", "_apify_place_id"):
        master = load_park_master(park_name)
        pid = str(master.get(key) or "").strip()
        if pid:
            return pid.replace("places/", "")

    if not google_key:
        return ""

    query = f"{park_name} {address}".strip()
    place_id, _snippet = google_text_search_place_id(google_key, query)
    return place_id or ""


def build_and_validate_maps_url(
    park: ParkRecord,
    google_key: str,
) -> None:
    if park.maps_url and "google.com/maps" in park.maps_url:
        park.maps_valid = is_valid_maps_url(park.maps_url, park.park_name, park.place_id)
        return

    if not park.place_id and google_key:
        park.place_id = resolve_place_id(google_key, park.park_name, park.address)

    row = {
        "name": park.park_name,
        "park_name": park.park_name,
        "address": park.address,
        "place_id": park.place_id,
        "_apify_place_id": park.place_id,
        "maps_url": park.maps_url,
    }
    park.maps_url = get_google_maps_url(row)
    park.maps_valid = is_valid_maps_url(park.maps_url, park.park_name, park.place_id)


def is_valid_maps_url(url: str, park_name: str, place_id: str = "") -> bool:
    if not url or "google.com/maps" not in url:
        return False
    if place_id and "query_place_id=" in url:
        return bool(park_name.strip())
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    query = (qs.get("query") or [""])[0].strip().lower()
    if not query:
        return False
    name_tokens = [w.lower() for w in re.findall(r"[a-zA-Z]{4,}", park_name)]
    if not name_tokens:
        return False
    return any(token in query for token in name_tokens[:3])


def qa_price(park: ParkRecord) -> None:
    powered = (park.powered_price or "").strip()
    if powered and powered not in ("—", "-", "N/A", "n/a"):
        if not powered.startswith("$"):
            powered = f"${powered}" if re.search(r"\d", powered) else powered
        if "/night" not in powered.lower() and re.search(r"\d", powered):
            powered = f"{powered}/night"
        park.powered_price = powered
        park.price_found = True
        print(f"[price found] {park.park_name} {powered}")
    else:
        park.powered_price = "—"
        park.price_note = "Price unavailable online"
        park.price_found = False
        print(f"[price missing] {park.park_name}")


def run_park_qa(parks: list[ParkRecord], *, api_key: str, google_key: str) -> None:
    qa_photos_batch(api_key, parks)

    for park in parks:
        if not park.photo_url:
            park.photo_approved = False
            print(f"[photo missing] {park.park_name}")
        elif park.photo_approved:
            pass
        elif is_stable_photo_url(park.photo_url):
            park.photo_approved = True
        elif park.from_approved:
            park.photo_approved = False
            print(f"[photo quality] {park.park_name} (approved photo kept)")
        else:
            park.photo_approved = False
            print(f"[photo missing] {park.park_name}")

        qa_price(park)
        build_and_validate_maps_url(park, google_key)
        if park.maps_valid:
            pass
        elif park.maps_url:
            print(f"[maps invalid] {park.park_name}")
        else:
            print(f"[maps missing] {park.park_name}")


def build_park_records(
    loc_dir: Path,
    scores: list[dict[str, Any]],
    *,
    api_key: str,
    google_key: str,
) -> list[ParkRecord]:
    prices_data = load_json_file(loc_dir / "prices.json", {})
    if not isinstance(prices_data, dict):
        prices_data = {}

    approved_list = load_approved_parks(loc_dir)
    scores_by_name = index_scores_by_name(scores)

    if approved_list is not None:
        print(f"[approved parks] Loaded {len(approved_list)} from approved-parks.json")
        print("[approved parks] Using approved list as source of truth")

        approved_names = {str(p.get("title") or "").strip() for p in approved_list if p.get("title")}
        for name in scores_by_name:
            if name not in approved_names:
                print(f"[approved parks] Skipped unapproved park: {name}")

        parks: list[ParkRecord] = []
        for item in approved_list:
            name = str(item.get("title") or "").strip()
            if not name:
                continue
            parks.append(
                build_record_from_approved(item, scores_by_name.get(name), prices_data)
            )

        run_park_qa(parks, api_key=api_key, google_key=google_key)
        for park in parks:
            finalize_comparison_data(park)
        return parks

    photos_data = load_json_file(loc_dir / "photos.json", {})
    websites_data = load_json_file(loc_dir / "websites.json", {})
    if not isinstance(photos_data, dict):
        photos_data = {}
    if not isinstance(websites_data, dict):
        websites_data = {}

    parks = []
    for park in scores:
        name = str(park.get("park_name") or "").strip()
        if not name:
            continue
        parks.append(
            build_record_from_scores(park, photos_data, websites_data, prices_data)
        )

    run_park_qa(parks, api_key=api_key, google_key=google_key)
    for park in parks:
        finalize_comparison_data(park)
    return parks


def parse_json_from_text(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
        if match:
            return json.loads(match.group(1))
        raise


def call_claude(api_key: str, prompt: str, *, max_tokens: int = 8192) -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "The 'anthropic' package is required. Install with: pip install anthropic"
        ) from exc

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    parts: list[str] = []
    for block in message.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts).strip()


def parks_to_context(parks: list[ParkRecord]) -> list[dict[str, Any]]:
    return [
        {
            "park_name": p.park_name,
            "total_score": p.total_score,
            "classification": p.classification,
            "google_rating": p.google_rating,
            "review_count": p.review_count,
            "best_for": p.best_for,
            "top_scoring_criteria": p.top_scoring_criteria,
            "water_fun": p.water_fun,
            "kids_play": p.kids_play,
            "pet_detail": p.pet_detail,
            "pet_friendly": p.pet_friendly,
            "address": p.address,
            "website": p.website,
            "powered_price": p.powered_price,
            "price_note": p.price_note,
            "nearest_beach": p.nearest_beach,
            "nearest_supermarket": p.nearest_supermarket,
            "rationale_top3": p.rationale_top3,
        }
        for p in parks
    ]


def generate_narrative_content(
    api_key: str,
    *,
    template_text: str,
    location_name: str,
    state: str,
    search_term: str,
    config: dict[str, Any],
    parks: list[ParkRecord],
    loc_dir: Path,
) -> dict[str, Any]:
    location_line = f"{location_name} {state}"
    banned = ", ".join(BANNED_WORDS)
    badges = ", ".join(ACTIVITY_BADGES)
    config_hint = ""
    if config.get("hero_headline"):
        config_hint += f"\nReference headline: {config['hero_headline']}"
    if config.get("hero_intro"):
        config_hint += f"\nReference intro: {config['hero_intro']}"

    existing_activities = load_json_file(loc_dir / "activities.json", [])
    activity_photo_hint = ""
    if isinstance(existing_activities, list) and existing_activities:
        activity_photo_hint = (
            "\nExisting activity photo URLs (use only if still relevant):\n"
            + json.dumps(
                [{a.get("name"): a.get("photo")} for a in existing_activities if a.get("name")],
                ensure_ascii=False,
            )
        )

    prompt = f"""You are writing content for a Family Holiday Parks location review file.

Gold Coast template (format and tone benchmark):
{template_text}

Location: {location_line}
Search term: {search_term}
{config_hint}

Park data (bookable parks only):
{json.dumps(parks_to_context(bookable_parks(parks)), indent=2, ensure_ascii=False)}

Write JSON only with this structure:
{{
  "heading": "Best Family Holiday & Caravan Parks in/on ...",
  "hero_intro": "Two short paragraphs separated by a blank line.",
  "why_families_love": ["bullet 1", "bullet 2", "bullet 3", "bullet 4", "bullet 5"],
  "local_knowledge": "One practical paragraph for parents.",
  "destination_summary": "2-4 substantial paragraphs separated by blank lines about the holiday park scene — not tourism copy, not FAQ. Cover what makes the destination unique for parks, park types, who stays, caravan/motorhome suitability, beach/river/nature/theme park access, booking patterns, local holiday culture, why families return. Local expert tone. Specific. Practical. Australian. No marketing fluff.",
  "park_cards": {{"Exact Park Name": "Best for families wanting ..."}},
  "activities": [
    {{
      "name": "Activity Name",
      "description": "Max 15 words, one sentence — what families do, no marketing filler.",
      "tag": "Category e.g. Theme Park, Nature, Free",
      "distance": "e.g. 10 mins from Park Name",
      "image_search_term": "e.g. Cape Byron Lighthouse Byron Bay",
      "badge": "one of: {badges} or empty"
    }}
  ]
}}

Rules:
- Tone: practical, concise, family-first, Australian, decision-focused — not a tourism brochure.
- Avoid: {banned}
- park_cards must include every park name exactly.
- Use existing best_for text when it already starts with "Best for families".
- activities: exactly 10 real family activities for {location_name}.
- activity descriptions: max 15 words, one sentence, Airbnb-style — what families experience; no filler like "perfect for families" or "breathtaking scenery".
- image_search_term: short Google image search phrase for each activity (not a URL).
- No markdown. JSON only.
{activity_photo_hint}
"""
    raw = call_claude(api_key, prompt, max_tokens=12000)
    return parse_json_from_text(raw)


def generate_seo_faq(
    api_key: str,
    *,
    location_name: str,
    state: str,
    parks: list[ParkRecord],
) -> list[dict[str, str]]:
    topics = [t.format(location=location_name) for t in SEO_FAQ_TOPICS]
    prompt = f"""Write 10 SEO-focused FAQs for parents choosing a family holiday park in {location_name} {state}.

Cover these search intents (combine related topics, 10 Q&As total):
{json.dumps(topics, indent=2)}

Park context:
{json.dumps(parks_to_context(parks), indent=2, ensure_ascii=False)}

Rules:
- Each answer 40–90 words.
- Specific to {location_name}, not generic Australia copy.
- Mention real park names where relevant.
- Written for parents deciding where to stay.
- Avoid: {", ".join(BANNED_WORDS)}

Reply JSON only:
{{"faqs": [{{"question": "...", "answer": "..."}}, ...]}}
Exactly 10 items.
"""
    raw = call_claude(api_key, prompt, max_tokens=6000)
    data = parse_json_from_text(raw)
    faqs = data.get("faqs") if isinstance(data, dict) else data
    if not isinstance(faqs, list):
        raise ValueError("FAQ response was not a list")

    extra_topics = [t.format(location=location_name) for t in EXTRA_FAQ_TOPICS]
    extra_prompt = f"""Write exactly 3 SEO-focused FAQs for parents visiting {location_name} {state}.

Topics (one Q&A each):
{json.dumps(extra_topics, indent=2)}

Park context:
{json.dumps(parks_to_context(parks), indent=2, ensure_ascii=False)}

Rules:
- Each answer 40–90 words.
- Specific to {location_name}.
- Avoid: {", ".join(BANNED_WORDS)}

Reply JSON only:
{{"faqs": [{{"question": "...", "answer": "..."}}, ...]}}
"""
    extra_raw = call_claude(api_key, extra_prompt, max_tokens=3000)
    extra_data = parse_json_from_text(extra_raw)
    extra_faqs = extra_data.get("faqs") if isinstance(extra_data, dict) else []
    if isinstance(extra_faqs, list):
        faqs.extend(extra_faqs[:3])

    return faqs


def ensure_best_for(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if text.lower().startswith("best for families"):
        return text if text.endswith(".") else f"{text}."
    return f"Best for families wanting {text.rstrip('.')}."


def format_tags(tags: list[str]) -> str:
    return ", ".join(tags[:4])


def format_price_line(park: ParkRecord) -> str:
    if park.price_found:
        line = f"{park.park_name} | {park.powered_price}"
        note = (park.price_note or "").strip()
        if note and note != "Price unavailable online":
            line += f" | {note}"
        return line
    return f"{park.park_name} | — | Price unavailable online"


def assemble_review_file(
    *,
    location_name: str,
    state: str,
    narrative: dict[str, Any],
    faqs: list[dict[str, str]],
    parks: list[ParkRecord],
) -> str:
    location_line = f"{location_name} {state}"
    lines: list[str] = []

    lines.append("LOCATION:")
    lines.append(location_line)
    lines.append("")

    lines.append("HEADING:")
    lines.append(str(narrative.get("heading") or f"Best Family Holiday & Caravan Parks in {location_name}").strip())
    lines.append("")

    lines.append("HERO INTRO:")
    hero = str(narrative.get("hero_intro") or "").strip()
    lines.append(hero)
    lines.append("")

    lines.append("WHY FAMILIES LOVE:")
    for bullet in (narrative.get("why_families_love") or [])[:5]:
        bullet = str(bullet).strip().lstrip("-").strip()
        if bullet:
            lines.append(f"- {bullet}")
    lines.append("")

    lines.append("LOCAL KNOWLEDGE:")
    lines.append(str(narrative.get("local_knowledge") or "").strip())
    lines.append("")

    lines.append("DESTINATION SUMMARY:")
    lines.append(str(narrative.get("destination_summary") or "").strip())
    lines.append("")

    comparison_parks = bookable_parks(parks)
    park_cards = narrative.get("park_cards") or {}
    lines.append("PARK CARDS:")
    for park in comparison_parks:
        card = ""
        if isinstance(park_cards, dict):
            card = str(park_cards.get(park.park_name) or "").strip()
        if not card:
            card = park.best_for
        lines.append(f"{park.park_name} | {ensure_best_for(card)}")
    lines.append("")

    lines.append("TAGS:")
    for park in comparison_parks:
        lines.append(f"{park.park_name} | {format_tags(park.top_scoring_criteria)}")
    lines.append("")

    lines.append("KIDS PLAY:")
    for park in comparison_parks:
        lines.append(f"{park.park_name} | {park.kids_categories or categorize_kids_play(park)}")
    lines.append("")

    lines.append("WATER FUN:")
    for park in comparison_parks:
        lines.append(f"{park.park_name} | {park.water_categories or categorize_water_fun(park)}")
    lines.append("")

    lines.append("PHOTOS:")
    for park in parks:
        lines.append(f"{park.park_name} | {park.photo_url or ''}".rstrip())
    lines.append("")

    lines.append("ADDRESSES:")
    for park in parks:
        lines.append(f"{park.park_name} | {park.address}")
    lines.append("")

    lines.append("WEBSITES:")
    for park in parks:
        lines.append(f"{park.park_name} | {park.website}")
    lines.append("")

    lines.append("PRICES:")
    for park in parks:
        lines.append(format_price_line(park))
    lines.append("")

    lines.append("FAQ:")
    for item in faqs:
        q = str(item.get("question") or "").strip()
        a = str(item.get("answer") or "").strip()
        if q and a:
            lines.append(f"Q: {q}")
            lines.append(f"A: {a}")
            lines.append("")

    activities = narrative.get("activities") or []
    lines.append("ACTIVITIES:")
    for act in activities[:10]:
        if not isinstance(act, dict):
            continue
        name = str(act.get("name") or "").strip()
        if not name:
            continue
        search_term = str(
            act.get("image_search_term") or act.get("photo") or ""
        ).strip()
        if search_term.startswith("http"):
            search_term = ""
        parts = [
            name,
            str(act.get("description") or "").strip(),
            str(act.get("tag") or "").strip(),
            str(act.get("distance") or "").strip(),
            search_term,
            str(act.get("badge") or "").strip(),
        ]
        lines.append(" | ".join(parts))

    return "\n".join(lines).strip() + "\n"


def print_location_summary(stats: LocationStats) -> None:
    action = "updated" if stats.updated else "created"
    if stats.created or stats.updated:
        print(f"[{action}] reviews/{stats.review_slug}.txt")
    print(f"[photos] {stats.photos_approved}/{stats.photos_total} approved")
    print(f"[activities] {stats.activities_count} generated")
    print(f"[faq] {stats.faq_count} generated")
    print(f"[prices] {stats.prices_found}/{stats.prices_total} found")
    print(f"[maps] {stats.maps_valid}/{stats.maps_total} valid")
    print(f"[build] {'success' if stats.build_ok else 'fail'}")


def process_location(
    row: dict[str, str],
    *,
    api_key: str,
    google_key: str,
    template_text: str,
    force: bool,
    publish: bool,
) -> LocationStats:
    location_name = row["location"].strip()
    state = row["state"].strip().upper()
    state_dir = STATE_MAP.get(state, state.lower())
    loc_slug = row["slug"].strip()
    review_slug = review_slug_for_row(row)
    out_path = REVIEWS_DIR / f"{review_slug}.txt"
    loc_dir = PROJECT_DIR / "locations" / state_dir / loc_slug
    stats = LocationStats(review_slug=review_slug)

    existed = out_path.exists()
    if existed and not force:
        print(f"[skip] {out_path.name} exists (use --force to overwrite)")
        return stats

    approved_list = load_approved_parks(loc_dir)
    scores_path = loc_dir / "scores.json"
    scores: list[dict[str, Any]] = []
    if scores_path.exists():
        raw_scores = load_json_file(scores_path, [])
        if isinstance(raw_scores, list):
            scores = raw_scores

    if approved_list is not None:
        if not approved_list:
            print(f"[skip] empty approved-parks.json for {location_name}")
            return stats
    elif not scores:
        print(f"[skip] no scores.json for {location_name}")
        return stats

    config = load_json_file(loc_dir / "config.json", {})
    if not isinstance(config, dict):
        config = {}

    print(f"\n[generate] {location_name} {state} -> {out_path.name}")
    parks = build_park_records(loc_dir, scores, api_key=api_key, google_key=google_key)
    if not parks:
        print(f"[skip] no parks found for {location_name}")
        return stats

    stats.photos_total = len(parks)
    stats.photos_approved = sum(1 for p in parks if p.photo_approved)
    stats.prices_total = len(parks)
    stats.prices_found = sum(1 for p in parks if p.price_found)
    stats.maps_total = len(parks)
    stats.maps_valid = sum(1 for p in parks if p.maps_valid)

    try:
        narrative = generate_narrative_content(
            api_key,
            template_text=template_text,
            location_name=location_name,
            state=state,
            search_term=row.get("search_term", "").strip(),
            config=config,
            parks=parks,
            loc_dir=loc_dir,
        )
        faqs = generate_seo_faq(
            api_key,
            location_name=location_name,
            state=state,
            parks=parks,
        )
    except Exception as exc:
        print(f"[error] Claude failed for {review_slug}: {exc}")
        return stats

    stats.activities_count = len(narrative.get("activities") or [])
    stats.faq_count = len(faqs)
    if stats.faq_count < 13:
        print(f"[warn] {review_slug}: FAQ has {stats.faq_count} items (expected 13)")
    if stats.activities_count < 10:
        print(f"[warn] {review_slug}: activities has {stats.activities_count} items (expected 10)")

    content = assemble_review_file(
        location_name=location_name,
        state=state,
        narrative=narrative,
        faqs=faqs,
        parks=parks,
    )

    REVIEWS_DIR.mkdir(exist_ok=True)
    out_path.write_text(content, encoding="utf-8")

    stats.build_ok = True
    stats.created = not existed
    stats.updated = existed and force
    print_location_summary(stats)

    if publish:
        cmd = [sys.executable, str(PROJECT_DIR / "update_location.py"), review_slug, "--publish"]
        print(f"[publish] {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd=PROJECT_DIR)
        if result.returncode != 0:
            print(f"[error] publish failed for {review_slug}")
            stats.build_ok = False

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 3: create/enrich review files with photo, price, maps and SEO FAQ QA."
    )
    parser.add_argument("--slug", help="Review slug e.g. byron-bay-nsw")
    parser.add_argument("--state", help="Filter by state e.g. QLD")
    parser.add_argument("--force", action="store_true", help="Overwrite existing review files")
    parser.add_argument("--publish", action="store_true", help="Run update_location.py --publish after each file")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY is required.")
        sys.exit(1)

    google_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not google_key:
        print("[warn] GOOGLE_MAPS_API_KEY not set; maps place_id resolution will be limited.")

    if not MASTER_REVIEW.exists():
        print(f"ERROR: Master template not found: {MASTER_REVIEW}")
        sys.exit(1)

    template_text = MASTER_REVIEW.read_text(encoding="utf-8")
    rows = read_locations(slug_filter=args.slug, state_filter=args.state)
    if not rows:
        print("No matching locations found.")
        sys.exit(0)

    print(f"Processing {len(rows)} location(s)...")
    totals = {"created": 0, "updated": 0, "failed": 0}

    for i, row in enumerate(rows):
        stats = process_location(
            row,
            api_key=api_key,
            google_key=google_key,
            template_text=template_text,
            force=args.force,
            publish=args.publish,
        )
        if stats.build_ok:
            if stats.created:
                totals["created"] += 1
            elif stats.updated:
                totals["updated"] += 1
        elif not (REVIEWS_DIR / f"{stats.review_slug}.txt").exists() or args.force:
            totals["failed"] += 1

        if i < len(rows) - 1:
            time.sleep(2)

    print(
        f"\n[summary] created={totals['created']} updated={totals['updated']} failed={totals['failed']}"
    )


if __name__ == "__main__":
    main()
