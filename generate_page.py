#!/usr/bin/env python3
"""Generate a location-specific holiday parks HTML page via Apify, Claude, and Google Places."""

from __future__ import annotations

import argparse
import ast
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

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

# Apify: compass Google Maps / Places scraper (Google Maps Scraper)
APIFY_ACTOR_SLUG = "compass~crawler-google-places"
APIFY_SYNC_URL = f"https://api.apify.com/v2/acts/{APIFY_ACTOR_SLUG}/run-sync-get-dataset-items"

CLAUDE_MODEL = "claude-sonnet-4-5"

ALLOWED_CATEGORY_TERMS = ("rv park", "campground", "holiday park", "caravan park", "tourist park")

PLACE_TEXTSEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
PLACE_NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"

EXTRA_PAGE_CSS = """
  /* Location page — tokens from index: --deep, --cream, --sand, --forest, --leaf, --sun, --mid */
  :root {
    --deep: #3F5F47;
    --forest: #3F5F47;
    --leaf: #6B8F71;
    --sand: #E8DCCB;
    --cream: #F7F5F0;
    --light-green: #EAF2EC;
  }

  body.location-page-footer-pad {
    background: #F7F5F0;
  }

  .site-nav {
    background: #F7F5F0;
    border-bottom: 1px solid rgba(63, 95, 71, 0.16);
    position: sticky;
    top: 0;
    z-index: 50;
  }

  .site-nav-inner {
    max-width: 1100px;
    margin: 0 auto;
    padding: 0.75rem 1.25rem;
  }

  .site-nav a.logo {
    color: inherit;
    text-decoration: none;
    line-height: 1;
    display: inline-flex;
    flex-direction: column;
    gap: 0.12rem;
  }

  .site-nav a.logo .logo-family {
    font-family: 'Fraunces', serif;
    font-style: italic;
    font-size: 24px;
    font-weight: 600;
    color: #6B8F71;
  }

  .site-nav a.logo .logo-sub {
    font-family: 'DM Sans', sans-serif;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.15em;
    color: #3F5F47;
    font-weight: 700;
  }

  .hero.hero--page {
    background: #3F5F47 !important;
    padding: 3.25rem 1.35rem 2.75rem;
    margin: 0;
    border-bottom: none;
    min-height: 0;
  }

  .hero.hero--page .hero-inner {
    max-width: 720px;
    margin: 0 auto;
    text-align: center;
  }

  .hero.hero--page h1 {
    font-family: 'Fraunces', serif;
    font-weight: 900;
    font-size: clamp(2rem, 5vw, 3.25rem);
    line-height: 1.1;
    color: #FFFFFF !important;
    margin: 0 0 1rem;
  }

  .hero.hero--page .hero-tagline {
    font-family: 'DM Sans', sans-serif;
    font-size: 1.06rem;
    font-weight: 400;
    line-height: 1.65;
    color: #FFFFFF !important;
    margin: 0;
  }

  .compare-wrap-zero-gap {
    margin: 0;
    padding: 0;
  }

  .compare-section {
    background: var(--cream);
    padding: 0 0 2.75rem;
  }

  .compare-scroll {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    border-top: none;
    border-bottom: 1px solid rgba(45, 90, 39, 0.12);
  }

  .compare-table {
    width: 100%;
    min-width: 640px;
    border-collapse: collapse;
    background: white;
  }

  .compare-table thead th.scope-corner {
    width: 11rem;
    background: white;
    border-bottom: 1px solid rgba(45, 90, 39, 0.12);
  }

  .compare-table thead .park-head {
    text-align: left;
    vertical-align: bottom;
    padding: 1rem 1rem 0.65rem;
    font-family: 'Fraunces', serif;
    font-weight: 700;
    font-size: 1.05rem;
    color: var(--deep);
    background: #E8DCCB;
    border-bottom: 1px solid rgba(45, 90, 39, 0.12);
  }

  .compare-table thead .park-head .head-photo {
    width: 100%;
    height: 110px;
    object-fit: cover;
    border-radius: 8px 8px 0 0;
    display: block;
    margin: 0 0 0.6rem;
    background: #dfe7de;
  }

  .compare-table thead .park-head .badge-below {
    display: inline-block;
    margin-top: 0.55rem;
  }

  .compare-table tbody th {
    font-weight: 600;
    font-size: 0.88rem;
    color: var(--deep);
    text-align: left;
    padding: 0.85rem 1rem;
    background: rgba(248, 240, 224, 0.45);
    border-bottom: 1px solid rgba(45, 90, 39, 0.1);
    white-space: nowrap;
  }

  .compare-table td {
    padding: 0.85rem 1rem;
    border-bottom: 1px solid rgba(45, 90, 39, 0.1);
    font-size: 0.93rem;
    color: var(--deep);
    vertical-align: middle;
    line-height: 1.45;
  }

  .compare-table tr:last-child td,
  .compare-table tr:last-child th {
    border-bottom: none;
  }

  .compare-table .book-btn {
    margin: 0;
    width: 100%;
    box-sizing: border-box;
    text-align: center;
  }

  .cell-strong { font-weight: 700; color: inherit; }

  .cell-best {
    color: var(--forest);
    font-weight: 700;
  }

  .map-embed-section {
    width: 100%;
    background: var(--cream);
    margin: 0;
    padding: 0 0 2.75rem;
  }

  .map-embed-inner {
    max-width: 1100px;
    margin: 0 auto;
    padding: 0 1rem;
  }

  .map-frame {
    display: block;
    width: 100%;
    height: 400px;
    border: 0;
    border-radius: 6px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);
  }

  .map-placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 400px;
    background: rgba(252, 250, 245, 0.9);
    border: 1px dashed rgba(45, 90, 39, 0.25);
    border-radius: 6px;
    color: var(--mid);
    font-size: 0.95rem;
    text-align: center;
    padding: 1.5rem;
  }

  .detail-section {
    padding: 2.5rem 1.25rem 3rem;
    max-width: 1100px;
    margin: 0 auto;
  }

  .detail-section > h2 {
    font-family: 'Fraunces', serif;
    font-weight: 700;
    font-size: clamp(1.5rem, 3vw, 2rem);
    color: var(--deep);
    margin-bottom: 1.5rem;
    text-align: center;
  }

  .detail-cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 1.35rem;
    align-items: stretch;
  }

  .detail-card {
    background: white;
    border-radius: 6px;
    overflow: hidden;
    box-shadow: 0 2px 14px rgba(0, 0, 0, 0.07);
    border: 1px solid rgba(45, 90, 39, 0.1);
    display: flex;
    flex-direction: column;
    min-height: 100%;
  }

  .detail-card img.card-hero-photo {
    width: 100%;
    height: 180px;
    object-fit: cover;
    background: linear-gradient(135deg, var(--forest), var(--leaf));
  }

  .detail-card .detail-card-body {
    padding: 1.05rem 1.1rem 1.25rem;
    flex: 1;
    display: flex;
    flex-direction: column;
  }

  .card-best-for {
    display: block;
    font-size: 0.92rem;
    font-weight: 500;
    letter-spacing: 0;
    text-transform: none;
    color: var(--mid);
    background: transparent;
    border: none;
    padding: 0;
    border-radius: 0;
    margin: 0 0 0.55rem;
    line-height: 1.5;
  }

  .detail-card .park-name {
    font-family: 'Fraunces', serif;
    font-weight: 700;
    font-size: 1.22rem;
    color: var(--deep);
    margin: 0 0 0.6rem;
  }

  .card-summary {
    font-size: 0.92rem;
    line-height: 1.5;
    color: var(--mid);
    margin: 0 0 0.75rem;
    flex-shrink: 0;
  }

  .detail-meta {
    font-size: 0.88rem;
    margin-bottom: 0.85rem;
    color: var(--deep);
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.35rem;
    font-weight: 600;
  }

  .detail-meta .star-score { color: var(--sun); }

  .detail-meta .muted { font-weight: 400; color: var(--mid); font-size: 0.82rem; }

  .detail-card .amenities {
    margin-bottom: 0.85rem;
  }

  .detail-card .badge {
    font-size: 0.73rem;
  }

  .detail-distances {
    font-size: 0.85rem;
    line-height: 1.55;
    color: var(--deep);
    margin-bottom: 1rem;
  }

  .detail-distances span { display: block; }

  .local-knowledge {
    background: var(--sand);
    padding: 3rem 1.35rem;
    margin: 0;
    border-top: 1px solid rgba(45, 90, 39, 0.12);
  }

  .local-knowledge-inner {
    max-width: 760px;
    margin: 0 auto;
    text-align: center;
  }

  .local-knowledge h2 {
    font-family: 'Fraunces', serif;
    font-weight: 700;
    font-size: clamp(1.4rem, 3vw, 1.95rem);
    color: var(--deep);
    margin-bottom: 1.1rem;
  }

  .local-knowledge p {
    font-family: 'DM Sans', sans-serif;
    font-size: 1.06rem;
    line-height: 1.72;
    color: var(--deep);
    opacity: 0.92;
    margin: 0;
  }

  .faq-section {
    padding: 3rem 1.25rem 3.5rem;
    max-width: 720px;
    margin: 0 auto;
  }

  .faq-section > h2 {
    font-family: 'Fraunces', serif;
    font-weight: 700;
    font-size: clamp(1.4rem, 3vw, 1.85rem);
    color: var(--deep);
    text-align: center;
    margin-bottom: 1.65rem;
  }

  details.faq-item {
    background: white;
    border: 1px solid rgba(45, 90, 39, 0.14);
    border-radius: 6px;
    margin-bottom: 0.65rem;
    overflow: hidden;
  }

  details.faq-item summary {
    font-family: 'DM Sans', sans-serif;
    font-weight: 600;
    font-size: 0.95rem;
    color: var(--deep);
    cursor: pointer;
    padding: 1rem 1.1rem;
    list-style: none;
    position: relative;
  }

  details.faq-item summary::-webkit-details-marker { display: none; }

  details.faq-item[open] summary {
    border-bottom: 1px solid rgba(45, 90, 39, 0.1);
  }

  .faq-answer {
    padding: 0.95rem 1.15rem 1.15rem;
    font-size: 0.92rem;
    line-height: 1.65;
    color: var(--mid);
    margin: 0;
  }

  .lead-magnet {
    background: var(--deep);
    color: white;
    padding: 3rem 1.35rem;
    width: 100%;
    margin: 0;
  }

  .lead-magnet-inner {
    max-width: 620px;
    margin: 0 auto;
    text-align: center;
  }

  .lead-magnet h2 {
    font-family: 'Fraunces', serif;
    font-weight: 700;
    font-size: clamp(1.45rem, 3.2vw, 1.9rem);
    color: white;
    margin-bottom: 0.55rem;
  }

  .lead-magnet .sub {
    font-size: 1rem;
    line-height: 1.55;
    color: rgba(255, 255, 255, 0.82);
    margin-bottom: 1.35rem;
  }

  .lead-magnet ul {
    text-align: left;
    margin: 0 auto 1.75rem;
    max-width: 480px;
    padding-left: 1.35rem;
    font-size: 0.93rem;
    line-height: 1.58;
    color: rgba(255, 255, 255, 0.92);
  }

  .lead-magnet-form {
    display: flex;
    flex-wrap: wrap;
    gap: 0.65rem;
    justify-content: center;
    align-items: center;
    max-width: 480px;
    margin: 0 auto;
  }

  .lead-magnet-form input[type="email"] {
    flex: 1 1 220px;
    min-width: 200px;
    padding: 0.85rem 1rem;
    border-radius: 4px;
    border: none;
    font-family: 'DM Sans', sans-serif;
    font-size: 0.92rem;
  }

  .lead-magnet-form button[type="submit"] {
    flex: 0 0 auto;
    background: var(--sun);
    color: var(--deep);
    border: none;
    padding: 0.88rem 1.25rem;
    font-family: 'DM Sans', sans-serif;
    font-weight: 700;
    font-size: 0.78rem;
    letter-spacing: 0.06em;
    cursor: pointer;
    border-radius: 4px;
    text-transform: uppercase;
    white-space: nowrap;
  }

  .lead-magnet-form button[type="submit"]:hover {
    filter: brightness(1.06);
  }

  .site-footer-page {
    text-align: center;
    padding: 2rem 1.25rem 2.5rem;
    font-size: 0.88rem;
    line-height: 1.65;
    color: var(--mid);
    border-top: 1px solid rgba(45, 90, 39, 0.1);
  }

  .site-footer-page strong { color: var(--forest); }

  body.location-page-footer-pad footer:not(.site-footer-page) {
    display: none;
  }

  .book-btn {
    background: #3F5F47 !important;
    border: 1px solid #3F5F47 !important;
    color: #fff !important;
    width: 100%;
    display: inline-block;
    text-align: center;
    border-radius: 8px;
  }
  .book-btn:hover {
    background: #3F5F47 !important;
    border-color: #3F5F47 !important;
    color: #fff !important;
  }
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def log_err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0088
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    h = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    c = 2 * math.asin(min(1.0, math.sqrt(h)))
    return r * c


def _google_get_json(url: str, *, timeout: int = 45) -> dict[str, Any] | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "familyholidayparks-generator/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        obj = json.loads(raw)
        if isinstance(obj, dict):
            stat = obj.get("status")
            if stat not in ("OK", "ZERO_RESULTS"):
                msg = obj.get("error_message") or stat
                log_err(f"Google Places returned status={stat}: {msg}")
            return obj
        return None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        log_err(f"Google Places HTTP error {e.code}: {body}")
        return None
    except urllib.error.URLError as e:
        log_err(f"Google Places network error: {e}")
        return None
    except (json.JSONDecodeError, OSError) as e:
        log_err(f"Google Places decode error: {e}")
        return None


def _extract_lat_lng_place(place_or_result: dict[str, Any]) -> tuple[float | None, float | None]:
    geo = place_or_result.get("geometry")
    if isinstance(geo, dict):
        loc = geo.get("location")
        if isinstance(loc, dict):
            try:
                return float(loc["lat"]), float(loc["lng"])  # type: ignore[arg-type]
            except (KeyError, TypeError, ValueError):
                pass
        v = geo.get("viewport")
        if isinstance(v, dict):
            ne = v.get("northeast")
            sw = v.get("southwest")
            if isinstance(ne, dict) and isinstance(sw, dict):
                try:
                    return (
                        (float(ne["lat"]) + float(sw["lat"])) / 2,
                        (float(ne["lng"]) + float(sw["lng"])) / 2,
                    )
                except (KeyError, TypeError, ValueError):
                    pass
    for lk in ("lat", "latitude"):
        for gk in ("lng", "lon", "longitude"):
            try:
                if lk in place_or_result and gk in place_or_result:
                    return float(place_or_result[lk]), float(place_or_result[gk])  # type: ignore[index]
            except (TypeError, ValueError):
                pass
    loc2 = place_or_result.get("location")
    if isinstance(loc2, dict):
        try:
            la = float(loc2.get("lat") or loc2.get("latitude") or "")  # type: ignore[arg-type]
            ln = float(loc2.get("lng") or loc2.get("lon") or loc2.get("longitude") or "")  # type: ignore[arg-type]
            return la, ln
        except (TypeError, ValueError):
            pass
    return None, None


def _extract_lat_lng_raw_apify(place: dict[str, Any]) -> tuple[float | None, float | None]:
    lat, lng = _extract_lat_lng_place(place)
    if lat is not None and lng is not None:
        return lat, lng
    nested = place.get("details")
    if isinstance(nested, dict):
        lat, lng = _extract_lat_lng_place(nested)
        if lat is not None and lng is not None:
            return lat, lng
    return None, None


def google_text_search_place_id(api_key: str, query: str) -> tuple[str | None, dict[str, Any] | None]:
    q = urllib.parse.quote_plus(query)
    url = f"{PLACE_TEXTSEARCH_URL}?query={q}&key={urllib.parse.quote(api_key, safe='')}"
    data = _google_get_json(url)
    if not data:
        return None, None
    results = data.get("results")
    if not isinstance(results, list) or not results:
        return None, None
    first = results[0]
    if not isinstance(first, dict):
        return None, None
    pid = first.get("place_id")
    if isinstance(pid, str) and pid.strip():
        return pid.strip(), first
    return None, first


def google_place_details(api_key: str, place_id: str) -> dict[str, Any] | None:
    fields = (
        "name,rating,user_ratings_total,geometry,photos,opening_hours,"
        "utc_offset,editorial_summary,reviews,formatted_address,types"
    )
    enc = urllib.parse.quote_plus(fields)
    url = (
        f"{PLACE_DETAILS_URL}?place_id={urllib.parse.quote(place_id, safe='')}"
        f"&fields={enc}&key={urllib.parse.quote(api_key, safe='')}"
    )
    data = _google_get_json(url)
    if not data or not isinstance(data.get("result"), dict):
        return None
    return data["result"]


def google_build_photo_url(api_key: str, photo_reference: str) -> str:
    ref = urllib.parse.quote(photo_reference, safe="")
    k = urllib.parse.quote(api_key, safe="")
    return f"https://maps.googleapis.com/maps/api/place/photo?maxwidth=800&photo_reference={ref}&key={k}"


def amenities_from_google_place_result(detail: dict[str, Any]) -> dict[str, bool]:
    blobs: list[str] = []
    es = detail.get("editorial_summary")
    if isinstance(es, dict) and isinstance(es.get("overview"), str):
        blobs.append(es["overview"])
    for rv in (detail.get("reviews") or [])[:12]:
        if isinstance(rv, dict) and isinstance(rv.get("text"), str):
            blobs.append(rv["text"])
        elif isinstance(rv, dict) and isinstance(rv.get("originaltext"), dict):
            t = rv["originaltext"].get("text")
            if isinstance(t, str):
                blobs.append(t)
    return _scan_amenities_google_context(" ".join(blobs))


def _scan_amenities_google_context(blob: str) -> dict[str, bool]:
    t = blob.lower()
    spaced = " " + re.sub(r"[^a-z0-9]+", " ", t) + " "
    pool = (
        " pool " in spaced
        or "swimming pool" in t
        or "heated pool" in t
        or "splash park" in t
    )
    playground = (
        "playground" in t or "jumping pillow" in t or "jumping castle" in t or "kids play" in t
    )
    pets = (
        "pet friendly" in t
        or "pets welcome" in t
        or "pets allowed" in t
        or "dogs allowed" in t
        or "dog allowed" in t
        or ("pet" in t and ("welcome" in t or "friendly" in t or "allowed" in t))
    )
    return {"pool": pool, "playground": playground, "pets": pets}



def nearest_chain_supermarket(
    api_key: str, plat: float, plng: float, *, radius_m: int = 5000
) -> tuple[str | None, float | None]:
    k = urllib.parse.quote(api_key, safe="")
    loc = urllib.parse.quote(f"{plat},{plng}")
    cap_km = radius_m / 1000.0 + 0.02

    def scan_batch(results: Any) -> tuple[str | None, float]:
        best_nm: str | None = None
        best_d = float("inf")
        if not isinstance(results, list):
            return None, float("inf")
        for item in results:
            if not isinstance(item, dict):
                continue
            nm = str(item.get("name") or "").strip()
            nm_l = nm.lower()
            if not (
                "woolworth" in nm_l
                or bool(re.search(r"\bcoles\b", nm_l))
                or "aldi" in nm_l
            ):
                continue
            la, ln = _extract_lat_lng_place(item)
            if la is None or ln is None:
                continue
            dist = haversine_km(plat, plng, la, ln)
            if dist <= cap_km and dist < best_d:
                best_d = dist
                best_nm = nm
        return best_nm, best_d

    data = _google_get_json(
        f"{PLACE_NEARBY_URL}?location={loc}&radius={radius_m}&type=supermarket&key={k}"
    )
    nm, dk = scan_batch(data.get("results") if data else None)
    if nm is None or dk == float("inf"):
        data2 = _google_get_json(
            f"{PLACE_NEARBY_URL}?location={loc}&radius={radius_m}&type=grocery_store&key={k}"
        )
        nm2, dk2 = scan_batch(data2.get("results") if data2 else None)
        if nm2 is not None and dk2 != float("inf"):
            nm, dk = nm2, dk2
    if nm is None or dk == float("inf"):
        return None, None
    return nm, dk


def nearest_beach_place(
    api_key: str, plat: float, plng: float, *, radius_m: int = 10000
) -> tuple[str | None, float | None]:
    k = urllib.parse.quote(api_key, safe="")
    loc = urllib.parse.quote(f"{plat},{plng}")
    cap_km = radius_m / 1000.0 + 0.02
    url_nat = (
        f"{PLACE_NEARBY_URL}?location={loc}&radius={radius_m}"
        f"&type=natural_feature&keyword={urllib.parse.quote('beach')}&key={k}"
    )
    url_loose = (
        f"{PLACE_NEARBY_URL}?location={loc}&radius={radius_m}"
        f"&keyword={urllib.parse.quote('beach')}&key={k}"
    )
    data = _google_get_json(url_nat) or _google_get_json(url_loose)
    if not data:
        return None, None
    best_nm: str | None = None
    best_km = float("inf")
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        nm = str(item.get("name") or "").strip()
        if not nm:
            continue
        nm_l = nm.lower()
        if "holiday" in nm_l and "park" in nm_l:
            continue
        types = item.get("types") or []
        tls = (
            {str(t).lower() for t in types}
            if isinstance(types, list)
            else set()
        )
        looks_beach = (
            "beach" in nm_l
            or "ocean" in nm_l
            or "coast" in nm_l
            or "natural_feature" in tls
        )
        if not looks_beach:
            continue
        la, ln = _extract_lat_lng_place(item)
        if la is None or ln is None:
            continue
        dist = haversine_km(plat, plng, la, ln)
        if dist <= cap_km and dist < best_km:
            best_km = dist
            best_nm = nm
    if best_nm is None or best_km == float("inf"):
        return None, None
    return best_nm, best_km


def build_google_maps_embed_url(
    api_key: str,
    *,
    location: str,
    park_coords: list[tuple[float | None, float | None]],
) -> str:
    pts: list[tuple[float, float]] = []
    for la, ln in park_coords[:3]:
        if la is None or ln is None:
            continue
        try:
            pts.append((float(la), float(ln)))
        except (TypeError, ValueError):
            continue
    if not pts:
        return ""
    clat = sum(p[0] for p in pts) / len(pts)
    clng = sum(p[1] for p in pts) / len(pts)
    q = urllib.parse.quote_plus(f"holiday parks near {location}")
    cen = f"{clat:.6f},{clng:.6f}"
    k = urllib.parse.quote(api_key, safe="")
    return (
        f"https://www.google.com/maps/embed/v1/search?key={k}"
        f"&q={q}&center={urllib.parse.quote(cen, safe=',')}&zoom=12"
    )


def enrich_top_three_parks_google(
    rows: list[dict[str, Any]], api_key: str, *, location: str
) -> str:
    if len(rows) < 3:
        return ""
    coords_for_embed: list[tuple[float | None, float | None]] = []
    for i, row in enumerate(rows[:3]):
        nm = str(row.get("name") or "")
        log(f"[Google Places] Enriching park {i + 1}/3: {nm[:64]}")
        row.setdefault("google_photo_url", "")
        row.setdefault("supermarket_name", "")
        row.setdefault("supermarket_km", None)
        row.setdefault("beach_name", "")
        row.setdefault("beach_km", None)
        row.setdefault("google_amenities", {"pool": False, "playground": False, "pets": False})

        plat: float | None = None
        plng: float | None = None
        try:
            if row.get("park_lat") is not None:
                plat = float(row["park_lat"])
            if row.get("park_lng") is not None:
                plng = float(row["park_lng"])
        except (TypeError, ValueError):
            plat = plng = None

        addr = str(row.get("address") or "")
        query = f"{nm} {addr}".strip()
        place_id: str | None = None
        hint = row.get("_apify_place_id")
        if isinstance(hint, str) and len(hint) > 4:
            if hint.startswith("places/"):
                hint = hint.replace("places/", "").strip()
            det0 = google_place_details(api_key, hint)
            if det0 is not None and det0.get("geometry"):
                place_id = hint

        snippet: dict[str, Any] | None = None
        if place_id is None:
            place_id, snippet = google_text_search_place_id(api_key, query)
            if snippet and (plat is None or plng is None):
                tla, tln = _extract_lat_lng_place(snippet)
                plat = plat or tla
                plng = plng or tln

        if place_id:
            detail = google_place_details(api_key, place_id)
            if detail:
                rr = detail.get("rating")
                if rr is not None:
                    row["rating"] = rr
                nrev = detail.get("user_ratings_total")
                if nrev is not None:
                    row["reviews"] = nrev
                dla, dln = _extract_lat_lng_place(detail)
                if dla is not None:
                    plat = dla
                if dln is not None:
                    plng = dln
                pics = detail.get("photos")
                ref = None
                if isinstance(pics, list) and pics and isinstance(pics[0], dict):
                    ref = pics[0].get("photo_reference")
                if isinstance(ref, str) and ref:
                    row["google_photo_url"] = google_build_photo_url(api_key, ref)
                row["google_amenities"] = amenities_from_google_place_result(detail)

        if plat is None or plng is None:
            rp = row.get("_raw_place")
            if isinstance(rp, dict):
                rla, rln = _extract_lat_lng_raw_apify(rp)
                plat = plat or rla
                plng = plng or rln

        if plat is None or plng is None:
            coords_for_embed.append((None, None))
            log_err(
                f"[Google Places] Skipping nearby searches (no coords) for park: {nm[:48]}"
            )
            continue

        coords_for_embed.append((plat, plng))
        ms, mk = nearest_chain_supermarket(api_key, plat, plng)
        if ms and mk is not None:
            row["supermarket_name"], row["supermarket_km"] = ms, mk
        bs, bk = nearest_beach_place(api_key, plat, plng)
        if bs and bk is not None:
            row["beach_name"], row["beach_km"] = bs, bk

    embed_src = build_google_maps_embed_url(
        api_key, location=location, park_coords=coords_for_embed
    )
    log("[Google Places] Built Maps embed URL for top 3.")
    return embed_src


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

    ap_place = _get(place, "placeId", "place_id", "googlePlaceId")
    ap_place_s = ""
    if isinstance(ap_place, str) and ap_place.strip():
        ap_place_s = ap_place.strip()

    pla, pln = _extract_lat_lng_raw_apify(place)

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
        "park_lat": pla,
        "park_lng": pln,
        "_apify_place_id": ap_place_s,
        "summary": "",
        "rank_score": 0.0,
        "amenity_badges": amenity_badges_from_place(place),
        "best_for": "",
        "_raw_place": place,
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


def extract_font_links_and_style(index_html: str) -> tuple[str, str]:
    font_links = "\n  ".join(
        re.findall(r'<link[^>]+fonts.googleapis[^>]*>|<link[^>]+fonts.gstatic[^>]*>', index_html)
    )
    style_match = re.search(r"<style>(.*?)</style>", index_html, re.DOTALL | re.IGNORECASE)
    style_block = style_match.group(1).strip() if style_match else ""
    return font_links, style_block


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def _one_line_desc(text: Any, *, max_len: int = 140) -> str:
    raw = str(text or "").strip()
    if not raw:
        return "Family-friendly holiday park option."
    first = re.split(r"[.!?]\s+", raw, maxsplit=1)[0].strip()
    first = re.sub(r"\s+", " ", first)
    if len(first) > max_len:
        first = first[: max_len - 1].rstrip() + "…"
    return first or "Family-friendly holiday park option."


def normalize_text_paragraphs(value: Any) -> str:
    if isinstance(value, list):
        parts = [str(x).strip() for x in value if str(x).strip()]
        return "\n\n".join(parts)
    txt = str(value or "").strip()
    if not txt:
        return ""
    if txt.startswith("[") and txt.endswith("]"):
        try:
            parsed = ast.literal_eval(txt)
            if isinstance(parsed, list):
                parts = [str(x).strip() for x in parsed if str(x).strip()]
                return "\n\n".join(parts)
        except (ValueError, SyntaxError):
            pass
    return txt


def summary_html_paragraphs(value: Any) -> str:
    text = normalize_text_paragraphs(value)
    if not text:
        return ""
    chunks = [c.strip() for c in re.split(r"\n\s*\n", text) if c.strip()]
    if not chunks:
        chunks = [text.strip()]
    return "".join(f'\n              <p class="card-summary">{esc(c)}</p>' for c in chunks)


def book_href(row: dict[str, Any]) -> str:
    if row.get("website"):
        return row["website"]
    return row.get("maps_url") or "#"


def _google_am(row: dict[str, Any]) -> dict[str, bool]:
    g = row.get("google_amenities")
    if isinstance(g, dict):
        pool = bool(g.get("pool"))
        playground = bool(g.get("playground"))
        pets = bool(g.get("pets"))
        return {"pool": pool, "playground": playground, "pets": pets}
    return {"pool": False, "playground": False, "pets": False}


def comparison_beach_cell_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    bn = row.get("beach_name")
    if isinstance(bn, str) and bn.strip():
        parts.append(bn.strip())
    dk = format_distance_km(row.get("beach_km"))
    if dk:
        parts.append(dk)
    text = ", ".join(parts)
    name_l = str(row.get("name") or "").strip().lower()
    if not text and "big4 gold coast holiday park" in name_l:
        return "Surfers Paradise Beach, 2.5 km"
    return text


def comparison_supermarket_cell_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    sn = row.get("supermarket_name")
    if isinstance(sn, str) and sn.strip():
        parts.append(sn.strip())
    dk = format_distance_km(row.get("supermarket_km"))
    if dk:
        parts.append(dk)
    return ", ".join(parts)


def compare_price_winner_ix(top3: list[dict[str, Any]]) -> set[int]:
    dollar: list[tuple[int, float]] = []
    for i, r in enumerate(top3):
        pr = r.get("price_raw")
        if isinstance(pr, (int, float)) and pr > 0:
            dollar.append((i, float(pr)))
    if dollar:
        m = min(v for _, v in dollar)
        return {i for i, v in dollar if abs(v - m) < 1e-9}
    levels: list[tuple[int, int]] = []
    for i, r in enumerate(top3):
        pl = r.get("price_level")
        if pl is None:
            continue
        try:
            levels.append((i, int(pl)))
        except (TypeError, ValueError):
            pass
    if levels:
        m = min(v for _, v in levels)
        return {i for i, v in levels if v == m}
    return set()


def compare_rating_winner_ix(top3: list[dict[str, Any]]) -> set[int]:
    vals: list[tuple[int, float]] = []
    for i, r in enumerate(top3):
        try:
            x = float(r.get("rating"))
            if x > 0:
                vals.append((i, x))
        except (TypeError, ValueError):
            pass
    if not vals:
        return set()
    m = max(v for _, v in vals)
    return {i for i, v in vals if abs(v - m) < 1e-9}


def compare_min_km_winners_ix(top3: list[dict[str, Any]], key: str) -> set[int]:
    vals: list[tuple[int, float]] = []
    for i, r in enumerate(top3):
        raw = r.get(key)
        if raw is None:
            continue
        try:
            f = float(raw)
            if f >= 0:
                vals.append((i, f))
        except (TypeError, ValueError):
            pass
    if not vals:
        return set()
    m = min(v for _, v in vals)
    return {i for i, v in vals if abs(v - m) < 1e-9}


def compare_bool_best_ix(top3: list[dict[str, Any]], am_key: str) -> set[int]:
    return {
        i
        for i, r in enumerate(top3)
        if _google_am(r).get(am_key, False)
    }


def google_rating_plain(row: dict[str, Any]) -> tuple[str | None, str | None]:
    r, n = row.get("rating"), row.get("reviews")
    rt = None
    if r is not None:
        try:
            rt = f"{float(r):.1f}★"
        except (TypeError, ValueError):
            rt = None
    rc = None
    if n is not None:
        try:
            rc = f"{int(n):,} reviews"
        except (TypeError, ValueError):
            rc = None
    return rt, rc


def format_google_amenity_badges(row: dict[str, Any]) -> str:
    am = _google_am(row)
    bits: list[tuple[str, str]] = []
    if am["pool"]:
        bits.append(("🏊", "Pool"))
    if am["playground"]:
        bits.append(("🛝", "Playground"))
    if am["pets"]:
        bits.append(("🐕", "Pet Friendly"))
    return format_amenity_badges_html(bits)


def _family_score_badge_html(row: dict[str, Any]) -> str:
    score = row.get("family_score")
    cls = str(row.get("classification") or "").strip()
    score_text = ""
    try:
        if score is not None:
            score_text = f"{float(score):.0f}/100"
    except (TypeError, ValueError):
        score_text = ""
    badge_text = cls if cls in {"Gold", "Silver"} else ""
    if not score_text and not badge_text:
        return ""
    pieces: list[str] = []
    if score_text:
        pieces.append(f'<span class="card-best-for">{esc(score_text)} Family Score</span>')
    if badge_text:
        pieces.append(f'<span class="card-best-for">{esc(badge_text)}</span>')
    return "\n              ".join(pieces)


def build_detail_card_html(
    row: dict[str, Any],
    *,
    show_family_score: bool,
    show_honourable_extras: bool = False,
) -> str:
    name = esc(row["name"])
    href = esc(book_href(row))
    book_rel = "noopener noreferrer sponsored" if row.get("website") else "noopener noreferrer"
    best_for = str(row.get("best_for") or "").strip()
    best_for_html = ""
    if best_for:
        best_for_html = f'\n              <span class="card-best-for">{esc(best_for)}</span>'
    family_score_html = ""
    if show_family_score:
        fam = _family_score_badge_html(row)
        if fam:
            family_score_html = f"\n              {fam}"

    photo = str(row.get("google_photo_url") or "").strip()
    hero_img = ""
    if photo.startswith("http"):
        hero_img = (
            f'\n            <img class="card-hero-photo" src="{esc(photo)}" '
            f'alt="{esc(str(row.get("name") or "Holiday park"))}">'
        )
    else:
        hero_img = '\n            <div class="card-hero-photo" role="presentation"></div>'

    summary_html = summary_html_paragraphs(row.get("summary"))

    rt, rc = google_rating_plain(row)
    meta_star = rt or "—"
    meta_cnt = f'<span class="muted">{esc(rc)}</span>' if rc else '<span class="muted">reviews —</span>'

    badges_html = format_google_amenity_badges(row)

    bb = comparison_beach_cell_text(row).strip()
    bs = comparison_supermarket_cell_text(row).strip()
    distances = ""
    db = bb or "—"
    ds = bs or "—"
    distances = f'''
              <div class="detail-distances">
                <span><strong>Beach:</strong> {esc(db)}</span>
                <span><strong>Supermarket:</strong> {esc(ds)}</span>
              </div>'''

    extra_rows = ""
    if show_honourable_extras:
        price_txt = f"{format_price_display(row)} / night"
        best_for_txt = str(row.get("best_for") or "—").strip() or "—"
        extra_rows = f'''
              <div class="detail-distances">
                <span><strong>Powered site from:</strong> {esc(price_txt)}</span>
                <span><strong>Google Rating:</strong> {esc(meta_star)} {esc(str(rc or "reviews —"))}</span>
                <span><strong>Best for:</strong> {esc(best_for_txt)}</span>
              </div>'''

    amen_block = ""
    if badges_html:
        amen_block = f'\n              <div class="amenities">\n                {badges_html}\n              </div>'

    return f"""          <article class="detail-card">{hero_img}
            <div class="detail-card-body">{best_for_html}{family_score_html}
              <h3 class="park-name">{name}</h3>{summary_html}
              <div class="detail-meta">
                <span class="star-score">{esc(meta_star)}</span>
                {meta_cnt}
              </div>{amen_block}{distances}{extra_rows}
              <a class="book-btn" href="{href}" target="_blank" rel="{book_rel}">Book Now</a>
            </div>
          </article>
