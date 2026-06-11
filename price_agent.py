#!/usr/bin/env python3
"""
Collect powered site prices for approved parks and write locations/.../prices.json.

Usage:
  python price_agent.py --slug apollo-bay-victoria
  python price_agent.py --state VIC
  python price_agent.py --missing-only
  python price_agent.py --force
  python price_agent.py --limit 10
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import unescape
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

PROJECT_DIR = Path(__file__).resolve().parent
REPORT_PATH = PROJECT_DIR / "price-report.md"

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

STATE_NAMES = {
    "QLD": "queensland",
    "NSW": "new-south-wales",
    "VIC": "victoria",
    "SA": "south-australia",
    "WA": "western-australia",
    "TAS": "tasmania",
    "NT": "northern-territory",
    "ACT": "act",
}

PRICE_NOTE = (
    "Powered site for 2 adults. Children and peak dates may cost extra."
)

BLOCKED_ENGINE_PATTERNS = [
    r"newbook\.io",
    r"rmscloud\.com",
    r"resbook\.com",
    r"bookme\.com\.au",
    r"seekom\.com",
    r"ibexres\.com",
    r"checkfront\.com",
    r"booking\.bugsoftware\.com\.au",
    r"rentlever\.com",
    r"data-rms-",
    r"data-newbook-",
    r"id=[\"']booking-engine",
    r"class=[\"'][^\"']*booking-widget",
]

CLOUDFLARE_PATTERNS = [
    r"cf-browser-verification",
    r"challenge-platform",
    r"/cdn-cgi/challenge",
    r"Attention Required! \| Cloudflare",
    r"Just a moment\.\.\.",
    r"Checking your browser before accessing",
    r"cloudflare-ray",
]

JS_RENDERED_PATTERNS = [
    r'__NEXT_DATA__',
    r'data-reactroot',
    r'ng-version=',
    r'id=["\']__nuxt',
    r'id=["\'](?:root|app)["\']',
    r'enable javascript',
    r'javascript (?:is )?required',
    r'you need to enable javascript',
]

FAILURE_REASONS = (
    "No website",
    "Website unreachable",
    "Booking engine detected",
    "JavaScript rendered page",
    "No powered site found",
    "No rate found",
    "Rate calendar blocked",
    "Cloudflare protection",
    "Timeout",
)

SUMMARY_LABELS = {
    "Booking engine detected": "Booking engines",
    "JavaScript rendered page": "JavaScript pages",
    "Cloudflare protection": "Cloudflare",
    "No website": "No website",
    "No powered site found": "No powered site",
    "No rate found": "No rate found",
    "Rate calendar blocked": "Rate calendar blocked",
    "Website unreachable": "Website unreachable",
    "Timeout": "Timeout",
}

POWERED_POSITIVE = re.compile(
    r"\b(powered\s+site|powered\s+sites|power\s+site|ensuite\s+site|"
    r"caravan\s+site|motorhome\s+site|rv\s+site|drive[- ]through|"
    r"powered\s+camping|powered\s+van)\b",
    re.I,
)

CABIN_NEGATIVE = re.compile(
    r"\b(cabin|villa|chalet|glamping|tent\s+only|unpowered|studio|bungalow|"
    r"apartment|lodge|unit)\b",
    re.I,
)

PRICE_RE = re.compile(
    r"(?:from\s+)?\$\s*(\d{2,4})(?:\s*/\s*night|\s+per\s+night|\s+pn\b)?",
    re.I,
)

RATES_LINK_RE = re.compile(
    r'href=["\']([^"\']*(?:rate|price|tariff|fee|book|camp|site|stay)[^"\']*)["\']',
    re.I,
)

# Approximate Australian school holiday windows (all states) — avoid if possible.
SCHOOL_HOLIDAY_RANGES_2026: list[tuple[date, date]] = [
    (date(2026, 4, 4), date(2026, 4, 20)),
    (date(2026, 6, 27), date(2026, 7, 12)),
    (date(2026, 9, 19), date(2026, 10, 4)),
    (date(2026, 12, 19), date(2027, 1, 26)),
]


DEBUG_MODE = False

DIRECT_WEBSITE_FIELDS: tuple[str, ...] = (
    "website",
    "websiteUrl",
    "website_url",
    "officialWebsite",
    "official_website",
    "booking_url",
    "bookingUrl",
)

WEBSITE_STRIP_WORDS: tuple[str, ...] = (
    "holiday park",
    "tourist park",
    "caravan park",
    "campground",
    "resort",
    "family",
    "big4",
    "ingenia",
    "discovery",
    "nrma",
    "tasman",
)

REJECTED_WEBSITE_DOMAINS: tuple[str, ...] = (
    "google.com",
    "google.com.au",
    "maps.google",
    "duckduckgo.com",
    "bing.com",
    "tripadvisor.",
    "booking.com",
    "expedia.",
    "agoda.",
    "wikicamps.",
    "yelp.",
    "yellowpages.",
    "wotif.",
    "airbnb.",
    "hotels.com",
    "trivago.",
    "campermate.",
    "hipcamp.",
    "park4night.",
    "caravanparkreviews.",
)

FACEBOOK_DOMAINS: tuple[str, ...] = (
    "facebook.com",
    "fb.com",
    "m.facebook.com",
)

PREFERRED_OPERATOR_DOMAINS: tuple[str, ...] = (
    "big4.com.au",
    "ingeniaparks.com.au",
    "ingeniaholidays.com.au",
    "discoveryparks.com.au",
    "nrmaparksandresorts.com.au",
    "reflectionsholidayparks.com.au",
    "tasmanholidayparks.com.au",
    "gdayparks.com.au",
    "greatoceanroadparks.com.au",
)

DIRECTORY_URL_PATTERNS: tuple[str, ...] = (
    r"/blog/",
    r"/reviews?/",
    r"directory",
    r"wikicamps",
    r"campermate",
    r"tripadvisor",
)

DDG_RESULT_LINK_RE = re.compile(
    r'class="result__a"[^>]+href="([^"]+)"',
    re.I,
)


@dataclass
class ParkJob:
    name: str
    approved_record: dict[str, Any] | None = None
    score_record: dict[str, Any] | None = None
    master_record: dict[str, Any] | None = None


@dataclass
class ParkTarget:
    name: str
    website: str = ""
    google_maps_url: str = ""


@dataclass
class PriceResult:
    display: str = "—"
    price: float | None = None
    source_url: str = ""
    confidence: str = "missing"
    blocked: bool = False
    failure_reason: str = ""


@dataclass
class ScrapeState:
    timeout: bool = False
    unreachable: bool = False
    cloudflare: bool = False
    js_rendered: bool = False
    booking_engine: bool = False
    calendar_blocked: bool = False
    saw_powered_mention: bool = False
    saw_price_number: bool = False
    pages_fetched: int = 0


@dataclass
class RunReport:
    locations_checked: list[str] = field(default_factory=list)
    parks_checked: int = 0
    prices_found: list[str] = field(default_factory=list)
    prices_missing: list[str] = field(default_factory=list)
    blocked_engines: list[str] = field(default_factory=list)
    manual_follow_up: list[str] = field(default_factory=list)
    failure_counts: dict[str, int] = field(default_factory=dict)
    failures_by_park: list[str] = field(default_factory=list)


def log(msg: str) -> None:
    print(msg, flush=True)


def debug_log(msg: str) -> None:
    if DEBUG_MODE:
        log(msg)


def is_google_maps_url(url: str) -> bool:
    u = str(url or "").strip().lower()
    if not u:
        return False
    return any(
        marker in u
        for marker in (
            "google.com/maps",
            "maps.google",
            "place_id",
            "query_place_id",
            "/search/",
        )
    )


def normalize_park_name_for_website(name: str) -> str:
    s = str(name or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    for word in WEBSITE_STRIP_WORDS:
        s = s.replace(word, " ")
    return re.sub(r"\s+", " ", s).strip()


def extract_direct_website(record: dict[str, Any]) -> str:
    if not isinstance(record, dict):
        return ""
    for field in DIRECT_WEBSITE_FIELDS:
        raw = record.get(field)
        if raw is None:
            continue
        url = str(raw).strip()
        if not url or url in {"—", "-"} or is_google_maps_url(url):
            continue
        return url
    return ""


def extract_google_maps_url(record: dict[str, Any]) -> str:
    if not isinstance(record, dict):
        return ""
    for field in ("url", "google_maps_url", "maps_url"):
        url = str(record.get(field) or "").strip()
        if url and is_google_maps_url(url):
            return url
    return ""


def parse_website_entry_value(value: Any) -> str:
    if isinstance(value, str):
        url = value.strip()
        if (
            url
            and url not in {"—", "-"}
            and not is_google_maps_url(url)
            and not is_generic_operator_home(url)
        ):
            return url
        return ""
    if isinstance(value, dict):
        url = str(value.get("website") or "").strip()
        if (
            url
            and url not in {"—", "-"}
            and not is_google_maps_url(url)
            and not is_generic_operator_home(url)
        ):
            return url
    return ""


def load_websites_json_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def save_websites_json_store(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def lookup_website_in_store(
    park_name: str, websites_store: dict[str, Any]
) -> tuple[str, str]:
    """Return (url, match_kind) where match_kind is 'exact', 'normalised', or ''."""
    exact = parse_website_entry_value(websites_store.get(park_name))
    if exact:
        return exact, "exact"

    norm_name = normalize_park_name_for_website(park_name)
    if not norm_name:
        return "", ""

    for key, value in websites_store.items():
        url = parse_website_entry_value(value)
        if not url:
            continue
        if normalize_park_name_for_website(str(key)) == norm_name:
            return url, "normalised"
    return "", ""


def has_stored_website(park_name: str, websites_store: dict[str, Any]) -> bool:
    url, _ = lookup_website_in_store(park_name, websites_store)
    return bool(url)


def is_rejected_website_url(url: str, *, allow_facebook: bool = False) -> bool:
    if not url or is_google_maps_url(url):
        return True
    host = urllib.parse.urlparse(url).netloc.lower()
    if not host:
        return True
    for domain in REJECTED_WEBSITE_DOMAINS:
        if domain in host:
            return True
    if not allow_facebook:
        for domain in FACEBOOK_DOMAINS:
            if domain in host:
                return True
    for pattern in DIRECTORY_URL_PATTERNS:
        if re.search(pattern, url, re.I):
            return True
    return False


def score_website_candidate(url: str, park_name: str) -> int:
    if is_rejected_website_url(url):
        return -1000
    host = urllib.parse.urlparse(url).netloc.lower()
    path = urllib.parse.urlparse(url).path.lower()
    score = 0

    for operator in PREFERRED_OPERATOR_DOMAINS:
        if operator in host:
            score += 90

    if ".gov.au" in host:
        score += 75

    if host.endswith(".com.au"):
        score += 12

    tokens = [
        t
        for t in normalize_park_name_for_website(park_name).split()
        if len(t) > 2
    ]
    blob = f"{host} {path}"
    for token in tokens:
        if token in blob:
            score += 18

    if any(word in blob for word in ("caravan", "camping", "holiday", "tourist", "park")):
        score += 8

    if path in {"", "/"}:
        score -= 5

    slug = slugify_park_name(park_name)
    if slug and path.rstrip("/").endswith(slug):
        score += 35

    return score


def decode_ddg_redirect(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href:
        parsed = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(parsed.query)
        for key in ("uddg", "u"):
            if key in qs and qs[key]:
                return urllib.parse.unquote(qs[key][0])
    return unescape(href)


def fetch_search_result_urls(query: str, *, max_results: int = 12) -> list[str]:
    search_url = (
        "https://html.duckduckgo.com/html/?q="
        + urllib.parse.quote_plus(query)
    )
    req = urllib.request.Request(
        search_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read(500_000).decode("utf-8", errors="replace")
    except Exception:
        return []

    urls: list[str] = []
    seen: set[str] = set()
    for match in DDG_RESULT_LINK_RE.finditer(html):
        href = decode_ddg_redirect(match.group(1))
        if not href.startswith("http"):
            continue
        if href in seen:
            continue
        seen.add(href)
        urls.append(href)
        if len(urls) >= max_results:
            break
    return urls


def search_website_online(park_name: str, location_name: str) -> str:
    queries: list[str] = []
    name_lower = park_name.lower()

    if "big4" in name_lower:
        queries.append(f"site:big4.com.au {park_name}")
    if "ingenia" in name_lower:
        queries.append(f"site:ingeniaparks.com.au {park_name}")
        queries.append(f"site:ingeniaholidays.com.au {park_name}")
    if "discovery" in name_lower:
        queries.append(f"site:discoveryparks.com.au {park_name}")
    if "nrma" in name_lower:
        queries.append(f"site:nrmaparksandresorts.com.au {park_name}")
    if "reflections" in name_lower:
        queries.append(f"site:reflectionsholidayparks.com.au {park_name}")
    if "tasman" in name_lower:
        queries.append(f"site:tasmanholidayparks.com.au {park_name}")
    if "g'day" in name_lower or "gday" in name_lower:
        queries.append(f"site:gdayparks.com.au {park_name}")

    queries.extend(
        [
            f'"{park_name}" {location_name} official website',
            f"{park_name} {location_name} holiday park",
            f"{park_name} {location_name} tourist park caravan",
        ]
    )

    candidates: list[str] = []
    for query in queries[:7]:
        debug_log(f"[website search query] {park_name} -> {query}")
        candidates.extend(fetch_search_result_urls(query))
        time.sleep(0.8)

    best_url = ""
    best_score = -9999
    facebook_fallback = ""

    for url in candidates:
        host = urllib.parse.urlparse(url).netloc.lower()
        if any(domain in host for domain in FACEBOOK_DOMAINS):
            if not facebook_fallback:
                facebook_fallback = url
            continue
        score = score_website_candidate(url, park_name)
        debug_log(f"[website candidate] {park_name} score={score} url={url}")
        if score > best_score:
            best_score = score
            best_url = url

    if best_url and best_score >= 15:
        verified = verify_website_url(best_url, park_name)
        if verified:
            return verified

    if facebook_fallback and best_score < 15:
        verified = verify_website_url(facebook_fallback, park_name)
        if verified:
            return verified

    if best_url and best_score >= 10:
        verified = verify_website_url(best_url, park_name)
        if verified:
            return verified

    return guess_operator_website(park_name)


def guess_name_based_domains(park_name: str) -> list[str]:
    compact = re.sub(r"[^a-z0-9]", "", park_name.lower())
    if len(compact) < 6:
        return []
    return [
        f"https://www.{compact}.com.au",
        f"https://{compact}.com.au",
    ]


def is_generic_operator_home(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    path = (parsed.path or "/").rstrip("/") or "/"
    if host in {"www.big4.com.au", "big4.com.au"} and path == "/":
        return True
    if host.endswith("ingeniaholidays.com.au") and path == "/":
        return True
    if host.endswith("discoveryparks.com.au") and path == "/":
        return True
    return False


def verify_website_url(url: str, park_name: str = "") -> str:
    final, html, _, issue = fetch_url(url)
    candidate = final or url
    if not html or issue not in {"", "cloudflare"}:
        return ""
    if is_rejected_website_url(candidate) or is_generic_operator_home(candidate):
        return ""
    if park_name:
        slug = slugify_park_name(park_name)
        short_slug = slug[5:] if slug.startswith("big4-") else slug
        host = urllib.parse.urlparse(candidate).netloc.lower()
        path = urllib.parse.urlparse(candidate).path.lower()
        if any(operator in host for operator in PREFERRED_OPERATOR_DOMAINS):
            slug_tokens = [t for t in short_slug.split("-") if len(t) > 3]
            subdomain = host.split(".")[0]
            if slug not in path and short_slug not in path:
                if not any(token in subdomain for token in slug_tokens):
                    return ""
    return candidate


def guess_operator_website(park_name: str) -> str:
    """Try known operator URL patterns and verify the page exists."""
    slug = slugify_park_name(park_name)
    lower = park_name.lower()
    guesses: list[str] = []

    if "ingenia" in lower:
        guesses.append(
            f"https://www.ingeniaholidays.com.au/our-parks/queensland/{slug}"
        )
    if "big4" in lower:
        short_slug = slug[5:] if slug.startswith("big4-") else slug
        guesses.extend(
            [
                f"https://www.big4.com.au/caravan-parks/qld/{short_slug}",
                f"https://www.big4.com.au/caravan-parks/qld/{slug}",
                f"https://www.big4.com.au/holiday-parks/qld/{short_slug}",
                f"https://parklane.big4.com.au/",
            ]
        )
    if "discovery" in lower:
        guesses.append(f"https://discoveryparks.com.au/caravan-parks/{slug}")
    if "nrma" in lower:
        guesses.append(f"https://www.nrmaparksandresorts.com.au/{slug}")
    if "reflections" in lower:
        guesses.append(f"https://reflectionsholidayparks.com.au/parks/{slug}")
    if "tasman" in lower:
        guesses.append(f"https://www.tasmanholidayparks.com.au/parks/{slug}")
    if "g'day" in lower or "gday" in lower:
        guesses.append(f"https://www.gdayparks.com.au/parks/{slug}")

    guesses.extend(guess_name_based_domains(park_name))

    seen: set[str] = set()
    for url in guesses:
        if url in seen:
            continue
        seen.add(url)
        debug_log(f"[website operator guess] {park_name} -> {url}")
        found = verify_website_url(url, park_name)
        if found:
            return found
        time.sleep(0.4)
    return ""


def website_from_park_records(
    park_name: str,
    approved_record: dict[str, Any] | None,
    score_record: dict[str, Any] | None,
    master_record: dict[str, Any] | None,
) -> tuple[str, str]:
    website = ""
    google_maps_url = ""
    records = [
        rec
        for rec in (approved_record, score_record, master_record)
        if isinstance(rec, dict) and rec
    ]

    for record in records:
        maps_url = extract_google_maps_url(record)
        if maps_url and not google_maps_url:
            google_maps_url = maps_url

    for record in records:
        if "website" in record:
            raw = record.get("website", "")
            debug_log(f'[website raw] {park_name} -> "{raw}"')
            if not str(raw or "").strip():
                debug_log(f"[website empty] {park_name} — website key exists but empty")
            break

    for record in records:
        found = extract_direct_website(record)
        if found:
            website = found
            break

    return website, google_maps_url


def ensure_park_website(
    job: ParkJob,
    *,
    location_name: str,
    websites_store: dict[str, Any],
    websites_path: Path,
    force: bool,
    checked: str,
) -> ParkTarget:
    website, google_maps_url = website_from_park_records(
        job.name,
        job.approved_record,
        job.score_record,
        job.master_record,
    )
    if website:
        log(f"[website found] {job.name} -> {website}")
        return ParkTarget(name=job.name, website=website, google_maps_url=google_maps_url)

    stored_url, _match_kind = lookup_website_in_store(job.name, websites_store)
    if stored_url and is_generic_operator_home(stored_url):
        stored_url = ""
    if stored_url and not force:
        log(f"[website found] {job.name} -> {stored_url}")
        return ParkTarget(
            name=job.name,
            website=stored_url,
            google_maps_url=google_maps_url,
        )

    log(f"[website missing] {job.name}")

    if has_stored_website(job.name, websites_store) and not force:
        log(f"[website not found] {job.name}")
        return ParkTarget(name=job.name, website="", google_maps_url=google_maps_url)

    log(f"[website lookup] {job.name}")
    found = search_website_online(job.name, location_name)
    if found:
        log(f"[website found] {job.name} -> {found}")
        if not has_stored_website(job.name, websites_store) or force:
            websites_store[job.name] = {
                "website": found,
                "confidence": "medium",
                "source": "search",
                "date_checked": checked,
            }
            save_websites_json_store(websites_path, websites_store)
            log(f"[website saved] {job.name}")
        return ParkTarget(
            name=job.name,
            website=found,
            google_maps_url=google_maps_url,
        )

    log(f"[website not found] {job.name}")
    return ParkTarget(name=job.name, website="", google_maps_url=google_maps_url)


def slugify_park_name(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def load_park_master(park_name: str) -> dict[str, Any]:
    slug = slugify_park_name(park_name)
    master_file = PROJECT_DIR / "parks" / slug / "master.json"
    if not master_file.exists():
        return {}
    try:
        data = json.loads(master_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def collect_record_keys(*records: dict[str, Any] | None) -> str:
    seen: list[str] = []
    for record in records:
        if isinstance(record, dict) and record:
            for key in sorted(str(k) for k in record.keys()):
                if key not in seen:
                    seen.append(key)
    return ", ".join(seen)


def output_slug(csv_slug: str, state_abbr: str) -> str:
    suffix = STATE_NAMES.get(state_abbr.upper(), state_abbr.lower())
    return f"{csv_slug}-{suffix}"


def load_locations() -> list[dict[str, str]]:
    csv_path = PROJECT_DIR / "locations.csv"
    rows: list[dict[str, str]] = []
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(
                {
                    "location": row.get("location", "").strip(),
                    "state": row.get("state", "").strip().upper(),
                    "slug": row.get("slug", "").strip(),
                }
            )
    return rows


def resolve_location_row(slug_arg: str) -> dict[str, str] | None:
    key = slug_arg.strip().lower()
    for row in load_locations():
        csv_slug = row["slug"].lower()
        state = row["state"]
        candidates = {
            csv_slug,
            output_slug(csv_slug, state).lower(),
            f"{csv_slug}-{state.lower()}",
        }
        if key in candidates:
            return row
    return None


def loc_dir_for_row(row: dict[str, str]) -> Path:
    state_dir = STATE_MAP.get(row["state"], row["state"].lower())
    return PROJECT_DIR / "locations" / state_dir / row["slug"]


def index_scores_by_name(scores_path: Path) -> dict[str, dict[str, Any]]:
    if not scores_path.exists():
        return {}
    try:
        data = json.loads(scores_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in data:
        if isinstance(item, dict):
            name = str(item.get("park_name") or "").strip()
            if name:
                out[name] = item
    return out


def _park_name_from_record(item: dict[str, Any]) -> str:
    return str(
        item.get("park_name")
        or item.get("title")
        or item.get("name")
        or ""
    ).strip()


def parse_approved_park_entries(path: Path) -> list[tuple[str, dict[str, Any] | None]]:
    """Return ordered (park_name, approved_record_or_none) pairs."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    entries: list[tuple[str, dict[str, Any] | None]] = []

    def add_item(item: Any) -> None:
        if isinstance(item, dict):
            name = _park_name_from_record(item)
            if name:
                entries.append((name, item))
        elif isinstance(item, str) and item.strip():
            entries.append((item.strip(), None))

    if isinstance(data, dict) and isinstance(data.get("parks"), list):
        for item in data["parks"]:
            add_item(item)
        return entries

    if isinstance(data, dict) and isinstance(data.get("approved_parks"), list):
        for item in data["approved_parks"]:
            add_item(item)
        return entries

    if isinstance(data, list):
        for item in data:
            add_item(item)
    return entries


