#!/usr/bin/env python3
"""Generate a location-specific holiday parks HTML page via Apify + Claude."""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# Apify: compass Google Maps / Places scraper (Google Maps Scraper)
APIFY_ACTOR_SLUG = "compass~crawler-google-places"
APIFY_SYNC_URL = f"https://api.apify.com/v2/acts/{APIFY_ACTOR_SLUG}/run-sync-get-dataset-items"

CLAUDE_MODEL = "claude-sonnet-4-5"

ALLOWED_CATEGORY_TERMS = ("rv park", "campground", "holiday park", "caravan park", "tourist park")

EXTRA_PAGE_CSS = """
  /* Location page: dark hero + comparison table (extends index.html tokens) */
  .hero.hero--dark {
    padding: 4.5rem 0 3.5rem;
    background:
      radial-gradient(circle at 15% 20%, rgba(76, 138, 100, 0.35), transparent 40%),
      radial-gradient(circle at 85% 10%, rgba(255, 255, 255, 0.06), transparent 35%),
      linear-gradient(145deg, #0f2419 0%, #1f4d3a 42%, #142e24 100%);
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  }

  .hero.hero--dark .eyebrow {
    background: rgba(255, 255, 255, 0.12);
    color: var(--sand-100);
    border: 1px solid rgba(255, 255, 255, 0.12);
  }

  .hero.hero--dark h1 {
    color: var(--white);
  }

  .hero.hero--dark p {
    color: rgba(248, 242, 232, 0.88);
    margin-bottom: 0;
  }

  .card-summary {
    color: var(--text-700);
    font-size: 0.92rem;
    line-height: 1.45;
    margin: 0;
  }

  .compare-section {
    margin-bottom: 2.5rem;
  }

  .compare-section h2 {
    font-family: "Fraunces", Georgia, serif;
    font-size: clamp(1.35rem, 2.8vw, 1.85rem);
    color: var(--green-900);
    margin-bottom: 1rem;
  }

  .compare-scroll {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow);
    border: 1px solid rgba(47, 107, 78, 0.16);
  }

  .compare-table {
    width: 100%;
    min-width: 560px;
    border-collapse: collapse;
    background: var(--white);
  }

  .compare-table th,
  .compare-table td {
    padding: 0.85rem 1rem;
    text-align: left;
    font-size: 0.95rem;
    border-bottom: 1px solid var(--sand-200);
    vertical-align: top;
  }

  .compare-table thead th {
    font-family: "Fraunces", Georgia, serif;
    font-weight: 700;
    color: var(--green-900);
    background: var(--sand-100);
  }

  .compare-table tbody th {
    font-weight: 700;
    color: var(--text-900);
    background: #fcfaf6;
    width: 9.5rem;
    white-space: nowrap;
  }

  .compare-table td {
    color: var(--text-700);
  }

  .compare-table tr:last-child th,
  .compare-table tr:last-child td {
    border-bottom: 0;
  }

  .compare-table .cell-strong {
    color: var(--text-900);
    font-weight: 700;
  }

  .compare-table .book-btn {
    margin-top: 0;
    width: 100%;
    box-sizing: border-box;
  }

  .site-nav {
    background: rgba(15, 36, 25, 0.97);
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    position: sticky;
    top: 0;
    z-index: 50;
  }

  .site-nav-inner {
    max-width: 1100px;
    margin: 0 auto;
    padding: 0.85rem 1.25rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
  }

  .site-nav a.logo {
    font-family: "Fraunces", Georgia, serif;
    font-weight: 700;
    font-size: 1.05rem;
    color: var(--sand-100);
    text-decoration: none;
    letter-spacing: 0.02em;
  }

  .site-nav a.logo:hover {
    color: var(--white);
  }

  .site-nav-links {
    display: flex;
    align-items: center;
    gap: 1.25rem;
  }

  .site-nav-links a {
    color: rgba(248, 242, 232, 0.88);
    text-decoration: none;
    font-size: 0.92rem;
    font-weight: 500;
  }

  .site-nav-links a:hover {
    color: var(--white);
  }

  .card-best-for {
    display: inline-block;
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--green-900);
    background: rgba(74, 140, 63, 0.12);
    border: 1px solid rgba(74, 140, 63, 0.28);
    padding: 0.28rem 0.55rem;
    border-radius: 6px;
    margin-bottom: 0.5rem;
  }

  .intro-block {
    max-width: 720px;
    margin: 0 auto 2rem;
    padding: 1.25rem 1.35rem;
    background: var(--white);
    border-radius: var(--radius-lg);
    border: 1px solid rgba(47, 107, 78, 0.14);
    box-shadow: var(--shadow);
  }

  .intro-block p {
    margin: 0;
    font-size: 1.02rem;
    line-height: 1.65;
    color: var(--text-700);
  }

  .owner-cta {
    margin-top: 2.5rem;
    padding: 2rem 1.5rem;
    background: linear-gradient(145deg, #1f4d3a 0%, #142e24 100%);
    border-radius: var(--radius-lg);
    text-align: center;
    border: 1px solid rgba(255, 255, 255, 0.1);
  }

  .owner-cta p {
    margin: 0;
    font-size: 1.05rem;
    line-height: 1.55;
    color: rgba(248, 242, 232, 0.95);
    max-width: 520px;
    margin-left: auto;
    margin-right: auto;
  }

  .owner-cta a {
    color: #f5d08a;
    font-weight: 600;
    text-decoration: underline;
    text-underline-offset: 3px;
  }

  .owner-cta a:hover {
    color: var(--sand-100);
  }
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def log_err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def location_slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "location"


def find_places_payload(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    if isinstance(data, dict):
        for key in ("results", "places", "data", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [data]

    return []


def _collect_categories(place: dict[str, Any]) -> list[str]:
    categories: list[str] = []

    for key in ("categories", "category", "types", "type", "categoryName"):
        value = place.get(key)
        if isinstance(value, str):
            categories.append(value)
        elif isinstance(value, list):
            categories.extend([str(v) for v in value if isinstance(v, (str, int, float))])

    for nested_key in ("attributes", "details"):
        nested = place.get(nested_key)
        if isinstance(nested, dict):
            for key in ("categories", "category", "types", "type"):
                value = nested.get(key)
                if isinstance(value, str):
                    categories.append(value)
                elif isinstance(value, list):
                    categories.extend(
                        [str(v) for v in value if isinstance(v, (str, int, float))]
                    )

    return categories


def is_target_park(place: dict[str, Any]) -> bool:
    categories = _collect_categories(place)
    if not categories:
        return False

    merged = " | ".join(categories).lower()
    return any(term in merged for term in ALLOWED_CATEGORY_TERMS)


def dedupe_places(places: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for p in places:
        pid = p.get("placeId") or p.get("place_id") or p.get("googlePlaceId")
        if isinstance(pid, str) and pid.strip():
            key = f"id:{pid.strip()}"
        else:
            title = str(p.get("title") or p.get("name") or "").strip().lower()
            addr = str(
                p.get("address") or p.get("formatted_address") or ""
            ).strip().lower()
            key = f"n:{title}|{addr}"
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _get(place: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in place and place[k] not in (None, ""):
            return place[k]
        nested = place.get("details")
        if isinstance(nested, dict) and k in nested and nested[k] not in (None, ""):
            return nested[k]
    return None


def normalize_park(place: dict[str, Any], *, location_label: str) -> dict[str, Any]:
    name = _get(place, "title", "name") or "Holiday park"
    city = _get(place, "city", "locality") or ""
    state = _get(place, "state", "administrative_area_level_1") or ""

    rating = _get(place, "totalScore", "rating")
    reviews = _get(place, "reviewsCount", "user_ratings_total", "reviews")

    website = _get(place, "website")
    maps_url = _get(place, "url", "google_maps_url", "googleMapsUrl")

    street = _get(place, "street", "streetAddress") or ""
    parts = [p for p in (street, city, state) if p]
    address = ", ".join(parts) if parts else (
        str(_get(place, "formatted_address", "address") or "")
    )

    beach_km = _get(place, "beach_km", "distanceToBeachKm", "distance_to_beach_km")
    shops_km = _get(place, "shops_km", "distanceToShopsKm", "distance_to_shops_km")

    price_raw = _get(place, "minPrice", "priceFrom", "avgPrice", "price")
    price_level = _get(place, "price_level", "priceLevel")

    region_label = city or location_label
    if state and city:
        region_label = f"{city}, {state}"

    return {
        "name": str(name),
        "region_label": str(region_label),
        "address": str(address) if address else "",
        "rating": rating,
        "reviews": reviews,
        "website": str(website) if website else "",
        "maps_url": str(maps_url) if maps_url else "",
        "beach_km": beach_km,
        "shops_km": shops_km,
        "price_raw": price_raw,
        "price_level": price_level,
        "summary": "",
        "rank_score": 0.0,
        "amenity_badges": amenity_badges_from_place(place),
        "best_for": "",
    }


def rank_score(row: dict[str, Any]) -> float:
    try:
        r = float(row["rating"]) if row.get("rating") is not None else 0.0
    except (TypeError, ValueError):
        r = 0.0
    try:
        n = int(row["reviews"]) if row.get("reviews") is not None else 0
    except (TypeError, ValueError):
        n = 0
    if r <= 0:
        return float(n)
    return r * math.log1p(max(n, 0))


def format_price_display(row: dict[str, Any]) -> str:
    pr = row.get("price_raw")
    if isinstance(pr, (int, float)) and pr > 0:
        return f"${int(round(pr))}"
    if isinstance(pr, str) and pr.strip().startswith("$"):
        return pr.strip()
    pl = row.get("price_level")
    if pl is not None:
        try:
            n = int(pl)
            return {1: "$", 2: "$$", 3: "$$$", 4: "$$$$"}.get(n, "—")
        except (TypeError, ValueError):
            pass
    return "—"


def format_distance_km(value: Any) -> str | None:
    if value is None:
        return None
    try:
        f = float(value)
        if f < 0:
            return None
        if f >= 10:
            return f"{f:.0f} km"
        return f"{f:.1f} km"
    except (TypeError, ValueError):
        return None


def format_rating_line(row: dict[str, Any]) -> str:
    r, n = row.get("rating"), row.get("reviews")
    parts: list[str] = []
    if r is not None:
        try:
            parts.append(f"⭐ {float(r):.1f}")
        except (TypeError, ValueError):
            parts.append("⭐ —")
    else:
        parts.append("⭐ —")
    if n is not None:
        try:
            parts.append(f"({int(n):,})")
        except (TypeError, ValueError):
            parts.append("(—)")
    else:
        parts.append("(—)")
    return " ".join(parts)


def collect_place_text_blob(place: dict[str, Any]) -> str:
    parts: list[str] = []
    parts.append(str(_get(place, "title", "name") or ""))
    parts.append(str(_get(place, "description") or ""))
    parts.append(str(_get(place, "additionalInfo") or ""))
    am = place.get("amenities")
    if am is None:
        am = place.get("amenityCategories")
    if isinstance(am, list):
        parts.extend(str(x) for x in am)
    elif isinstance(am, str):
        parts.append(am)
    nested = place.get("details")
    if isinstance(nested, dict):
        for key in ("description", "amenities", "additionalInfo", "title", "name"):
            v = nested.get(key)
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, list):
                parts.extend(str(x) for x in v)
    return " ".join(parts).lower()


def amenity_badges_from_place(place: dict[str, Any]) -> list[tuple[str, str]]:
    blob = collect_place_text_blob(place)
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    rules: list[tuple[tuple[str, ...], str, str]] = [
        (("pool", "swimming"), "🏊", "Pool"),
        (("playground",), "🛝", "Playground"),
        (("bbq", "barbecue"), "🔥", "BBQ"),
        (("camp kitchen", "campkitchen"), "🍳", "Camp Kitchen"),
        (("pet", "dog"), "🐕", "Pet Friendly"),
        (("wifi", "wi-fi"), "📶", "WiFi"),
        (("laundry",), "🧺", "Laundry"),
    ]
    for keywords, emoji, label in rules:
        if label in seen:
            continue
        if any(kw in blob for kw in keywords):
            seen.add(label)
            out.append((emoji, label))
    return out


def format_amenity_badges_html(badges: list[tuple[str, str]]) -> str:
    if not badges:
        return ""
    return "".join(
        f'<span class="badge">{esc(emoji)} {esc(label)}</span>' for emoji, label in badges
    )


def row_has_known_price(row: dict[str, Any]) -> bool:
    return format_price_display(row) != "—"


def max_rating_in_rows(rows: list[dict[str, Any]]) -> float | None:
    best: float | None = None
    for row in rows:
        r = row.get("rating")
        if r is None:
            continue
        try:
            v = float(r)
        except (TypeError, ValueError):
            continue
        if v > 0 and (best is None or v > best):
            best = v
    return best


def extract_hero_tagline(intro: str) -> str:
    text = intro.strip()
    if not text:
        return ""
    chunk = re.split(r"\n\s*\n", text)[0].strip()
    sentences = re.split(r"(?<=[.!?])\s+", chunk)
    sentences = [s.strip() for s in sentences if s.strip()]
    for s in sentences:
        if re.search(r"famil|kids|children|parents", s, re.I):
            return s[:280]
    return (sentences[0] if sentences else chunk)[:280]


def compute_best_for_labels(top3: list[dict[str, Any]]) -> list[str]:
    n = len(top3)
    labels: list[str | None] = [None] * n

    def rv(i: int) -> float | None:
        try:
            v = float(top3[i].get("rating") or 0)
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None

    def bv(i: int) -> float | None:
        raw = top3[i].get("beach_km")
        if raw is None:
            return None
        try:
            f = float(raw)
        except (TypeError, ValueError):
            return None
        return f if f >= 0 else None

    def pv(i: int) -> float | None:
        pr = top3[i].get("price_raw")
        if isinstance(pr, (int, float)) and pr > 0:
            return float(pr)
        pl = top3[i].get("price_level")
        if pl is not None:
            try:
                return float(int(pl))
            except (TypeError, ValueError):
                pass
        return None

    rating_order = sorted(
        [i for i in range(n) if rv(i) is not None],
        key=lambda i: (-(rv(i) or 0.0), i),
    )
    beach_order = sorted(
        [i for i in range(n) if bv(i) is not None],
        key=lambda i: ((bv(i) or 0.0), i),
    )
    price_order = sorted(
        [i for i in range(n) if pv(i) is not None],
        key=lambda i: ((pv(i) or 0.0), i),
    )

    for i in rating_order:
        if labels[i] is None:
            labels[i] = "Top Rated"
            break
    for i in beach_order:
        if labels[i] is None:
            labels[i] = "Closest to Beach"
            break
    for i in price_order:
        if labels[i] is None:
            labels[i] = "Best Value"
            break

    unassigned = [i for i in range(n) if labels[i] is None]
    unassigned.sort(
        key=lambda i: int(top3[i].get("reviews") or 0),
        reverse=True,
    )
    fallback = ["Most Reviewed", "Family Favourite", "Local Favourite"]
    for j, i in enumerate(unassigned):
        labels[i] = fallback[j] if j < len(fallback) else "Family Favourite"

    return [x or "Family Favourite" for x in labels]


GOLD_COAST_THEME_PARK_TIMES: list[tuple[str, str]] = [
    ("ashmore", "~20 min"),
    ("main beach", "~25 min"),
    ("broadbeach", "~30 min"),
    ("surfers paradise", "~25 min"),
    ("coolangatta", "~45 min"),
]


def gold_coast_theme_park_time(address: str) -> str:
    low = address.lower()
    for suburb, mins in GOLD_COAST_THEME_PARK_TIMES:
        if suburb in low:
            return mins
    return "—"


def is_gold_coast_location(location: str) -> bool:
    return "gold coast" in location.strip().lower()


def extract_font_links_and_style(index_html: str) -> tuple[str, str]:
    font_links = "\n  ".join(
        re.findall(r'<link[^>]+fonts.googleapis[^>]*>|<link[^>]+fonts.gstatic[^>]*>', index_html)
    )
    style_match = re.search(r"<style>(.*?)</style>", index_html, re.DOTALL | re.IGNORECASE)
    style_block = style_match.group(1).strip() if style_match else ""
    return font_links, style_block


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def book_href(row: dict[str, Any]) -> str:
    if row.get("website"):
        return row["website"]
    return row.get("maps_url") or "#"


def build_card_html(row: dict[str, Any]) -> str:
    name = esc(row["name"])
    region = esc(row["region_label"])
    price = esc(format_price_display(row))
    rating_line = esc(format_rating_line(row))
    beach = format_distance_km(row.get("beach_km")) or "—"
    shops = format_distance_km(row.get("shops_km")) or "—"
    href = esc(book_href(row))
    book_rel = "noopener noreferrer sponsored" if row.get("website") else "noopener noreferrer"
    summary_html = ""
    if row.get("summary"):
        summary_html = f'\n              <p class="card-summary">{esc(row["summary"])}</p>'

    best_for = str(row.get("best_for") or "").strip()
    best_for_html = ""
    if best_for:
        best_for_html = f'\n              <span class="card-best-for">{esc(best_for)}</span>'

    badges = row.get("amenity_badges") or []
    badges_html = format_amenity_badges_html(badges)
    amenities_block = ""
    if badges_html:
        amenities_block = f'\n              <div class="amenities">\n                {badges_html}\n              </div>'

    return f"""          <article class="card">
            <div class="card-image">{region}</div>
            <div class="card-body">
              <h3 class="park-name">{name}</h3>{best_for_html}
              <div class="meta-line">
                <span><strong>{price}</strong> / night</span>
                <span>{rating_line}</span>
              </div>{summary_html}{amenities_block}
              <div class="distance">
                <span>Beach: {esc(beach)}</span>
                <span>Shops: {esc(shops)}</span>
              </div>
              <a class="book-btn" href="{href}" target="_blank" rel="{book_rel}">Book Now</a>
            </div>
          </article>