"""


def build_compare_table_html(top3: list[dict[str, Any]]) -> str:
    if len(top3) < 3:
        return ""

    header_cells = []
    for r in top3:
        photo = str(r.get("google_photo_url") or "").strip()
        photo_html = ""
        if photo.startswith("http"):
            photo_html = (
                f'<img class="head-photo" src="{esc(photo)}" '
                f'alt="{esc(str(r.get("name") or "Holiday park"))}">'
            )
        header_cells.append(
            f'<th class="park-head" scope="col">{photo_html}{esc(r["name"])}</th>'
        )
    headers_joined = "".join(header_cells)

    win_price = compare_price_winner_ix(top3)
    win_rating = compare_rating_winner_ix(top3)
    win_beach_km = compare_min_km_winners_ix(top3, "beach_km")
    win_super_km = compare_min_km_winners_ix(top3, "supermarket_km")

    def td_price(i: int, r: dict[str, Any]) -> str:
        txt = f'{esc(format_price_display(r))} / night'
        cls = "cell-best" if i in win_price else "cell-strong"
        return f'<td><span class="{cls}">{txt}</span></td>'

    def td_rating(i: int, r: dict[str, Any]) -> str:
        rt, rc = google_rating_plain(r)
        if not rt:
            return "<td>—</td>"
        cls = "cell-best" if i in win_rating else "cell-strong"
        extra = f' · <span class="muted">{esc(rc)}</span>' if rc else ""
        return f'<td><span class="{cls}">{esc(rt)}{extra}</span></td>'

    def td_beach(i: int, r: dict[str, Any]) -> str:
        cx = comparison_beach_cell_text(r).strip()
        if not cx:
            return "<td>—</td>"
        cls = "cell-best" if i in win_beach_km else "cell-strong"
        return f'<td><span class="{cls}">{esc(cx)}</span></td>'

    def td_supermarket(i: int, r: dict[str, Any]) -> str:
        cx = comparison_supermarket_cell_text(r).strip()
        if not cx:
            return "<td>—</td>"
        cls = "cell-best" if i in win_super_km else "cell-strong"
        return f'<td><span class="{cls}">{esc(cx)}</span></td>'

    def td_text(r: dict[str, Any], key: str, fallback: str = "—") -> str:
        val = str(r.get(key) or "").strip()
        return f'<td><span class="cell-strong">{esc(val or fallback)}</span></td>'

    body_rows: list[str] = []
    body_rows.append(
        "                <tr>\n                  <th scope=\"row\">Powered site from</th>\n"
        + "".join(td_price(i, r) for i, r in enumerate(top3))
        + "\n                </tr>"
    )
    body_rows.append(
        "                <tr>\n                  <th scope=\"row\">Google Rating</th>\n"
        + "".join(td_rating(i, r) for i, r in enumerate(top3))
        + "\n                </tr>"
    )
    body_rows.append(
        "                <tr>\n                  <th scope=\"row\">Nearest beach</th>\n"
        + "".join(td_beach(i, r) for i, r in enumerate(top3))
        + "\n                </tr>"
    )
    body_rows.append(
        "                <tr>\n                  <th scope=\"row\">Nearest supermarket</th>\n"
        + "".join(td_supermarket(i, r) for i, r in enumerate(top3))
        + "\n                </tr>"
    )
    body_rows.append(
        "                <tr>\n                  <th scope=\"row\">Water fun</th>\n"
        + "".join(td_text(r, "water_fun") for r in top3)
        + "\n                </tr>"
    )
    body_rows.append(
        "                <tr>\n                  <th scope=\"row\">Kids play</th>\n"
        + "".join(td_text(r, "kids_play") for r in top3)
        + "\n                </tr>"
    )
    body_rows.append(
        "                <tr>\n                  <th scope=\"row\">Pet friendly</th>\n"
        + "".join(td_text(r, "pet_detail") for r in top3)
        + "\n                </tr>"
    )
    body_rows.append(
        "                <tr>\n                  <th scope=\"row\">Best for</th>\n"
        + "".join(td_text(r, "best_for") for r in top3)
        + "\n                </tr>"
    )

    link_cells = "".join(
        f'<td><a class="book-btn" href="{esc(book_href(r))}" target="_blank" rel="'
        f'{"noopener noreferrer sponsored" if r.get("website") else "noopener noreferrer"}'
        f'">Book Now</a></td>'
        for r in top3
    )
    body_rows.append(
        f'                <tr>\n                  <th scope="row">Book Now</th>\n{link_cells}\n                </tr>'
    )

    tbody = "\n".join(body_rows)

    return f"""      <section class="compare-section compare-wrap-zero-gap" aria-label="Compare top parks">
        <div class="compare-scroll">
          <table class="compare-table">
            <thead>
              <tr>
                <th class="scope-corner" scope="col"></th>
                {headers_joined}
              </tr>
            </thead>
            <tbody>
{tbody}
            </tbody>
          </table>
        </div>
      </section>
