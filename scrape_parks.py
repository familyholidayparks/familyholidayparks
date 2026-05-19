#!/usr/bin/env python3
"""Scrape holiday park listings and merge into master CSV."""

from __future__ import annotations

import asyncio
import csv
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import Page, async_playwright

ROOT = Path(__file__).resolve().parent
SCRAPED_CSV = ROOT / "holiday_parks_scraped.csv"
MASTER_EXISTING = ROOT / "australian_holiday_parks.csv"
MASTER_OUT = ROOT / "holiday_parks_master.csv"

CSV_COLUMNS = [
    "Park Name",
    "Town/Suburb",
    "State",
    "Chain/Brand",
    "Source",
    "Website",
]

# Sources refreshed by scraping — safe to remove from base before re-merging
SCRAPED_SOURCES_REPLACE = frozenset({
    "GDay",
    "Kui",
    "FamilyParks",
    "Aspen",
    "LakeMac",
    "SunshineCoast",
    "WestBeach",
})

# Manually curated sources — never remove from base
PROTECTED_SOURCES = frozenset({
    "Council",
    "NRMA",
    "Council/NRMA",
})

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

BROWSER_HEADERS = {
    "Accept-Language": "en-AU,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

AU_STATES = {
    "ACT",
    "NSW",
    "NT",
    "QLD",
    "SA",
    "TAS",
    "VIC",
    "WA",
    "AUSTRALIAN CAPITAL TERRITORY",
    "NEW SOUTH WALES",
    "NORTHERN TERRITORY",
    "QUEENSLAND",
    "SOUTH AUSTRALIA",
    "TASMANIA",
    "VICTORIA",
    "WESTERN AUSTRALIA",
}

SKIP_LOCATION_RE = re.compile(
    r"\b(NZ|NEW\s+ZEALAND|UK|UNITED\s+KINGDOM|ENGLAND|SCOTLAND|WALES)\b",
    re.I,
)

GDAY_STATE_MAP = {
    "act": "ACT",
    "nsw": "NSW",
    "nt": "NT",
    "qld": "QLD",
    "sa": "SA",
    "tas": "TAS",
    "vic": "VIC",
    "wa": "WA",
}

STATE_ALIASES = {
    "AUSTRALIAN CAPITAL TERRITORY": "ACT",
    "NEW SOUTH WALES": "NSW",
    "NORTHERN TERRITORY": "NT",
    "QUEENSLAND": "QLD",
    "SOUTH AUSTRALIA": "SA",
    "TASMANIA": "TAS",
    "VICTORIA": "VIC",
    "WESTERN AUSTRALIA": "WA",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def clean_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def normalize_state(state: str) -> str:
    state = state.strip().upper()
    if not state:
        return ""
    if state in GDAY_STATE_MAP.values():
        return state
    if state in STATE_ALIASES:
        return STATE_ALIASES[state]
    if state.lower() in GDAY_STATE_MAP:
        return GDAY_STATE_MAP[state.lower()]
    return state


def should_skip_location(town: str, state: str, url: str = "") -> bool:
    blob = f"{town} {state} {url}"
    if SKIP_LOCATION_RE.search(blob):
        return True
    if "new-zealand" in url.lower() or "/nz/" in url.lower():
        return True
    state_up = state.strip().upper()
    if state_up and state_up not in AU_STATES and state_up not in GDAY_STATE_MAP.values():
        if any(x in state_up for x in ("NZ", "UK", "NORTH", "SOUTH")) and "AU" not in state_up:
            return True
    return False


def slug_to_name(slug: str) -> str:
    return re.sub(r"\s+", " ", slug.replace("-", " ").title()).strip()


def park_row(
    name: str,
    town: str,
    state: str,
    chain: str,
    source: str,
    website: str,
) -> dict[str, str]:
    return {
        "Park Name": name,
        "Town/Suburb": town,
        "State": state,
        "Chain/Brand": chain,
        "Source": source,
        "Website": website,
    }


def finalize_parks(
    candidates: list[dict[str, str]], label: str
) -> list[dict[str, str]]:
    parks: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in candidates:
        name = row.get("Park Name", "").strip()
        if not name:
            continue
        if re.match(r"^https?://", name, re.I):
            continue
        row["State"] = normalize_state(row.get("State", ""))
        website = row.get("Website", "")
        if should_skip_location(
            row.get("Town/Suburb", ""),
            row.get("State", ""),
            website,
        ):
            continue
        key = normalize_name(name)
        if key in seen:
            continue
        seen.add(key)
        parks.append(row)
    log(f"[{label}] Collected {len(parks)} Australian parks")
    return parks


def parse_town_state_from_text(text: str) -> tuple[str, str]:
    text = re.sub(r"\s+", " ", text.replace("\n", " ").strip())
    if not text:
        return "", ""

    if "," in text:
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if len(parts) >= 2:
            town = ", ".join(parts[:-1])
            state = normalize_state(parts[-1])
            return town, state

    tokens = text.split()
    if tokens:
        last = normalize_state(tokens[-1])
        if last in AU_STATES or last in GDAY_STATE_MAP.values():
            return " ".join(tokens[:-1]), last
    return text, ""


async def wait_after_load(page: Page) -> None:
    await page.wait_for_timeout(3000)


async def scroll_to_bottom(page: Page, pause_ms: int = 700, max_rounds: int = 40) -> None:
    prev_height = 0
    for _ in range(max_rounds):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(pause_ms)
        height = await page.evaluate("document.body.scrollHeight")
        if height == prev_height:
            break
        prev_height = height


async def wait_for_first_card(page: Page, selector: str, timeout_ms: int = 60000) -> None:
    """Wait only until the first match exists in the DOM, then proceed."""
    await page.wait_for_selector(selector, state="attached", timeout=timeout_ms)
    count = await page.locator(selector).count()
    log(f"  Found {count} matching elements — extracting immediately")


async def scrape_gday(page: Page) -> list[dict[str, str]]:
    url = "https://gdayparks.com.au/parks"
    log(f"[GDay] Loading {url}")
    await page.goto(url, wait_until="networkidle", timeout=90000)
    await wait_after_load(page)

    try:
        await page.click("text=Clear filters", timeout=5000)
        await page.wait_for_timeout(1500)
    except Exception:
        pass

    log("[GDay] Waiting for first park card in DOM…")
    await wait_for_first_card(page, ".result[data-property-code]", timeout_ms=90000)
    log("[GDay] Scrolling to load all parks…")
    await scroll_to_bottom(page)

    log("[GDay] Extracting park data…")
    raw = await page.evaluate(
        """
        () => {
            const rows = [];
            for (const card of document.querySelectorAll('.result[data-property-code]')) {
                const title = card.querySelector('.title.view-park-link');
                const name = title?.innerText?.trim() || '';
                if (!name || /view\\s*park/i.test(name)) continue;

                const stateCode = (card.dataset.propertyState || '').toLowerCase();
                const locEl = card.querySelector('.location, .subtitle, [class*="location"], p');
                const locText = locEl?.innerText?.replace(/\\s+/g, ' ').trim() || '';
                const href = title?.href || card.querySelector('a.view-park-link')?.href || '';

                rows.push({ name, stateCode, locText, href });
            }
            return rows;
        }
        """
    )

    parks: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in raw:
        name = row.get("name", "").strip()
        if not name:
            continue

        state = normalize_state(
            GDAY_STATE_MAP.get(row.get("stateCode", "").lower(), "")
        )
        town = ""
        loc_text = row.get("locText", "")
        if loc_text:
            parts = [p.strip() for p in re.split(r"[\n,]+", loc_text) if p.strip()]
            if parts:
                if not state and parts[0].upper() in GDAY_STATE_MAP.values():
                    state = parts[0].upper()
                    town = parts[1] if len(parts) > 1 else ""
                elif parts[0].upper() in GDAY_STATE_MAP.values():
                    state = GDAY_STATE_MAP.get(parts[0].lower(), parts[0].upper())
                    town = parts[1] if len(parts) > 1 else ""
                else:
                    town, parsed_state = parse_town_state_from_text(loc_text)
                    if parsed_state:
                        state = parsed_state

        website = clean_url(row.get("href", ""))
        if should_skip_location(town, state, website):
            continue

        key = normalize_name(name)
        if key in seen:
            continue
        seen.add(key)

        parks.append(
            {
                "Park Name": name,
                "Town/Suburb": town,
                "State": state,
                "Chain/Brand": "G'Day Parks",
                "Source": "GDay",
                "Website": website,
            }
        )

    log(f"[GDay] Collected {len(parks)} Australian parks")
    return parks


async def scrape_kui(page: Page) -> list[dict[str, str]]:
    # /parks returns 404; the live finder is at /find-a-park
    candidates = [
        "https://kuiparks.com.au/parks",
        "https://kuiparks.com.au/find-a-park",
    ]
    loaded_url = None
    for url in candidates:
        log(f"[Kui] Loading {url}")
        response = await page.goto(url, wait_until="networkidle", timeout=90000)
        await wait_after_load(page)
        if response and response.status < 400:
            title = await page.title()
            if "not found" not in title.lower():
                loaded_url = url
                break
        log(f"[Kui] Skipping {url} (not available)")

    if not loaded_url:
        log("[Kui] Could not load parks listing")
        return []

    log("[Kui] Waiting for storepoint location cards…")
    await wait_for_first_card(page, ".storepoint-location", timeout_ms=90000)
    log("[Kui] Scrolling to load all parks…")
    await scroll_to_bottom(page)

    log("[Kui] Extracting park data…")
    raw = await page.evaluate(
        """
        () => {
            const rows = [];
            const seen = new Set();
            const stateTags = ['act', 'nsw', 'nt', 'qld', 'sa', 'tas', 'vic', 'wa'];

            for (const card of document.querySelectorAll('.storepoint-location')) {
                const name = card.querySelector('.storepoint-name')?.innerText?.trim() || '';
                const websiteEl = card.querySelector('a[href*="kuiparks.com.au/parks/"]');
                const href = (websiteEl?.href || '').split('?')[0].replace(/\\/$/, '');
                if (!name || !href || href.endsWith('/parks')) continue;
                if (seen.has(href)) continue;
                seen.add(href);

                let state = '';
                for (const code of stateTags) {
                    if (card.querySelector(`.tag.tag-${code} .tag-text`)) {
                        state = code.toUpperCase();
                        break;
                    }
                }

                let town = '';
                const text = card.innerText.replace(/\\s+/g, ' ').trim();
                const statePost = text.match(
                    /,\\s*(ACT|NSW|NT|QLD|SA|TAS|VIC|WA|Queensland|New South Wales)\\s*,\\s*\\d{4}/i
                );
                if (statePost) {
                    if (!state) {
                        const st = statePost[1].toUpperCase();
                        state = st === 'QUEENSLAND' ? 'QLD'
                            : st === 'NEW SOUTH WALES' ? 'NSW'
                            : st.length <= 3 ? st : st;
                    }
                    const before = text.slice(0, statePost.index).trim();
                    const words = before.split(/\\s+/).filter(Boolean);
                    if (words.length >= 2) {
                        town = words.slice(-2).join(' ');
                    } else if (words.length === 1) {
                        town = words[0];
                    }
                }

                rows.push({ name, town, state, href });
            }
            return rows;
        }
        """
    )

    candidates = [
        park_row(
            row["name"],
            row.get("town", ""),
            normalize_state(row.get("state", "")),
            "Kui Parks",
            "Kui",
            clean_url(row.get("href", "")),
        )
        for row in raw
        if row.get("name") and not re.match(r"^https?://", row["name"], re.I)
    ]
    return finalize_parks(candidates, "Kui")


async def scrape_family(page: Page) -> list[dict[str, str]]:
    url = "https://www.familyparks.com.au/parks/"
    log(f"[FamilyParks] Loading {url}")
    await page.goto(url, wait_until="networkidle", timeout=90000)
    await wait_after_load(page)

    log("[FamilyParks] Waiting for first park card in DOM…")
    await wait_for_first_card(page, ".card", timeout_ms=90000)
    log("[FamilyParks] Scrolling to load all parks…")
    await scroll_to_bottom(page)

    raw = await page.evaluate(
        """
        () => {
            const rows = [];
            for (const card of document.querySelectorAll('.card')) {
                const name = card.querySelector('h3')?.innerText?.trim() || '';
                const h4 = card.querySelector('h4');
                const locText = h4?.innerText?.replace(/\\s+/g, ' ').trim() || '';
                const region = h4?.querySelector('.region')?.innerText?.trim() || '';

                let website = '';
                for (const a of card.querySelectorAll('a[href]')) {
                    const href = a.href;
                    if (!href) continue;
                    if (href.includes('check_availability')) continue;
                    if (href.includes('/parks') && !href.match(/\\/parks\\/[^/?#]+/)) continue;
                    if (href.includes('familyparks.com.au')) {
                        website = href;
                        break;
                    }
                }
                if (!website) {
                    const any = card.querySelector('a[href*="http"]');
                    website = any?.href || '';
                }

                rows.push({ name, locText, region, website });
            }
            return rows;
        }
        """
    )

    parks: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in raw:
        name = row.get("name", "").strip()
        if not name:
            continue

        region = (row.get("region") or "").strip().upper()
        loc_text = row.get("locText", "")
        town, state = parse_town_state_from_text(loc_text)
        if region:
            state = region
            if town.endswith(region):
                town = town[: -len(region)].strip(" ,")

        website = clean_url(row.get("website", ""))
        if should_skip_location(town, state, website):
            continue

        key = normalize_name(name)
        if key in seen:
            continue
        seen.add(key)

        parks.append(
            {
                "Park Name": name,
                "Town/Suburb": town,
                "State": state,
                "Chain/Brand": "Family Parks Australia",
                "Source": "FamilyParks",
                "Website": website,
            }
        )

    log(f"[FamilyParks] Collected {len(parks)} Australian parks")
    return parks


async def scrape_holiday_haven(page: Page) -> list[dict[str, str]]:
    url = "https://www.holidayhaven.com.au/parks"
    log(f"[HolidayHaven] Loading {url}")
    response = await page.goto(url, wait_until="networkidle", timeout=90000)
    await wait_after_load(page)

    if response and response.status >= 400:
        log(f"[HolidayHaven] Page returned {response.status} — skipping")
        return []

    log("[HolidayHaven] Waiting for first park card in DOM…")
    try:
        await wait_for_first_card(
            page,
            'a[href*="holidayhaven.com.au"], .card, [class*="park"]',
            timeout_ms=30000,
        )
    except Exception:
        log("[HolidayHaven] No park cards found — skipping")
        return []

    log("[HolidayHaven] Scrolling to load all parks…")
    await scroll_to_bottom(page)

    log("[HolidayHaven] Extracting park data…")
    raw = await page.evaluate(
        """
        () => {
            const rows = [];
            const seen = new Set();
            const host = 'holidayhaven.com.au';
            for (const a of document.querySelectorAll('a[href]')) {
                const href = (a.href || '').split('?')[0].replace(/\\/$/, '');
                if (!href.includes(host)) continue;
                if (!/park|caravan|holiday|accommodation|stay/i.test(href)) continue;
                if (href.endsWith('/parks') || href.endsWith('/find-a-park')) continue;
                if (seen.has(href)) continue;
                seen.add(href);

                const card = a.closest('.card, article, li, [class*="park"]') || a;
                const nameEl = card.querySelector('h2, h3, h4, .title, strong') || a;
                const name = (nameEl?.innerText || a.innerText || '').replace(/\\s+/g, ' ').trim();
                const locEl = card.querySelector('.location, .subtitle, p, h4, h5');
                const locText = locEl?.innerText?.replace(/\\s+/g, ' ').trim() || '';

                if (!name || name.length < 3) continue;
                rows.push({ name, locText, href });
            }
            return rows;
        }
        """
    )

    candidates: list[dict[str, str]] = []
    for row in raw:
        name = row.get("name", "").strip()
        town, state = parse_town_state_from_text(row.get("locText", ""))
        candidates.append(
            park_row(
                name, town, state, "Holiday Haven", "Council", clean_url(row.get("href", ""))
            )
        )
    return finalize_parks(candidates, "HolidayHaven")


async def scrape_lake_mac(page: Page) -> list[dict[str, str]]:
    url = "https://lakemacholidayparks.com.au"
    log(f"[LakeMac] Loading {url}")
    await page.goto(url, wait_until="networkidle", timeout=90000)
    await wait_after_load(page)

    log("[LakeMac] Waiting for first park card in DOM…")
    await wait_for_first_card(page, ".park-list a, a[href*='-caravan-park']", timeout_ms=90000)
    log("[LakeMac] Scrolling to load all parks…")
    await scroll_to_bottom(page)

    log("[LakeMac] Extracting park data…")
    raw = await page.evaluate(
        """
        () => {
            const rows = [];
            const seen = new Set();
            for (const a of document.querySelectorAll('.park-list a, a[href*=\"-caravan-park\"]')) {
                let href = (a.href || '').split('?')[0].replace(/\\/$/, '');
                if (!href.includes('lakemacholidayparks.com.au')) continue;
                if (!/-caravan-park$/.test(href)) continue;
                const slug = href.split('/').filter(Boolean).pop() || '';
                if (/find-a-caravan|friendly|group|compare/i.test(slug)) continue;
                if (seen.has(href)) continue;
                seen.add(href);
                const name = slug.replace(/-/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());
                const town = slug.split('-')[0].replace(/\\b\\w/g, c => c.toUpperCase());
                rows.push({ name, town, state: 'NSW', href });
            }
            return rows;
        }
        """
    )

    candidates = [
        park_row(
            row["name"],
            row.get("town", ""),
            row.get("state", "NSW"),
            "Lake Mac Holiday Parks",
            "LakeMac",
            clean_url(row.get("href", "")),
        )
        for row in raw
        if row.get("name")
    ]
    return finalize_parks(candidates, "LakeMac")


async def scrape_sunshine_coast(page: Page) -> list[dict[str, str]]:
    url = "https://sunshinecoastholidayparks.com.au"
    log(f"[SunshineCoast] Loading {url}")
    await page.goto(url, wait_until="networkidle", timeout=90000)
    await wait_after_load(page)

    log("[SunshineCoast] Waiting for first park link in DOM…")
    await wait_for_first_card(page, 'a[href*="/holiday_parks/"]', timeout_ms=90000)
    log("[SunshineCoast] Scrolling to load all parks…")
    await scroll_to_bottom(page)

    log("[SunshineCoast] Extracting park data…")
    raw = await page.evaluate(
        """
        () => {
            const rows = [];
            const seen = new Set();
            for (const a of document.querySelectorAll('a[href*=\"/holiday_parks/\"]')) {
                const href = (a.href || '').split('?')[0].replace(/\\/$/, '');
                if (!href.includes('holiday_parks/')) continue;
                if (seen.has(href)) continue;
                seen.add(href);

                let name = a.innerText.replace(/\\s+/g, ' ').trim();
                const slug = href.split('/').filter(Boolean).pop() || '';
                if (!name || name.length < 4) {
                    name = slug.replace(/_/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());
                }
                const townMatch = name.match(/^(.+?)\\s+(Beach|Family)?\\s*Holiday Park/i);
                const town = townMatch ? townMatch[1].trim() : '';
                rows.push({ name, town, state: 'QLD', href });
            }
            return rows;
        }
        """
    )

    candidates = [
        park_row(
            row["name"],
            row.get("town", ""),
            row.get("state", "QLD"),
            "Sunshine Coast Holiday Parks",
            "SunshineCoast",
            clean_url(row.get("href", "")),
        )
        for row in raw
        if row.get("name")
    ]
    return finalize_parks(candidates, "SunshineCoast")


async def scrape_aspen(page: Page) -> list[dict[str, str]]:
    url = "https://www.aspengroup.com.au/parks"
    log(f"[Aspen] Loading {url}")
    await page.goto(url, wait_until="networkidle", timeout=120000)
    await wait_after_load(page)

    log("[Aspen] Waiting for first park link in DOM…")
    await wait_for_first_card(
        page, 'a[href*="aspenholidayparks.com.au/"]', timeout_ms=90000
    )
    log("[Aspen] Scrolling to load all parks…")
    await scroll_to_bottom(page)

    log("[Aspen] Extracting park data…")
    raw = await page.evaluate(
        """
        () => {
            const rows = [];
            const byHref = new Map();
            for (const a of document.querySelectorAll('a[href*=\"aspenholidayparks.com.au/\"]')) {
                const href = (a.href || '').split('?')[0].replace(/\\/$/, '');
                if (href.endsWith('aspenholidayparks.com.au')) continue;

                const text = a.innerText.replace(/\\s+/g, ' ').trim();
                if (!text) continue;

                const existing = byHref.get(href);
                if (!existing || text.length > existing.text.length) {
                    byHref.set(href, { href, text });
                }
            }
            for (const { href, text } of byHref.values()) {
                let name = text;
                let town = '';
                let state = '';

                const pipe = text.split('|').map(s => s.trim());
                if (pipe.length >= 2) {
                    name = pipe[0];
                    const loc = pipe[1];
                    if (/^(NSW|VIC|QLD|SA|WA|TAS|NT|ACT)$/i.test(loc)) {
                        state = loc.toUpperCase();
                    } else {
                        town = loc;
                    }
                }
                if (pipe.length >= 3 && /^(NSW|VIC|QLD|SA|WA|TAS|NT|ACT)$/i.test(pipe[2])) {
                    state = pipe[2].toUpperCase();
                }
                if (/holiday park/i.test(text) && pipe.length >= 2 && !state) {
                    const st = pipe[1].match(/^(NSW|VIC|QLD|SA|WA|TAS|NT|ACT)$/i);
                    if (st) state = st[0].toUpperCase();
                }
                if (!state) {
                    const slug = href.split('/').pop() || '';
                    if (slug.includes('darwin')) state = 'NT';
                    else if (slug.includes('karratha')) state = 'WA';
                    else if (slug.includes('adelaide') || slug.includes('highway-1')) state = 'SA';
                    else if (slug.includes('merimbula') || slug.includes('tomakin') || slug.includes('port-stephens')) state = 'NSW';
                }
                if (/co-living|coliving/i.test(href + ' ' + name)) continue;

                rows.push({ name, town, state, href });
            }
            return rows;
        }
        """
    )

    candidates = [
        park_row(
            row["name"],
            row.get("town", ""),
            row.get("state", ""),
            "Aspen Holiday Parks",
            "Aspen",
            clean_url(row.get("href", "")),
        )
        for row in raw
        if row.get("name")
    ]
    return finalize_parks(candidates, "Aspen")


async def scrape_west_beach(page: Page) -> list[dict[str, str]]:
    url = "https://westbeachparks.com.au"
    log(f"[WestBeach] Loading {url}")
    await page.goto(url, wait_until="networkidle", timeout=90000)
    await wait_after_load(page)

    log("[WestBeach] Waiting for first accommodation listing in DOM…")
    await wait_for_first_card(
        page, 'a[href*="/holiday-accommodation/"]', timeout_ms=90000
    )
    log("[WestBeach] Scrolling to load all parks…")
    await scroll_to_bottom(page)

    log("[WestBeach] Extracting park data…")
    raw = await page.evaluate(
        """
        () => {
            const rows = [];
            const seen = new Set();
            for (const a of document.querySelectorAll('a[href*=\"/holiday-accommodation/\"]')) {
                const href = (a.href || '').split('?')[0].replace(/\\/$/, '');
                const match = href.match(/holiday-accommodation\\/([^/]+)$/i);
                if (!match) continue;
                const slug = match[1];
                if (slug === 'online-booking') continue;
                if (seen.has(href)) continue;
                seen.add(href);

                let name = a.innerText.replace(/\\s+/g, ' ').trim();
                if (!name || /sleeps|bed|bath|from \\$/i.test(name)) {
                    name = slug.replace(/-/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());
                }
                if (/find out more/i.test(name)) {
                    name = slug.replace(/-/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());
                }
                rows.push({
                    name,
                    town: 'West Beach',
                    state: 'SA',
                    href,
                });
            }
            return rows;
        }
        """
    )

    candidates = [
        park_row(
            row["name"],
            row.get("town", "West Beach"),
            row.get("state", "SA"),
            "West Beach Parks",
            "WestBeach",
            clean_url(row.get("href", "")),
        )
        for row in raw
        if row.get("name")
    ]
    return finalize_parks(candidates, "WestBeach")


def write_csv(
    path: Path, rows: list[dict[str, str]], fieldnames: list[str]
) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in fieldnames})


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or CSV_COLUMNS)
        return fieldnames, list(reader)