"""


def build_compare_table_html(
    top3: list[dict[str, Any]],
    *,
    location: str,
) -> str:
    if len(top3) < 3:
        return ""

    headers = "".join(f"<th>{esc(r['name'])}</th>" for r in top3)

    def row_cells(getter: Any) -> str:
        return "".join(f"<td>{getter(r)}</td>" for r in top3)

    show_price = any(row_has_known_price(r) for r in top3)
    show_beach = any(format_distance_km(r.get("beach_km")) for r in top3)
    show_shops = any(format_distance_km(r.get("shops_km")) for r in top3)
    show_theme = is_gold_coast_location(location)
    show_amenities = any((r.get("amenity_badges") or []) for r in top3)

    body_rows: list[str] = []

    if show_price:
        price_cells = row_cells(
            lambda r: f'<span class="cell-strong">{esc(format_price_display(r))}</span> / night'
        )
        body_rows.append(
            f"""                <tr>
                  <th scope="row">Price / night</th>
                  {price_cells}
                </tr>"""
        )

    rating_cells = row_cells(lambda r: esc(format_rating_line(r)))
    body_rows.append(
        f"""                <tr>
                  <th scope="row">Rating</th>
                  {rating_cells}
                </tr>"""
    )

    if show_beach:
        beach_cells = row_cells(lambda r: esc(format_distance_km(r.get("beach_km")) or "—"))
        body_rows.append(
            f"""                <tr>
                  <th scope="row">Beach</th>
                  {beach_cells}
                </tr>"""
        )

    if show_shops:
        shops_cells = row_cells(lambda r: esc(format_distance_km(r.get("shops_km")) or "—"))
        body_rows.append(
            f"""                <tr>
                  <th scope="row">Shops</th>
                  {shops_cells}
                </tr>"""
        )

    if show_theme:
        theme_cells = row_cells(
            lambda r: esc(gold_coast_theme_park_time(str(r.get("address") or "")))
        )
        body_rows.append(
            f"""                <tr>
                  <th scope="row">To Theme Parks</th>
                  {theme_cells}
                </tr>"""
        )

    if show_amenities:
        amen_cells = row_cells(lambda r: format_amenity_badges_html(r.get("amenity_badges") or []) or "—")
        body_rows.append(
            f"""                <tr>
                  <th scope="row">Amenities</th>
                  {amen_cells}
                </tr>"""
        )

    link_cells = "".join(
        f'<td><a class="book-btn" href="{esc(book_href(r))}" target="_blank" rel="'
        f'{"noopener noreferrer sponsored" if r.get("website") else "noopener noreferrer"}'
        f'">Book Now</a></td>'
        for r in top3
    )
    body_rows.append(
        f"""                <tr>
                  <th scope="row">Book</th>
                  {link_cells}
                </tr>"""
    )

    tbody = "\n".join(body_rows)

    return f"""        <div class="compare-section">
          <h2>Top 3 parks — side by side</h2>
          <div class="compare-scroll">
            <table class="compare-table">
              <thead>
                <tr>
                  <th scope="col"></th>
                  {headers}
                </tr>
              </thead>
              <tbody>
{tbody}
              </tbody>
            </table>
          </div>
        </div>
