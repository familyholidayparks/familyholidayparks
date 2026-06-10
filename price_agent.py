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


@dataclass
class ParkTarget:
    name: str
    website: str = ""


@dataclass
class PriceResult:
    display: str = "—"
    price: float | None = None
    source_url: str = ""
    confidence: str = "missing"
    blocked: bool = False


@dataclass
class RunReport:
    locations_checked: list[str] = field(default_factory=list)
    parks_checked: int = 0
    prices_found: list[str] = field(default_factory=list)
    prices_missing: list[str] = field(default_factory=list)
    blocked_engines: list[str] = field(default_factory=list)
    manual_follow_up: list[str] = field(default_factory=list)


def log(msg: str) -> None:
    print(msg, flush=True)


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


def parse_approved_parks(path: Path) -> list[ParkTarget]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    parks: list[ParkTarget] = []

    if isinstance(data, dict) and isinstance(data.get("parks"), list):
        for item in data["parks"]:
            if isinstance(item, dict):
                name = str(item.get("title") or item.get("name") or "").strip()
                website = str(item.get("website") or "").strip()
                if name:
                    parks.append(ParkTarget(name=name, website=website))
        return parks

    if isinstance(data, dict) and isinstance(data.get("approved_parks"), list):
        for item in data["approved_parks"]:
            if isinstance(item, dict):
                name = str(item.get("title") or item.get("name") or "").strip()
                website = str(item.get("website") or "").strip()
                if name:
                    parks.append(ParkTarget(name=name, website=website))
            elif isinstance(item, str) and item.strip():
                parks.append(ParkTarget(name=item.strip()))
        return parks

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                name = str(item.get("park_name") or item.get("title") or "").strip()
                website = str(item.get("website") or "").strip()
                if name:
                    parks.append(ParkTarget(name=name, website=website))
            elif isinstance(item, str) and item.strip():
                parks.append(ParkTarget(name=item.strip()))
    return parks


def load_park_targets(loc_dir: Path) -> list[ParkTarget]:
    approved_path = loc_dir / "approved-parks.json"
    scores_path = loc_dir / "scores.json"
    websites_path = loc_dir / "websites.json"

    websites_extra: dict[str, str] = {}
    if websites_path.exists():
        try:
            raw = json.loads(websites_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                websites_extra = {str(k): str(v) for k, v in raw.items()}
        except Exception:
            pass

    scores_by_name = index_scores_by_name(scores_path)

    if approved_path.exists():
        parks = parse_approved_parks(approved_path)
        for park in parks:
            if not park.website:
                park.website = str(scores_by_name.get(park.name, {}).get("website") or "").strip()
            if not park.website:
                park.website = websites_extra.get(park.name, "").strip()
        return parks

    if scores_path.exists():
        parks = []
        for item in scores_by_name.values():
            name = str(item.get("park_name") or "").strip()
            if name:
                parks.append(
                    ParkTarget(
                        name=name,
                        website=str(item.get("website") or websites_extra.get(name, "")).strip(),
                    )
                )
        return parks
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


def fetch_url(url: str, timeout: int = 20) -> tuple[str, str, bool]:
    """Return (final_url, html_text, blocked_engine)."""
    if not url:
        return "", "", False
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
    except (urllib.error.URLError, TimeoutError, OSError):
        return url, "", False

    blocked = any(re.search(pat, html, re.I) for pat in BLOCKED_ENGINE_PATTERNS)
    return final_url, html, blocked


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
        return PriceResult(confidence="missing")

    home_url, home_html, home_blocked = fetch_url(park.website)
    if not home_html:
        return PriceResult(confidence="missing", source_url=park.website)

    if home_blocked and not POWERED_POSITIVE.search(html_to_text(home_html)):
        return PriceResult(source_url=home_url or park.website, confidence="missing", blocked=True)

    pages: list[tuple[str, str, bool]] = [(home_url or park.website, home_html, home_blocked)]
    for url in candidate_rate_urls(home_url or park.website, home_html):
        final, html, blocked = fetch_url(url)
        if html:
            pages.append((final, html, blocked))
        time.sleep(0.4)

    # Try date-specific booking URLs when engine not fully blocked on homepage.
    if not home_blocked:
        for book_url in build_booking_urls(home_url or park.website, search_date):
            final, html, blocked = fetch_url(book_url)
            if html:
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

    if blocked_any:
        return PriceResult(source_url=home_url or park.website, confidence="missing", blocked=True)

    return PriceResult(source_url=home_url or park.website, confidence="missing")


def load_existing_prices(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def price_entry_from_result(result: PriceResult, checked: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "display": result.display,
        "price": result.price,
        "type": "Powered site",
        "note": PRICE_NOTE,
        "date_checked": checked,
        "source_url": result.source_url,
        "confidence": result.confidence,
    }
    if result.price is None:
        entry["display"] = "—"
    return entry


def should_skip_park(
    name: str,
    existing: dict[str, Any],
    *,
    missing_only: bool,
    force: bool,
) -> bool:
    if force or not missing_only:
        return False
    current = existing.get(name)
    if not isinstance(current, dict):
        return False
    confidence = str(current.get("confidence") or "").lower()
    price = current.get("price")
    if confidence in {"high", "medium", "low"} and price not in (None, "", 0):
        return True
    return False


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
    parks = load_park_targets(loc_dir)
    if not parks:
        return

    report.locations_checked.append(label)
    prices_path = loc_dir / "prices.json"
    existing = load_existing_prices(prices_path)
    output: dict[str, Any] = dict(existing) if not force else {}
    search_date = baseline_search_date()
    checked = date.today().isoformat()

    log(f"\n[{row['slug']}] {label} — search date {search_date.isoformat()}")

    for park in parks:
        if limit_remaining[0] <= 0:
            return

        if should_skip_park(park.name, existing, missing_only=missing_only, force=force):
            log(f"  [skip] {park.name} (existing price)")
            continue

        limit_remaining[0] -= 1
        report.parks_checked += 1

        result = fetch_price_for_park(park, search_date)
        entry = price_entry_from_result(result, checked)
        output[park.name] = entry

        if result.blocked:
            report.blocked_engines.append(f"{park.name} ({result.source_url or park.website})")

        if result.price is not None and result.confidence != "missing":
            report.prices_found.append(
                f"{park.name} {entry['display']} confidence={result.confidence}"
            )
            log(
                f"[price found] {park.name} {entry['display']} "
                f"confidence={result.confidence}"
            )
            if result.blocked:
                report.manual_follow_up.append(
                    f"{park.name} — verify date-specific rate (booking engine detected)"
                )
        elif result.blocked:
            report.manual_follow_up.append(
                f"{park.name} — booking engine blocked automated price search"
            )
            report.prices_missing.append(park.name)
            log(f"[blocked] {park.name}")
        else:
            report.prices_missing.append(park.name)
            log(f"[price missing] {park.name}")

        time.sleep(0.6)

    prices_path.parent.mkdir(parents=True, exist_ok=True)
    prices_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    log(f"  [saved] {prices_path.relative_to(PROJECT_DIR).as_posix()}")


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

    lines.extend(["", "## Prices missing", ""])
    if report.prices_missing:
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
    args = parser.parse_args()

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