def row_for_master(row: dict[str, str], fieldnames: list[str]) -> dict[str, str]:
    return {col: row.get(col, "") for col in fieldnames}


def merge_master(existing_path: Path, scraped: list[dict[str, str]], out_path: Path) -> None:
    if not existing_path.exists():
        raise FileNotFoundError(
            f"Master source not found: {existing_path}. "
            "Place australian_holiday_parks.csv in the project folder."
        )

    log(f"Loading base from {existing_path.name}")
    fieldnames, base_rows = read_csv(existing_path)

    existing_names = {
        normalize_name(row.get("Park Name", ""))
        for row in base_rows
        if row.get("Park Name")
    }

    master_rows: list[dict[str, str]] = []
    removed = 0
    for row in base_rows:
        source = row.get("Source", "").strip()
        if source in PROTECTED_SOURCES:
            master_rows.append(row)
            continue
        if source in SCRAPED_SOURCES_REPLACE:
            removed += 1
            continue
        master_rows.append(row)

    if removed:
        log(
            f"Removed {removed} stale rows from replaceable scraped sources "
            f"({', '.join(sorted(SCRAPED_SOURCES_REPLACE))})"
        )

    added = 0
    for row in scraped:
        key = normalize_name(row.get("Park Name", ""))
        if not key or key in existing_names:
            continue
        master_rows.append(row_for_master(row, fieldnames))
        existing_names.add(key)
        added += 1

    write_csv(out_path, master_rows, fieldnames)
    log(
        f"Saved {out_path.name}: {len(master_rows) - added} kept from base, "
        f"+ {added} new scraped = {len(master_rows)} total"
    )