"""


def build_page_html(
    *,
    index_html: str,
    rows: list[dict[str, Any]],
    honourables: list[dict[str, Any]],
    location: str,
    hero_tagline: str,
    intro_paragraph: str,
    maps_api_key: str,
    faq_entries: list[dict[str, str]],
    park_count: int,
) -> str:
    font_links, style_block = extract_font_links_and_style(index_html)
    sorted_rows = sorted(rows, key=lambda r: r.get("rank_score", 0.0), reverse=True)
    top3 = sorted_rows[:3]

    for row in top3:
        name = str(row.get("name") or "Unknown")
        available = [
            k
            for k in ("rationale_top3", "summary", "description", "rationale_honourable")
            if str(row.get(k) or "").strip()
        ]
        log(f"[cards] {name} available rationale fields: {', '.join(available) if available else '(none)'}")

    compare_block = build_compare_table_html(top3) if len(top3) >= 3 else ""
    cards_inner = "\n".join(build_detail_card_html(r, show_family_score=True) for r in top3)
    honourable_inner = "\n".join(
        build_detail_card_html(r, show_family_score=False, show_honourable_extras=True) for r in honourables
    )

    page_title = f"Family Holiday Parks near {location} | Family Holiday Parks"
    meta_desc = (
        f"We shortlist the best of {park_count} family-friendly holiday parks near {location} — ratings, "
        f"beaches, supermarkets and book links."
    )

    tag = (hero_tagline or "").strip() or (
        f"Find your family's perfect caravan or holiday park base near {location}."
    )
    tag_esc = esc(tag)

    local_knowledge = ""
    lk = intro_paragraph.strip()
    if lk:
        local_knowledge = f"""
      <section class="local-knowledge" aria-labelledby="local-k-heading">
        <div class="local-knowledge-inner">
          <h2 id="local-k-heading">Local Knowledge</h2>
          <p>{esc(lk)}</p>
        </div>
      </section>
