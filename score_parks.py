#!/usr/bin/env python3
"""Score family holiday parks for a location and persist ranked outputs."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

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


def get_location_dir(project_dir: Path, location: str) -> Path:
    """Resolve locations/state/slug directory from locations.csv."""
    import csv as _csv

    bare = re.sub(
        r"\b(Queensland|New South Wales|Victoria|South Australia|Western Australia|Tasmania|Northern Territory|Australian Capital Territory|QLD|NSW|VIC|SA|WA|TAS|NT|ACT)\b",
        "",
        location,
        flags=re.IGNORECASE,
    ).strip().strip(",").strip()
    bare = re.sub(r"\s+", " ", bare).strip()
    log(f"[debug] get_location_dir: location='{location}' bare='{bare}'")

    csv_path = project_dir / "locations.csv"
    loc_key = re.sub(r"\s+", " ", location.strip()).strip().lower()
    bare_key = bare.lower()
    if csv_path.exists():
        with open(csv_path, encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                row_loc = re.sub(r"\s+", " ", row.get("location", "").strip()).strip().lower()
                if row_loc == loc_key or row_loc == bare_key:
                    state = STATE_MAP.get(row.get("state", "").strip().upper(), "other")
                    slug = row.get("slug", "").strip()
                    if slug:
                        return project_dir / "locations" / state / slug
    fallback_slug = re.sub(r"[^a-z0-9]+", "-", location.lower()).strip("-")
    return project_dir / "locations" / "other" / fallback_slug


def init_location_dir(loc_dir: Path) -> None:
    """Create location folder and empty template files if missing."""
    loc_dir.mkdir(parents=True, exist_ok=True)
    for filename, default in [
        ("photos.json", "{}"),
        ("prices.json", "{}"),
        ("websites.json", "{}"),
        ("whitelist.json", "{}"),
        ("config.json", "{}"),
    ]:
        fp = loc_dir / filename
        if not fp.exists():
            fp.write_text(default, encoding="utf-8")


def save_executive_summary(loc_dir: Path, park_name: str, summary: str) -> None:
    if not summary or not summary.strip():
        return
    summaries_dir = loc_dir / "executive-summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    park_slug = re.sub(r"[^a-z0-9]+", "-", park_name.lower()).strip("-")
    summary_path = summaries_dir / f"{park_slug}.txt"
    summary_path.write_text(summary.strip(), encoding="utf-8")
    log(f"[summary] Saved executive summary: {summary_path.name}")


APIFY_GMAPS_ACTOR = "compass~crawler-google-places"
APIFY_GMAPS_REVIEWS_ACTOR = "compass~google-maps-reviews-scraper"
APIFY_BASE_URL = "https://api.apify.com/v2"
PLACE_TEXTSEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"

# Exclude only when the park name positively matches non-caravan accommodation (substring, lowercased).
NON_CARAVAN_NAME_KEYWORDS = (
    "hotel",
    "motel",
    "resort",
    "suites",
    "apartments",
    "inn",
    "lodge",
    "backpacker",
    "hostel",
    "steakhouse",
    "restaurant",
    "golf",
    "marina",
)

# Never apply non-caravan name exclusions when the park name contains any of these (case-insensitive).
NAME_FILTER_WHITELIST_TERMS = (
    "NRMA Treasure Island",
    "Treasure Island",
    "Tallebudgera",
    "Kirra Beach",
    "Broadwater Tourist Park",
)

# Excluded after the name filter (case-insensitive substring match on park title/name).
MANUAL_EXCLUDE = (
    "BreakFree Diamond Beach Broadbeach",
    "Chateau Beachside",
    "Mantra Sun City Surfers Paradise",
    "Mantra on View Surfers Paradise",
    "Casino Village",
    "AutoCamp Zion",
    "Ashmore Palms Holiday Village",
)

# Minimum Google user_ratings_total (or Apify proxy) before scoring.
MIN_GOOGLE_REVIEWS_FOR_SCORING = 25

AIRTABLE_ENABLED = False  # Set to True to enable Airtable sync

SCORING_PROMPT = (
    "You are a family holiday park expert for Australian families with young children. "
    "Score this park out of 100 using these exact weighted criteria:\n"
    "- entertainment_score: 20 (on-site kids entertainment)\n"
    "- nature_score: 20 (natural environment and nearby nature)\n"
    "- cleanliness_score: 15 (cleanliness and maintenance)\n"
    "- value_score: 15 (value for families)\n"
    "- site_size_score: 10 (site size and space)\n"
    "- sentiment_score: 10 (family review sentiment)\n"
    "- location_score: 10 (location and accessibility)\n"
    "Use only the provided data and review evidence. Here is the park data:\n"
    "[INSERT ALL PARK DATA INCLUDING ALL REVIEW TEXT]\n\n"
    "Return JSON only with fields:\n"
    "total_score, entertainment_score, nature_score, site_size_score, cleanliness_score, "
    "value_score, sentiment_score, location_score, pet_score, classification, rationale_top3, "
    "rationale_honourable, key_phrases, best_suited_for, watch_out, water_fun, kids_play, "
    "pet_detail, best_for, wifi_available, pet_friendly, executive_summary.\n"
    "Set classification by total_score: Gold 80-100, Silver 65-79, Bronze 50-64, Not Listed <50.\n"
    "rationale_top3: exactly 2 short paragraphs, only if Gold, else empty string.\n"
    "rationale_honourable: exactly 1 short paragraph, only if Silver, else empty string.\n"
    "key_phrases: up to 10 exact phrases from reviews.\n"
    "best_suited_for: one sentence.\n"
    "watch_out: one honest sentence with positive framing.\n"
    "water_fun: short 5-8 words about pool/water facilities based on review evidence.\n"
    "kids_play: short 5-8 words about playground/kids entertainment based on review evidence.\n"
    "pet_detail: specific pet policy/detail from review evidence; if not mentioned use "
    "'Check directly with park'.\n"
    "best_for: one sentence on the type of family this park suits best, based on review evidence.\n"
    "wifi_available: respond with exactly \"yes\" or \"no\" based on review evidence of "
    "wifi/internet access at the park. If unclear respond \"unknown\".\n"
    "pet_friendly: respond with exactly \"yes\" or \"no\" based on review evidence of "
    "pets/dogs being allowed. If unclear respond \"unknown\".\n"
    "executive_summary: a structured summary using exactly this format:\n\n"
    "PARK: {park name}\n"
    "SCORE: {total}/100\n"
    "DATE SCORED: {today's date}\n"
    "REVIEWS ANALYSED: {count}\n\n"
    "WHY THIS SCORE:\n"
    "Entertainment ({score}/20) — [what reviews said about kids entertainment, activities, waterpark etc]\n"
    "Nature ({score}/20) — [what reviews said about natural environment, beach access, bush, creek etc]\n"
    "Cleanliness ({score}/15) — [what reviews said about cleanliness and maintenance]\n"
    "Value ({score}/15) — [what reviews said about value for money and pricing]\n"
    "Site Size ({score}/10) — [what reviews said about site size and space for rigs]\n"
    "Sentiment ({score}/10) — [overall guest sentiment, repeat visitation, recommendation rate]\n"
    "Location ({score}/10) — [what reviews said about location, proximity to attractions, beach, town]\n\n"
    "WHY NOT 100:\n"
    "[3 specific reasons the park lost points based on review evidence. Be honest and specific.]\n\n"
    "NOTABLE REVIEW PHRASES:\n"
    "[6-8 short exact phrases from reviews that most influenced the score, in quotes, comma separated]"
)

EXPECTED_SCORE_FIELDS = {
    "total_score",
    "entertainment_score",
    "nature_score",
    "site_size_score",
    "cleanliness_score",
    "value_score",
    "sentiment_score",
    "location_score",
    "pet_score",
    "classification",
    "rationale_top3",
    "rationale_honourable",
    "key_phrases",
    "best_suited_for",
    "watch_out",
    "water_fun",
    "kids_play",
    "pet_detail",
    "best_for",
}
FAMILY_TERMS = ("family", "kids", "children", "child", "toddler")
CLAUDE_MIN_WAIT_SECONDS = 10
CLAUDE_LOW_TOKEN_THRESHOLD = 5000
_NEXT_CLAUDE_ALLOWED_TS = 0.0
REVIEW_BATCH_SIZE = 500
TOKEN_ESTIMATE_CHARS_PER_TOKEN = 4
TOKEN_ESTIMATE_BUFFER = 5000
TOKEN_ESTIMATE_MAX = 120000
ESTIMATED_CLAUDE_COST_PER_PARK = 0.02
ESTIMATED_MINUTES_PER_PARK = 2
SCORE_NUMERIC_FIELDS = (
    "total_score",
    "entertainment_score",
    "nature_score",
    "site_size_score",
    "cleanliness_score",
    "value_score",
    "sentiment_score",
    "location_score",
    "pet_score",
)


def log(message: str) -> None:
    print(message, flush=True)


def log_err(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-") or "location"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score family holiday parks for a location.")
    parser.add_argument("location", type=str, help='Location string, e.g. "Gold Coast Queensland"')
    parser.add_argument(
        "--debug-reviews",
        action="store_true",
        help="Save per-park structured review audit JSON files to review-data/",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip parks that already exist in the current [location]-scores.json output.",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Non-interactive mode: skip manual approval checkpoint.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Delete raw/approved cache files before running.",
    )
    parser.add_argument(
        "--rescore",
        type=str,
        default="",
        metavar="NAMES",
        help="Pipe-separated park names to remove from [location]-scores.json before running so they are "
        "rescored. Use 'Park One|Park Two|Park Three'. With --resume, other parks that remain "
        "in the file are still skipped.",
    )
    parser.add_argument(
        "--fresh-copy",
        action="store_true",
        help="Allow overwriting existing rationale copy fields in scores JSON for rescored parks.",
    )
    return parser.parse_args()


def parse_rescore_park_names(raw: str) -> set[str]:
    if not (raw and str(raw).strip()):
        return set()
    return {part.strip() for part in str(raw).split("|") if part.strip()}


def post_json(url: str, payload: dict[str, Any], *, timeout: int = 180) -> Any:
    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def get_json(url: str, *, timeout: int = 60, params: dict[str, Any] | None = None) -> Any:
    resp = requests.get(url, timeout=timeout, params=params)
    resp.raise_for_status()
    return resp.json()


def find_places_payload(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("results", "places", "data", "items"):
            candidate = data.get(key)
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]
        return [data]
    return []


def collect_categories(place: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("categories", "category", "types", "type", "categoryName"):
        v = place.get(key)
        if isinstance(v, str):
            values.append(v)
        elif isinstance(v, list):
            values.extend([str(x) for x in v if isinstance(x, (str, int, float))])
    nested = place.get("details")
    if isinstance(nested, dict):
        for key in ("categories", "category", "types", "type"):
            v = nested.get(key)
            if isinstance(v, str):
                values.append(v)
            elif isinstance(v, list):
                values.extend([str(x) for x in v if isinstance(x, (str, int, float))])
    return " | ".join(values).lower()


def _park_name_blob(place: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("title", "name"):
        v = place.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    nested = place.get("details")
    if isinstance(nested, dict):
        for key in ("title", "name"):
            v = nested.get(key)
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
    return " ".join(parts)


def _first_non_caravan_name_keyword(name_lower: str) -> str | None:
    for term in NON_CARAVAN_NAME_KEYWORDS:
        if term in name_lower:
            return term
    return None


def _first_whitelist_term_matched(name_lower: str) -> str | None:
    """Return the whitelist phrase that matched (original casing), or None."""
    for raw in NAME_FILTER_WHITELIST_TERMS:
        needle = raw.lower().strip()
        if needle and needle in name_lower:
            return raw.strip()
    return None


def evaluate_name_filter(place: dict[str, Any]) -> tuple[bool, str]:
    """
    Returns (include, reason). Whitelist wins over exclusion keywords.
    """
    name_lower = _park_name_blob(place).lower()
    wl = _first_whitelist_term_matched(name_lower)
    if wl:
        return True, f"whitelist — name contains '{wl}' (exclusion filter skipped)"

    matched = _first_non_caravan_name_keyword(name_lower)
    if matched:
        return (
            False,
            f"excluded — name contains non-caravan term '{matched}'",
        )
    return True, "passed — no non-caravan exclusion terms in combined name fields"


def is_target_park(place: dict[str, Any]) -> bool:
    """Keep all parks unless the name matches non-caravan exclusion terms."""
    include, _reason = evaluate_name_filter(place)
    return include


def _manual_exclude_match(park_name: str) -> str | None:
    """Return the MANUAL_EXCLUDE entry that matches, or None."""
    pn = park_name.strip().lower()
    if not pn:
        return None
    for term in MANUAL_EXCLUDE:
        t = term.strip().lower()
        if not t:
            continue
        if t == pn or t in pn or pn in t:
            return term.strip()
    return None


def dedupe_places(places: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for p in places:
        pid = p.get("placeId") or p.get("place_id") or p.get("googlePlaceId")
        if isinstance(pid, str) and pid.strip():
            key = f"id:{pid.strip()}"
        else:
            name = str(p.get("title") or p.get("name") or "").strip().lower()
            addr = str(p.get("address") or p.get("formatted_address") or "").strip().lower()
            key = f"n:{name}|{addr}"
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def dedupe_places_by_name(places: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for p in places:
        name = str(p.get("title") or p.get("name") or "").strip().lower()
        if not name:
            pid = str(p.get("placeId") or p.get("place_id") or p.get("googlePlaceId") or "").strip().lower()
            if not pid:
                continue
            name = f"id:{pid}"
        if name in seen:
            continue
        seen.add(name)
        out.append(p)
    return out


def run_apify_actor(actor: str, token: str, payload: dict[str, Any], *, timeout_sec: int = 900) -> list[dict[str, Any]]:
    run_url = f"{APIFY_BASE_URL}/acts/{actor}/run-sync-get-dataset-items"
    response = post_json(f"{run_url}?token={token}", payload, timeout=timeout_sec)
    return find_places_payload(response)


def normalize_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
            if ts > 1e12:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    relative = re.match(r"(?i)^\s*(\d+)\s+(day|week|month|year)s?\s+ago\s*$", text)
    if relative:
        qty = int(relative.group(1))
        unit = relative.group(2).lower()
        now = datetime.now(timezone.utc)
        if unit == "day":
            return now - timedelta(days=qty)
        if unit == "week":
            return now - timedelta(weeks=qty)
        if unit == "month":
            return now - timedelta(days=qty * 30)
        if unit == "year":
            return now - timedelta(days=qty * 365)
    iso_guess = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_guess)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def date_in_last_3_years(value: Any) -> bool:
    dt = normalize_date(value)
    if not dt:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=365 * 3)
    return dt >= cutoff


def structured_review(
    *,
    source: str,
    date_value: Any,
    star_rating: Any,
    text: Any,
    reviewer_type: Any,
) -> dict[str, Any] | None:
    review_text = str(text or "").strip()
    if not review_text:
        return None
    dt = normalize_date(date_value)
    if not dt:
        return None
    rating: float | None = None
    try:
        if star_rating is not None and str(star_rating).strip():
            rating = float(star_rating)
    except (TypeError, ValueError):
        rating = None
    return {
        "source": source,
        "date": dt.date().isoformat(),
        "star_rating": rating,
        "review_text": review_text,
        "reviewer_type": str(reviewer_type or "").strip(),
    }


def scrape_parks_with_apify(token: str, location: str) -> list[dict[str, Any]]:
    search_terms = [
        f"caravan parks {location}",
        f"caravan park {location}",
        f"tourist park {location}",
    ]
    log(f"[1/9] Apify scrape started with {len(search_terms)} separate searches.")
    combined_rows: list[dict[str, Any]] = []
    for idx, term in enumerate(search_terms, start=1):
        payload = {
            "searchStringsArray": [term],
            "maxCrawledPlacesPerSearch": 100,
            "language": "en",
        }
        rows = run_apify_actor(APIFY_GMAPS_ACTOR, token, payload)
        log(f"[1/9] Search {idx}/{len(search_terms)} '{term}' returned {len(rows)} rows.")
        combined_rows.extend(rows)
    deduped = dedupe_places_by_name(combined_rows)
    log(f"[1/9] Combined rows before dedupe: {len(combined_rows)}.")
    log(f"[1/9] Total rows after dedupe by park name: {len(deduped)}.")
    return deduped


def load_park_whitelist(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {str(k).strip().lower() for k, v in raw.items() if v}
        if isinstance(raw, list):
            return {str(k).strip().lower() for k in raw}
    except Exception:
        return set()
    return set()


def load_cached_raw_parks(cache_file: Path) -> list[dict[str, Any]] | None:
    if not cache_file.exists():
        return None
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except Exception as exc:
        log_err(f"[1/9] Failed reading raw parks cache {cache_file.name}: {exc}")
        return None
    rows = find_places_payload(payload)
    log(f"[1/9] Loaded {len(rows)} raw parks from cache: {cache_file.name}")
    return rows


def save_cached_raw_parks(cache_file: Path, raw_rows: list[dict[str, Any]]) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(raw_rows, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"[1/9] Saved raw parks cache: {cache_file.name}")


def finalize_raw_park_rows(raw_rows: list[dict[str, Any]], *, source_label: str) -> list[dict[str, Any]]:
    rows = dedupe_places_by_name(raw_rows)
    log(f"[1/9] After dedupe by park name ({source_label}): {len(rows)} rows.")
    log(f"[1/9] --- Name filter: evaluating {len(rows)} parks ({source_label}) ---")
    kept: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        park_name = str(row.get("title") or row.get("name") or "Unknown park")
        include, reason = evaluate_name_filter(row)
        if include:
            kept.append(row)
            log(
                f"[1/9] FILTER PASS [{idx}/{len(rows)}] '{park_name}' ({source_label}): {reason}"
            )
        else:
            log(
                f"[1/9] FILTER EXCLUDE [{idx}/{len(rows)}] '{park_name}' ({source_label}): {reason}"
            )
    log(f"[1/9] After non-caravan name exclusion filter: {len(kept)} parks remain.")

    after_manual: list[dict[str, Any]] = []
    log(f"[1/9] --- Manual exclude list ({len(MANUAL_EXCLUDE)} entries) ---")
    for row in kept:
        park_name = str(row.get("title") or row.get("name") or "Unknown park")
        hit = _manual_exclude_match(park_name)
        if hit:
            log(
                f"[1/9] MANUAL EXCLUDE '{park_name}' ({source_label}): "
                f"matched list entry '{hit}'"
            )
            continue
        after_manual.append(row)
    kept = after_manual
    log(f"[1/9] After manual exclude: {len(kept)} parks will go to Places/review scoring.")

    log("[1/9] ========== Parks scheduled for scoring (verify list) ==========")
    if not kept:
        log("[1/9]   (none)")
    else:
        for n, r in enumerate(kept, start=1):
            nm = str(r.get("title") or r.get("name") or "Unknown park")
            log(f"[1/9]   {n}. {nm}")
    log("[1/9] ========== End of parks list ==========")
    return kept


def _park_rating_and_reviews_for_checkpoint(park: dict[str, Any]) -> tuple[str, str]:
    rating_raw = park.get("totalScore") or park.get("rating")
    reviews_raw = park.get("reviewsCount") or park.get("reviews")
    rating_text = "—"
    reviews_text = "—"
    try:
        if rating_raw is not None:
            rating_text = f"{float(rating_raw):.1f}"
    except (TypeError, ValueError):
        rating_text = "—"
    try:
        if reviews_raw is not None:
            reviews_text = f"{int(float(reviews_raw)):,}"
    except (TypeError, ValueError):
        reviews_text = "—"
    return rating_text, reviews_text


def save_approved_parks(path: Path, parks: list[dict[str, Any]], *, location: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "location": location,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "approved_count": len(parks),
        "parks": parks,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def load_approved_parks(path: Path) -> tuple[list[dict[str, Any]] | None, str]:
    if not path.exists():
        return None, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "approved_parks" in data:
            approved_at = str(data.get("date") or "").strip()
            raw = data["approved_parks"]
            if isinstance(raw, list):
                out: list[dict[str, Any]] = []
                for item in raw:
                    if isinstance(item, dict):
                        out.append(item)
                    elif isinstance(item, str) and item.strip():
                        out.append({"title": item.strip(), "name": item.strip()})
                return out, approved_at
        if isinstance(data, dict) and isinstance(data.get("parks"), list):
            approved_at = str(data.get("approved_at") or "").strip()
            return [p for p in data["parks"] if isinstance(p, dict)], approved_at
        if isinstance(data, list):
            if data and isinstance(data[0], str):
                return [
                    {"title": str(n).strip(), "name": str(n).strip()}
                    for n in data
                    if isinstance(n, str) and str(n).strip()
                ], ""
            return [p for p in data if isinstance(p, dict)], ""
    except Exception as exc:
        log_err(f"[1/9] Failed reading approved parks file {path.name}: {exc}")
        return None, ""
    return None, ""


def save_progress_file(
    path: Path,
    *,
    parks_completed: int,
    parks_remaining: int,
    estimated_minutes_to_completion: float,
) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "parks_completed": parks_completed,
        "parks_remaining": parks_remaining,
        "estimated_time_to_completion_minutes": round(estimated_minutes_to_completion, 2),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def rationale_field_to_prose(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(x).strip() for x in value if str(x).strip())
    if not isinstance(value, str):
        return str(value or "").strip()
    s = value.strip()
    if s.startswith("[") and s.endswith("]"):
        try:
            parsed = ast.literal_eval(s)
            if isinstance(parsed, list):
                return " ".join(str(x).strip() for x in parsed if str(x).strip())
        except (ValueError, SyntaxError, MemoryError):
            pass
    return s


def merge_scores(
    existing_scores: list[dict[str, Any]],
    new_scores: list[dict[str, Any]],
    *,
    preserve_existing_copy: bool = True,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in existing_scores:
        if not isinstance(row, dict):
            continue
        name = str(row.get("park_name") or "").strip()
        if name:
            cleaned = dict(row)
            cleaned["rationale_top3"] = rationale_field_to_prose(cleaned.get("rationale_top3"))
            cleaned["rationale_honourable"] = rationale_field_to_prose(cleaned.get("rationale_honourable"))
            merged[name] = cleaned
    for row in new_scores:
        if not isinstance(row, dict):
            continue
        name = str(row.get("park_name") or "").strip()
        if name:
            incoming = dict(row)
            incoming["rationale_top3"] = rationale_field_to_prose(incoming.get("rationale_top3"))
            incoming["rationale_honourable"] = rationale_field_to_prose(incoming.get("rationale_honourable"))
            if preserve_existing_copy and name in merged:
                prev = merged[name]
                for copy_field in ("rationale_top3", "rationale_honourable"):
                    prev_val = str(prev.get(copy_field) or "").strip()
                    if prev_val:
                        incoming[copy_field] = prev_val
                existing = prev
                new_score = incoming
            else:
                existing = {}
                new_score = incoming
            incoming["website"] = str(existing.get("website") or new_score.get("website") or "")
            incoming["lat"] = existing.get("lat") or new_score.get("lat")
            incoming["lng"] = existing.get("lng") or new_score.get("lng")
            merged[name] = incoming
    out = list(merged.values())
    out.sort(key=lambda r: float(r.get("total_score") or 0), reverse=True)
    return out


def apply_rank_classifications(rows: list[dict[str, Any]]) -> None:
    sorted_rows = sorted(rows, key=lambda r: float(r.get("total_score") or 0), reverse=True)
    for i, row in enumerate(sorted_rows):
        if i == 0:
            row["classification"] = "Gold"
        elif i == 1:
            row["classification"] = "Silver"
        elif i == 2:
            row["classification"] = "Bronze"
        else:
            row["classification"] = ""
    rows[:] = sorted_rows


def run_manual_review_checkpoint(
    parks: list[dict[str, Any]],
    *,
    location: str,
    slug: str,
    approved_path: Path,
) -> list[dict[str, Any]] | None:
    selected = list(parks)
    while True:
        log("")
        log(f"[CHECKPOINT] Manual review for {location}")
        for idx, park in enumerate(selected, start=1):
            name = str(park.get("title") or park.get("name") or f"Park {idx}")
            rating_text, reviews_text = _park_rating_and_reviews_for_checkpoint(park)
            log(f"  {idx}. {name} | rating={rating_text} | reviews={reviews_text}")
        total = len(selected)
        est_cost = total * ESTIMATED_CLAUDE_COST_PER_PARK
        est_minutes = total * ESTIMATED_MINUTES_PER_PARK
        log(
            f"[CHECKPOINT] Total parks={total} | Estimated Claude cost=${est_cost:.2f} | "
            f"Estimated run time={est_minutes} minutes"
        )
        choice = input("Type 'yes' to proceed, 'edit' to remove parks by number, or 'quit': ").strip().lower()
        if choice == "yes":
            save_approved_parks(approved_path, selected, location=location)
            log(f"[CHECKPOINT] Saved approved park list: {slug}-approved-parks.json")
            return selected
        if choice == "quit":
            log("[CHECKPOINT] Exiting without scoring.")
            return None
        if choice != "edit":
            log("[CHECKPOINT] Invalid choice. Please type yes, edit, or quit.")
            continue
        raw = input("Enter park numbers to remove (comma-separated, e.g. 2,5,9): ").strip()
        if not raw:
            log("[CHECKPOINT] No numbers provided; returning to menu.")
            continue
        indices: set[int] = set()
        bad_token = False
        for token in raw.split(","):
            t = token.strip()
            if not t:
                continue
            if not t.isdigit():
                bad_token = True
                break
            indices.add(int(t))
        if bad_token or not indices:
            log("[CHECKPOINT] Invalid number list. Use comma-separated integers.")
            continue
        next_selected: list[dict[str, Any]] = []
        for i, park in enumerate(selected, start=1):
            if i in indices:
                name = str(park.get("title") or park.get("name") or f"Park {i}")
                log(f"[CHECKPOINT] Removed #{i}: {name}")
                continue
            next_selected.append(park)
        selected = next_selected
        if not selected:
            log("[CHECKPOINT] All parks removed. Type quit or restart with new run.")


def _coerce_review_count(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def extract_place_id(park: dict[str, Any]) -> str:
    for key in ("placeId", "place_id", "googlePlaceId"):
        value = park.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().replace("places/", "")
    return ""


def google_place_details(api_key: str, place_id: str) -> dict[str, Any] | None:
    params = {
        "place_id": place_id,
        "fields": (
            "place_id,name,formatted_address,rating,user_ratings_total,website,geometry,photos,reviews,url,types"
        ),
        "reviews_sort": "newest",
        "key": api_key,
    }
    try:
        payload = get_json(PLACE_DETAILS_URL, params=params)
    except Exception as exc:
        log_err(f"[2/9] Google details failed for {place_id}: {exc}")
        return None
    if not isinstance(payload, dict) or payload.get("status") != "OK":
        log_err(f"[2/9] Google details status for {place_id}: {payload.get('status') if isinstance(payload, dict) else 'invalid'}")
        return None
    result = payload.get("result")
    return result if isinstance(result, dict) else None


def google_places_text_search(api_key: str, query: str) -> dict[str, Any] | None:
    try:
        payload = get_json(
            PLACE_TEXTSEARCH_URL,
            params={"query": query, "key": api_key},
        )
    except Exception as exc:
        log_err(f"[2/9] Google Places Text Search failed for '{query}': {exc}")
        return None
    if not isinstance(payload, dict):
        return None
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return None
    first = results[0]
    return first if isinstance(first, dict) else None


def passes_google_type_check(name: str, types: list[str], whitelist: set[str]) -> bool:
    name_lower = str(name or "").strip().lower()
    if name_lower in whitelist:
        log(f"[2/9] Google Places type check for {name}: kept (whitelisted)")
        return True
    allowed = {"campground", "rv_park", "caravan_park"}
    types_lower = {str(t).lower() for t in (types or [])}
    return bool(allowed & types_lower)


def photo_url_from_details(details: dict[str, Any], api_key: str) -> str:
    photos = details.get("photos")
    if not isinstance(photos, list) or not photos or not isinstance(photos[0], dict):
        return ""
    ref = photos[0].get("photo_reference")
    if not isinstance(ref, str) or not ref:
        return ""
    return (
        "https://maps.googleapis.com/maps/api/place/photo?maxwidth=800"
        f"&photo_reference={requests.utils.quote(ref, safe='')}&key={requests.utils.quote(api_key, safe='')}"
    )


def scrape_google_maps_reviews(token: str, park_name: str, location: str) -> list[dict[str, Any]]:
    payload = {
        "searchStringsArray": [f"{park_name} {location}"],
        "maxItems": 500,
        "maxReviews": 500,
        "language": "en",
    }
    try:
        items = run_apify_actor(APIFY_GMAPS_REVIEWS_ACTOR, token, payload, timeout_sec=240)
    except Exception as exc:
        log_err(f"[3/9] Google reviews scrape failed for {park_name}: {exc}")
        return []
    log(f"[3/9] Apify raw review items returned for {park_name}: {len(items)}")
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        date_value = item.get("publishedAtDate") or item.get("publishedAt") or item.get("date")
        if not date_in_last_3_years(date_value):
            continue
        review = structured_review(
            source="Google Maps",
            date_value=date_value,
            star_rating=item.get("stars") or item.get("rating"),
            text=item.get("text") or item.get("reviewText") or item.get("content"),
            reviewer_type=item.get("reviewerType") or item.get("authorLocalGuideLevel") or item.get("reviewer"),
        )
        if review:
            out.append(review)
    return out


def safe_json_parse(raw: str) -> dict[str, Any] | None:
    clean = raw.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```[a-zA-Z0-9]*\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean)
    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parse_score_fields_with_fallback(raw: str) -> dict[str, Any] | None:
    """
    Parse score payload robustly. First tries JSON, then falls back to regex field extraction
    when Claude output is truncated.
    """
    parsed = safe_json_parse(raw)
    if isinstance(parsed, dict):
        return parsed

    out: dict[str, Any] = {}
    for field in SCORE_NUMERIC_FIELDS:
        match = re.search(rf'"?{re.escape(field)}"?\s*:\s*([-+]?\d+(?:\.\d+)?)', raw, flags=re.IGNORECASE)
        if not match:
            continue
        num_text = match.group(1)
        try:
            out[field] = float(num_text) if "." in num_text else int(num_text)
        except ValueError:
            continue

    class_match = re.search(
        r'"?classification"?\s*:\s*"?(Gold|Silver|Bronze|Not Listed)"?',
        raw,
        flags=re.IGNORECASE,
    )
    if class_match:
        token = class_match.group(1).strip()
        if token.lower() == "not listed":
            out["classification"] = "Not Listed"
        else:
            out["classification"] = token[0].upper() + token[1:].lower()

    if not out:
        return None
    return out


class ClaudeRateLimitError(RuntimeError):
    """Raised when Claude responds with a rate limit error (HTTP 429)."""


def _is_rate_limit_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return True
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) == 429:
        return True
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "ratelimit" in msg


def _parse_reset_wait_seconds(reset_value: str | None) -> float:
    if not reset_value:
        return CLAUDE_MIN_WAIT_SECONDS
    txt = str(reset_value).strip()
    if not txt:
        return CLAUDE_MIN_WAIT_SECONDS
    now = time.time()
    try:
        n = float(txt)
        if n > now + 1:
            return max(CLAUDE_MIN_WAIT_SECONDS, n - now)
        if n > 0:
            return max(CLAUDE_MIN_WAIT_SECONDS, n)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(CLAUDE_MIN_WAIT_SECONDS, dt.timestamp() - now)
    except ValueError:
        return CLAUDE_MIN_WAIT_SECONDS


def _update_claude_wait_from_headers(headers: dict[str, str]) -> None:
    global _NEXT_CLAUDE_ALLOWED_TS
    remaining_raw = headers.get("x-ratelimit-remaining-tokens")
    reset_raw = headers.get("x-ratelimit-reset-tokens")
    remaining: int | None = None
    if remaining_raw:
        try:
            remaining = int(float(remaining_raw))
        except (TypeError, ValueError):
            remaining = None
    if remaining is not None and remaining < CLAUDE_LOW_TOKEN_THRESHOLD:
        wait_for = _parse_reset_wait_seconds(reset_raw)
        log(
            f"[5/9] Claude tokens low ({remaining}); waiting {int(wait_for)}s until token reset."
        )
    else:
        wait_for = CLAUDE_MIN_WAIT_SECONDS
    _NEXT_CLAUDE_ALLOWED_TS = max(_NEXT_CLAUDE_ALLOWED_TS, time.time() + wait_for)


def _claude_wait_if_needed() -> None:
    now = time.time()
    if _NEXT_CLAUDE_ALLOWED_TS > now:
        wait_for = int(_NEXT_CLAUDE_ALLOWED_TS - now)
        log(f"[5/9] Waiting {wait_for}s before next Claude call to avoid rate limits...")
        time.sleep(max(1, _NEXT_CLAUDE_ALLOWED_TS - now))


def claude_text_call(client: Any, prompt: str, *, max_tokens: int) -> str | None:
    _claude_wait_if_needed()
    headers: dict[str, str] = {}
    try:
        msg = None
        raw_client = getattr(client, "with_raw_response", None)
        if raw_client is not None and hasattr(raw_client, "messages"):
            raw_resp = raw_client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            if hasattr(raw_resp, "parse"):
                msg = raw_resp.parse()
            elif hasattr(raw_resp, "data"):
                msg = raw_resp.data
            hdr_src = getattr(raw_resp, "headers", None)
            if hdr_src is not None:
                for hk in ("x-ratelimit-remaining-tokens", "x-ratelimit-reset-tokens"):
                    try:
                        hv = hdr_src.get(hk)
                    except Exception:
                        hv = None
                    if hv is not None:
                        headers[hk] = str(hv)
        if msg is None:
            msg = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
    except Exception as exc:
        if _is_rate_limit_error(exc):
            raise ClaudeRateLimitError(str(exc)) from exc
        log_err(f"[5/9] Claude request failed: {exc}")
        return None
    _update_claude_wait_from_headers(headers)
    text_parts: list[str] = []
    for part in msg.content:
        if getattr(part, "type", None) == "text":
            text_parts.append(part.text)
    return "".join(text_parts).strip()


def _split_review_batches(reviews: list[dict[str, Any]], batch_size: int = REVIEW_BATCH_SIZE) -> list[list[dict[str, Any]]]:
    if batch_size <= 0:
        return [reviews]
    return [reviews[i : i + batch_size] for i in range(0, len(reviews), batch_size)]


def _validate_score_payload(parsed: dict[str, Any]) -> bool:
    if "total_score" not in parsed:
        return False
    try:
        total = float(parsed["total_score"])
    except (TypeError, ValueError):
        return False
    return 0 <= total <= 100


def _score_single_batch(client: Any, park_payload: dict[str, Any], batch_reviews: list[dict[str, Any]]) -> dict[str, Any]:
    structured_reviews = [
        {
            "source": r.get("source"),
            "date": r.get("date"),
            "rating": r.get("star_rating"),
            "text": r.get("review_text"),
        }
        for r in batch_reviews
        if isinstance(r, dict)
    ]

    # Snapshot prompt before token trimming — same construction as below, full review list only.
    pre_trim_reviews = list(structured_reviews)
    pre_trim_payload = dict(park_payload)
    pre_trim_payload["all_reviews_structured"] = pre_trim_reviews
    pre_trim_payload["all_reviews_text_list"] = [str(r.get("text") or "") for r in pre_trim_reviews]
    pre_trim_payload["all_reviews_text"] = "\n\n".join(pre_trim_payload["all_reviews_text_list"])
    pre_trim_context = (
        f"You have access to {len(pre_trim_reviews)} reviews from Google Maps spanning the last 3 years. "
        "Weight more recent reviews higher than older ones and prioritize family traveller signals.\n\n"
    )
    full_prompt_pre_trim = pre_trim_context + SCORING_PROMPT.replace(
        "[INSERT ALL PARK DATA INCLUDING ALL REVIEW TEXT]",
        json.dumps(pre_trim_payload, ensure_ascii=True),
    )
    pre_len = len(full_prompt_pre_trim)
    log(f"[5/9] Scoring prompt (pre-trim, before Claude): total_chars={pre_len}")
    log(f"[5/9] Scoring prompt (pre-trim) first 500 chars:\n{full_prompt_pre_trim[:500]}")
    log(f"[5/9] Scoring prompt (pre-trim) last 500 chars:\n{full_prompt_pre_trim[-500:]}")

    # Dynamic token estimator: scoring prompt chars/4 + review text chars/4 + fixed safety buffer.
    prompt_template_chars = len(SCORING_PROMPT)
    base_prompt_tokens = int(prompt_template_chars / TOKEN_ESTIMATE_CHARS_PER_TOKEN)

    def estimate_tokens(reviews: list[dict[str, Any]]) -> int:
        chars = 0
        for rv in reviews:
            chars += len(str(rv.get("text") or ""))
        review_tokens = int(chars / TOKEN_ESTIMATE_CHARS_PER_TOKEN)
        return base_prompt_tokens + review_tokens + TOKEN_ESTIMATE_BUFFER

    estimated_tokens = estimate_tokens(structured_reviews)
    if estimated_tokens > TOKEN_ESTIMATE_MAX:
        while structured_reviews and estimated_tokens > TOKEN_ESTIMATE_MAX:
            structured_reviews.pop()  # keep most recent reviews first, trim older tail
            estimated_tokens = estimate_tokens(structured_reviews)
        log(
            f"[5/9] Token guard applied: reduced batch to {len(structured_reviews)} reviews "
            f"(estimated tokens: {estimated_tokens})."
        )

    log(
        f"[5/9] Prompt chars={prompt_template_chars}, base_prompt_tokens≈{base_prompt_tokens}, "
        f"estimated_total_tokens≈{estimated_tokens}, reviews_in_batch={len(structured_reviews)}"
    )

    scoring_payload = dict(park_payload)
    scoring_payload["all_reviews_structured"] = structured_reviews
    scoring_payload["all_reviews_text_list"] = [str(r.get("text") or "") for r in structured_reviews]
    scoring_payload["all_reviews_text"] = "\n\n".join(scoring_payload["all_reviews_text_list"])
    scoring_context = (
        f"You have access to {len(structured_reviews)} reviews from Google Maps spanning the last 3 years. "
        "Weight more recent reviews higher than older ones and prioritize family traveller signals.\n\n"
    )
    full_prompt = scoring_context + SCORING_PROMPT.replace(
        "[INSERT ALL PARK DATA INCLUDING ALL REVIEW TEXT]",
        json.dumps(scoring_payload, ensure_ascii=True),
    )
    final_text = claude_text_call(client, full_prompt, max_tokens=4000)
    if not final_text:
        raise RuntimeError("Claude returned empty response text for scoring batch.")
    parsed = parse_score_fields_with_fallback(final_text)
    if not parsed:
        raise RuntimeError(f"Claude returned unparseable batch response: {final_text[:1200]}")
    if not _validate_score_payload(parsed):
        raise RuntimeError(f"Claude returned invalid batch score payload: {json.dumps(parsed)[:1200]}")
    log(f"[debug] Claude returned fields: {list(parsed.keys())}")
    return parsed


def _weighted_aggregate_batch_scores(batch_scores: list[tuple[dict[str, Any], int]]) -> dict[str, Any]:
    total_weight = sum(max(1, n) for _score, n in batch_scores)
    if total_weight <= 0:
        total_weight = 1
    numeric_keys = (
        "total_score",
        "entertainment_score",
        "nature_score",
        "site_size_score",
        "cleanliness_score",
        "value_score",
        "sentiment_score",
        "location_score",
        "pet_score",
    )
    out: dict[str, Any] = {}
    for key in numeric_keys:
        weighted_sum = 0.0
        for score, n in batch_scores:
            weighted_sum += float(score.get(key) or 0) * max(1, n)
        out[key] = round(weighted_sum / total_weight, 2)
    batches = [score for score, _n in batch_scores]
    # Preserve non-numeric fields from first batch
    for field in [
        "executive_summary",
        "rationale_top3",
        "rationale_honourable",
        "water_fun",
        "kids_play",
        "pet_detail",
        "best_for",
        "wifi_available",
        "pet_friendly",
        "key_phrases",
    ]:
        if field not in out and batches:
            val = batches[0].get(field)
            if val:
                out[field] = val
    return out


def _build_batch_themes(batch_scores: list[tuple[dict[str, Any], int]]) -> list[dict[str, Any]]:
    themes: list[dict[str, Any]] = []
    for i, (score, n_reviews) in enumerate(batch_scores, start=1):
        themes.append(
            {
                "batch_index": i,
                "review_count": n_reviews,
                "total_score": score.get("total_score"),
                "classification": score.get("classification"),
                "key_phrases": score.get("key_phrases") if isinstance(score.get("key_phrases"), list) else [],
                "watch_out": str(score.get("watch_out") or ""),
                "best_suited_for": str(score.get("best_suited_for") or ""),
                "water_fun": str(score.get("water_fun") or ""),
                "kids_play": str(score.get("kids_play") or ""),
                "pet_detail": str(score.get("pet_detail") or ""),
                "best_for": str(score.get("best_for") or ""),
            }
        )
    return themes


def _final_rationale_from_aggregates(
    client: Any,
    *,
    park_name: str,
    location: str,
    aggregated: dict[str, Any],
    batch_themes: list[dict[str, Any]],
) -> dict[str, Any]:
    prompt = (
        "You are finalizing a family holiday park assessment.\n"
        "Using the aggregated scores and batch themes below, return JSON only with fields:\n"
        "classification, rationale_top3, rationale_honourable, key_phrases, best_suited_for, watch_out, "
        "water_fun, kids_play, pet_detail, best_for, wifi_available, pet_friendly.\n"
        "wifi_available: respond with exactly \"yes\" or \"no\" based on review evidence of "
        "wifi/internet access at the park. If unclear respond \"unknown\".\n"
        "pet_friendly: respond with exactly \"yes\" or \"no\" based on review evidence of "
        "pets/dogs being allowed. If unclear respond \"unknown\".\n"
        "Keep classification aligned with score bands: Gold 80-100, Silver 65-79, Bronze 50-64, Not Listed below 50.\n\n"
        f"Park: {park_name}\nLocation: {location}\n"
        f"Aggregated scores: {json.dumps(aggregated, ensure_ascii=True)}\n"
        f"Batch themes: {json.dumps(batch_themes, ensure_ascii=True)}\n"
    )
    text = claude_text_call(client, prompt, max_tokens=1500)
    if not text:
        raise RuntimeError("Claude returned empty rationale response.")
    parsed = safe_json_parse(text)
    if not parsed:
        raise RuntimeError(f"Claude returned non-JSON rationale response: {text[:1200]}")
    return parsed


def score_with_claude(anthropic_key: str, park_payload: dict[str, Any]) -> dict[str, Any] | None:
    try:
        import anthropic
    except ImportError:
        log_err("[5/9] Missing anthropic package.")
        return None

    all_reviews = (
        park_payload.get("all_reviews_structured")
        if isinstance(park_payload.get("all_reviews_structured"), list)
        else []
    )
    reviews_sorted = sorted(
        [r for r in all_reviews if isinstance(r, dict)],
        key=lambda r: normalize_date(r.get("date")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    total_reviews = len(reviews_sorted)
    batches = _split_review_batches(reviews_sorted, REVIEW_BATCH_SIZE)
    log(f"[5/9] Total reviews scraped: {total_reviews}")
    log(f"[5/9] Number of Claude scoring batches: {len(batches)} (max {REVIEW_BATCH_SIZE} reviews each)")

    client = anthropic.Anthropic(api_key=anthropic_key)
    batch_scores: list[tuple[dict[str, Any], int]] = []
    for i, batch in enumerate(batches, start=1):
        batch_score = _score_single_batch(client, park_payload, batch)
        batch_scores.append((batch_score, len(batch)))
        log(
            f"[5/9] Batch {i}/{len(batches)} score: total={batch_score.get('total_score')} "
            f"(reviews={len(batch)})"
        )

    if not batch_scores:
        raise RuntimeError("No batch scores were produced.")

    aggregated = _weighted_aggregate_batch_scores(batch_scores)
    batch_themes = _build_batch_themes(batch_scores)
    rationale = _final_rationale_from_aggregates(
        client,
        park_name=str(park_payload.get("name") or ""),
        location=str(park_payload.get("location") or ""),
        aggregated=aggregated,
        batch_themes=batch_themes,
    )

    final_score = dict(aggregated)
    final_score["classification"] = str(rationale.get("classification") or aggregated.get("classification") or "")
    final_score["rationale_top3"] = rationale_field_to_prose(rationale.get("rationale_top3"))
    final_score["rationale_honourable"] = rationale_field_to_prose(rationale.get("rationale_honourable"))
    final_score["key_phrases"] = rationale.get("key_phrases") if isinstance(rationale.get("key_phrases"), list) else []
    final_score["best_suited_for"] = str(rationale.get("best_suited_for") or "")
    final_score["watch_out"] = str(rationale.get("watch_out") or "")
    final_score["water_fun"] = str(rationale.get("water_fun") or "")
    final_score["kids_play"] = str(rationale.get("kids_play") or "")
    final_score["pet_detail"] = str(rationale.get("pet_detail") or "Check directly with park")
    final_score["best_for"] = str(rationale.get("best_for") or "")
    final_score["wifi_available"] = str(rationale.get("wifi_available") or "unknown")
    final_score["pet_friendly"] = str(rationale.get("pet_friendly") or "unknown")
    log(f"[5/9] Final aggregated score: total={final_score.get('total_score')} class={final_score.get('classification')}")
    if not _validate_score_payload(final_score):
        raise RuntimeError(f"Final aggregated score payload invalid: {json.dumps(final_score)[:1200]}")
    return final_score


def score_with_claude_retry(anthropic_key: str, park_payload: dict[str, Any], park_name: str) -> dict[str, Any] | None:
    waits = [0, 30, 60, 120]
    for attempt in range(1, len(waits) + 1):
        wait_time = waits[attempt - 1]
        if wait_time > 0:
            log(f"[5/9] Retry attempt {attempt}/{len(waits)} for {park_name} in {wait_time}s...")
            time.sleep(wait_time)
        try:
            return score_with_claude(anthropic_key, park_payload)
        except Exception as exc:
            log_err(f"[5/9] Claude scoring error for {park_name} (attempt {attempt}/{len(waits)}): {exc!r}")
            if attempt >= len(waits):
                log_err(f"[5/9] Giving up on {park_name} after retries.")
                return None
    return None


def top_criterion(score: dict[str, Any]) -> str:
    criteria = [
        ("entertainment_score", "Entertainment"),
        ("nature_score", "Nature"),
        ("site_size_score", "Site Size"),
        ("cleanliness_score", "Cleanliness"),
        ("value_score", "Value"),
        ("sentiment_score", "Sentiment"),
        ("location_score", "Location"),
        ("pet_score", "Pet Friendly"),
    ]
    best_label = "N/A"
    best_value = -1.0
    for key, label in criteria:
        try:
            val = float(score.get(key, 0))
        except (TypeError, ValueError):
            continue
        if val > best_value:
            best_value = val
            best_label = label
    return best_label


def create_airtable_record(
    location: str,
    park: dict[str, Any],
    score: dict[str, Any],
    *,
    date_assessed: str,
) -> dict[str, Any]:
    def truncate_text(value: str, max_chars: int) -> str:
        if len(value) <= max_chars:
            return value
        return value[:max_chars]

    def as_text(value: Any) -> str:
        if value is None:
            return ""
        return str(value)

    def as_int_number(value: Any) -> int:
        if value is None:
            return 0
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return 0

    def as_decimal_number(value: Any) -> float:
        if value is None:
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    review_text = json.dumps(park.get("all_reviews_structured", []), ensure_ascii=False, indent=2)
    review_text = truncate_text(review_text, 50000)
    phrases = score.get("key_phrases") if isinstance(score.get("key_phrases"), list) else []
    lat = park.get("lat")
    lng = park.get("lng")
    coord_text = ""
    if lat is not None and lng is not None:
        coord_text = f"{lat}, {lng}"
    return {
        "fields": {
            "Park Name": as_text(park.get("name", "")),
            "Location": as_text(location),
            "Total Score": as_int_number(score.get("total_score")),
            "Classification": as_text(score.get("classification", "")),
            "Entertainment Score": as_int_number(score.get("entertainment_score")),
            "Nature Score": as_int_number(score.get("nature_score")),
            "Site Size Score": as_int_number(score.get("site_size_score")),
            "Cleanliness Score": as_int_number(score.get("cleanliness_score")),
            "Value Score": as_int_number(score.get("value_score")),
            "Sentiment Score": as_int_number(score.get("sentiment_score")),
            "Location Score": as_int_number(score.get("location_score")),
            "Pet Score": as_int_number(score.get("pet_score")),
            "Rationale Top 3": truncate_text(as_text(score.get("rationale_top3", "")), 5000),
            "Rationale Honourable": truncate_text(as_text(score.get("rationale_honourable", "")), 5000),
            "Key Phrases (as long text)": as_text("\n".join([str(p) for p in phrases])),
            "Best Suited For": as_text(score.get("best_suited_for", "")),
            "Watch Out": as_text(score.get("watch_out", "")),
            "Water Fun": as_text(score.get("water_fun", "")),
            "Kids Play": as_text(score.get("kids_play", "")),
            "Pet Detail": as_text(score.get("pet_detail", "Check directly with park")),
            "Best For": as_text(score.get("best_for", "")),
            "Website URL": as_text(park.get("website", "")),
            "Google Place ID": as_text(park.get("google_place_id", "")),
            "Google Rating": as_decimal_number(park.get("google_rating")),
            "Review Count": as_int_number(park.get("review_count")),
            "All Reviews Text (full raw text of all reviews collected — save everything)": as_text(review_text),
            "Data Sources": as_text(", ".join(park.get("data_sources", []))),
            "Photo URL": as_text(park.get("photo_url", "")),
            "Coordinates (lat lng as text)": as_text(coord_text),
            "Date Assessed": as_text(date_assessed),
        }
    }


def save_debug_review_file(
    *,
    review_data_dir: Path,
    park_name: str,
    location: str,
    date_assessed: str,
    google_count: int,
    tripadvisor_count: int,
    booking_count: int,
    all_reviews_structured: list[dict[str, Any]],
) -> Path:
    review_data_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "park_name": park_name,
        "location": location,
        "date_assessed": date_assessed,
        "review_count_by_source": {
            "google_maps": google_count,
            "tripadvisor": tripadvisor_count,
            "booking_com": booking_count,
            "overall_total": len(all_reviews_structured),
        },
        "reviews": all_reviews_structured,
    }
    park_slug = slugify(park_name)
    out_path = review_data_dir / f"{park_slug}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


def load_cached_review_file(review_data_dir: Path, park_name: str) -> dict[str, Any] | None:
    park_slug = slugify(park_name)
    cache_path = review_data_dir / f"{park_slug}.json"
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log_err(f"[4/9] Failed reading review cache {cache_path.name}: {exc}")
        return None
    if not isinstance(payload, dict):
        return None
    reviews = payload.get("reviews")
    if not isinstance(reviews, list):
        return None
    source_counts = payload.get("review_count_by_source")
    if not isinstance(source_counts, dict):
        source_counts = {}
    return {
        "reviews": [r for r in reviews if isinstance(r, dict)],
        "counts": {
            "google_maps": int(source_counts.get("google_maps", 0) or 0),
            "tripadvisor": int(source_counts.get("tripadvisor", 0) or 0),
            "booking_com": int(source_counts.get("booking_com", 0) or 0),
            "overall_total": int(source_counts.get("overall_total", 0) or 0),
        },
        "cache_path": cache_path,
    }


def delete_review_cache_file(review_data_dir: Path, park_name: str) -> None:
    park_slug = slugify(park_name)
    cache_path = review_data_dir / f"{park_slug}.json"
    if not cache_path.exists():
        return
    try:
        cache_path.unlink()
        log(f"[3/9] Deleted empty review cache for {park_name}: {cache_path.name}")
    except OSError as exc:
        log_err(f"[3/9] Failed to delete empty review cache for {park_name}: {exc}")


def airtable_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def ensure_airtable_parks_table(token: str, base_id: str) -> bool:
    meta_url = f"https://api.airtable.com/v0/meta/bases/{base_id}/tables"
    headers = airtable_headers(token)
    try:
        resp = requests.get(meta_url, headers=headers, timeout=45)
    except Exception as exc:
        log_err(f"[6/9] Airtable metadata check failed: {exc}")
        return False

    if resp.status_code != 200:
        log_err(f"[6/9] Airtable metadata endpoint error: {resp.status_code} {resp.text[:300]}")
        return False

    payload = resp.json()
    tables = payload.get("tables") if isinstance(payload, dict) else None
    if isinstance(tables, list):
        for table in tables:
            if isinstance(table, dict) and str(table.get("name", "")).strip().lower() == "parks":
                return True

    create_payload = {
        "name": "Parks",
        "description": "Scored park records",
        "fields": [
            {"name": "Park Name", "type": "singleLineText"},
            {"name": "Location", "type": "singleLineText"},
            {"name": "Total Score", "type": "number", "options": {"precision": 0}},
            {"name": "Classification", "type": "singleLineText"},
            {"name": "Entertainment Score", "type": "number", "options": {"precision": 0}},
            {"name": "Nature Score", "type": "number", "options": {"precision": 0}},
            {"name": "Site Size Score", "type": "number", "options": {"precision": 0}},
            {"name": "Cleanliness Score", "type": "number", "options": {"precision": 0}},
            {"name": "Value Score", "type": "number", "options": {"precision": 0}},
            {"name": "Sentiment Score", "type": "number", "options": {"precision": 0}},
            {"name": "Location Score", "type": "number", "options": {"precision": 0}},
            {"name": "Pet Score", "type": "number", "options": {"precision": 0}},
            {"name": "Rationale Top 3", "type": "multilineText"},
            {"name": "Rationale Honourable", "type": "multilineText"},
            {"name": "Key Phrases (as long text)", "type": "multilineText"},
            {"name": "Best Suited For", "type": "multilineText"},
            {"name": "Watch Out", "type": "multilineText"},
            {"name": "Water Fun", "type": "singleLineText"},
            {"name": "Kids Play", "type": "singleLineText"},
            {"name": "Pet Detail", "type": "singleLineText"},
            {"name": "Best For", "type": "multilineText"},
            {"name": "Website URL", "type": "url"},
            {"name": "Google Place ID", "type": "singleLineText"},
            {"name": "Google Rating", "type": "number", "options": {"precision": 1}},
            {"name": "Review Count", "type": "number", "options": {"precision": 0}},
            {
                "name": "All Reviews Text (full raw text of all reviews collected — save everything)",
                "type": "multilineText",
            },
            {"name": "Data Sources", "type": "multilineText"},
            {"name": "Photo URL", "type": "url"},
            {"name": "Coordinates (lat lng as text)", "type": "singleLineText"},
            {"name": "Date Assessed", "type": "singleLineText"},
        ],
    }
    try:
        create_resp = requests.post(meta_url, headers=headers, json=create_payload, timeout=45)
    except Exception as exc:
        log_err(f"[6/9] Airtable table create request failed: {exc}")
        return False
    if create_resp.status_code in (200, 201):
        log("[6/9] Created missing Airtable table: Parks")
        return True
    log_err(
        f"[6/9] Failed to create Airtable table Parks: "
        f"{create_resp.status_code} {create_resp.text[:500]}"
    )
    return False


def save_to_airtable(token: str, base_id: str, record: dict[str, Any]) -> None:
    url = f"https://api.airtable.com/v0/{base_id}/Parks"
    headers = airtable_headers(token)
    resp = requests.post(url, headers=headers, json=record, timeout=45)
    resp.raise_for_status()


def main() -> int:
    load_dotenv()
    args = parse_args()
    location = args.location.strip()
    if not location:
        log_err("Location argument is required.")
        return 1

    log(f"[0/9] SCORING_PROMPT template character length (base scoring instructions): {len(SCORING_PROMPT)}")

    apify_token = os.getenv("APIFY_TOKEN", "").strip()
    google_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    airtable_token = os.getenv("AIRTABLE_TOKEN", "").strip()
    airtable_base = os.getenv("AIRTABLE_BASE_ID", "").strip()

    if not apify_token:
        log_err("Missing APIFY_TOKEN.")
        return 1
    if not anthropic_key:
        log_err("Missing ANTHROPIC_API_KEY.")
        return 1

    project_dir = Path(__file__).resolve().parent
    slug = slugify(location)
    loc_dir = get_location_dir(project_dir, location)
    init_location_dir(loc_dir)

    scores_path = loc_dir / "scores.json"
    failed_parks_path = project_dir / "failed-scoring-parks.txt"
    progress_path = project_dir / f"{slug}-progress.json"
    assessed_date = str(date.today())
    raw_parks_cache_file = loc_dir / "raw-parks.json"
    approved_parks_path = loc_dir / "approved-parks.json"
    whitelist_path = loc_dir / "whitelist.json"
    reviews_dir = loc_dir / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    completed_park_names: set[str] = set()
    failed_scoring_parks: list[str] = []
    existing_scores: list[dict[str, Any]] = []

    if scores_path.exists():
        try:
            loaded = json.loads(scores_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                existing_scores = [x for x in loaded if isinstance(x, dict)]
            for item in existing_scores:
                nm = str(item.get("park_name") or "").strip()
                if nm:
                    completed_park_names.add(nm)
            if args.resume:
                log(
                    f"[0/9] Resume enabled: found {len(completed_park_names)} already-scored parks in {scores_path.name}."
                )
        except Exception as exc:
            log_err(f"[0/9] Failed reading existing scores file: {exc}")

    rescore_names = parse_rescore_park_names(args.rescore)
    if rescore_names:
        before_n = len(existing_scores)
        removed_from_file: list[str] = []
        kept_scores: list[dict[str, Any]] = []
        for row in existing_scores:
            if not isinstance(row, dict):
                continue
            pn = str(row.get("park_name") or "").strip()
            if pn in rescore_names:
                removed_from_file.append(pn)
            else:
                kept_scores.append(row)
        completed_park_names -= rescore_names
        not_in_file = sorted(rescore_names - set(removed_from_file))
        if args.fresh_copy:
            existing_scores = kept_scores
            log(
                f"[0/9] --rescore + --fresh-copy: removed {len(removed_from_file)} of {before_n} score row(s) "
                f"from memory; targets={len(rescore_names)}."
            )
            if removed_from_file:
                log(f"[0/9] --rescore: will replace after successful scoring: {', '.join(removed_from_file)}")
        else:
            log(
                f"[0/9] --rescore (copy preserved): forcing re-score for {len(rescore_names)} park(s) "
                f"without removing existing rationale fields from {scores_path.name}."
            )
        if not_in_file:
            log(
                f"[0/9] --rescore: no matching score entry (check spelling vs park_name in JSON): "
                f"{', '.join(not_in_file)}"
            )

    if args.fresh:
        for cache_path in (raw_parks_cache_file, approved_parks_path):
            if cache_path.exists():
                try:
                    cache_path.unlink()
                    log(f"[0/9] --fresh removed cache file: {cache_path.name}")
                except OSError as exc:
                    log_err(f"[0/9] --fresh could not remove {cache_path.name}: {exc}")

    park_whitelist = load_park_whitelist(whitelist_path)

    airtable_ready = False
    if airtable_token and airtable_base:
        airtable_ready = ensure_airtable_parks_table(airtable_token, airtable_base)
        if not airtable_ready:
            log_err("[6/9] Airtable table verification failed; continuing without Airtable saves.")

    raw_cached = None
    raw_source_label = "fresh Apify scrape"
    if rescore_names:
        if raw_parks_cache_file.exists():
            raw_cached = load_cached_raw_parks(raw_parks_cache_file)
            if raw_cached is not None:
                raw_source_label = "raw parks cache"
        if raw_cached is None:
            raw_cached = scrape_parks_with_apify(apify_token, location)
            try:
                save_cached_raw_parks(raw_parks_cache_file, raw_cached)
            except Exception as exc:
                log_err(f"[1/9] Failed to save raw parks cache {raw_parks_cache_file.name}: {exc}")
    else:
        raw_cached = load_cached_raw_parks(raw_parks_cache_file)
        if raw_cached is None:
            raw_cached = scrape_parks_with_apify(apify_token, location)
            try:
                save_cached_raw_parks(raw_parks_cache_file, raw_cached)
            except Exception as exc:
                log_err(f"[1/9] Failed to save raw parks cache {raw_parks_cache_file.name}: {exc}")
        else:
            raw_source_label = "raw parks cache"
    parks = finalize_raw_park_rows(raw_cached, source_label=raw_source_label)
    if not parks:
        log_err("No eligible parks found.")
        return 1

    approved_loaded = False
    approved_park_names: list[str] = []
    if (not args.fresh) and approved_parks_path.exists():
        approved, approved_at = load_approved_parks(approved_parks_path)
        if approved is not None:
            approved_park_names = [
                str(p.get("title") or p.get("name") or "").strip()
                for p in approved
                if isinstance(p, dict) and str(p.get("title") or p.get("name") or "").strip()
            ]
            parks = approved
            approved_loaded = True
            date_text = approved_at or "unknown date"
            log(f"[CHECKPOINT] Loaded approved parks list: {len(parks)} parks approved on {date_text}")
        else:
            log_err("[CHECKPOINT] Approved parks file exists but could not be loaded; falling back to checkpoint.")

    if not approved_loaded:
        if args.auto:
            log("[CHECKPOINT] --auto enabled; skipping manual review checkpoint.")
        elif not sys.stdin.isatty():
            log("[CHECKPOINT] Non-interactive session detected; skipping manual review checkpoint.")
        else:
            approved_selection = run_manual_review_checkpoint(
                parks,
                location=location,
                slug=slug,
                approved_path=approved_parks_path,
            )
            if approved_selection is None:
                return 0
            parks = approved_selection

    enriched_and_scored: list[dict[str, Any]] = []
    review_scrape_calls = 0
    active_parks = [
        p
        for p in parks
        if not (args.resume and str(p.get("title") or p.get("name") or "").strip() in completed_park_names)
    ]
    total_to_process = len(active_parks)
    completed_now = 0
    run_start = time.monotonic()
    for idx, park in enumerate(active_parks, start=1):
        name = str(park.get("title") or park.get("name") or f"Park {idx}")
        log(f"[2-6/9] Processing {idx}/{total_to_process}: {name}")

        data_sources = ["Apify Google Maps"]
        google_details: dict[str, Any] | None = None
        google_place_id = extract_place_id(park)

        places_query = f"{name} {location}".strip()
        if google_key:
            textsearch = google_places_text_search(google_key, places_query)
            if textsearch is None:
                log(f"[2/9] Google Places type check for {name}: kept (no result from Text Search)")
            else:
                types_raw = textsearch.get("types")
                types_list = types_raw if isinstance(types_raw, list) else []
                park_name = name
                name_lower = park_name.strip().lower()
                in_approved = approved_park_names and name_lower in {
                    a.strip().lower() for a in approved_park_names
                }
                if not in_approved and not passes_google_type_check(park_name, types_list, park_whitelist):
                    log(
                        f"[2/9] Google Places type check for {park_name}: excluded "
                        "(types do not include campground/rv_park/caravan_park)"
                    )
                    continue
                elif in_approved:
                    log(f"[2/9] Google Places type check for {park_name}: kept (manually approved)")
        else:
            log("[2/9] GOOGLE_MAPS_API_KEY not set; skipping Google Places type check (benefit of doubt).")

        if google_key and google_place_id:
            google_details = google_place_details(google_key, google_place_id)
            if google_details:
                data_sources.append("Google Places")
        elif google_key:
            log_err(f"[2/9] No Google place ID found for {name}, using Apify-only base.")

        website = ""
        photo_url = ""
        lat = None
        lng = None
        rating = park.get("totalScore") or park.get("rating")
        review_count = park.get("reviewsCount") or park.get("reviews")
        if google_details:
            website = str(google_details.get("website") or "")
            photo_url = photo_url_from_details(google_details, google_key)
            rating = google_details.get("rating", rating)
            review_count = google_details.get("user_ratings_total", review_count)
            geometry = google_details.get("geometry")
            if isinstance(geometry, dict) and isinstance(geometry.get("location"), dict):
                try:
                    lat = float(geometry["location"]["lat"])
                    lng = float(geometry["location"]["lng"])
                except Exception:
                    lat = lng = None
        if not website:
            website = str(park.get("website") or "")

        rc = _coerce_review_count(review_count)
        if rc is not None:
            if rc < MIN_GOOGLE_REVIEWS_FOR_SCORING:
                log(
                    f"[2/9] Excluded '{name}': insufficient Google review count "
                    f"({rc} < {MIN_GOOGLE_REVIEWS_FOR_SCORING}; need at least "
                    f"{MIN_GOOGLE_REVIEWS_FOR_SCORING} for reliable scoring)"
                )
                continue
            log(
                f"[2/9] Review count threshold '{name}': {rc} >= "
                f"{MIN_GOOGLE_REVIEWS_FOR_SCORING} (proceed)"
            )
        else:
            log(
                f"[2/9] Review count threshold '{name}': unknown (Places/Apify had no count) — "
                "benefit of doubt, proceed"
            )

        cached_review_blob = load_cached_review_file(reviews_dir, name)
        if cached_review_blob is not None:
            all_reviews_structured = cached_review_blob["reviews"]
            google_reviews = [
                r
                for r in all_reviews_structured
                if str(r.get("source") or "").strip().lower() == "google maps"
            ]
            tripadvisor_reviews = []
            booking_reviews = []
            log(
                f"[3/9] Loaded cached reviews for {name}: "
                f"Google={len(google_reviews)}, Total={len(all_reviews_structured)}"
            )
            if len(all_reviews_structured) == 0:
                delete_review_cache_file(reviews_dir, name)
        else:
            if review_scrape_calls > 0:
                log("[3/9] Waiting 30 seconds before next Apify review scrape call...")
                time.sleep(30)
            google_reviews = scrape_google_maps_reviews(apify_token, name, location)
            review_scrape_calls += 1
            tripadvisor_reviews = []
            booking_reviews = []
            all_reviews_structured = google_reviews + tripadvisor_reviews + booking_reviews
            if len(all_reviews_structured) == 0:
                delete_review_cache_file(reviews_dir, name)
            else:
                try:
                    save_debug_review_file(
                        review_data_dir=reviews_dir,
                        park_name=name,
                        location=location,
                        date_assessed=assessed_date,
                        google_count=len(google_reviews),
                        tripadvisor_count=0,
                        booking_count=0,
                        all_reviews_structured=all_reviews_structured,
                    )
                    log(f"[3/9] Saved review cache for {name}.")
                except Exception as exc:
                    log_err(f"[3/9] Failed to save review cache for {name}: {exc}")
        tripadvisor_reviews: list[dict[str, Any]] = []
        booking_reviews: list[dict[str, Any]] = []
        if google_reviews:
            data_sources.append("Google Maps Reviews (Apify)")
        all_reviews_text_list = [str(r.get("review_text") or "") for r in all_reviews_structured if str(r.get("review_text") or "").strip()]
        family_tripadvisor_count = sum(
            1
            for r in tripadvisor_reviews
            if "family" in str(r.get("reviewer_type") or "").lower()
        )
        log(
            f"[4/9] Review counts for {name}: "
            f"Google={len(google_reviews)}, TripAdvisor={len(tripadvisor_reviews)} "
            f"(family-tagged={family_tripadvisor_count}), Booking={len(booking_reviews)}, "
            f"Total={len(all_reviews_structured)}"
        )
        if args.debug_reviews:
            try:
                debug_path = save_debug_review_file(
                    review_data_dir=reviews_dir,
                    park_name=name,
                    location=location,
                    date_assessed=assessed_date,
                    google_count=len(google_reviews),
                    tripadvisor_count=len(tripadvisor_reviews),
                    booking_count=len(booking_reviews),
                    all_reviews_structured=all_reviews_structured,
                )
                log(f"[4/9] Review debug file saved: {debug_path.name}")
            except Exception as exc:
                log_err(f"[4/9] Failed to write review debug file for {name}: {exc}")
        park_payload = {
            "name": name,
            "location": location,
            "address": str(park.get("address") or park.get("formatted_address") or ""),
            "website": website,
            "google_place_id": google_place_id,
            "google_rating": rating,
            "review_count": review_count,
            "lat": lat,
            "lng": lng,
            "photo_url": photo_url,
            "apify_raw": park,
            "google_details": google_details or {},
            "google_maps_reviews": google_reviews,
            "tripadvisor_reviews": tripadvisor_reviews,
            "booking_reviews": booking_reviews,
            "all_reviews_structured": all_reviews_structured,
            "all_reviews_text_list": all_reviews_text_list,
            "all_reviews_text": "\n\n".join(all_reviews_text_list),
            "data_sources": data_sources,
        }

        score = score_with_claude_retry(anthropic_key, park_payload, name)
        if not score:
            log_err(f"[5/9] Claude scoring failed for {name}; skipping park.")
            failed_scoring_parks.append(name)
            continue

        scored_data = score
        log(f"[debug] Claude returned fields: {list(scored_data.keys())}")
        log(f"[debug] executive_summary value: {str(scored_data.get('executive_summary', 'MISSING'))[:100]}")
        executive_summary = scored_data.get("executive_summary") or ""
        if executive_summary:
            save_executive_summary(loc_dir, name, executive_summary)

        combined = {**park_payload, "score": score}
        enriched_and_scored.append(combined)
        completed_now += 1
        remaining = max(0, total_to_process - completed_now)
        elapsed_sec = max(1.0, time.monotonic() - run_start)
        avg_sec = elapsed_sec / max(1, completed_now)
        eta_minutes = (avg_sec * remaining) / 60.0
        try:
            save_progress_file(
                progress_path,
                parks_completed=completed_now,
                parks_remaining=remaining,
                estimated_minutes_to_completion=eta_minutes,
            )
        except Exception as exc:
            log_err(f"[7/9] Failed writing progress file: {exc}")
        if completed_now % 5 == 0:
            log(
                f"[7/9] Progress: completed={completed_now}, remaining={remaining}, "
                f"eta≈{eta_minutes:.1f} minutes"
            )
        if AIRTABLE_ENABLED and airtable_token and airtable_base and airtable_ready:
            try:
                record = create_airtable_record(
                    location,
                    park_payload,
                    score,
                    date_assessed=assessed_date,
                )
                save_to_airtable(airtable_token, airtable_base, record)
                log(f"[6/9] Airtable saved: {name}")
            except Exception as exc:
                log_err(f"Airtable save failed for {name}: {exc} (continuing)")

    if failed_scoring_parks:
        try:
            failed_parks_path.write_text(
                "\n".join(failed_scoring_parks) + "\n",
                encoding="utf-8",
            )
            log(f"[5/9] Saved failed parks list: {failed_parks_path.name}")
        except Exception as exc:
            log_err(f"[5/9] Failed to save failed parks list: {exc}")

    if not enriched_and_scored and not existing_scores:
        log_err("No parks were scored successfully.")
        return 1

    ranked = sorted(
        enriched_and_scored,
        key=lambda r: float(r.get("score", {}).get("total_score", 0) or 0),
        reverse=True,
    )

    summary_rows_new: list[dict[str, Any]] = []
    if ranked:
        log("[7/9] Ranked summary (newly scored parks this run)")
        print("-" * 95)
        print(f"{'Park':45} {'Score':>6} {'Rank':>5} {'Top Criterion':>20}")
        print("-" * 95)
        for rank_pos, row in enumerate(ranked, start=1):
            score = row["score"]
            apify_raw = row.get("apify_raw") if isinstance(row.get("apify_raw"), dict) else {}
            google_details = row.get("google_details") if isinstance(row.get("google_details"), dict) else {}
            places_data = {**apify_raw, **google_details}
            summary = {
                "park_name": row.get("name"),
                "total_score": score.get("total_score"),
                "classification": score.get("classification"),
                "top_scoring_criteria": top_criterion(score),
                "rationale_top3": score.get("rationale_top3"),
                "rationale_honourable": score.get("rationale_honourable"),
                "water_fun": score.get("water_fun"),
                "kids_play": score.get("kids_play"),
                "pet_detail": score.get("pet_detail"),
                "best_for": score.get("best_for"),
                "wifi_available": score.get("wifi_available"),
                "pet_friendly": score.get("pet_friendly"),
                "executive_summary": score.get("executive_summary"),
                "google_rating": places_data.get("rating") or places_data.get("totalScore") or row.get("google_rating"),
                "review_count": (
                    places_data.get("reviewsCount")
                    or places_data.get("reviews_count")
                    or row.get("review_count")
                ),
                "website": str(row.get("website") or ""),
                "lat": row.get("lat"),
                "lng": row.get("lng"),
            }
            summary_rows_new.append(summary)
            print(
                f"{str(summary['park_name'])[:45]:45} "
                f"{str(summary['total_score']):>6} "
                f"{rank_pos:>5} "
                f"{str(summary['top_scoring_criteria'])[:20]:>20}"
            )
        print("-" * 95)
    merged_scores = merge_scores(
        existing_scores,
        summary_rows_new,
        preserve_existing_copy=not args.fresh_copy,
    )
    apply_rank_classifications(merged_scores)
    tmp_sp = scores_path.with_suffix(".tmp")
    tmp_sp.write_text(json.dumps(merged_scores, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_sp, scores_path)
    log(
        f"[7/9] Saved merged scores JSON: {scores_path.name} "
        f"({len(existing_scores)} existing + {len(summary_rows_new)} new = {len(merged_scores)} total)"
    )

    log("[8/9] Top 3 is derived at page generation time from scores (see generate_page.py).")
    log("[9/9] Complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
