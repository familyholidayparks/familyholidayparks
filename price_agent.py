#!/usr/bin/env python3
"""
Collect powered site prices for approved parks and write locations/.../prices.json.

Usage:
  python price_agent.py --slug apollo-bay-victoria
  python price_agent.py --state VIC
  python price_agent.py --missing-only
  python price_agent.py --force
  python price_agent.py --limit 10
  python price_agent.py --slug noosa --debug --browser
"""
from __future__ import annotations

import argparse
import ast
import base64
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
    "Booking engine requires interactive date selection",
    "RMS booking engine requires interactive date selection",
    "JavaScript rendered page",
    "No powered site found",
    "No reliable powered site price found",
    "No rate found",
    "Rate calendar blocked",
    "Cloudflare protection",
    "Timeout",
)

SUMMARY_LABELS = {
    "Booking engine detected": "Booking engines",
    "Booking engine requires interactive date selection": "Interactive date booking",
    "RMS booking engine requires interactive date selection": "RMS interactive booking",
    "JavaScript rendered page": "JavaScript pages",
    "Cloudflare protection": "Cloudflare",
    "No website": "No website",
    "No powered site found": "No powered site",
    "No reliable powered site price found": "No reliable price",
    "No rate found": "No rate found",
    "Rate calendar blocked": "Rate calendar blocked",
    "Website unreachable": "Website unreachable",
    "Timeout": "Timeout",
}

POWERED_POSITIVE = re.compile(
    r"\b(powered\s+only\s+site|powered\s+only|powered\s+sites?|powered\s+slab|"
    r"powered\s+camping|powered\s+campsite|powered\s+van\s+site|caravan\s+site|"
    r"motorhome\s+site|rv\s+site|ensuite\s+site|deluxe\s+powered\s+site|"
    r"grass\s+powered\s+site|drive\s+through\s+powered\s+site|power\s+site)\b",
    re.I,
)

POWERED_BLOCK_LABELS = re.compile(
    r"\b(powered\s+only\s+site|powered\s+only|powered\s+sites?|powered\s+slab|"
    r"powered\s+camping|powered\s+campsite|powered\s+van\s+site|caravan\s+site|"
    r"motorhome\s+site|rv\s+site|ensuite\s+site|deluxe\s+powered\s+site|"
    r"grass\s+powered\s+site|drive\s+through\s+powered\s+site)\b",
    re.I,
)

PRICE_REJECT_CONTEXT = re.compile(
    r"\b(email|powered\s+by\s+rms|booking\s+fee|deposit|bond|extra\s+person|"
    r"child|membership|discount|cabin|villa|room|apartment|conditions?|policy)\b",
    re.I,
)

UNPOWERED_ONLY = re.compile(
    r"\b(non[- ]?powered\s+site|non[- ]?powered|unpowered|no\s+power)\b",
    re.I,
)

BAD_RATE_URL_FRAGMENTS: tuple[str, ...] = (
    "terms",
    "conditions",
    "policy",
    "privacy",
    "faq",
    "contact",
    "facebook",
    "instagram",
    "powered-by",
    "booking-terms",
)

POWERED_BLOCK_WINDOW = 450

NOT_FOUND_PAGE_RE = re.compile(
    r"page\s+not\s+found|404\s+not\s+found|can't\s+be\s+found|cannot\s+be\s+found",
    re.I,
)

PRICE_CONTEXT_RADIUS = 300
PLAYWRIGHT_MAX_ELEMENT_TRIES = 3

MEMBERSHIP_RE = re.compile(r"\b(member|membership|club\s+price|member\s+rate)\b", re.I)

SOCIAL_LINK_DOMAINS: tuple[str, ...] = (
    "facebook.com",
    "instagram.com",
    "youtube.com",
)

BROWSER_PAGE_TIMEOUT_MS = 20_000
BROWSER_PARK_MAX_SECONDS = 90

BROWSER_LINK_TERMS: tuple[tuple[str, int], ...] = (
    ("book now", 100),
    ("check availability", 98),
    ("powered sites", 95),
    ("caravan sites", 93),
    ("rates", 90),
    ("sites", 75),
    ("accommodation", 70),
    ("book", 65),
)

TARIFF_FALLBACK_REASONS = frozenset({
    "Booking engine requires interactive date selection",
    "RMS booking engine requires interactive date selection",
    "Website unreachable",
    "No website",
    "No powered site found",
})

REJECTED_TARIFF_LINK_DOMAINS: tuple[str, ...] = (
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "tripadvisor",
    "booking.com",
    "expedia",
    "agoda",
)

TARIFF_SEARCH_QUERIES: tuple[str, ...] = (
    '"{name}" powered site rates',
    '"{name}" camping fees',
    '"{name}" tariff',
    '"{name}" powered campsite price',
    '"{name}" booking powered site',
)

TARIFF_PAGE_SUFFIXES: tuple[str, ...] = (
    "/tariffs",
    "/fees",
    "/rates",
    "/rates-and-fees",
    "/pricing",
    "/prices",
    "/booking",
    "/accommodation",
    "/powered-sites",
    "/powered-sites-and-camping",
    "/camping",
    "/caravan-and-camping",
    "/sites",
)

TARIFF_LINK_TERMS: tuple[tuple[str, int], ...] = (
    ("tariffs", 100),
    ("camping fees", 98),
    ("fees", 95),
    ("rates", 90),
    ("powered sites", 88),
    ("accommodation", 75),
    ("camping", 70),
)

DATE_PICKER_PATTERNS = [
    r'input[^>]+type=["\']date["\']',
    r'name=["\'][^"\']*(?:checkin|check-in|arrival|departure|checkout|check-out)',
    r'check[- ]?in',
    r'check[- ]?out',
    r'arrival\s+date',
    r'departure\s+date',
    r'select\s+(your\s+)?dates',
    r'choose\s+(your\s+)?dates',
    r'date[- ]?picker',
    r'datepicker',
    r'booking[- ]?calendar',
    r'class=["\'][^"\']*\bcalendar\b',
    r'data-rms-',
    r'data-newbook-',
]

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
BROWSER_MODE = False
_PLAYWRIGHT_WARNED = False

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