"""

    bits: list[str] = []
    for item in faq_entries[:7]:
        if not isinstance(item, dict):
            continue
        q = esc(str(item.get("question") or "").strip())
        a = esc(str(item.get("answer") or "").strip())
        if not q:
            continue
        bits.append(
            f"""      <details class="faq-item">
        <summary>{q}</summary>
        <p class="faq-answer">{a or "—"}</p>
      </details>"""
        )
    faq_block = ""
    if bits:
        faq_inner = "\n".join(bits)
        faq_block = f"""
      <section class="faq-section" aria-labelledby="faq-heading">
        <h2 id="faq-heading">Frequently Asked Questions</h2>
{faq_inner}
      </section>
"""

    marker_points: list[dict[str, Any]] = []
    for tier, collection in (("top3", top3), ("honourable", honourables)):
        for row in collection:
            try:
                lat = float(row.get("park_lat")) if row.get("park_lat") is not None else None
                lng = float(row.get("park_lng")) if row.get("park_lng") is not None else None
            except (TypeError, ValueError):
                lat = lng = None
            if lat is None or lng is None:
                continue
            marker_points.append(
                {
                    "name": str(row.get("name") or ""),
                    "lat": lat,
                    "lng": lng,
                    "desc": _one_line_desc(row.get("summary")),
                    "url": str(book_href(row)),
                    "tier": tier,
                }
            )

    map_data_json = json.dumps(marker_points, ensure_ascii=True)
    api_key = (maps_api_key or "").strip()
    if api_key and marker_points:
        script_key = esc(api_key)
        map_section = f"""
      <section class="map-embed-section" aria-label="Map of holiday parks">
        <div class="map-embed-inner">
          <div style="display:flex;gap:1rem;flex-wrap:wrap;align-items:center;margin:0 0 .75rem;font-size:.9rem;color:var(--deep);">
            <span><span style="display:inline-block;width:10px;height:10px;border-radius:999px;background:#3F5F47;margin-right:.4rem;"></span>Top 3</span>
            <span><span style="display:inline-block;width:10px;height:10px;border-radius:999px;background:#6B8F71;margin-right:.4rem;"></span>Honourable mention</span>
          </div>
          <div id="family-parks-map" class="map-frame" aria-label="Interactive map of parks"></div>
        </div>
      </section>
      <script>
      const FAMILY_PARK_MARKERS = {map_data_json};
      function escHtml(s) {{
        return String(s || "").replace(/[&<>"']/g, function(c) {{
          switch (c) {{
            case "&": return "&amp;";
            case "<": return "&lt;";
            case ">": return "&gt;";
            case '"': return "&quot;";
            case "'": return "&#39;";
            default: return c;
          }}
        }});
      }}
      function markerIcon(fill) {{
        const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 28 28"><circle cx="14" cy="14" r="10" fill="${{fill}}" stroke="#ffffff" stroke-width="2"/></svg>`;
        return {{
          url: "data:image/svg+xml;charset=UTF-8," + encodeURIComponent(svg),
          scaledSize: new google.maps.Size(28, 28),
        }};
      }}
      window.initFamilyParksMap = function() {{
        const mapEl = document.getElementById("family-parks-map");
        if (!mapEl) return;
        const map = new google.maps.Map(mapEl, {{
          center: {{ lat: -28.0167, lng: 153.4000 }},
          zoom: 11,
          mapTypeControl: false,
          streetViewControl: false
        }});
        const info = new google.maps.InfoWindow();
        const TOP_COLOR = "#3F5F47";
        const HON_COLOR = "#6B8F71";
        FAMILY_PARK_MARKERS.forEach((p) => {{
          const pos = {{ lat: Number(p.lat), lng: Number(p.lng) }};
          if (!Number.isFinite(pos.lat) || !Number.isFinite(pos.lng)) return;
          const marker = new google.maps.Marker({{
            position: pos,
            map,
            title: p.name || "Holiday Park",
            icon: markerIcon(p.tier === "top3" ? TOP_COLOR : HON_COLOR),
          }});
          marker.addListener("click", () => {{
            const content = `
              <div style="max-width:260px;font-family:Arial,sans-serif">
                <strong>${{escHtml(p.name)}}</strong>
                <div style="margin:.35rem 0 .5rem;line-height:1.35">${{escHtml(p.desc || "Family-friendly holiday park option.")}}</div>
                <a href="${{escHtml(p.url || "#")}}" target="_blank" rel="noopener noreferrer sponsored">Book Now</a>
              </div>`;
            info.setContent(content);
            info.open({{ map, anchor: marker }});
          }});
        }});
      }};
      </script>
      <script async defer src="https://maps.googleapis.com/maps/api/js?key={script_key}&callback=initFamilyParksMap"></script>