def load_park_jobs(loc_dir: Path) -> list[ParkJob]:
    approved_path = loc_dir / "approved-parks.json"
    scores_path = loc_dir / "scores.json"
    scores_by_name = index_scores_by_name(scores_path)

    if approved_path.exists():
        jobs: list[ParkJob] = []
        for name, approved_record in parse_approved_park_entries(approved_path):
            jobs.append(
                ParkJob(
                    name=name,
                    approved_record=approved_record,
                    score_record=scores_by_name.get(name),
                    master_record=load_park_master(name),
                )
            )
        return jobs

    if scores_path.exists():
        jobs = []
        for item in scores_by_name.values():
            name = str(item.get("park_name") or "").strip()
            if name:
                jobs.append(
                    ParkJob(
                        name=name,
                        approved_record=None,
                        score_record=item,
                        master_record=load_park_master(name),
                    )
                )
        return jobs
    return []


def baseline_search_date(today: date | None = None) -> date:
    today = today or date.today()
    start = today + timedelta(days=30)
    candidate = start
    # Advance to next Tuesday or Wednesday.
    while candidate.weekday() not in (1, 2):
        candidate += timedelta(days=1)
    # Skip school holidays if possible (try up to 8 weeks ahead).
    for _ in range(56):
        if not any(start <= candidate <= end for start, end in SCHOOL_HOLIDAY_RANGES_2026):
            if candidate.weekday() in (1, 2):
                return candidate
        candidate += timedelta(days=1)
        while candidate.weekday() not in (1, 2):
            candidate += timedelta(days=1)
    return start