WEBSITE_OVERRIDES: dict[str, str] = {
    "Noosa River Holiday Park": "https://www.noosaholidayparks.com.au/noosa-river",
    "Noosa North Shore Beach Campground": (
        "https://www.noosaholidayparks.com.au/noosa-north-shore-beach-campground"
    ),
}

# Tried in order; only used when validate_website_url returns OK (HTTP 200).
WEBSITE_OVERRIDE_CANDIDATES: dict[str, list[str]] = {
    "BIG4 Park Lane Noosa North Shore": [
        "https://www.big4.com.au/caravan-parks/qld/sunshine-coast/noosa-north-shore-retreat",
    ],
}

TARIFF_ENTRY_URL_OVERRIDES: dict[str, list[str]] = {
    "Noosa River Holiday Park": [
        "https://www.noosaholidayparks.com.au/Our-Parks/Noosa-River-Holiday-Park",
        "https://www.noosaholidayparks.com.au/Our-Parks/Noosa-River-Holiday-Park/Park-Overview",
        "https://www.noosaholidayparks.com.au/Our-Parks/Noosa-River-Holiday-Park/Facilities",
    ],
    "Noosa North Shore Beach Campground": [
        "https://www.noosaholidayparks.com.au/Our-Parks/Noosa-North-Shore-Beach-Campground",
        "https://www.noosaholidayparks.com.au/Our-Parks/Noosa-North-Shore-Beach-Campground/Park-Overview",
        "https://www.noosaholidayparks.com.au/Our-Parks/Noosa-North-Shore-Beach-Campground/Facilities",
    ],
    "BIG4 Park Lane Noosa North Shore": [
        "https://www.big4.com.au/caravan-parks/qld/sunshine-coast/noosa-north-shore-retreat/accommodation",
    ],
}

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
    method: str = ""


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


def is_social_url(url: str) -> bool:
    lower = (url or "").lower()
    return any(domain in lower for domain in SOCIAL_LINK_DOMAINS)


def is_rms_url(url: str) -> bool:
    return "rmscloud.com" in (url or "").lower()


def is_rejected_rate_page(url: str) -> bool:
    lower = (url or "").lower()
    return any(fragment in lower for fragment in BAD_RATE_URL_FRAGMENTS)


def is_rejected_price_source(
    *,
    source_url: str = "",
    page_title: str = "",
    text: str = "",
) -> bool:
    blob = f"{source_url} {page_title}".lower()
    if is_rejected_rate_page(source_url):
        return True
    if any(fragment in blob for fragment in BAD_RATE_URL_FRAGMENTS):
        return True
    head = (text or "")[:2500].lower()
    if "powered by rms" in head and not POWERED_BLOCK_LABELS.search(text or ""):
        return True
    return False


def log_price_found(
    park_name: str,
    amount: float,
    *,
    confidence: str = "medium",
    method: str = "browser",
) -> None:
    display = int(amount) if float(amount).is_integer() else amount
    log(
        f"[price found] {park_name} ${display}/night "
        f"confidence={confidence} method={method}"
    )


def validate_website_url(url: str, park_name: str = "") -> tuple[str, bool]:
    """Return (final_url, valid). Only accept HTTP 200 pages that are not 404/403 shells."""
    if not url:
        return "", False
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
        with urllib.request.urlopen(req, timeout=20) as resp:
            status = getattr(resp, "status", 200)
            final_url = resp.geturl() or url
            raw = resp.read(200_000)
            charset = resp.headers.get_content_charset() or "utf-8"
            body = raw.decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        log(f"[website invalid] {park_name} -> {url} status={status}")
        return url, False
    except (urllib.error.URLError, TimeoutError, OSError):
        log(f"[website invalid] {park_name} -> {url} status=unreachable")
        return url, False

    if status not in (200,):
        log(f"[website invalid] {park_name} -> {url} status={status}")
        return final_url, False

    title_blob = body[:8000].lower()
    if NOT_FOUND_PAGE_RE.search(title_blob) or "page not found" in title_blob:
        log(f"[website invalid] {park_name} -> {final_url} status=404")
        return final_url, False
    if status == 403 or "403 forbidden" in title_blob:
        log(f"[website invalid] {park_name} -> {final_url} status=403")
        return final_url, False

    return final_url, True


def resolve_valid_website_override(park_name: str) -> str:
    for candidate in WEBSITE_OVERRIDE_CANDIDATES.get(park_name, []):
        final, ok = validate_website_url(candidate, park_name)
        if ok:
            return final
    override = WEBSITE_OVERRIDES.get(park_name, "").strip()
    if override:
        final, ok = validate_website_url(override, park_name)
        if ok:
            return final
    return ""


def _price_context_label(context: str, block_label: str = "") -> str:
    if block_label:
        return block_label
    match = POWERED_BLOCK_LABELS.search(context)
    if match:
        return match.group(0)
    reject = PRICE_REJECT_CONTEXT.search(context)
    if reject:
        return reject.group(0)
    return context.strip()[:40]


def _powered_label_is_valid(text: str, match: re.Match[str]) -> bool:
    """Reject labels like 'Powered Site' inside 'Non Powered Site'."""
    prefix = text[max(0, match.start() - 8) : match.start()].lower()
    if re.search(r"\b(non|un|no)\s*$", prefix):
        return False
    return True


def _block_is_unpowered(block_label: str, block_text: str) -> bool:
    sample = f"{block_label} {block_text[:120]}"
    if re.search(r"\bpowered\s+only\b", sample, re.I):
        return False
    return bool(UNPOWERED_ONLY.search(sample))