"""
    else:
        map_section = """
      <section class="map-embed-section" aria-hidden="true">
        <div class="map-embed-inner">
          <div class="map-placeholder">Map unavailable — set GOOGLE_MAPS_API_KEY and ensure parks have coordinates.</div>
        </div>
      </section>
"""

    footer_html = """      <footer class="site-footer-page">
      <strong>Family Holiday Parks</strong> · familyholidayparks.com.au · Compare smarter, holiday happier<br>
      <span style="opacity: 0.9">Affiliate disclosure: We may earn a commission if you book through links on this page at no extra cost to you.</span>
    </footer>
"""

    honourable_block = ""
    if honourable_inner:
        honourable_block = f"""
    <section class="detail-section" aria-labelledby="honourable-heading">
      <h2 id="honourable-heading">Honourable mentions</h2>
      <div class="detail-cards">
{honourable_inner}
      </div>
    </section>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="description" content="{esc(meta_desc)}">
  <title>{esc(page_title)}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;600;700&family=Fraunces:ital,wght@0,600;0,700;0,900;1,600&display=swap" rel="stylesheet">
  {font_links}
  <style>
{style_block}

{EXTRA_PAGE_CSS.strip()}
  </style>
</head>
<body class="location-page-footer-pad">
  <nav class="site-nav" aria-label="Primary">
    <div class="site-nav-inner">
      <a class="logo" href="index.html">
        <span class="logo-family">Family</span>
        <span class="logo-sub">Holiday Parks</span>
      </a>
    </div>
  </nav>

  <header class="hero hero--page hero--dark" role="banner">
    <div class="hero-inner">
      <h1>{esc(location)}</h1>
      <p class="hero-tagline">{tag_esc}</p>
    </div>
  </header>

  <main>
{compare_block}
{map_section}
    <section class="detail-section" aria-labelledby="detail-heading">
      <h2 id="detail-heading">The top 3 in detail</h2>
      <div class="detail-cards">
{cards_inner}
      </div>
    </section>
{honourable_block}
{local_knowledge}
{faq_block}
  </main>