def is_cloudflare_page(html: str) -> bool:
    return any(re.search(pat, html, re.I) for pat in CLOUDFLARE_PATTERNS)


def is_booking_engine_page(html: str) -> bool:
    return any(re.search(pat, html, re.I) for pat in BLOCKED_ENGINE_PATTERNS)


def is_js_rendered_page(html: str) -> bool:
    text = html_to_text(html)
    if len(text.strip()) >= 400:
        return False
    return any(re.search(pat, html, re.I) for pat in JS_RENDERED_PATTERNS)


def note_page_signals(html: str, state: ScrapeState) -> None:
    if not html:
        return
    text = html_to_text(html)
    if is_cloudflare_page(html):
        state.cloudflare = True
    if is_booking_engine_page(html):
        state.booking_engine = True
    if is_js_rendered_page(html):
        state.js_rendered = True
    if POWERED_POSITIVE.search(text):
        state.saw_powered_mention = True
    if PRICE_RE.search(text):
        state.saw_price_number = True


def classify_failure(state: ScrapeState) -> str:
    if state.timeout:
        return "Timeout"
    if state.unreachable and state.pages_fetched == 0:
        return "Website unreachable"
    if state.cloudflare:
        return "Cloudflare protection"
    if state.calendar_blocked:
        return "Rate calendar blocked"
    if state.booking_engine:
        return "Booking engine detected"
    if state.js_rendered and not state.saw_powered_mention:
        return "JavaScript rendered page"
    if state.saw_powered_mention and not state.saw_price_number:
        return "No rate found"
    if not state.saw_powered_mention:
        return "No powered site found"
    return "No rate found"