def _prices_in_block(
    block_text: str,
    block_label: str,
    *,
    should_log: bool,
) -> list[tuple[float, str]]:
    found: list[tuple[float, str]] = []
    for match in PRICE_RE.finditer(block_text):
        amount = float(match.group(1))
        if amount < 25 or amount > 600:
            continue
        local_start = max(0, match.start() - 120)
        local_end = min(len(block_text), match.end() + 120)
        local_ctx = block_text[local_start:local_end]
        display_amount = int(amount) if amount.is_integer() else amount
        price_str = f"${display_amount}"

        if PRICE_REJECT_CONTEXT.search(local_ctx):
            if should_log:
                log(
                    f'[price rejected] "{price_str}" near '
                    f'"{_price_context_label(local_ctx, block_label)}"'
                )
            continue

        if should_log:
            log(
                f'[price candidate] "{price_str}" near '
                f'"{_price_context_label(local_ctx, block_label)}..."'
            )
        found.append((amount, local_ctx.strip()))
    return found


def locator_is_actionable(locator: Any, *, for_fill: bool = False) -> bool:
    try:
        if locator.count() == 0:
            return False
        el = locator.first
        if not el.is_visible():
            return False
        if not el.is_enabled():
            return False
        if for_fill and not el.is_editable():
            return False
        return True
    except Exception:
        return False


def safe_click(locator: Any, *, timeout: int, label: str = "") -> bool:
    for attempt in range(PLAYWRIGHT_MAX_ELEMENT_TRIES):
        try:
            if not locator_is_actionable(locator):
                debug_log(
                    f"[browser] skip click {label or 'element'} — not visible/enabled"
                )
                return False
            locator.first.click(timeout=timeout)
            return True
        except Exception as exc:
            debug_log(f"[browser] click failed {label or 'element'}: {exc}")
            if attempt >= PLAYWRIGHT_MAX_ELEMENT_TRIES - 1:
                return False
            time.sleep(0.25)
    return False


def safe_fill(locator: Any, value: str, *, timeout: int, label: str = "") -> bool:
    for attempt in range(PLAYWRIGHT_MAX_ELEMENT_TRIES):
        try:
            if not locator_is_actionable(locator, for_fill=True):
                debug_log(
                    f"[browser] skip fill {label or 'element'} — not visible/editable"
                )
                return False
            locator.first.fill(value, timeout=timeout)
            return True
        except Exception as exc:
            debug_log(f"[browser] fill failed {label or 'element'}: {exc}")
            if attempt >= PLAYWRIGHT_MAX_ELEMENT_TRIES - 1:
                return False
            time.sleep(0.25)
    return False


def page_has_hidden_booking_fields(page: Any) -> bool:
    """True when booking needs hidden date/email fields we cannot interact with."""
    try:
        return bool(
            page.evaluate(
                """() => {
                  for (const el of document.querySelectorAll('input, select, textarea')) {
                    const type = (el.type || '').toLowerCase();
                    const label = (
                      (el.name || '') + ' ' + (el.id || '') + ' ' +
                      (el.placeholder || '') + ' ' + (el.getAttribute('aria-label') || '')
                    ).toLowerCase();
                    const hidden = (
                      type === 'hidden' ||
                      el.offsetParent === null ||
                      (el.checkVisibility && !el.checkVisibility())
                    );
                    const isDate = (
                      type === 'date' ||
                      /arrival|departure|check[- ]?in|check[- ]?out/.test(label)
                    );
                    const isEmail = type === 'email' || /\\bemail\\b/.test(label);
                    if (hidden && (isDate || isEmail)) return true;
                  }
                  return false;
                }"""
            )
        )
    except Exception:
        return False


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


def coerce_website_url(raw: Any) -> str:
    """Normalise a website field that may be a URL string, dict, or serialised dict."""
    if raw is None:
        return ""
    if isinstance(raw, dict):
        return parse_website_entry_value(raw)
    text = str(raw).strip()
    if not text or text in {"—", "-"} or is_google_maps_url(text):
        return ""
    if text.startswith("{") and "website" in text:
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, dict):
                url = parse_website_entry_value(parsed)
                if url:
                    return url
        except Exception:
            pass
        match = re.search(r"https?://[^\s'\"\\]+", text)
        if match:
            return match.group(0).rstrip("',}")
    if text.startswith("http"):
        return text
    return ""