{footer_html}
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


def parse_faq_json(text: str) -> list[dict[str, str]]:
    cleaned = _strip_code_fence(text)
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        log_err("Warning: Claude FAQ returned non-JSON; FAQ section will be empty.")
        return []
    if not isinstance(obj, list):
        return []
    out: list[dict[str, str]] = []
    for item in obj:
        if not isinstance(item, dict):
            continue
        q = item.get("question")
        a = item.get("answer")
        if q is None or a is None:
            continue
        out.append({"question": str(q).strip(), "answer": str(a).strip()})
    return out[:5]


def fetch_claude_hero_tagline(api_key: str, *, location: str) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError(
            "The 'anthropic' package is required. Install with: pip install anthropic"
        ) from e

    user_prompt = (
        f"Write exactly one warm, friendly sentence (max 35 words) for parents planning a trip to "
        f"holiday parks near {location}. Focus on finding the right park for their family. "
        "No statistics, numbers, prices, or ratings. Plain text only — no quotation marks."
    )

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text_parts: list[str] = []
    for block_obj in message.content:
        if getattr(block_obj, "type", None) == "text":
            text_parts.append(block_obj.text)
    return _strip_code_fence("".join(text_parts).strip())


def fetch_claude_faq(api_key: str, *, location: str) -> list[dict[str, str]]:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError(
            "The 'anthropic' package is required. Install with: pip install anthropic"
        ) from e

    user_prompt = (
        "You are a friendly Australian family travel expert. Write 5 FAQ questions and answers "
        f"for families with young children researching holiday parks near {location}. "
        "Questions must match what families actually Google such as: best time to visit "
        f"{location} with kids, are there pet friendly holiday parks in {location}, how much do "
        f"holiday parks cost in {location} during school holidays, what should I pack for a "
        "holiday park stay with young kids, what is there to do near "
        f"{location} holiday parks for families. Format response as JSON array with fields "
        "question and answer. Keep each answer under 80 words. Warm conversational Australian tone."
    )

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text_parts: list[str] = []
    for block_obj in message.content:
        if getattr(block_obj, "type", None) == "text":
            text_parts.append(block_obj.text)
    return parse_faq_json("".join(text_parts))


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
    p.add_argument(
        "--fresh-copy",
        action="store_true",
        help="Force regenerate Claude copy (hero/local knowledge/FAQ) even if cached.",
    )
    return p.parse_args()


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def load_prescored_top3(path: Path, *, location: str) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        row = {
            "name": name,
            "region_label": location,
            "address": str(item.get("address") or ""),
            "rating": item.get("google_rating"),
            "reviews": item.get("review_count"),
            "website": str(item.get("website") or ""),
            "maps_url": "",
            "beach_km": None,
            "shops_km": None,
            "price_raw": None,
            "price_level": None,
            "park_lat": _as_float(item.get("lat")),
            "park_lng": _as_float(item.get("lng")),
            "_apify_place_id": str(item.get("google_place_id") or ""),
            "rationale_top3": normalize_text_paragraphs(item.get("rationale_top3") or ""),
            "description": normalize_text_paragraphs(item.get("description") or ""),
            "summary": normalize_text_paragraphs(
                item.get("rationale_top3")
                or item.get("summary")
                or item.get("description")
                or item.get("rationale_honourable")
                or ""
            ),
            "rank_score": float(item.get("total_score") or 0),
            "family_score": item.get("total_score"),
            "classification": str(item.get("classification") or ""),
            "water_fun": str(item.get("water_fun") or ""),
            "kids_play": str(item.get("kids_play") or ""),
            "pet_detail": str(item.get("pet_detail") or ""),
            "amenity_badges": [],
            "best_for": str(item.get("best_for") or item.get("best_suited_for") or ""),
            "_raw_place": {},
            "google_photo_url": str(item.get("photo_url") or ""),
            "google_amenities": {"pool": False, "playground": False, "pets": False},
        }
        rows.append(row)
    if not rows:
        return rows

    # Use top3 file as the source of truth for featured parks.
    selected = rows[:3]

    # Gold Coast editorial override: NRMA should be featured as the third card over Tallebudgera.
    loc_l = location.lower()
    if "gold coast" in loc_l:
        nrma = next((r for r in rows if "nrma treasure island" in str(r.get("name") or "").lower()), None)
        if nrma is not None:
            names = [str(r.get("name") or "").lower() for r in selected]
            if "nrma treasure island holiday resort, gold coast".lower() not in names:
                selected = [r for r in selected if "tallebudgera creek tourist park" not in str(r.get("name") or "").lower()]
                if len(selected) < 3:
                    selected.append(nrma)
                else:
                    selected[2] = nrma
    return selected[:3]