def missing_result(
    reason: str,
    *,
    source_url: str = "",
    blocked: bool = False,
) -> PriceResult:
    return PriceResult(
        source_url=source_url,
        confidence="missing",
        blocked=blocked,
        failure_reason=reason,
    )


def fetch_url(url: str, timeout: int = 20) -> tuple[str, str, bool, str]:
    """Return (final_url, html_text, blocked_engine, fetch_issue).

    fetch_issue is one of: "", "timeout", "unreachable", "cloudflare".
    """
    if not url:
        return "", "", False, "unreachable"
    if not url.startswith("http"):
        url = "https://" + url.lstrip("/")

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; FamilyHolidayParksPriceAgent/1.0; "
                "+https://familyholidayparks.com.au)"
            ),
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final_url = resp.geturl()
            raw = resp.read(600_000)
            charset = resp.headers.get_content_charset() or "utf-8"
            html = raw.decode(charset, errors="replace")
    except TimeoutError:
        return url, "", False, "timeout"
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read(100_000).decode(
                exc.headers.get_content_charset() or "utf-8",
                errors="replace",
            )
        except Exception:
            pass
        if body and is_cloudflare_page(body):
            return exc.geturl() or url, body, False, "cloudflare"
        return url, "", False, "unreachable"
    except urllib.error.URLError as exc:
        reason = str(getattr(exc, "reason", exc)).lower()
        if isinstance(exc.reason, TimeoutError) or "timed out" in reason:
            return url, "", False, "timeout"
        return url, "", False, "unreachable"
    except OSError as exc:
        if "timed out" in str(exc).lower():
            return url, "", False, "timeout"
        return url, "", False, "unreachable"

    if is_cloudflare_page(html):
        return final_url, html, False, "cloudflare"

    blocked = is_booking_engine_page(html)
    return final_url, html, blocked, ""