def extract_direct_website(record: dict[str, Any]) -> str:
    if not isinstance(record, dict):
        return ""
    for field in DIRECT_WEBSITE_FIELDS:
        raw = record.get(field)
        if raw is None:
            continue
        url = coerce_website_url(raw)
        if url:
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
        final, ok = validate_website_url(website, job.name)
        if ok:
            log(f"[website] {final}")
            return ParkTarget(name=job.name, website=final, google_maps_url=google_maps_url)
        website = ""

    stored_url, _match_kind = lookup_website_in_store(job.name, websites_store)
    if stored_url and is_generic_operator_home(stored_url):
        stored_url = ""
    if stored_url:
        final, ok = validate_website_url(stored_url, job.name)
        if not ok:
            stored_url = ""
        else:
            stored_url = final
    if stored_url and not force:
        log(f"[website] {stored_url}")
        return ParkTarget(
            name=job.name,
            website=stored_url,
            google_maps_url=google_maps_url,
        )

    log(f"[website missing] {job.name}")

    if has_stored_website(job.name, websites_store) and not force:
        log(f"[website not found] {job.name}")
        return ParkTarget(name=job.name, website="", google_maps_url=google_maps_url)

    override_url = resolve_valid_website_override(job.name)
    if override_url:
        log(f"[website] {override_url}")
        if not has_stored_website(job.name, websites_store) or force:
            websites_store[job.name] = {
                "website": override_url,
                "confidence": "high",
                "source": "manual override",
                "date_checked": checked,
            }
            save_websites_json_store(websites_path, websites_store)
            log(f"[website saved] {job.name}")
        return ParkTarget(
            name=job.name,
            website=override_url,
            google_maps_url=google_maps_url,
        )

    log(f"[website lookup] {job.name}")
    found = search_website_online(job.name, location_name)
    if found:
        final, ok = validate_website_url(found, job.name)
        if ok:
            log(f"[website] {final}")
            if not has_stored_website(job.name, websites_store) or force:
                websites_store[job.name] = {
                    "website": final,
                    "confidence": "medium",
                    "source": "search",
                    "date_checked": checked,
                }
                save_websites_json_store(websites_path, websites_store)
                log(f"[website saved] {job.name}")
            return ParkTarget(
                name=job.name,
                website=final,
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


def extract_powered_prices(
    text: str,
    *,
    log_decisions: bool = False,
    source_url: str = "",
    page_title: str = "",
) -> list[tuple[float, str]]:
    """Return lowest powered-site price from labelled site blocks (not page-wide)."""
    if not text or is_rejected_price_source(
        source_url=source_url, page_title=page_title, text=text
    ):
        return []

    should_log = log_decisions or DEBUG_MODE
    block_lows: list[tuple[float, str]] = []

    for match in POWERED_BLOCK_LABELS.finditer(text):
        if not _powered_label_is_valid(text, match):
            continue
        label = match.group(0)
        block_text = text[match.start() : match.start() + POWERED_BLOCK_WINDOW]

        if _block_is_unpowered(label, block_text):
            if should_log:
                log(f'[price rejected] block skipped near "{label}" (unpowered)')
            continue

        block_prices = _prices_in_block(block_text, label, should_log=should_log)
        if not block_prices:
            continue

        non_member = [(a, c) for a, c in block_prices if not MEMBERSHIP_RE.search(c)]
        pool = non_member if non_member else block_prices
        best = min(pool, key=lambda x: x[0])
        block_lows.append(best)

    if not block_lows:
        return []

    best_amount, best_ctx = min(block_lows, key=lambda x: x[0])
    return [(best_amount, best_ctx)]


def try_extract_browser_prices(
    park_name: str,
    text: str,
    source_url: str,
    page_title: str = "",
    *,
    log_decisions: bool = True,
) -> PriceResult | None:
    """Extract price from visible page text; handle RMS pages without date retries."""
    prices = extract_powered_prices(
        text,
        log_decisions=log_decisions,
        source_url=source_url,
        page_title=page_title,
    )
    if prices:
        amount = prices[0][0]
        log_price_found(park_name, amount, confidence="medium", method="browser")
        return browser_price_success(amount, source_url)

    if is_rms_url(source_url):
        if (
            page_has_hidden_booking_fields_from_text(text)
            or is_interactive_date_ui("", text)
            or "powered by rms" in text.lower()
        ):
            return missing_result(
                "RMS booking engine requires interactive date selection",
                source_url=source_url,
                blocked=True,
            )
    return None


def page_has_hidden_booking_fields_from_text(text: str) -> bool:
    return bool(
        re.search(
            r"\b(arrival|departure|check[- ]?in|check[- ]?out)\s+date\b",
            text,
            re.I,
        )
        and re.search(r"\b(search\s+availability|equipment\s+type)\b", text, re.I)
    )


def _import_playwright() -> tuple[Any, Any] | tuple[None, None]:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright

        return sync_playwright, PlaywrightTimeout
    except ImportError:
        return None, None


def warn_playwright_missing() -> None:
    global _PLAYWRIGHT_WARNED
    if _PLAYWRIGHT_WARNED:
        return
    _PLAYWRIGHT_WARNED = True
    log("Playwright is not installed.")
    log("Run: pip install playwright")
    log("Then: python -m playwright install chromium")


def score_browser_link_text(text: str) -> int:
    t = re.sub(r"\s+", " ", str(text or "").lower()).strip()
    if not t or len(t) > 80:
        return 0
    best = 0
    for term, score in BROWSER_LINK_TERMS:
        if term in t:
            best = max(best, score)
    return best


def is_interactive_date_ui(html: str, text: str) -> bool:
    blob = f"{html}\n{text}"
    if any(re.search(pat, blob, re.I) for pat in DATE_PICKER_PATTERNS):
        return True
    if re.search(
        r"\b(select|choose|pick)\s+(your\s+)?(dates?|arrival|check[- ]?in)\b",
        text,
        re.I,
    ):
        return True
    return False


def page_has_date_inputs(page: Any) -> bool:
    try:
        if page.locator('input[type="date"]').count() > 0:
            return True
        if page.locator(
            '[class*="datepicker"], [class*="date-picker"], '
            '[id*="datepicker"], [data-testid*="date"]'
        ).count() > 0:
            return True
    except Exception:
        pass
    return False


def collect_browser_link_targets(page: Any, base_url: str) -> list[tuple[str, str, bool]]:
    """Return (label, url, is_click_only) sorted by link relevance."""
    try:
        raw_items: list[dict[str, str]] = page.evaluate(
            """() => {
              const out = [];
              const seen = new Set();
              for (const el of document.querySelectorAll('a, button, [role="button"]')) {
                if (!el || el.offsetParent === null) continue;
                const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                if (!text || text.length > 80) continue;
                const key = text.toLowerCase();
                if (seen.has(key)) continue;
                seen.add(key);
                const href = el.tagName === 'A' ? (el.getAttribute('href') || '') : '';
                out.push({ text, href });
              }
              return out;
            }"""
        )
    except Exception:
        return []

    scored: list[tuple[int, str, str, bool]] = []
    for item in raw_items:
        label = str(item.get("text") or "").strip()
        href = str(item.get("href") or "").strip()
        score = score_browser_link_text(label)
        if score <= 0:
            continue
        if href and href.startswith("#"):
            href = ""
        if href and not href.startswith(("http", "/")):
            href = ""
        url = absolutize_url(base_url, href) if href else ""
        if url and is_rejected_tariff_link(url):
            continue
        if url:
            path = urllib.parse.urlparse(url).path.lower().rstrip("/")
            if path in {"/our-parks", "/book", "/booking"}:
                continue
        scored.append((score, label, url, not bool(href)))

    scored.sort(key=lambda x: (-x[0], x[1]))
    return [(label, url, click_only) for _, label, url, click_only in scored[:12]]


def browser_price_success(amount: float, source_url: str) -> PriceResult:
    display_amount = int(amount) if float(amount).is_integer() else amount
    return PriceResult(
        display=f"${display_amount}/night",
        price=amount,
        source_url=source_url,
        confidence="medium",
        method="browser",
    )


def browser_tariff_price_success(amount: float, source_url: str) -> PriceResult:
    display_amount = int(amount) if float(amount).is_integer() else amount
    return PriceResult(
        display=f"${display_amount}/night",
        price=amount,
        source_url=source_url,
        confidence="low",
        method="tariff_search",
    )


def is_rejected_tariff_link(url: str) -> bool:
    if not url:
        return True
    lower = url.lower()
    for bad in REJECTED_TARIFF_LINK_DOMAINS:
        if bad in lower:
            return True
    return False


def score_tariff_link_text(text: str) -> int:
    t = re.sub(r"\s+", " ", str(text or "").lower()).strip()
    if not t or len(t) > 80:
        return 0
    best = 0
    for term, score in TARIFF_LINK_TERMS:
        if term in t:
            best = max(best, score)
    return best


def build_tariff_urls_for_site(entry_url: str) -> list[str]:
    """Official park URL plus likely tariffs/fees/rates paths on the same site."""
    parsed = urllib.parse.urlparse(entry_url)
    if not parsed.netloc:
        return []
    site_base = f"{parsed.scheme}://{parsed.netloc}"
    path = (parsed.path or "").rstrip("/")
    urls: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        key = url.rstrip("/")
        if key and key not in seen:
            seen.add(key)
            urls.append(url)

    add(entry_url)
    for suffix in TARIFF_PAGE_SUFFIXES:
        add(absolutize_url(site_base, suffix))
        if path:
            add(absolutize_url(entry_url, suffix.lstrip("/")))
    return urls


def decode_bing_redirect(href: str) -> str:
    match = re.search(r"u=a1([^&]+)", href)
    if not match:
        return href
    raw = match.group(1)
    pad = raw + "=" * (-len(raw) % 4)
    try:
        return base64.b64decode(pad).decode("utf-8")
    except Exception:
        return href


def fetch_bing_search_result_urls(page: Any, query: str, *, max_results: int = 8) -> list[str]:
    """Use Playwright on Bing when DuckDuckGo HTML search is blocked."""
    urls: list[str] = []
    seen: set[str] = set()
    try:
        page.goto(
            "https://www.bing.com/search?q=" + urllib.parse.quote_plus(query),
            wait_until="domcontentloaded",
            timeout=BROWSER_PAGE_TIMEOUT_MS,
        )
        page.wait_for_timeout(1500)
        hrefs: list[str] = page.evaluate(
            """() => {
              const out = [];
              const seen = new Set();
              for (const a of document.querySelectorAll('#b_results a[href], li.b_algo a[href]')) {
                const href = a.href || '';
                if (!href || seen.has(href)) continue;
                seen.add(href);
                out.push(href);
              }
              return out;
            }"""
        )
    except Exception as exc:
        debug_log(f"[tariff search bing] failed query={query}: {exc}")
        return []

    for href in hrefs:
        url = decode_bing_redirect(href) if "bing.com/ck/" in href else href
        if not url.startswith("http"):
            continue
        if "bing.com" in url.lower() or "microsoft.com" in url.lower():
            continue
        if url in seen or is_rejected_tariff_link(url):
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= max_results:
            break
    return urls


def pick_best_tariff_search_url(
    candidates: list[str], park_name: str, official_website: str = ""
) -> str:
    best_url = ""
    best_score = -9999
    official_host = (
        urllib.parse.urlparse(official_website).netloc.lower() if official_website else ""
    )
    for url in candidates:
        score = score_website_candidate(url, park_name)
        host = urllib.parse.urlparse(url).netloc.lower()
        if official_host and official_host in host:
            score += 100
        path = urllib.parse.urlparse(url).path.lower()
        for term in ("tariff", "fee", "rate", "camping", "powered", "accommodation"):
            if term in path:
                score += 12
        debug_log(f"[tariff search candidate] {park_name} score={score} url={url}")
        if score > best_score:
            best_score = score
            best_url = url

    if best_url and best_score >= 12:
        return best_url
    return ""


def search_tariff_official_url(
    park_name: str,
    official_website: str = "",
    *,
    page: Any | None = None,
) -> str:
    """Return the top official URL from tariff-focused web search."""
    candidates: list[str] = []
    seen: set[str] = set()
    for template in TARIFF_SEARCH_QUERIES:
        query = template.format(name=park_name)
        debug_log(f"[tariff search query] {park_name} -> {query}")
        for url in fetch_search_result_urls(query, max_results=6):
            if url in seen or is_rejected_tariff_link(url):
                continue
            seen.add(url)
            candidates.append(url)
        if page is not None:
            for url in fetch_bing_search_result_urls(page, query, max_results=6):
                if url in seen or is_rejected_tariff_link(url):
                    continue
                seen.add(url)
                candidates.append(url)
        time.sleep(0.5)

    return pick_best_tariff_search_url(candidates, park_name, official_website)


def collect_tariff_link_targets(page: Any, base_url: str) -> list[tuple[str, str, bool]]:
    """Return (label, url, is_click_only) for tariffs/fees/rates links — no social OTAs."""
    try:
        raw_items: list[dict[str, str]] = page.evaluate(
            """() => {
              const out = [];
              const seen = new Set();
              for (const el of document.querySelectorAll('a, button, [role="button"]')) {
                if (!el || el.offsetParent === null) continue;
                const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                if (!text || text.length > 80) continue;
                const key = text.toLowerCase();
                if (seen.has(key)) continue;
                seen.add(key);
                const href = el.tagName === 'A' ? (el.getAttribute('href') || '') : '';
                out.push({ text, href });
              }
              return out;
            }"""
        )
    except Exception:
        return []

    scored: list[tuple[int, str, str, bool]] = []
    for item in raw_items:
        label = str(item.get("text") or "").strip()
        href = str(item.get("href") or "").strip()
        score = score_tariff_link_text(label)
        if score <= 0:
            continue
        if href and href.startswith("#"):
            href = ""
        if href and not href.startswith(("http", "/")):
            href = ""
        url = absolutize_url(base_url, href) if href else ""
        if url and is_rejected_tariff_link(url):
            continue
        scored.append((score, label, url, not bool(href)))

    scored.sort(key=lambda x: (-x[0], x[1]))
    return [(label, url, click_only) for _, label, url, click_only in scored[:10]]


def _apply_browser_fallbacks(
    park: ParkTarget,
    search_date: date,
    html_result: PriceResult,
    *,
    use_browser: bool,
) -> PriceResult:
    if not use_browser or html_result.price is not None:
        return html_result

    if park.website:
        browser_result = fetch_price_with_browser(park, search_date)
        if browser_result.price is not None:
            return browser_result
        html_result = _prefer_browser_failure(html_result, browser_result)

    if html_result.failure_reason in TARIFF_FALLBACK_REASONS:
        tariff_result = fetch_price_tariff_fallback(park, html_result.failure_reason)
        if tariff_result.price is not None:
            return tariff_result
        return missing_result(
            "No reliable powered site price found",
            source_url=tariff_result.source_url or html_result.source_url or park.website,
        )
    return html_result


def _prefer_browser_failure(html_result: PriceResult, browser_result: PriceResult) -> PriceResult:
    if browser_result.failure_reason in {
        "Booking engine requires interactive date selection",
        "RMS booking engine requires interactive date selection",
    }:
        return browser_result
    if browser_result.failure_reason == "Timeout" and html_result.failure_reason != "Timeout":
        return browser_result
    if browser_result.failure_reason == "No powered site found" and html_result.failure_reason in {
        "Website unreachable",
        "No rate found",
    }:
        return browser_result
    return html_result


def fetch_price_tariff_fallback(park: ParkTarget, prior_reason: str) -> PriceResult:
    """Second browser pass: web search + tariffs/fees pages for powered site rates."""
    sync_playwright, PlaywrightTimeout = _import_playwright()
    if sync_playwright is None:
        warn_playwright_missing()
        return missing_result(
            "No reliable powered site price found",
            source_url=park.website or "",
        )

    log(f"[tariff fallback] {park.name} after {prior_reason}")
    deadline = time.monotonic() + BROWSER_PARK_MAX_SECONDS
    last_url = park.website or ""

    def remaining_ms() -> int:
        return max(1000, int((deadline - time.monotonic()) * 1000))

    def timed_out() -> bool:
        return time.monotonic() >= deadline

    site_entries: list[str] = []
    seen_hosts: set[str] = set()

    def add_site(url: str) -> None:
        if not url:
            return
        if not url.startswith("http"):
            url = f"https://{url.lstrip('/')}"
        host = urllib.parse.urlparse(url).netloc.lower()
        if host and host not in seen_hosts:
            seen_hosts.add(host)
            site_entries.append(url)

    for override_url in TARIFF_ENTRY_URL_OVERRIDES.get(park.name, []):
        add_site(override_url)

    if park.website:
        add_site(park.website)

    urls_to_scan: list[str] = []
    for site_entry in site_entries:
        urls_to_scan.extend(build_tariff_urls_for_site(site_entry))

    seen_scan: set[str] = set()
    for url in urls_to_scan:
        key = url.rstrip("/")
        if key in seen_scan:
            continue
        seen_scan.add(key)
        final, html, _, issue = fetch_url(url)
        if not html or issue in {"timeout", "unreachable"}:
            continue
        prices = extract_powered_prices(
            html_to_text(html),
            log_decisions=DEBUG_MODE,
            source_url=final,
        )
        if prices:
            amount = prices[0][0]
            log_price_found(park.name, amount, confidence="low", method="tariff_search")
            return browser_tariff_price_success(amount, final)

    if not site_entries and not park.website:
        return missing_result("No reliable powered site price found", source_url=last_url)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()

            try:
                search_url = search_tariff_official_url(
                    park.name, park.website, page=page
                )
                if search_url:
                    log(f"[tariff search] Top official result -> {search_url}")
                    add_site(search_url)
                    for extra in build_tariff_urls_for_site(search_url):
                        if extra not in urls_to_scan:
                            urls_to_scan.append(extra)

                for site_entry in site_entries:
                    if timed_out():
                        break
                    for url in build_tariff_urls_for_site(site_entry):
                        if timed_out():
                            break
                        page.set_default_timeout(min(BROWSER_PAGE_TIMEOUT_MS, remaining_ms()))
                        try:
                            log(f"[tariff fallback] Opening {park.name} -> {url}")
                            page.goto(
                                url,
                                wait_until="domcontentloaded",
                                timeout=min(BROWSER_PAGE_TIMEOUT_MS, remaining_ms()),
                            )
                            last_url = page.url
                            if is_rejected_rate_page(last_url):
                                continue
                            found = try_extract_browser_prices(
                                park.name,
                                page.inner_text("body"),
                                last_url,
                                page.title(),
                                log_decisions=True,
                            )
                            if found and found.price is not None:
                                return browser_tariff_price_success(
                                    found.price, found.source_url
                                )
                        except PlaywrightTimeout:
                            if timed_out():
                                break
                            continue
                        except Exception as exc:
                            debug_log(f"[tariff fallback] page failed {url}: {exc}")
                            continue

                        for label, target_url, click_only in collect_tariff_link_targets(
                            page, page.url
                        ):
                            if timed_out():
                                break
                            if click_only and not target_url:
                                locator = page.locator(
                                    f'a:has-text("{label[:30]}"), button:has-text("{label[:30]}")'
                                )
                                if not safe_click(
                                    locator,
                                    timeout=min(BROWSER_PAGE_TIMEOUT_MS, remaining_ms()),
                                    label=label,
                                ):
                                    continue
                                try:
                                    page.wait_for_load_state(
                                        "domcontentloaded",
                                        timeout=min(BROWSER_PAGE_TIMEOUT_MS, remaining_ms()),
                                    )
                                except Exception:
                                    pass
                                log(f"[browser] clicked {label} -> {page.url}")
                            elif (
                                target_url
                                and not is_rejected_tariff_link(target_url)
                                and not is_social_url(target_url)
                            ):
                                log(f"[browser] clicked {label} -> {target_url}")
                                try:
                                    page.goto(
                                        target_url,
                                        wait_until="domcontentloaded",
                                        timeout=min(BROWSER_PAGE_TIMEOUT_MS, remaining_ms()),
                                    )
                                except Exception:
                                    continue
                            else:
                                continue

                            last_url = page.url
                            if is_rejected_rate_page(last_url):
                                continue
                            found = try_extract_browser_prices(
                                park.name,
                                page.inner_text("body"),
                                last_url,
                                page.title(),
                                log_decisions=True,
                            )
                            if found and found.price is not None:
                                return browser_tariff_price_success(
                                    found.price, found.source_url
                                )
            finally:
                browser.close()
    except Exception as exc:
        debug_log(f"[tariff fallback] error {park.name}: {exc}")

    return missing_result("No reliable powered site price found", source_url=last_url)


def fetch_price_with_browser(park: ParkTarget, _search_date: date) -> PriceResult:
    """Playwright fallback when static HTML extraction finds no powered site price."""
    sync_playwright, PlaywrightTimeout = _import_playwright()
    if sync_playwright is None:
        warn_playwright_missing()
        return missing_result("No powered site found", source_url=park.website)

    if not park.website:
        return missing_result("No website")

    deadline = time.monotonic() + BROWSER_PARK_MAX_SECONDS
    start_url = (
        park.website
        if park.website.startswith("http")
        else f"https://{park.website.lstrip('/')}"
    )
    start_urls = [start_url]
    if "big4.com.au" in start_url.lower():
        accommodation = f"{start_url.rstrip('/')}/accommodation"
        if accommodation not in start_urls:
            start_urls.insert(0, accommodation)
    log(f"[browser] opening {park.name} -> {start_urls[0]}")

    def remaining_ms() -> int:
        left = deadline - time.monotonic()
        return max(1000, int(left * 1000))

    def timed_out() -> bool:
        return time.monotonic() >= deadline

    saw_date_ui = False
    last_url = start_url

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()
            page.set_default_timeout(min(BROWSER_PAGE_TIMEOUT_MS, remaining_ms()))

            try:
                rms_failure: PriceResult | None = None
                for open_url in start_urls:
                    if timed_out():
                        break
                    page.goto(
                        open_url,
                        wait_until="domcontentloaded",
                        timeout=min(BROWSER_PAGE_TIMEOUT_MS, remaining_ms()),
                    )
                    last_url = page.url
                    if is_rejected_rate_page(last_url):
                        continue
                    found = try_extract_browser_prices(
                        park.name,
                        page.inner_text("body"),
                        last_url,
                        page.title(),
                        log_decisions=True,
                    )
                    if found and found.price is not None:
                        return found
                    if (
                        found
                        and found.failure_reason
                        == "RMS booking engine requires interactive date selection"
                    ):
                        rms_failure = found

                if rms_failure and len(start_urls) == 1:
                    return rms_failure

                visited: set[str] = set()

                def scan_current(label: str = "home") -> PriceResult | None:
                    nonlocal saw_date_ui, last_url
                    last_url = page.url
                    if last_url in visited or is_rejected_rate_page(last_url):
                        return None
                    visited.add(last_url)

                    html = page.content()
                    text = page.inner_text("body")
                    if is_rms_url(last_url):
                        return try_extract_browser_prices(
                            park.name,
                            text,
                            last_url,
                            page.title(),
                            log_decisions=True,
                        )

                    if (
                        is_interactive_date_ui(html, text)
                        or page_has_date_inputs(page)
                        or page_has_hidden_booking_fields(page)
                    ):
                        saw_date_ui = True

                    return try_extract_browser_prices(
                        park.name,
                        text,
                        last_url,
                        page.title(),
                        log_decisions=True,
                    )

                found = scan_current()
                if found and found.price is not None:
                    return found
                if (
                    found
                    and found.failure_reason
                    == "RMS booking engine requires interactive date selection"
                ):
                    rms_failure = found

                targets = collect_browser_link_targets(page, page.url)
                for label, target_url, click_only in targets:
                    if timed_out():
                        return missing_result(
                            "Timeout",
                            source_url=last_url,
                        )

                    page.set_default_timeout(min(BROWSER_PAGE_TIMEOUT_MS, remaining_ms()))

                    try:
                        if click_only:
                            locator = page.get_by_role(
                                "button", name=re.compile(re.escape(label[:40]), re.I)
                            )
                            if locator.count() == 0:
                                locator = page.locator(
                                    f'a:has-text("{label[:30]}"), button:has-text("{label[:30]}")'
                                )
                            if not safe_click(
                                locator,
                                timeout=min(BROWSER_PAGE_TIMEOUT_MS, remaining_ms()),
                                label=label,
                            ):
                                continue
                            try:
                                page.wait_for_load_state(
                                    "domcontentloaded",
                                    timeout=min(BROWSER_PAGE_TIMEOUT_MS, remaining_ms()),
                                )
                            except Exception:
                                pass
                            log(f"[browser] clicked {label} -> {page.url}")
                        else:
                            if (
                                not target_url
                                or is_social_url(target_url)
                                or is_rejected_rate_page(target_url)
                            ):
                                continue
                            log(f"[browser] clicked {label} -> {target_url}")
                            page.goto(
                                target_url,
                                wait_until="domcontentloaded",
                                timeout=min(BROWSER_PAGE_TIMEOUT_MS, remaining_ms()),
                            )
                    except PlaywrightTimeout:
                        if timed_out():
                            return missing_result("Timeout", source_url=last_url)
                        continue
                    except Exception as exc:
                        debug_log(f"[browser] navigation failed {label}: {exc}")
                        continue

                    found = scan_current(label)
                    if found and found.price is not None:
                        return found
                    if (
                        found
                        and found.failure_reason
                        == "RMS booking engine requires interactive date selection"
                    ):
                        rms_failure = found

                if rms_failure:
                    return rms_failure

                if saw_date_ui:
                    reason = (
                        "RMS booking engine requires interactive date selection"
                        if is_rms_url(last_url)
                        else "Booking engine requires interactive date selection"
                    )
                    return missing_result(
                        reason,
                        source_url=last_url,
                        blocked=True,
                    )

                return missing_result("No powered site found", source_url=last_url)
            finally:
                browser.close()
    except Exception as exc:
        msg = str(exc)
        if "Executable doesn't exist" in msg or "playwright install" in msg.lower():
            warn_playwright_missing()
            return missing_result("No powered site found", source_url=start_url)
        debug_log(f"[browser] error {park.name}: {exc}")
        if timed_out():
            return missing_result("Timeout", source_url=last_url)
        return missing_result("No powered site found", source_url=last_url)


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


def fetch_price_for_park(
    park: ParkTarget,
    search_date: date,
    *,
    use_browser: bool = False,
) -> PriceResult:
    if not park.website:
        return _apply_browser_fallbacks(
            park,
            search_date,
            missing_result("No website"),
            use_browser=use_browser,
        )

    state = ScrapeState()
    home_url, home_html, home_blocked, home_issue = fetch_url(park.website)

    if home_issue == "timeout":
        state.timeout = True
        return _apply_browser_fallbacks(
            park,
            search_date,
            missing_result("Timeout", source_url=park.website),
            use_browser=use_browser,
        )
    if home_issue == "unreachable" or not home_html:
        state.unreachable = True
        return _apply_browser_fallbacks(
            park,
            search_date,
            missing_result("Website unreachable", source_url=park.website),
            use_browser=use_browser,
        )
    if home_issue == "cloudflare":
        state.cloudflare = True
        state.pages_fetched = 1
        note_page_signals(home_html, state)
        return _apply_browser_fallbacks(
            park,
            search_date,
            missing_result(
                "Cloudflare protection",
                source_url=home_url or park.website,
            ),
            use_browser=use_browser,
        )

    state.pages_fetched = 1
    note_page_signals(home_html, state)

    if home_blocked and not POWERED_POSITIVE.search(html_to_text(home_html)):
        return _apply_browser_fallbacks(
            park,
            search_date,
            missing_result(
                "Booking engine detected",
                source_url=home_url or park.website,
                blocked=True,
            ),
            use_browser=use_browser,
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
                date_prices = extract_powered_prices(
                    html_to_text(html),
                    log_decisions=DEBUG_MODE,
                    source_url=final,
                )
                if date_prices:
                    amount = date_prices[0][0]
                    log_price_found(
                        park.name, amount, confidence="high", method="html"
                    )
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
        if is_rejected_rate_page(page_url):
            continue
        if blocked:
            blocked_any = True
            state.booking_engine = True
        text = html_to_text(html)
        prices = extract_powered_prices(
            text,
            log_decisions=DEBUG_MODE,
            source_url=page_url,
        )
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
        log_price_found(park.name, amount, confidence=confidence, method="html")
        return PriceResult(
            display=f"${int(amount) if amount.is_integer() else amount:.0f}/night",
            price=amount,
            source_url=page_url,
            confidence=confidence,
            blocked=blocked_any,
        )

    reason = classify_failure(state)
    html_result = missing_result(
        reason,
        source_url=home_url or park.website,
        blocked=blocked_any or state.booking_engine,
    )

    return _apply_browser_fallbacks(
        park, search_date, html_result, use_browser=use_browser
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
    if result.method:
        entry["method"] = result.method
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
    use_browser: bool,
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

        log(f"[checking] {job.name}")
        if park.website:
            log(f"[website] {park.website}")

        result = fetch_price_for_park(park, search_date, use_browser=use_browser)
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
            if (
                result.blocked
                or reason
                in {
                    "Booking engine detected",
                    "Booking engine requires interactive date selection",
                    "RMS booking engine requires interactive date selection",
                }
            ):
                report.blocked_engines.append(
                    f"{job.name} ({result.source_url or park.website})"
                )
                if reason == "Booking engine requires interactive date selection":
                    report.manual_follow_up.append(
                        f"{job.name} — booking engine needs interactive date selection"
                    )
                else:
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
    global DEBUG_MODE, BROWSER_MODE

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
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Use Playwright browser fallback when HTML extraction finds no price",
    )
    args = parser.parse_args()
    DEBUG_MODE = bool(args.debug)
    BROWSER_MODE = bool(args.browser)
    if BROWSER_MODE and _import_playwright()[0] is None:
        warn_playwright_missing()

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
            use_browser=BROWSER_MODE,
            report=report,
            limit_remaining=limit_remaining,
        )

    write_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