def merge_csv_files(
    base_path: Path = MASTER_EXISTING,
    scraped_path: Path = SCRAPED_CSV,
    out_path: Path = MASTER_OUT,
) -> None:
    """Merge base + scraped CSV files into master (no scraping)."""
    if not scraped_path.exists():
        raise FileNotFoundError(f"Scraped file not found: {scraped_path}")
    _, scraped_rows = read_csv(scraped_path)
    merge_master(base_path, scraped_rows, out_path)


SCRAPERS = (
    scrape_gday,
    scrape_kui,
    scrape_family,
    scrape_holiday_haven,
    scrape_lake_mac,
    scrape_sunshine_coast,
    scrape_aspen,
    scrape_west_beach,
)


async def run_scrapers() -> list[dict[str, str]]:
    all_parks: list[dict[str, str]] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            locale="en-AU",
            extra_http_headers=BROWSER_HEADERS,
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        page = await context.new_page()

        for scraper in SCRAPERS:
            try:
                parks = await scraper(page)
                all_parks.extend(parks)
            except Exception as exc:
                log(f"ERROR in {scraper.__name__}: {exc}")

        await browser.close()
    return all_parks


async def main() -> None:
    log("Starting holiday park scrape (Playwright / Chromium)")
    scraped = await run_scrapers()

    log(f"Writing {len(scraped)} scraped rows to {SCRAPED_CSV.name}")
    write_csv(SCRAPED_CSV, scraped, CSV_COLUMNS)

    merge_master(MASTER_EXISTING, scraped, MASTER_OUT)
    log("Done.")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--merge-only":
        merge_csv_files()
    else:
        asyncio.run(main())