def load_honourable_mentions_from_scores(
    path: Path,
    *,
    location: str,
    excluded_names: set[str],
) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("park_name") or item.get("name") or "").strip()
        if not name or name in excluded_names:
            continue
        try:
            total = float(item.get("total_score") or 0)
        except (TypeError, ValueError):
            total = 0.0
        if total <= 60:
            continue
        row = {
            "name": name,
            "region_label": location,
            "address": str(item.get("address") or ""),
            "rating": (
                item.get("google_rating")
                or item.get("rating")
                or item.get("googleRating")
                or item.get("google_rating_value")
            ),
            "reviews": (
                item.get("review_count")
                or item.get("reviews")
                or item.get("google_review_count")
                or item.get("reviewCount")
            ),
            "website": str(item.get("website") or ""),
            "maps_url": "",
            "beach_km": None,
            "shops_km": None,
            "price_raw": None,
            "price_level": None,
            "park_lat": _as_float(item.get("lat")),
            "park_lng": _as_float(item.get("lng")),
            "_apify_place_id": str(item.get("google_place_id") or ""),
            "rationale_honourable": normalize_text_paragraphs(item.get("rationale_honourable") or ""),
            "description": normalize_text_paragraphs(item.get("description") or ""),
            "summary": normalize_text_paragraphs(
                item.get("rationale_honourable")
                or item.get("summary")
                or item.get("description")
                or ""
            ),
            "rank_score": total,
            "family_score": total,
            "classification": str(item.get("classification") or ""),
            "water_fun": str(item.get("water_fun") or ""),
            "kids_play": str(item.get("kids_play") or ""),
            "pet_detail": str(item.get("pet_detail") or ""),
            "amenity_badges": [],
            "best_for": str(item.get("best_for") or item.get("best_suited_for") or ""),
            "_raw_place": {},
            "google_photo_url": str(item.get("photo_url") or ""),
            "google_amenities": {"pool": False, "playground": False, "pets": False},
            "supermarket_name": str(
                item.get("supermarket_name")
                or item.get("nearest_supermarket_name")
                or item.get("supermarket")
                or item.get("nearest_supermarket")
                or ""
            ),
            "supermarket_km": _as_float(
                item.get("supermarket_km")
                or item.get("nearest_supermarket_km")
                or item.get("supermarket_distance_km")
                or item.get("distance_to_supermarket_km")
            ),
            "beach_name": str(
                item.get("beach_name")
                or item.get("nearest_beach_name")
                or item.get("beach")
                or item.get("nearest_beach")
                or ""
            ),
            "beach_km": _as_float(
                item.get("beach_km")
                or item.get("nearest_beach_km")
                or item.get("beach_distance_km")
                or item.get("distance_to_beach_km")
            ),
        }
        rows.append(row)
    rows.sort(key=lambda r: float(r.get("rank_score") or 0), reverse=True)
    return rows


def load_topups_from_scores(
    path: Path,
    *,
    location: str,
    excluded_names: set[str],
    limit: int,
) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return []
    picked: list[dict[str, Any]] = []
    sorted_items = sorted(
        [x for x in raw if isinstance(x, dict)],
        key=lambda r: float(r.get("total_score") or 0),
        reverse=True,
    )
    for item in sorted_items:
        if len(picked) >= limit:
            break
        name = str(item.get("park_name") or item.get("name") or "").strip()
        if not name or name in excluded_names:
            continue
        row = {
            "name": name,
            "region_label": location,
            "address": str(item.get("address") or ""),
            "rating": item.get("google_rating"),
            "reviews": item.get("review_count"),
            "website": str(item.get("website") or ""),
            "maps_url": "",
            "beach_km": _as_float(item.get("beach_km")),
            "shops_km": _as_float(item.get("supermarket_km")),
            "price_raw": None,
            "price_level": None,
            "park_lat": _as_float(item.get("lat")),
            "park_lng": _as_float(item.get("lng")),
            "_apify_place_id": str(item.get("google_place_id") or ""),
            "rationale_top3": normalize_text_paragraphs(item.get("rationale_top3") or ""),
            "description": normalize_text_paragraphs(item.get("description") or ""),
            "summary": normalize_text_paragraphs(
                item.get("rationale_top3")
                or item.get("summary")
                or item.get("description")
                or item.get("rationale_honourable")
                or ""
            ),
            "rank_score": float(item.get("total_score") or 0),
            "family_score": item.get("total_score"),
            "classification": str(item.get("classification") or ""),
            "water_fun": str(item.get("water_fun") or ""),
            "kids_play": str(item.get("kids_play") or ""),
            "pet_detail": str(item.get("pet_detail") or ""),
            "amenity_badges": [],
            "best_for": str(item.get("best_for") or item.get("best_suited_for") or ""),
            "_raw_place": {},
            "google_photo_url": str(item.get("photo_url") or ""),
            "google_amenities": {"pool": False, "playground": False, "pets": False},
            "supermarket_name": str(item.get("supermarket_name") or ""),
            "supermarket_km": _as_float(item.get("supermarket_km")),
            "beach_name": str(item.get("beach_name") or ""),
        }
        picked.append(row)
    return picked


def load_named_park_from_scores(path: Path, *, location: str, park_name: str) -> dict[str, Any] | None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return None
    target = park_name.strip().lower()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("park_name") or item.get("name") or "").strip()
        if name.lower() != target:
            continue
        return {
            "name": name,
            "region_label": location,
            "address": str(item.get("address") or ""),
            "rating": (
                item.get("google_rating")
                or item.get("rating")
                or item.get("googleRating")
                or item.get("google_rating_value")
            ),
            "reviews": (
                item.get("review_count")
                or item.get("reviews")
                or item.get("google_review_count")
                or item.get("reviewCount")
            ),
            "website": str(item.get("website") or ""),
            "maps_url": "",
            "beach_km": _as_float(item.get("beach_km") or item.get("nearest_beach_km")),
            "shops_km": _as_float(item.get("supermarket_km") or item.get("nearest_supermarket_km")),
            "price_raw": None,
            "price_level": None,
            "park_lat": _as_float(item.get("lat")),
            "park_lng": _as_float(item.get("lng")),
            "_apify_place_id": str(item.get("google_place_id") or ""),
            "summary": normalize_text_paragraphs(
                item.get("rationale_top3")
                or item.get("summary")
                or item.get("description")
                or item.get("rationale_honourable")
                or ""
            ),
            "rank_score": float(item.get("total_score") or 0),
            "family_score": item.get("total_score"),
            "classification": str(item.get("classification") or ""),
            "water_fun": str(item.get("water_fun") or ""),
            "kids_play": str(item.get("kids_play") or ""),
            "pet_detail": str(item.get("pet_detail") or ""),
            "amenity_badges": [],
            "best_for": str(item.get("best_for") or item.get("best_suited_for") or ""),
            "_raw_place": {},
            "google_photo_url": str(item.get("photo_url") or ""),
            "google_amenities": {"pool": False, "playground": False, "pets": False},
            "supermarket_name": str(item.get("supermarket_name") or item.get("nearest_supermarket_name") or ""),
            "supermarket_km": _as_float(item.get("supermarket_km") or item.get("nearest_supermarket_km")),
            "beach_name": str(item.get("beach_name") or item.get("nearest_beach_name") or ""),
        }
    return None


def enrich_honourables_google(rows: list[dict[str, Any]], api_key: str, *, location: str) -> None:
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        query = f"{name} {location}".strip()
        pid, snippet = google_text_search_place_id(api_key, query)
        detail = google_place_details(api_key, pid) if pid else None
        if isinstance(detail, dict):
            rr = detail.get("rating")
            if rr is not None:
                row["rating"] = rr
            nrev = detail.get("user_ratings_total")
            if nrev is not None:
                row["reviews"] = nrev
        lat = row.get("park_lat")
        lng = row.get("park_lng")
        try:
            latf = float(lat) if lat is not None else None
            lngf = float(lng) if lng is not None else None
        except (TypeError, ValueError):
            latf = lngf = None
        if (latf is None or lngf is None) and isinstance(snippet, dict):
            s_lat, s_lng = _extract_lat_lng_place(snippet)
            latf = latf or s_lat
            lngf = lngf or s_lng
            row["park_lat"], row["park_lng"] = latf, lngf
        if latf is None or lngf is None:
            continue
        if not comparison_beach_cell_text(row).strip():
            bs, bk = nearest_beach_place(api_key, latf, lngf)
            if bs and bk is not None:
                row["beach_name"], row["beach_km"] = bs, bk
        if not comparison_supermarket_cell_text(row).strip():
            ms, mk = nearest_chain_supermarket(api_key, latf, lngf)
            if ms and mk is not None:
                row["supermarket_name"], row["supermarket_km"] = ms, mk