"""


def build_page_html(
    *,
    index_html: str,
    rows: list[dict[str, Any]],
    location: str,
    intro_paragraph: str,
    hero_tagline: str,
    park_count: int,
    top_rating: float | None,
) -> str:
    font_links, style_block = extract_font_links_and_style(index_html)
    sorted_rows = sorted(rows, key=lambda r: r.get("rank_score", 0.0), reverse=True)
    top3 = sorted_rows[:3]

    page_title = (
        f"{park_count} Family Holiday Parks near {location} | Family Holiday Parks"
    )
    meta_desc = (
        f"Compare {park_count} family-friendly RV parks, campgrounds, and holiday parks near "
        f"{location}. Ratings, distances and book links — plus what local families love about "
        f"the region."
    )

    cards_inner = "\n".join(build_card_html(r) for r in sorted_rows)
    compare_block = build_compare_table_html(top3, location=location)

    hero_bits: list[str] = [
        f"We found <strong>{park_count}</strong> family-friendly holiday parks in this area."
    ]
    if top_rating is not None:
        hero_bits.append(
            f"The top rating in this list is <strong>{top_rating:.1f}★</strong>."
        )
    if hero_tagline.strip():
        hero_bits.append(esc(hero_tagline.strip()))
    hero_sub_html = " ".join(hero_bits)

    intro_section = ""
    ip = intro_paragraph.strip()
    if ip:
        intro_section = f"""
        <div class="intro-block">
          <p>{esc(ip)}</p>
        </div>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="description" content="{esc(meta_desc)}">
  <title>{esc(page_title)}</title>
  {font_links}
  <style>
{style_block}