def html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = unescape(re.sub(r"\s+", " ", text))
    return text


def absolutize_url(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, href)


def candidate_rate_urls(base_url: str, html: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    base_host = urllib.parse.urlparse(base_url).netloc

    for match in RATES_LINK_RE.finditer(html):
        href = match.group(1).strip()
        if href.startswith("#") or href.startswith("mailto:"):
            continue
        full = absolutize_url(base_url, href)
        host = urllib.parse.urlparse(full).netloc
        if host and host != base_host:
            continue
        if full not in seen:
            seen.add(full)
            urls.append(full)

    for suffix in (
        "/rates",
        "/rates-and-fees",
        "/pricing",
        "/prices",
        "/tariffs",
        "/accommodation/caravan-and-camping",
        "/caravan-and-camping",
        "/sites",
        "/powered-sites",
        "/book",
        "/booking",
    ):
        full = absolutize_url(base_url, suffix)
        if full not in seen:
            seen.add(full)
            urls.append(full)

    return urls[:8]


def extract_powered_prices(text: str) -> list[tuple[float, str]]:
    """Return list of (price, context_snippet) for powered-site mentions."""
    results: list[tuple[float, str]] = []

    for match in PRICE_RE.finditer(text):
        amount = float(match.group(1))
        if amount < 25 or amount > 600:
            continue
        start = max(0, match.start() - 120)
        end = min(len(text), match.end() + 120)
        context = text[start:end]

        if CABIN_NEGATIVE.search(context) and not POWERED_POSITIVE.search(context):
            continue

        if POWERED_POSITIVE.search(context):
            results.append((amount, context.strip()))
            continue

        # Broader page context: powered mentioned nearby in same line/block.
        line_start = text.rfind("\n", 0, match.start())
        line_end = text.find("\n", match.end())
        line = text[line_start:line_end if line_end != -1 else None]
        if POWERED_POSITIVE.search(line) and not CABIN_NEGATIVE.search(line):
            results.append((amount, line.strip()))

    # De-duplicate by amount, keep lowest powered rate.
    by_amount: dict[float, str] = {}
    for amount, ctx in results:
        by_amount.setdefault(amount, ctx)
    return sorted(by_amount.items(), key=lambda x: x[0])


def build_booking_urls(website: str, search_date: date) -> list[str]:
    parsed = urllib.parse.urlparse(website if website.startswith("http") else f"https://{website}")
    base = f"{parsed.scheme}://{parsed.netloc}"
    iso = search_date.isoformat()
    queries = [
        f"{base}/book?checkin={iso}&checkout={(search_date + timedelta(days=1)).isoformat()}&adults=2",
        f"{base}/booking?arrival={iso}&departure={(search_date + timedelta(days=1)).isoformat()}&adults=2",
        f"{base}/?checkin={iso}&checkout={(search_date + timedelta(days=1)).isoformat()}",
    ]
    return queries


def fetch_price_for_park(park: ParkTarget, search_date: date) -> PriceResult:
    if not park.website:
        return missing_result("No website")

    state = ScrapeState()
    home_url, home_html, home_blocked, home_issue = fetch_url(park.website)

    if home_issue == "timeout":
        state.timeout = True
        return missing_result("Timeout", source_url=park.website)
    if home_issue == "unreachable" or not home_html:
        state.unreachable = True
        return missing_result("Website unreachable", source_url=park.website)
    if home_issue == "cloudflare":
        state.cloudflare = True
        state.pages_fetched = 1
        note_page_signals(home_html, state)
        return missing_result(
            "Cloudflare protection",
            source_url=home_url or park.website,
        )

    state.pages_fetched = 1
    note_page_signals(home_html, state)

    if home_blocked and not POWERED_POSITIVE.search(html_to_text(home_html)):
        return missing_result(
            "Booking engine detected",
            source_url=home_url or park.website,
            blocked=True,
        )

    pages: list[tuple[str, str, bool]] = [(home_url or park.website, home_html, home_blocked)]
    for url in candidate_rate_urls(home_url or park.website, home_html):
        final, html, blocked, issue = fetch_url(url)
        if issue == "timeout":
            state.timeout = True
        elif issue == "unreachable":
            state.unreachable = True
        elif issue == "cloudflare":
            state.cloudflare = True
        if html:
            state.pages_fetched += 1
            note_page_signals(html, state)
            pages.append((final, html, blocked))
        time.sleep(0.4)

    # Try date-specific booking URLs when engine not fully blocked on homepage.
    if not home_blocked:
        for book_url in build_booking_urls(home_url or park.website, search_date):
            final, html, blocked, issue = fetch_url(book_url)
            if blocked:
                state.calendar_blocked = True
            if issue == "timeout":
                state.timeout = True
            elif issue == "unreachable":
                state.unreachable = True
            elif issue == "cloudflare":
                state.cloudflare = True
            if html:
                state.pages_fetched += 1
                note_page_signals(html, state)
                pages.append((final, html, blocked))
                date_prices = extract_powered_prices(html_to_text(html))
                if date_prices:
                    amount = date_prices[0][0]
                    return PriceResult(
                        display=f"${int(amount) if amount.is_integer() else amount:.0f}/night",
                        price=amount,
                        source_url=final,
                        confidence="high",
                    )
            time.sleep(0.4)

    blocked_any = False
    best_low: tuple[float, str, str, str] | None = None  # amount, url, confidence

    for page_url, html, blocked in pages:
        if blocked:
            blocked_any = True
            state.booking_engine = True
        text = html_to_text(html)
        prices = extract_powered_prices(text)
        if not prices:
            continue
        amount = prices[0][0]
        confidence = "medium" if blocked else "low"
        if best_low is None or amount < best_low[0]:
            best_low = (amount, page_url, confidence, prices[0][1])

    if best_low:
        amount, page_url, confidence, _ = best_low
        if blocked_any and confidence != "medium":
            confidence = "medium"
        return PriceResult(
            display=f"${int(amount) if amount.is_integer() else amount:.0f}/night",
            price=amount,
            source_url=page_url,
            confidence=confidence,
            blocked=blocked_any,
        )

    reason = classify_failure(state)
    return missing_result(
        reason,
        source_url=home_url or park.website,
        blocked=blocked_any or state.booking_engine,
    )


def load_existing_prices(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def price_entry_from_result(result: PriceResult, checked: str) -> dict[str, Any]:
    if result.price is None:
        entry: dict[str, Any] = {
            "display": "—",
            "confidence": "missing",
        }
        if result.failure_reason:
            entry["failure_reason"] = result.failure_reason
        return entry

    entry = {
        "display": result.display,
        "price": result.price,
        "type": "Powered site",
        "note": PRICE_NOTE,
        "date_checked": checked,
        "source_url": result.source_url,
        "confidence": result.confidence,
    }
    return entry


def has_valid_price(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    confidence = str(entry.get("confidence") or "").lower()
    price = entry.get("price")
    if confidence in {"high", "medium", "low", "manual"} and price not in (None, "", 0):
        return True
    display = str(entry.get("display") or "").strip()
    return bool(display and display not in {"—", "-"})


def should_skip_park(
    name: str,
    existing: dict[str, Any],
    *,
    missing_only: bool,
    force: bool,
) -> bool:
    if force:
        return False
    return has_valid_price(existing.get(name))


def process_location(
    row: dict[str, str],
    *,
    missing_only: bool,
    force: bool,
    report: RunReport,
    limit_remaining: list[int],
) -> None:
    loc_dir = loc_dir_for_row(row)
    if not loc_dir.exists():
        return

    label = f"{row['location']} {row['state']} ({row['slug']})"
    location_name = f"{row['location']} {row['state']}".strip()
    jobs = load_park_jobs(loc_dir)
    if not jobs:
        return

    report.locations_checked.append(label)
    prices_path = loc_dir / "prices.json"
    websites_path = loc_dir / "websites.json"
    existing = load_existing_prices(prices_path)
    websites_store = load_websites_json_store(websites_path)
    output: dict[str, Any] = dict(existing) if not force else {}
    search_date = baseline_search_date()
    checked = date.today().isoformat()

    log(f"\n[{row['slug']}] {label} — search date {search_date.isoformat()}")

    for job in jobs:
        if limit_remaining[0] <= 0:
            break

        keys_label = collect_record_keys(
            job.approved_record, job.score_record, job.master_record
        )
        debug_log(f"[park data keys] {job.name}: {keys_label or '(none)'}")

        park = ensure_park_website(
            job,
            location_name=location_name,
            websites_store=websites_store,
            websites_path=websites_path,
            force=force,
            checked=checked,
        )

        if should_skip_park(job.name, existing, missing_only=missing_only, force=force):
            log(f"  [skip] {job.name} (existing price)")
            continue

        limit_remaining[0] -= 1
        report.parks_checked += 1

        result = fetch_price_for_park(park, search_date)
        entry = price_entry_from_result(result, checked)

        if force or not has_valid_price(existing.get(job.name)):
            output[job.name] = entry
        else:
            log(f"  [skip price update] {job.name} (existing price, use --force to overwrite)")

        if result.blocked:
            report.blocked_engines.append(
                f"{job.name} ({result.source_url or park.website})"
            )

        if result.price is not None and result.confidence != "missing":
            report.prices_found.append(
                f"{job.name} {entry['display']} confidence={result.confidence}"
            )
            log(
                f"[price found] {job.name} {entry['display']} "
                f"confidence={result.confidence}"
            )
            if result.blocked:
                report.manual_follow_up.append(
                    f"{job.name} — verify date-specific rate (booking engine detected)"
                )
        else:
            reason = result.failure_reason or "No rate found"
            report.prices_missing.append(job.name)
            report.failures_by_park.append(f"{job.name}: {reason}")
            report.failure_counts[reason] = report.failure_counts.get(reason, 0) + 1
            log(f"[price missing] {job.name} reason={reason}")
            if result.blocked or reason == "Booking engine detected":
                report.blocked_engines.append(
                    f"{job.name} ({result.source_url or park.website})"
                )
                report.manual_follow_up.append(
                    f"{job.name} — booking engine blocked automated price search"
                )

        time.sleep(0.6)

    prices_path.parent.mkdir(parents=True, exist_ok=True)
    prices_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    log(f"  [saved] {prices_path.relative_to(PROJECT_DIR).as_posix()}")
    if websites_path.exists():
        log(f"  [saved] {websites_path.relative_to(PROJECT_DIR).as_posix()}")


def write_report(report: RunReport) -> None:
    lines = [
        "# Price Agent Report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        f"- Parks checked: {report.parks_checked}",
        f"- Prices found: {len(report.prices_found)}",
        f"- Prices missing: {len(report.prices_missing)}",
        f"- Blocked booking engines: {len(report.blocked_engines)}",
        "",
        "## Locations checked",
        "",
    ]
    if report.locations_checked:
        lines.extend(f"- {loc}" for loc in report.locations_checked)
    else:
        lines.append("- None")

    lines.extend(["", "## Prices found", ""])
    if report.prices_found:
        lines.extend(f"- {item}" for item in report.prices_found)
    else:
        lines.append("- None")

    lines.extend(["", "## Failure summary", ""])
    if report.failure_counts:
        for reason in FAILURE_REASONS:
            count = report.failure_counts.get(reason, 0)
            if count:
                label = SUMMARY_LABELS.get(reason, reason)
                lines.append(f"{label}: {count}")
    else:
        lines.append("- None")

    lines.extend(["", "## Prices missing", ""])
    if report.failures_by_park:
        lines.extend(f"- {item}" for item in report.failures_by_park)
    elif report.prices_missing:
        lines.extend(f"- {name}" for name in report.prices_missing)
    else:
        lines.append("- None")

    lines.extend(["", "## Blocked booking engines", ""])
    if report.blocked_engines:
        lines.extend(f"- {item}" for item in report.blocked_engines)
    else:
        lines.append("- None")

    lines.extend(["", "## Manual follow-up list", ""])
    if report.manual_follow_up:
        lines.extend(f"- {item}" for item in report.manual_follow_up)
    else:
        lines.append("- None")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"\n[report] {REPORT_PATH.relative_to(PROJECT_DIR).as_posix()}")


def select_locations(
    *,
    slug: str | None,
    state: str | None,
) -> list[dict[str, str]]:
    rows = load_locations()
    if slug:
        row = resolve_location_row(slug)
        return [row] if row else []
    if state:
        st = state.strip().upper()
        return [r for r in rows if r["state"] == st and (PROJECT_DIR / "locations" / STATE_MAP.get(st, st.lower()) / r["slug"] / "approved-parks.json").exists()]
    return [
        r
        for r in rows
        if (loc_dir_for_row(r) / "approved-parks.json").exists()
    ]


def main() -> int:
    global DEBUG_MODE

    parser = argparse.ArgumentParser(description="Collect powered site prices for approved parks")
    parser.add_argument("--slug", help="Location output slug e.g. apollo-bay-victoria")
    parser.add_argument("--state", help="Process all locations in a state e.g. VIC")
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Only fetch parks without an existing price",
    )
    parser.add_argument("--force", action="store_true", help="Re-fetch all parks")
    parser.add_argument("--limit", type=int, default=0, help="Max parks to check this run")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Log website field resolution details per park",
    )
    args = parser.parse_args()
    DEBUG_MODE = bool(args.debug)

    locations = select_locations(slug=args.slug, state=args.state)
    if args.slug and not locations:
        log(f"ERROR: Unknown slug '{args.slug}'")
        return 1

    if not locations:
        log("No locations to process (need approved-parks.json).")
        return 0

    report = RunReport()
    limit_remaining = [args.limit if args.limit > 0 else 10_000]

    for row in locations:
        if limit_remaining[0] <= 0:
            break
        process_location(
            row,
            missing_only=args.missing_only,
            force=args.force,
            report=report,
            limit_remaining=limit_remaining,
        )

    write_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