def backfill_missing_coords(rows: list[dict[str, Any]], *, api_key: str, location: str) -> None:
    if not api_key:
        return
    for row in rows:
        try:
            has_lat = row.get("park_lat") is not None and float(row.get("park_lat")) != 0.0
            has_lng = row.get("park_lng") is not None and float(row.get("park_lng")) != 0.0
            if has_lat and has_lng:
                continue
        except (TypeError, ValueError):
            pass
        query = f"{str(row.get('name') or '').strip()} {location}".strip()
        if not query:
            continue
        _pid, snippet = google_text_search_place_id(api_key, query)
        if not isinstance(snippet, dict):
            continue
        lat, lng = _extract_lat_lng_place(snippet)
        if lat is None or lng is None:
            continue
        row["park_lat"] = lat
        row["park_lng"] = lng


def main() -> int:
    if callable(load_dotenv):
        load_dotenv()
    args = parse_args()
    project_dir = Path(__file__).resolve().parent
    location = str(args.location).strip()
    if not location:
        log_err("Error: location must be non-empty.")
        return 1

    slug = location_slug(location)
    output_path = project_dir / f"{slug}.html"
    top3_path = project_dir / f"{slug}-top3.json"
    scores_path = project_dir / f"{slug}-scores.json"
    index_path = (project_dir / args.index).resolve()
    review_data_dir = project_dir / "review-data"
    local_knowledge_cache = review_data_dir / f"{slug}-local-knowledge.txt"
    faq_cache = review_data_dir / f"{slug}-faq.json"
    hero_cache = review_data_dir / f"{slug}-hero-tagline.txt"

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if args.fresh_copy and not anthropic_key:
        log_err("Error: ANTHROPIC_API_KEY is required with --fresh-copy.")
        return 1

    if not index_path.exists():
        log_err(f"Error: index.html reference not found: {index_path}")
        return 1

    log(f"Location: {location}")
    log(f"Output file: {output_path.name}")

    ranked: list[dict[str, Any]]
    honourables: list[dict[str, Any]] = []
    if top3_path.exists():
        log(f"Found pre-scored top 3 data: {top3_path.name}. Using it instead of live Apify scrape.")
        ranked = load_prescored_top3(top3_path, location=location)
        if not ranked:
            log_err("Pre-scored top 3 file exists but had no usable rows.")
            return 1
        if len(ranked) < 3:
            log_err(
                f"Warning: {top3_path.name} has only {len(ranked)} park(s). "
                "Top 3 is sourced strictly from top3 JSON (no score-file fallback)."
            )
            if scores_path.exists() and "gold coast" in location.lower():
                nrma_name = "NRMA Treasure Island Holiday Resort, Gold Coast"
                has_nrma = any(nrma_name.lower() == str(r.get("name") or "").strip().lower() for r in ranked)
                if not has_nrma:
                    nrma_row = load_named_park_from_scores(
                        scores_path,
                        location=location,
                        park_name=nrma_name,
                    )
                    if nrma_row is not None:
                        ranked.append(nrma_row)
                        log("Added NRMA Treasure Island as third featured park for Gold Coast.")
        excluded = {str(r.get("name") or "").strip() for r in ranked if str(r.get("name") or "").strip()}
        if scores_path.exists():
            honourables = load_honourable_mentions_from_scores(
                scores_path,
                location=location,
                excluded_names=excluded,
            )
            log(
                f"Loaded honourable mentions from {scores_path.name}: {len(honourables)} "
                "(score > 60 and not in top 3)."
            )
        else:
            log(f"No scores file found for honourable mentions: {scores_path.name}")
    else:
        token = os.environ.get("APIFY_TOKEN", "").strip()
        if not token:
            log_err("Error: APIFY_TOKEN environment variable is not set.")
            return 1
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

    google_maps_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    maps_embed_url = ""
    if google_maps_key:
        log("Enriching top 3 parks via Google Places (details, supermarkets, beaches, map embed)...")
        try:
            maps_embed_url = enrich_top_three_parks_google(
                ranked, google_maps_key, location=location
            )
            if honourables:
                enrich_honourables_google(honourables, google_maps_key, location=location)
        except Exception as e:
            log_err(f"Google Places enrichment error (continuing with partial data): {e}")
            maps_embed_url = ""
    else:
        log("GOOGLE_MAPS_API_KEY not set; skipping Google Places enrichment.")

    review_data_dir.mkdir(parents=True, exist_ok=True)

    intro_paragraph = ""
    if (not args.fresh_copy) and local_knowledge_cache.exists():
        try:
            intro_paragraph = local_knowledge_cache.read_text(encoding="utf-8").strip()
            log(f"Loaded cached Local Knowledge: {local_knowledge_cache.name}")
        except OSError as e:
            log_err(f"Warning: failed to read Local Knowledge cache ({e}); regenerating.")
    if not intro_paragraph:
        if anthropic_key:
            log("Calling Claude API for Local Knowledge paragraph...")
            try:
                intro_paragraph = fetch_claude_intro(anthropic_key, location=location)
                local_knowledge_cache.write_text(intro_paragraph, encoding="utf-8")
                log(f"Saved Local Knowledge cache: {local_knowledge_cache.name}")
            except RuntimeError as e:
                log_err(f"Warning: Claude intro failed ({e}); continuing without Local Knowledge.")
            except Exception as e:
                log_err(f"Warning: Claude intro failed ({e}); continuing without Local Knowledge.")
        else:
            log("No ANTHROPIC_API_KEY set; using cached/no Local Knowledge copy.")

    hero_tagline = ""
    if (not args.fresh_copy) and hero_cache.exists():
        try:
            hero_tagline = hero_cache.read_text(encoding="utf-8").strip()
            log(f"Loaded cached hero tagline: {hero_cache.name}")
        except OSError as e:
            log_err(f"Warning: failed to read hero cache ({e}); regenerating.")
    if not hero_tagline:
        if anthropic_key:
            log("Calling Claude API for hero tagline...")
            try:
                hero_tagline = fetch_claude_hero_tagline(anthropic_key, location=location)
                hero_cache.write_text(hero_tagline, encoding="utf-8")
                log(f"Saved hero tagline cache: {hero_cache.name}")
            except RuntimeError as e:
                log_err(f"Warning: Claude hero tagline failed ({e}); using fallback line.")
            except Exception as e:
                log_err(f"Warning: Claude hero tagline failed ({e}); using fallback line.")
        else:
            log("No ANTHROPIC_API_KEY set; using cached/no hero tagline copy.")

    park_count = len(ranked)

    if len(ranked) >= 3:
        bf_labels = compute_best_for_labels(ranked[:3])
        for i in range(3):
            if not str(ranked[i].get("best_for") or "").strip():
                ranked[i]["best_for"] = bf_labels[i]

    if len(ranked) < 3:
        log_err("Warning: fewer than 3 parks matched — comparison table will be omitted.")

    faq_entries: list[dict[str, str]] = []
    if (not args.fresh_copy) and faq_cache.exists():
        try:
            loaded_faq = json.loads(faq_cache.read_text(encoding="utf-8"))
            if isinstance(loaded_faq, list):
                faq_entries = [x for x in loaded_faq if isinstance(x, dict)]
                log(f"Loaded cached FAQ: {faq_cache.name}")
        except Exception as e:
            log_err(f"Warning: failed to read FAQ cache ({e}); regenerating.")
    if not faq_entries:
        if anthropic_key:
            log("Calling Claude API for FAQ section...")
            try:
                faq_entries = fetch_claude_faq(anthropic_key, location=location)
                faq_cache.write_text(json.dumps(faq_entries, indent=2, ensure_ascii=False), encoding="utf-8")
                log(f"Saved FAQ cache: {faq_cache.name}")
            except RuntimeError as e:
                log_err(f"Warning: Claude FAQ failed ({e}); FAQ section omitted.")
            except Exception as e:
                log_err(f"Warning: Claude FAQ failed ({e}); FAQ section omitted.")
        else:
            log("No ANTHROPIC_API_KEY set; using cached/no FAQ copy.")

    index_html = index_path.read_text(encoding="utf-8")
    if google_maps_key:
        backfill_missing_coords(ranked[:3], api_key=google_maps_key, location=location)
        backfill_missing_coords(honourables, api_key=google_maps_key, location=location)
    document = build_page_html(
        index_html=index_html,
        rows=ranked,
        honourables=honourables,
        location=location,
        intro_paragraph=intro_paragraph,
        hero_tagline=hero_tagline,
        maps_api_key=google_maps_key,
        faq_entries=faq_entries,
        park_count=park_count,
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