{EXTRA_PAGE_CSS.strip()}
  </style>
</head>
<body>
  <nav class="site-nav" aria-label="Primary">
    <div class="site-nav-inner">
      <a class="logo" href="index.html">Family Holiday Parks</a>
      <div class="site-nav-links">
        <a href="index.html">All Locations</a>
      </div>
    </div>
  </nav>

  <header class="hero hero--dark">
    <div class="container hero-content">
      <span class="eyebrow">Family Holiday Parks</span>
      <h1>{esc(location)}</h1>
      <p>
        {hero_sub_html}
      </p>
    </div>
  </header>

  <main>
    <section class="featured">
      <div class="container">
{intro_section}
        <div class="section-header">
          <h2>Holiday parks near {esc(location)}</h2>
          <p>
            Listings are filtered to RV park, campground, holiday park, or similar categories.
            Rankings combine star rating and review volume; the top parks include AI-written family
            snapshots based on name, rating, review count, and address.
          </p>
        </div>
{compare_block}
        <div class="cards">
{cards_inner}
        </div>

        <p class="affiliate-note">
          Affiliate disclosure: We may earn a commission if you book through links on this page, at no extra cost to you.
        </p>

        <section class="owner-cta" aria-label="List your park">
          <p>
            Own a holiday park in {esc(location)}?
            <a href="mailto:pm@familyholidayparks.com.au">Get a free listing on Family Holiday Parks</a>
            — reach thousands of families planning their next trip.
          </p>
        </section>
      </div>
    </section>
  </main>

  <footer>
    © 2026 Family Holiday Parks. Compare smarter, holiday happier.
  </footer>
</body>
</html>
"""


def run_apify_google_maps(token: str, location: str, *, timeout_sec: int = 900) -> list[dict[str, Any]]:
    payload = {
        "searchStringsArray": ["holiday park", "RV park", "campground"],
        "locationQuery": location,
        "maxCrawledPlacesPerSearch": 40,
        "language": "en",
    }
    url = f"{APIFY_SYNC_URL}?token={urllib.parse.quote(token, safe='')}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Apify HTTP {e.code}: {err_body[:800]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Apify request failed: {e}") from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Apify returned non-JSON: {raw[:300]}") from e

    places = find_places_payload(data)
    return [p for p in places if isinstance(p, dict)]


def _strip_code_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def parse_summaries_json(text: str, expected: int) -> list[str]:
    cleaned = _strip_code_fence(text)
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        log_err("Warning: Claude returned non-JSON; summaries will be empty.")
        return [""] * expected

    if isinstance(obj, dict) and "summaries" in obj:
        arr = obj["summaries"]
    elif isinstance(obj, list):
        arr = obj
    elif isinstance(obj, dict):
        arr = [obj[str(i)] for i in range(expected) if str(i) in obj]
        if len(arr) != expected:
            arr = list(obj.values())
    else:
        return []

    out: list[str] = []
    for i, item in enumerate(arr):
        if isinstance(item, str):
            out.append(item.strip())
        elif isinstance(item, dict) and "text" in item:
            out.append(str(item["text"]).strip())
        elif item is not None:
            out.append(str(item).strip())
    while len(out) < expected:
        out.append("")
    return out[:expected]


def fetch_claude_summaries(
    api_key: str,
    parks_for_ai: list[dict[str, Any]],
    *,
    location: str,
) -> list[str]:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError(
            "The 'anthropic' package is required. Install with: pip install anthropic"
        ) from e

    lines = []
    for i, p in enumerate(parks_for_ai):
        lines.append(
            f"{i}. Name: {p.get('name', '')}\n"
            f"   Rating: {p.get('rating', '')}\n"
            f"   Reviews: {p.get('reviews', '')}\n"
            f"   Address: {p.get('address', '')}"
        )
    block = "\n".join(lines)
    user_prompt = f"""Location context: {location}

You are writing for a family holiday travel site. For each numbered park below, write exactly two sentences: friendly, accurate, non-hyped, suitable for parents planning a trip. Do not invent facilities; you may only infer general appeal from the name, rating, review count, and address.

Parks:
{block}

Respond with JSON only in this exact shape (array length {len(parks_for_ai)}):
{{"summaries": ["two sentences for park 0", "two sentences for park 1", ...]}}
"""

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text_parts: list[str] = []
    for block_obj in message.content:
        if getattr(block_obj, "type", None) == "text":
            text_parts.append(block_obj.text)
    combined = "".join(text_parts)
    return parse_summaries_json(combined, len(parks_for_ai))


def fetch_claude_intro(api_key: str, *, location: str) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError(
            "The 'anthropic' package is required. Install with: pip install anthropic"
        ) from e

    user_prompt = (
        "You are a passionate Australian family travel writer who has visited hundreds of "
        "holiday parks. Write a 3-4 sentence intro paragraph for a page about family holiday "
        f"parks near {location}. Include what makes this region special for families, the best "
        "time to visit, and one specific local tip that shows real knowledge of the area. Write "
        "in a warm, helpful, human tone. No generic phrases like great destination or something "
        "for everyone."
    )

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text_parts: list[str] = []
    for block_obj in message.content:
        if getattr(block_obj, "type", None) == "text":
            text_parts.append(block_obj.text)
    combined = "".join(text_parts).strip()
    return _strip_code_fence(combined)


def git_commit_and_push(project_dir: Path, message: str) -> None:
    try:
        log("Running: git add -A")
        r_add = subprocess.run(
            ["git", "add", "-A"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if r_add.returncode != 0:
            log_err(f"git add failed: exit {r_add.returncode}")
            if r_add.stderr:
                log_err(r_add.stderr.strip())
            if "not a git repository" in (r_add.stderr or "").lower():
                log_err(
                    "Initialize the repo first (git init) and add a remote before push will succeed."
                )
            return

        r_empty = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if r_empty.returncode == 0:
            log("Nothing to commit (no staged changes). Attempting git push anyway.")
        else:
            log(f"Running: git commit -m {message!r}")
            r_commit = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=project_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if r_commit.returncode != 0:
                log_err(f"git commit failed: exit {r_commit.returncode}")
                if r_commit.stdout:
                    log(r_commit.stdout.strip())
                if r_commit.stderr:
                    log_err(r_commit.stderr.strip())
                return
            if r_commit.stdout.strip():
                log(r_commit.stdout.strip())

        log("Running: git push")
        r_push = subprocess.run(
            ["git", "push"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if r_push.returncode != 0:
            log_err(f"git push failed: exit {r_push.returncode}")
            if r_push.stdout:
                log(r_push.stdout.strip())
            if r_push.stderr:
                log_err(r_push.stderr.strip())
            log(
                "Push failed (initialize git, add a remote, or check auth). "
                "The HTML file was still written successfully."
            )
        elif r_push.stdout.strip():
            log(r_push.stdout.strip())
    except FileNotFoundError:
        log_err("Git executable not found; skipping git steps.")
    except OSError as e:
        log_err(f"Git error: {e}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Scrape holiday parks via Apify, summarise with Claude, emit HTML."
    )
    p.add_argument(
        "location",
        type=str,
        help='Location name (e.g. "Gold Coast Central")',
    )
    p.add_argument(
        "--index",
        default="index.html",
        help="Path to design reference HTML (default: index.html)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    project_dir = Path(__file__).resolve().parent
    location = str(args.location).strip()
    if not location:
        log_err("Error: location must be non-empty.")
        return 1

    slug = location_slug(location)
    output_path = project_dir / f"{slug}.html"
    index_path = (project_dir / args.index).resolve()

    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        log_err("Error: APIFY_TOKEN environment variable is not set.")
        return 1

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not anthropic_key:
        log_err("Error: ANTHROPIC_API_KEY environment variable is not set.")
        return 1

    if not index_path.exists():
        log_err(f"Error: index.html reference not found: {index_path}")
        return 1

    log(f"Location: {location}")
    log(f"Output file: {output_path.name}")

    log("Calling Apify Google Maps scraper (this may take several minutes)...")
    try:
        raw_places = run_apify_google_maps(token, location)
    except RuntimeError as e:
        log_err(f"Apify error: {e}")
        return 1
    except Exception as e:
        log_err(f"Unexpected Apify error: {e}")
        return 1

    log(f"Received {len(raw_places)} place records from Apify.")

    deduped = dedupe_places(raw_places)
    log(f"After deduplication: {len(deduped)} unique places.")

    filtered = [p for p in deduped if is_target_park(p)]
    log(f"After category filter (RV / campground / holiday park): {len(filtered)} places.")

    if not filtered:
        log_err(
            "No matching parks found. Try a broader location or different search coverage."
        )
        return 1

    rows = [normalize_park(p, location_label=location) for p in filtered]
    for r in rows:
        r["rank_score"] = rank_score(r)

    ranked = sorted(rows, key=lambda x: x.get("rank_score", 0.0), reverse=True)
    top_for_ai = ranked[: min(10, len(ranked))]
    log(f"Top {len(top_for_ai)} parks selected for Claude summaries (max 10).")

    log("Calling Claude API for family-friendly summaries...")
    try:
        summaries = fetch_claude_summaries(
            anthropic_key,
            top_for_ai,
            location=location,
        )
    except RuntimeError as e:
        log_err(f"Claude error: {e}")
        return 1
    except Exception as e:
        log_err(f"Unexpected Claude error: {e}")
        return 1

    if len(summaries) != len(top_for_ai):
        log("Warning: summary count mismatch from Claude; padding with blanks.")

    for i, row in enumerate(top_for_ai):
        row["summary"] = summaries[i] if i < len(summaries) else ""

    log("Summaries merged into park records (top listings only).")

    log("Calling Claude API for location intro paragraph...")
    intro_paragraph = ""
    try:
        intro_paragraph = fetch_claude_intro(anthropic_key, location=location)
    except RuntimeError as e:
        log_err(f"Warning: Claude intro failed ({e}); continuing without intro paragraph.")
    except Exception as e:
        log_err(f"Warning: Claude intro failed ({e}); continuing without intro paragraph.")

    hero_tagline = extract_hero_tagline(intro_paragraph)
    park_count = len(ranked)
    top_rating = max_rating_in_rows(ranked)

    if len(ranked) >= 3:
        bf_labels = compute_best_for_labels(ranked[:3])
        for i in range(3):
            ranked[i]["best_for"] = bf_labels[i]

    if len(ranked) < 3:
        log_err("Warning: fewer than 3 parks matched — comparison table will be omitted.")

    index_html = index_path.read_text(encoding="utf-8")
    document = build_page_html(
        index_html=index_html,
        rows=ranked,
        location=location,
        intro_paragraph=intro_paragraph,
        hero_tagline=hero_tagline,
        park_count=park_count,
        top_rating=top_rating,
    )

    try:
        output_path.write_text(document, encoding="utf-8")
    except OSError as e:
        log_err(f"Failed to write HTML: {e}")
        return 1

    log(f"Saved: {output_path}")

    git_commit_and_push(
        project_dir,
        message=f"Add generated holiday parks page for {location}",
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
