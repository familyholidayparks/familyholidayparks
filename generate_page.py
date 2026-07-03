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
from datetime import date
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

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

NEARBY_LOCATIONS = {
    "Gold Coast QLD": [("Noosa", "/noosa-queensland"), ("Sunshine Coast", "/sunshine-coast-queensland"), ("Byron Bay", "/byron-bay-new-south-wales")],
    "Noosa QLD": [("Sunshine Coast", "/sunshine-coast-queensland"), ("Gold Coast", "/gold-coast-queensland"), ("Rainbow Beach", "/rainbow-beach-queensland")],
    "Sunshine Coast QLD": [("Noosa", "/noosa-queensland"), ("Gold Coast", "/gold-coast-queensland"), ("Bribie Island", "/bribie-island-queensland")],
    "Byron Bay NSW": [("Ballina", "/ballina-new-south-wales"), ("Gold Coast", "/gold-coast-queensland"), ("Lennox Head", "/lennox-head-new-south-wales")],
    "Airlie Beach QLD": [("Cairns", "/cairns-queensland"), ("Townsville", "/townsville-queensland"), ("Mission Beach", "/mission-beach-queensland")],
    "Cairns QLD": [("Port Douglas", "/port-douglas-queensland"), ("Mission Beach", "/mission-beach-queensland"), ("Airlie Beach", "/airlie-beach-queensland")],
    "Port Douglas QLD": [("Cairns", "/cairns-queensland"), ("Mission Beach", "/mission-beach-queensland"), ("Airlie Beach", "/airlie-beach-queensland")],
    "Hervey Bay QLD": [("Rainbow Beach", "/rainbow-beach-queensland"), ("Bundaberg", "/bundaberg-queensland"), ("Agnes Water", "/agnes-water-queensland")],
    "Agnes Water QLD": [("1770", "/1770-queensland"), ("Bundaberg", "/bundaberg-queensland"), ("Hervey Bay", "/hervey-bay-queensland")],
    "1770 QLD": [("Agnes Water", "/agnes-water-queensland"), ("Bundaberg", "/bundaberg-queensland"), ("Yeppoon", "/yeppoon-queensland")],
    "Great Ocean Road VIC": [("Lorne", "/lorne-victoria"), ("Apollo Bay", "/apollo-bay-victoria"), ("Torquay", "/torquay-victoria")],
    "Phillip Island VIC": [("Mornington Peninsula", "/mornington-peninsula-victoria"), ("Inverloch", "/inverloch-victoria"), ("Gippsland", "/gippsland-victoria")],
    "Margaret River WA": [("Busselton", "/busselton-western-australia"), ("Dunsborough", "/dunsborough-western-australia"), ("Perth", "/perth-western-australia")],
    "Broome WA": [("Exmouth", "/exmouth-western-australia"), ("Perth", "/perth-western-australia"), ("Darwin", "/darwin-northern-territory")],
    "Hobart TAS": [("Freycinet", "/freycinet-tasmania"), ("East Coast Tasmania", "/east-coast-tasmania-tasmania"), ("Launceston", "/launceston-tasmania")],
    "Kangaroo Island SA": [("Adelaide", "/adelaide-south-australia"), ("Victor Harbor", "/victor-harbor-south-australia"), ("Goolwa", "/goolwa-south-australia")],
}

# (keyword, logo_path) — keyword is matched case-insensitively against park name + website URL.
BRAND_LOGOS: list[tuple[str, str]] = [
    ("big4", "/images/logos/big4.png"),
    ("nrma", "/images/logos/nrma.png"),
    ("nobby beach", "/images/logos/nobby-beach.png"),
    ("goldcoasttouristparks", "/images/logos/gold-coast-tourist-parks.jpg"),
    ("jacobs well", "/images/logos/gold-coast-tourist-parks.jpg"),
]


def get_brand_logo(park_name: str, website: str = "") -> str:
    """Return the logo img path for a park (matched on name or website URL), or ''."""
    haystack = (park_name + " " + website).lower()
    for keyword, path in BRAND_LOGOS:
        if keyword in haystack:
            return path
    return ""


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

# Apify: compass Google Maps / Places scraper (Google Maps Scraper)
APIFY_ACTOR_SLUG = "compass~crawler-google-places"
APIFY_SYNC_URL = f"https://api.apify.com/v2/acts/{APIFY_ACTOR_SLUG}/run-sync-get-dataset-items"

CLAUDE_MODEL = "claude-sonnet-4-5"

ALLOWED_CATEGORY_TERMS = ("rv park", "campground", "holiday park", "caravan park", "tourist park")

PLACE_TEXTSEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
PLACE_NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"

META_PIXEL = """
<!-- Meta Pixel Code -->
<script>
!function(f,b,e,v,n,t,s)
{if(f.fbq)return;n=f.fbq=function(){n.callMethod?
n.callMethod.apply(n,arguments):n.queue.push(arguments)};
if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
n.queue=[];t=b.createElement(e);t.async=!0;
t.src=v;s=b.getElementsByTagName(e)[0];
s.parentNode.insertBefore(t,s)}(window, document,'script',
'https://connect.facebook.net/en_US/fbevents.js');
fbq('init', '909873062100576');
fbq('track', 'PageView');
</script>
<noscript><img height="1" width="1" style="display:none"
src="https://www.facebook.com/tr?id=909873062100576&ev=PageView&noscript=1"
/></noscript>
<!-- End Meta Pixel Code -->
"""

GA4_TAG = """
<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-VVPFY2WRM1"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'G-VVPFY2WRM1');
</script>
"""

EXTRA_PAGE_CSS = """
  /* ═══════════════════════════════════════════
     FAMILY HOLIDAY PARKS — Location Page
     Design system: mirrors homepage exactly
     Airbnb-inspired. One accent: #0072CE
     ═══════════════════════════════════════════ */

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --teal: #0072CE;
    --teal-h: #005fa8;
    --teal-light: #f0f6ff;
    --text: #222222;
    --text-2: #717171;
    --border: #DDDDDD;
    --bg: #FFFFFF;
    --bg-2: #F7F7F7;
    --r: 12px;
    --shadow: 0 1px 2px rgba(0,0,0,0.08);
    --shadow-md: 0 2px 10px rgba(0,0,0,0.1);
    --shadow-lg: 0 4px 24px rgba(0,0,0,0.1);
    /* legacy aliases */
    --deep: #0072CE; --forest: #0072CE; --leaf: #717171;
    --sand: #f0f6ff; --cream: #F7F7F7; --light-green: #f0f6ff;
    --accent: #0072CE; --accent-hover: #005fa8;
    --text-secondary: #717171; --bg-secondary: #F7F7F7;
  }

  html { scroll-behavior: smooth; }
  body {
    font-family: 'DM Sans', -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    -webkit-font-smoothing: antialiased;
    font-size: 16px;
    line-height: 1.5;
  }

  /* ── NAV ── */
  .site-nav {
    background: rgba(255,255,255,0.97);
    border-bottom: 1px solid var(--border);
    position: sticky;
    top: 0;
    z-index: 100;
    backdrop-filter: blur(8px);
  }
  .site-nav-inner {
    max-width: 1120px;
    margin: 0 auto;
    padding: 0 24px;
    height: 64px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }
  .site-nav a.logo img { height: 36px; width: auto; display: block; }
  .nav-back-link {
    font-size: 14px;
    font-weight: 600;
    color: var(--text);
    text-decoration: none;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 10px 16px;
    border: 1.5px solid var(--border);
    border-radius: 100px;
    transition: box-shadow 0.15s, border-color 0.15s;
  }
  .nav-back-link:hover { box-shadow: var(--shadow-md); border-color: var(--text); }

  /* ── HERO — full width photo, no overlay ── */
  .hero.hero--page {
    width: 100%;
    background: var(--bg-2);
    overflow: hidden;
    position: relative;
  }
  .hero.hero--page .hero-photo {
    width: 100%;
    height: 420px;
    object-fit: cover;
    display: block;
  }
  .hero.hero--page .hero-photo-placeholder {
    width: 100%;
    height: 420px;
    background: linear-gradient(135deg, #e6f2fb 0%, #d0e8f7 100%);
    display: block;
  }
  /* location title sits BELOW photo on white background */
  .hero-title-block {
    max-width: 1120px;
    margin: 0 auto;
    padding: 24px 24px 0;
  }
  .hero-eyebrow {
    font-size: 13px;
    color: var(--text-2);
    margin-bottom: 6px;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .hero-eyebrow::before {
    content: '';
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--teal);
    display: inline-block;
  }
  .hero.hero--page h1 {
    font-family: 'Fraunces', serif;
    font-size: clamp(1.6rem, 3.5vw, 2.4rem);
    font-weight: 700;
    color: var(--text);
    line-height: 1.15;
    letter-spacing: -0.02em;
    margin-bottom: 8px;
  }
  .hero.hero--page .hero-tagline {
    font-size: 15px;
    color: var(--text-2);
    line-height: 1.6;
    max-width: 680px;
  }
  .hero-intro-p { margin-bottom: 0.75rem; }
  .hero-intro-p:last-child { margin-bottom: 0; }

  /* ── DIVIDER ── */
  .section-divider {
    border: none;
    border-top: 1px solid var(--border);
    margin: 0;
  }

  /* ── CONTENT WRAPPER ── */
  .page-content { max-width: 1120px; margin: 0 auto; padding: 0 24px; }

  /* ── PARK CARDS SCROLL ── */
  .parks-section {
    padding: 32px 0 0;
    border-bottom: 1px solid var(--border);
  }
  .parks-section-header {
    max-width: 1120px;
    margin: 0 auto;
    padding: 0 24px;
    margin-bottom: 16px;
  }
  .parks-section-header h2 {
    font-family: 'Fraunces', serif;
    font-size: clamp(1.2rem, 2.5vw, 1.5rem);
    font-weight: 700;
    color: var(--text);
    letter-spacing: -0.01em;
    margin-bottom: 2px;
  }
  .parks-section-header p { font-size: 14px; color: var(--text-2); }
  .parks-scroll {
    display: flex;
    gap: 16px;
    overflow-x: auto;
    padding: 4px 24px 28px;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
    scroll-snap-type: x mandatory;
  }
  .parks-scroll::-webkit-scrollbar { display: none; }

  /* ── PARK CARD ── */
  .park-card, .detail-card-wrapper {
    min-width: 240px;
    max-width: 260px;
    flex: 0 0 240px;
    background: white;
    border-radius: var(--r);
    border: 1px solid var(--border);
    overflow: hidden;
    display: flex;
    flex-direction: column;
    scroll-snap-align: start;
    transition: box-shadow 0.2s;
    cursor: pointer;
  }
  .park-card:hover, .detail-card-wrapper:hover { box-shadow: var(--shadow-lg); }
  .park-card-photo { position: relative; flex-shrink: 0; }
  .park-card-photo img, .park-card-photo .placeholder {
    width: 100%;
    height: 180px;
    object-fit: cover;
    display: block;
  }
  .park-card-photo .placeholder {
    background: var(--bg-2);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 2rem;
    color: var(--border);
  }
  .park-card-score {
    position: absolute;
    top: 10px; right: 10px;
    background: white;
    color: var(--text);
    font-size: 12px;
    font-weight: 700;
    padding: 4px 10px;
    border-radius: 100px;
    box-shadow: var(--shadow-md);
  }
  .park-card-rank {
    position: absolute;
    top: 10px; left: 10px;
    width: 26px; height: 26px;
    border-radius: 50%;
    background: white;
    display: flex; align-items: center; justify-content: center;
    font-size: 12px; font-weight: 800;
    color: var(--teal);
    box-shadow: var(--shadow-md);
  }
  .park-card-body {
    padding: 14px 16px 16px;
    display: flex; flex-direction: column; gap: 6px; flex: 1;
  }
  .park-card-name, .detail-card .park-name {
    font-size: 14px; font-weight: 700;
    color: var(--text); line-height: 1.3; margin: 0;
  }
  .park-card-desc, .card-best-for {
    font-size: 13px; color: var(--text-2); line-height: 1.5; margin: 0;
  }
  .park-card-chips { display: flex; flex-wrap: wrap; gap: 4px; }
  .chip {
    font-size: 12px; font-weight: 500;
    padding: 3px 10px; border-radius: 100px;
    background: var(--bg-2); color: var(--text-2);
    border: 1px solid var(--border); white-space: nowrap;
  }
  .park-card-meta {
    font-size: 13px; color: var(--text-2);
    display: flex; flex-direction: column; gap: 2px;
  }
  .park-card-meta strong { color: var(--text); font-weight: 600; }

  /* ── BOOK BUTTON ── */
  .book-btn, .park-card-book {
    display: block; width: 100%;
    text-align: center;
    background: #222;
    color: white;
    font-size: 13px; font-weight: 700;
    padding: 12px;
    border-radius: 8px;
    border: none;
    text-decoration: none;
    cursor: pointer;
    transition: background 0.15s;
    box-shadow: none;
    animation: none;
    margin-top: auto;
    letter-spacing: 0.01em;
  }
  .book-btn:hover, .park-card-book:hover { background: #000; }

  /* ── DETAIL CARD (top 3 full cards) ── */
  .detail-card {
    background: white;
    border-radius: var(--r);
    border: 1px solid var(--border);
    overflow: hidden;
    display: flex; flex-direction: column;
    flex: 1 1 300px;
    transition: box-shadow 0.2s;
  }
  .detail-card:hover { box-shadow: var(--shadow-lg); }
  .detail-card img.card-hero-photo {
    width: 100%; height: 200px;
    object-fit: cover; display: block;
    background: var(--bg-2);
  }
  .detail-card-body { padding: 18px; flex: 1; display: flex; flex-direction: column; gap: 8px; }
  .card-summary, .card-summary-wrap { font-size: 14px; color: var(--text-2); line-height: 1.6; flex-grow: 1; }
  .detail-meta {
    font-size: 14px; color: var(--text);
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  }
  .detail-meta .star-score { font-weight: 600; }
  .detail-meta .muted { color: var(--text-2); font-weight: 400; }
  .detail-distances { font-size: 13px; color: var(--text-2); line-height: 1.6; }
  .detail-distances span { display: block; }
  .detail-section { max-width: 1120px; margin: 0 auto; padding: 32px 24px; }
  .detail-section > h2 {
    font-family: 'Fraunces', serif;
    font-size: clamp(1.2rem, 2.5vw, 1.5rem);
    font-weight: 700; color: var(--text);
    margin-bottom: 20px;
  }
  .detail-cards { display: flex; flex-wrap: wrap; gap: 16px; align-items: stretch; }

  /* ── COMPARE TABLE ── */
  .compare-section {
    border-top: 1px solid var(--border);
    background: white;
    padding-bottom: 40px;
  }
  .compare-section > h2 {
    font-family: 'Fraunces', serif;
    font-size: clamp(1.2rem, 2.5vw, 1.5rem);
    font-weight: 700; color: var(--text);
    padding: 32px 24px 4px;
  }
  .compare-section > p {
    font-size: 14px; color: var(--text-2);
    padding: 0 24px 16px;
  }
  .compare-scroll {
    overflow-x: auto; -webkit-overflow-scrolling: touch;
  }
  .compare-scroll::-webkit-scrollbar { height: 3px; }
  .compare-scroll::-webkit-scrollbar-thumb { background: var(--border); border-radius: 100px; }
  .compare-table {
    width: 100%; min-width: 600px;
    border-collapse: separate; border-spacing: 0;
    background: white;
  }
  /* Top label row */
  .compare-table thead tr:first-child th {
    font-size: 10px; font-weight: 600;
    letter-spacing: 0.1em; text-transform: uppercase;
    color: var(--text-2); background: var(--bg-2);
    padding: 8px 16px; border-bottom: 1px solid var(--border);
    text-align: center;
  }
  .compare-table thead tr:first-child th:first-child {
    background: white; border-bottom: 1px solid var(--border);
  }
  /* Park name headers */
  .compare-table thead .park-head {
    text-align: left; vertical-align: bottom;
    padding: 16px 16px 12px;
    font-size: 13px; font-weight: 600; color: var(--text);
    line-height: 1.35;
    background: white;
    border-bottom: 2px solid var(--border);
    min-width: 160px; max-width: 200px;
  }
  .compare-table thead th.scope-corner {
    background: white; position: sticky; left: 0; z-index: 3;
    border-bottom: 2px solid var(--border);
    min-width: 100px; max-width: 100px;
    vertical-align: bottom; padding-bottom: 12px;
  }
  /* Row headers */
  .compare-table tbody th {
    font-size: 11px; font-weight: 600;
    color: var(--text-2); text-align: left;
    padding: 12px 16px;
    background: white;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
    position: sticky; left: 0; z-index: 2;
    min-width: 100px; max-width: 100px;
    text-transform: uppercase; letter-spacing: 0.06em;
  }
  /* Data cells */
  .compare-table td {
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
    font-size: 13px; color: var(--text);
    vertical-align: middle; line-height: 1.45;
    min-width: 160px;
  }
  .compare-table tbody tr:hover td { background: #fafafa; }
  .compare-table tbody tr:hover th { background: #fafafa; }
  .compare-table tbody tr:last-child td,
  .compare-table tbody tr:last-child th { border-bottom: none; }
  /* Score pills — all same */
  .score-gold, .score-silver, .score-plain {
    background: var(--bg-2); color: var(--text);
    font-weight: 700; font-size: 13px;
    padding: 4px 12px; border-radius: 100px;
    display: inline-block; border: 1px solid var(--border);
  }
  .cell-strong { font-weight: 600; }
  .muted { color: var(--text-2); }
  .price-notes { font-size: 12px; color: var(--text-2); margin: 0; padding-left: 1rem; }

  /* ── MAP ── */
  .map-embed-section { border-top: 1px solid var(--border); padding: 32px 24px; max-width: 1120px; margin: 0 auto; }
  .map-frame { width: 100%; height: 400px; border: 0; border-radius: var(--r); box-shadow: var(--shadow); display: block; }
  .map-placeholder { height: 400px; background: var(--bg-2); border-radius: var(--r); border: 1px solid var(--border); display: flex; align-items: center; justify-content: center; color: var(--text-2); font-size: 14px; }

  /* ── WHY FAMILIES ── */
  .why-families-bg { border-top: 1px solid var(--border); background: var(--bg-2); }
  .why-families-section { max-width: 1120px; margin: 0 auto; padding: 32px 24px; text-align: center; }
  .why-families-section h2 {
    font-family: 'Fraunces', serif;
    font-size: clamp(1.2rem, 2.5vw, 1.5rem);
    font-weight: 700; color: var(--text); margin-bottom: 20px;
  }
  .why-families-list { list-style: none; display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; padding: 0; }
  .why-families-list li {
    background: white; border: 1px solid var(--border);
    border-radius: 100px; padding: 10px 20px;
    font-size: 14px; color: var(--text); font-weight: 500;
    display: flex; align-items: center; gap: 8px;
  }
  .why-families-list li::before { content: '✓'; color: var(--teal); font-weight: 700; font-size: 13px; }

  /* ── LOCAL KNOWLEDGE ── */
  .local-knowledge {
    border-top: 1px solid var(--border);
    padding: 32px 24px;
    max-width: 760px;
    margin: 0 auto;
  }
  .local-knowledge h2 {
    font-family: 'Fraunces', serif;
    font-size: clamp(1.2rem, 2.5vw, 1.5rem);
    font-weight: 700; color: var(--text); margin-bottom: 14px;
  }
  .local-knowledge p { font-size: 15px; line-height: 1.75; color: var(--text-2); }

  /* ── NEARBY ── */
  .nearby-locations { border-top: 1px solid var(--border); padding: 32px 24px; max-width: 760px; margin: 0 auto; }
  .nearby-locations h2 {
    font-family: 'Fraunces', serif;
    font-size: clamp(1.2rem, 2.5vw, 1.5rem);
    font-weight: 700; color: var(--text);
    margin-bottom: 16px;
  }
  .nearby-locations ul { list-style: none; padding: 0; display: flex; flex-direction: column; gap: 8px; }
  .nearby-locations li a {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 20px;
    background: white; border: 1px solid var(--border);
    border-radius: var(--r);
    font-size: 15px; font-weight: 500; color: var(--text);
    text-decoration: none; transition: box-shadow 0.15s, border-color 0.15s;
  }
  .nearby-locations li a::after { content: '→'; color: var(--text-2); }
  .nearby-locations li a:hover { box-shadow: var(--shadow-md); border-color: var(--text); }

  /* ── FAQ ── */
  .faq-section { border-top: 1px solid var(--border); padding: 32px 24px; max-width: 760px; margin: 0 auto; }
  .faq-section > h2 {
    font-family: 'Fraunces', serif;
    font-size: clamp(1.2rem, 2.5vw, 1.5rem);
    font-weight: 700; color: var(--text);
    margin-bottom: 20px;
  }
  details.faq-item {
    border: 1px solid var(--border); border-radius: var(--r);
    margin-bottom: 8px; overflow: hidden; background: white;
  }
  details.faq-item summary {
    font-size: 15px; font-weight: 600; color: var(--text);
    cursor: pointer; padding: 18px 20px;
    list-style: none;
    display: flex; justify-content: space-between; align-items: center; gap: 16px;
  }
  details.faq-item summary::-webkit-details-marker { display: none; }
  details.faq-item summary::after { content: '+'; font-size: 20px; font-weight: 300; color: var(--text-2); flex-shrink: 0; }
  details.faq-item[open] summary::after { content: '−'; }
  details.faq-item[open] summary { border-bottom: 1px solid var(--border); }
  .faq-answer { padding: 16px 20px 20px; font-size: 15px; line-height: 1.65; color: var(--text-2); }

  /* ── LEAD MAGNET ── */
  .lead-magnet { border-top: 1px solid var(--border); background: var(--bg-2); padding: 48px 24px; text-align: center; }
  .lead-magnet-inner { max-width: 480px; margin: 0 auto; }
  .lead-magnet h2 { font-family: 'Fraunces', serif; font-size: clamp(1.4rem, 3vw, 1.9rem); font-weight: 700; color: var(--text); margin-bottom: 8px; }
  .lead-magnet .sub { font-size: 15px; color: var(--text-2); margin-bottom: 24px; line-height: 1.6; }
  .lead-magnet ul { text-align: left; margin: 0 0 24px; padding-left: 20px; font-size: 14px; line-height: 1.7; color: var(--text-2); }
  .lead-magnet-form { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; }
  .lead-magnet-form input[type="email"] {
    flex: 1 1 220px; min-width: 200px;
    padding: 13px 18px; border-radius: 8px;
    border: 1.5px solid var(--border);
    font-size: 15px; font-family: inherit; color: var(--text); outline: none;
    transition: border-color 0.15s;
  }
  .lead-magnet-form input:focus { border-color: var(--teal); }
  .lead-magnet-form button[type="submit"] {
    flex: 0 0 auto;
    background: var(--teal); color: white; border: none;
    padding: 13px 24px; font-size: 15px; font-weight: 700;
    font-family: inherit; cursor: pointer; border-radius: 8px;
    transition: background 0.15s; white-space: nowrap;
  }
  .lead-magnet-form button:hover { background: var(--teal-h); }

  /* ── STICKY BOTTOM BAR ── */
  .sticky-bottom {
    position: fixed; bottom: 0; left: 0; right: 0; z-index: 200;
    background: white; border-top: 1px solid var(--border);
    padding: 12px 24px max(16px, env(safe-area-inset-bottom));
    display: flex; align-items: center; justify-content: space-between; gap: 16px;
  }
  .sticky-bottom-info { display: flex; flex-direction: column; gap: 1px; }
  .sticky-bottom-info .loc { font-size: 14px; font-weight: 600; color: var(--text); }
  .sticky-bottom-info .parks-count { font-size: 13px; color: var(--text-2); }
  .sticky-bottom-cta {
    background: var(--teal); color: white;
    font-size: 14px; font-weight: 700;
    padding: 12px 24px; border-radius: 8px;
    text-decoration: none; white-space: nowrap;
    transition: background 0.15s;
    flex-shrink: 0;
  }
  .sticky-bottom-cta:hover { background: var(--teal-h); }
  /* Push content above sticky bar */
  body { padding-bottom: 88px; }

  /* ── FOOTER ── */
  .site-footer-page {
    border-top: 1px solid var(--border);
    background: white;
    text-align: center;
    padding: 32px 24px 48px;
    font-size: 13px;
    color: var(--text-2);
  }
  .site-footer-page img { height: 36px; width: auto; display: block; margin: 0 auto 10px; opacity: 0.6; }
  .site-footer-page a { color: var(--text-2); text-decoration: none; }
  .site-footer-page a:hover { color: var(--teal); text-decoration: underline; }
  body.location-page-footer-pad footer:not(.site-footer-page) { display: none; }

  /* ── MISC ── */
  .amenities { margin-bottom: 12px; }
  .badge { display: inline-block; font-size: 12px; font-weight: 500; padding: 3px 10px; border-radius: 100px; background: var(--bg-2); color: var(--text-2); border: 1px solid var(--border); margin: 2px; }
  .compare-wrap-zero-gap { margin: 0; padding: 0; }

  /* ── MOBILE ── */
  @media (max-width: 768px) {
    .site-nav-inner { padding: 0 16px; height: 56px; }
    .hero.hero--page .hero-photo,
    .hero.hero--page .hero-photo-placeholder { height: 280px; }
    .hero-title-block { padding: 16px 16px 0; }
    .detail-cards { flex-direction: column; }
    .parks-scroll { padding: 4px 16px 20px; }
    .park-card, .detail-card-wrapper { min-width: 220px; flex: 0 0 220px; }
    .compare-table tbody th { font-size: 11px; padding: 10px 8px; min-width: 90px; max-width: 90px; white-space: normal; line-height: 1.2; }
    .compare-table thead th.scope-corner { width: 90px; min-width: 90px; }
    .compare-table td { font-size: 13px; padding: 10px 8px; min-width: 130px; }
    .compare-table thead .park-head { font-size: 13px; padding: 12px 8px; }
    .faq-section, .local-knowledge, .destination-summary, .nearby-locations, .lead-magnet { padding: 28px 16px; }
    .why-families-section { padding: 28px 16px; }
    .map-embed-section { padding: 28px 16px; }
    .detail-section { padding: 28px 16px; }
    .parks-section-header { padding: 0 16px; }
    .sticky-bottom { padding: 10px 16px max(16px, env(safe-area-inset-bottom)); }
    .sticky-bottom-cta { padding: 10px 18px; font-size: 13px; }
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
    rows: list[dict[str, Any]],
    api_key: str,
    *,
    location: str,
    refresh_places: bool = False,
) -> str:
    if len(rows) < 3:
        return ""
    coords_for_embed: list[tuple[float | None, float | None]] = []
    for i, row in enumerate(rows[:3]):
        nm = str(row.get("name") or "")
        # Skip enrichment if all data is already cached
        photo_cached = str(row.get("photo_url_override") or row.get("photo_url_cached") or "").strip()
        beach_cached = row.get("nearest_beach_cached")
        super_cached = row.get("nearest_supermarket_cached")

        has_photo = photo_cached.startswith(("http", "/images/"))
        has_beach = isinstance(beach_cached, dict) and beach_cached.get("name") and beach_cached.get("km") is not None
        has_super = isinstance(super_cached, dict) and super_cached.get("name") and super_cached.get("km") is not None

        if has_photo and has_beach and has_super and not refresh_places:
            log(f"[Google Places] Skipping {nm[:48]} — all data cached.")
            coords_for_embed.append((row.get("park_lat") or row.get("lat"), row.get("park_lng") or row.get("lng")))
            continue
        row.setdefault("google_photo_url", "")
        row.setdefault("supermarket_name", "")
        row.setdefault("supermarket_km", None)
        row.setdefault("beach_name", "")
        row.setdefault("beach_km", None)
        row.setdefault("google_amenities", {"pool": False, "playground": False, "pets": False})

        if not refresh_places:
            puc = str(row.get("photo_url_override") or row.get("photo_url_cached") or "").strip()
            nbc = row.get("nearest_beach_cached")
            nsc = row.get("nearest_supermarket_cached")
            photo_ok = puc.startswith(("http", "/images/"))
            beach_ok = (
                isinstance(nbc, dict)
                and str(nbc.get("name") or "").strip()
                and nbc.get("km") is not None
            )
            super_ok = (
                isinstance(nsc, dict)
                and str(nsc.get("name") or "").strip()
                and nsc.get("km") is not None
            )
            try:
                _lat = row.get("park_lat") or row.get("lat")
                has_lat = _lat is not None and float(_lat) != 0.0
                has_lng = row.get("park_lng") is not None and float(row.get("park_lng")) != 0.0
            except (TypeError, ValueError):
                has_lat = has_lng = False
            if photo_ok and beach_ok and super_ok and has_lat and has_lng:
                label = nm.strip()[:80] or "(unnamed)"
                log(f"[Google Places] Skipping park {i + 1}/3 — {label} — using cached data")
                coords_for_embed.append((float(row.get("park_lat") or row.get("lat")), float(row.get("park_lng") or row.get("lng"))))
                continue

        log(f"[Google Places] Enriching park {i + 1}/3: {nm[:64]}")
        plat: float | None = None
        plng: float | None = None
        try:
            lat_val = row.get("park_lat") or row.get("lat")
            lng_val = row.get("park_lng") or row.get("lng")
            if lat_val is not None:
                plat = float(lat_val)
            if lng_val is not None:
                plng = float(lng_val)
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
                google_places_photo_url = ""
                if isinstance(ref, str) and ref:
                    google_places_photo_url = google_build_photo_url(api_key, ref)
                # Never overwrite manually set photo overrides
                if row.get("photo_url_override"):
                    row["google_photo_url"] = row["photo_url_override"]
                    row["photo_url_cached"] = row["photo_url_override"]
                else:
                    row["google_photo_url"] = google_places_photo_url
                    if google_places_photo_url:
                        row["photo_url_cached"] = google_places_photo_url
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


def activity_description_display(desc: str, max_words: int = 15) -> str:
    """One concise sentence, max 15 words, for activity cards."""
    text = re.sub(r"\s+", " ", str(desc or "").strip())
    if not text:
        return ""
    first_sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip()
    words = first_sentence.split()
    if len(words) > max_words:
        first_sentence = " ".join(words[:max_words]).rstrip(",;:")
        if not first_sentence.endswith("."):
            first_sentence += "."
    return first_sentence


def location_slug(name: str) -> str:
    expanded = name.strip()
    for abbr, full_name in STATE_NAMES.items():
        expanded = re.sub(rf"\b{re.escape(abbr)}\b", full_name, expanded, flags=re.IGNORECASE)
    s = re.sub(r"[^a-zA-Z0-9]+", "-", expanded.lower()).strip("-")
    return s or "location"


def lookup_csv_row(
    project_dir: Path, location: str, loc_dir: Path | None = None
) -> dict[str, str] | None:
    """Match a location string or directory slug to a locations.csv row."""
    import csv as _csv

    bare = re.sub(
        r"\b(Queensland|New South Wales|Victoria|South Australia|Western Australia|Tasmania|Northern Territory|Australian Capital Territory|QLD|NSW|VIC|SA|WA|TAS|NT|ACT)\b",
        "",
        location,
        flags=re.IGNORECASE,
    ).strip().strip(",").strip()
    bare = re.sub(r"\s+", " ", bare).strip()
    loc_key = re.sub(r"\s+", " ", location.strip()).strip().lower()
    bare_key = bare.lower()
    dir_slug = loc_dir.name.lower() if loc_dir else ""
    arg_key = re.sub(r"[^a-z0-9]+", "-", location.strip().lower()).strip("-")

    csv_path = project_dir / "locations.csv"
    if not csv_path.exists():
        return None
    with open(csv_path, encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            row_loc = re.sub(r"\s+", " ", row.get("location", "").strip()).strip().lower()
            row_slug = row.get("slug", "").strip().lower()
            state_abbr = row.get("state", "").strip().upper()
            state_suffix = STATE_NAMES.get(state_abbr, state_abbr.lower())
            output_slug = f"{row_slug}-{state_suffix}" if row_slug and state_abbr else ""
            if (
                row_loc == loc_key
                or row_loc == bare_key
                or (dir_slug and row_slug == dir_slug)
                or (arg_key and arg_key in {row_slug, output_slug, f"{row_slug}-{state_abbr.lower()}"})
            ):
                return row
    return None


def output_slug_for_location(
    project_dir: Path, location: str, loc_dir: Path | None = None
) -> str:
    """Canonical public filename slug: {csv_slug}-{state_full} from locations.csv."""
    row = lookup_csv_row(project_dir, location, loc_dir)
    if row:
        slug = row.get("slug", "").strip()
        state = row.get("state", "").strip().upper()
        if slug and state:
            return f"{slug}-{STATE_NAMES.get(state, state.lower())}"
    return location_slug(location)


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


def display_name(name: str) -> str:
    overrides = {
        "NRMA Treasure Island Holiday Resort, Gold Coast": "NRMA Treasure Island Holiday Resort",
    }
    return overrides.get(name.strip(), name.strip())


def _one_line_desc(text: Any, *, max_len: int = 140) -> str:
    raw = str(text or "").strip()
    if not raw:
        return "Family-friendly holiday park option."
    first = re.split(r"[.!?]\s+", raw, maxsplit=1)[0].strip()
    first = re.sub(r"\s+", " ", first)
    if len(first) > max_len:
        first = first[: max_len - 1].rstrip() + "…"
    return first or "Family-friendly holiday park option."


def sanitize_no_numbers(text: str) -> str:
    return re.sub(r"\d+", "", text or "").strip()


def editorial_top3_copy(row: dict[str, Any]) -> str:
    rationale = normalize_text_paragraphs(row.get("rationale_top3") or "")
    if rationale:
        return rationale

    phrases = row.get("key_phrases") if isinstance(row.get("key_phrases"), list) else []
    phrase_text = ", ".join(str(p).strip() for p in phrases[:3] if str(p).strip())
    water = str(row.get("water_fun") or "").strip()
    kids = str(row.get("kids_play") or "").strip()
    best_for = str(row.get("best_for") or "").strip()
    watch_out = str(row.get("watch_out") or "").strip()
    name = str(row.get("name") or "This park").strip()
    p1 = (
        f"{name} is a well-regarded family holiday park with a strong reputation among returning guests. "
        "The atmosphere is friendly and organised, with facilities maintained to a consistently high standard."
    )
    p2 = (
        f"On-site highlights include {sanitize_no_numbers(water or 'water play')} and {sanitize_no_numbers(kids or 'kids activities')}. "
        f"{('Guests regularly mention ' + sanitize_no_numbers(phrase_text) + ' as standout features.') if phrase_text else 'Guests describe a relaxed, family-friendly rhythm that suits all ages.'}"
    )
    p3 = (
        f"Best suited to {sanitize_no_numbers(best_for).lower() if best_for else 'families seeking a balanced holiday base'}. "
        f"{('Worth knowing: ' + sanitize_no_numbers(watch_out).rstrip('. ') + '.') if watch_out else ''}"
    )
    return "\n\n".join(p.strip() for p in [p1, p2, p3] if p.strip())


PRICE_STRIP_WORDS = (
    "holiday park",
    "tourist park",
    "caravan park",
    "family",
    "resort",
)


def normalize_park_name_for_price(name: str) -> str:
    s = str(name or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    for word in PRICE_STRIP_WORDS:
        s = s.replace(word, " ")
    return re.sub(r"\s+", " ", s).strip()


def parse_price_entry(value: Any) -> dict[str, Any] | None:
    """Parse one prices.json value (string or structured dict)."""
    if isinstance(value, str):
        display = value.strip()
        if not display or display in {"—", "-"}:
            return None
        nums = re.findall(r"\d+(?:\.\d+)?", display)
        price_num = float(nums[0]) if nums else None
        return {
            "display": display,
            "price": price_num,
            "note": "",
            "confidence": "low",
        }

    if isinstance(value, dict):
        if str(value.get("confidence") or "").strip().lower() == "missing":
            return None
        display = str(value.get("display") or "").strip()
        price_num = value.get("price")
        if not display and price_num is not None:
            try:
                amount = float(price_num)
                if amount > 0:
                    amount_text = str(int(amount)) if amount.is_integer() else f"{amount:.0f}"
                    display = f"${amount_text}/night"
            except (TypeError, ValueError):
                pass
        if not display or display in {"—", "-"}:
            return None
        note = str(value.get("note") or value.get("pricing_notes") or "").strip()
        if isinstance(value.get("pricing_notes"), list):
            note = note or "; ".join(
                str(x).strip() for x in value["pricing_notes"] if str(x).strip()
            )
        return {
            "display": display,
            "price": price_num,
            "note": note,
            "confidence": str(value.get("confidence") or "low").strip() or "low",
        }

    return None


def destination_summary_section_html(text: str, bare_location: str) -> str:
    """Render 2–4 paragraphs for the holiday park scene section."""
    body_text = str(text or "").strip()
    if not body_text:
        return ""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body_text) if p.strip()]
    if len(paras) <= 1:
        paras = [line.strip() for line in body_text.splitlines() if line.strip()]
    if not paras:
        return ""
    body = "\n".join(f"  <p>{esc(p)}</p>" for p in paras)
    return f"""
<section class="content-section destination-summary">
  <h2>The {esc(bare_location)} Holiday Park Scene</h2>
{body}
</section>
"""


def if_we_were_booking_section_html(text: str) -> str:
    """Render the 'If We Were Booking...' editorial section."""
    body_text = str(text or "").strip()
    if not body_text:
        return ""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body_text) if p.strip()]
    if not paras:
        return ""
    body = "\n".join(f"  <p>{esc(p)}</p>" for p in paras)
    return f"""
<section class="content-section if-we-were-booking">
  <h2>If We Were Booking...</h2>
{body}
</section>
"""


def _parse_price(val) -> str:
    """Safely extract display price from string or dict."""
    if not val:
        return ""
    if isinstance(val, dict):
        if "display" not in val:
            nested = val.get("powered_weekday")
            if nested is not None and nested is not val:
                return _parse_price(nested)
        return val.get("display") or (val.get("price") and f"${val['price']}/night") or "—"
    if isinstance(val, str):
        text = val.strip()
        if "display" in text and (text.startswith("{") or text.startswith("${")):
            match = re.search(r"""['"]display['"]\s*:\s*['"]([^'"]+)['"]""", text)
            if match:
                return match.group(1).strip()
    return str(val)


MISSING_SORT_PRICE = 9999.0


def powered_sort_price_num(
    r: dict[str, Any],
    *,
    project_dir: Path | None = None,
    manual_prices: dict[str, Any] | None = None,
) -> float:
    """Numeric powered-site price for Best Value sorting; MISSING_SORT_PRICE = unavailable."""
    park_name = str(r.get("park_name") or r.get("name") or "").strip()

    if manual_prices:
        entry = lookup_manual_price(manual_prices, park_name)
        if entry is not None:
            if str(entry.get("confidence") or "").strip().lower() == "missing":
                return MISSING_SORT_PRICE
            pn = entry.get("price")
            if pn is not None:
                try:
                    p = float(pn)
                    if p > 0:
                        return p
                except (TypeError, ValueError):
                    pass
            display = str(entry.get("display") or "").strip()
            if display and display not in {"—", "-"}:
                nums = re.findall(r"\d+(?:\.\d+)?", display)
                if nums:
                    return float(nums[0])

    for src in (
        r.get("powered_weekday"),
        (r.get("prices") or {}).get("powered_weekday"),
    ):
        display = _parse_price(src)
        if display and display not in {"—", "-"}:
            nums = re.findall(r"\d+(?:\.\d+)?", display)
            if nums:
                return float(nums[0])

    if project_dir:
        master = load_park_master(project_dir, park_name)
        mpw = master.get("prices", {}).get("powered_weekday") or ""
        display = _parse_price(mpw)
        if display and display not in {"—", "-"}:
            nums = re.findall(r"\d+(?:\.\d+)?", display)
            if nums:
                return float(nums[0])

    return MISSING_SORT_PRICE


def load_manual_prices(path: Path) -> dict[str, Any]:
    """Load prices.json into exact and normalised lookup indexes."""
    empty: dict[str, Any] = {"by_exact": {}, "by_norm": {}}
    if not path.exists():
        return empty
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return empty
    if not isinstance(raw, dict):
        return empty

    by_exact: dict[str, dict[str, Any]] = {}
    by_norm: dict[str, dict[str, Any]] = {}
    for park_name, value in raw.items():
        name = str(park_name or "").strip()
        if not name:
            continue
        entry = parse_price_entry(value)
        if not entry:
            continue
        entry["park_name"] = name
        by_exact[name.lower()] = entry
        norm = normalize_park_name_for_price(name)
        if norm and norm not in by_norm:
            by_norm[norm] = entry
    return {"by_exact": by_exact, "by_norm": by_norm}


def lookup_manual_price(
    manual_prices: dict[str, Any], park_name: str
) -> dict[str, Any] | None:
    if not park_name:
        return None
    by_exact = manual_prices.get("by_exact") or {}
    by_norm = manual_prices.get("by_norm") or {}
    exact = park_name.strip().lower()
    if exact in by_exact:
        return by_exact[exact]
    norm = normalize_park_name_for_price(park_name)
    if norm and norm in by_norm:
        return by_norm[norm]
    return None


def load_manual_photos(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {str(k).strip(): str(v).strip() for k, v in raw.items() if k and v}


def apply_manual_photos(rows: list[dict[str, Any]], manual_photos: dict[str, str]) -> None:
    for row in rows:
        nm = str(row.get("name") or "").strip()
        if nm in manual_photos:
            existing = str(row.get("photo_url_override") or "").strip()
            if existing.startswith("/images/"):
                continue
            url = manual_photos[nm]
            row["google_photo_url"] = url
            row["photo_url_override"] = url
            row["photo_url_cached"] = url


def apply_manual_prices(rows: list[dict[str, Any]], manual_prices: dict[str, Any]) -> None:
    for row in rows:
        park_name = str(row.get("park_name") or row.get("name") or "").strip()
        entry = lookup_manual_price(manual_prices, park_name)
        if not entry:
            log(f"[price missing] {park_name}")
            continue

        display = str(entry.get("display") or "").strip()
        row["powered_weekday"] = display
        row["powered_site_price"] = display
        note = str(entry.get("note") or "").strip()
        if note:
            row["deals"] = note
            row["pricing_notes"] = [note]
        log(f"[price loaded] {park_name} -> {display}")


def load_park_websites(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {str(k).strip().lower(): str(v).strip() for k, v in raw.items() if k and v}
    except Exception:
        return {}


def apply_park_websites(rows: list[dict[str, Any]], websites: dict[str, str]) -> None:
    for row in rows:
        nm = str(row.get("name") or "").strip().lower()
        if nm in websites:
            row["website"] = websites[nm]


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
    website = str(row.get("website") or "").strip()
    maps = str(row.get("maps_url") or "").strip()
    if website:
        return website
    return maps or "#"


def get_google_maps_url(row: dict[str, Any]) -> str:
    name = str(row.get("name") or row.get("park_name") or "").strip()
    address = str(row.get("address") or "").strip()

    for key in ("maps_url", "google_maps_url", "googleMapsUrl"):
        url = str(row.get(key) or "").strip()
        if "google.com/maps" in url or "maps.app.goo.gl" in url:
            return url

    raw_url = str(row.get("url") or "").strip()
    if "google.com/maps" in raw_url or "maps.app.goo.gl" in raw_url:
        return raw_url

    for key in ("_apify_place_id", "place_id", "placeId", "googlePlaceId"):
        place_id = str(row.get(key) or "").strip().replace("places/", "")
        if place_id and name:
            return (
                "https://www.google.com/maps/search/?api=1&query="
                + urllib.parse.quote_plus(name)
                + "&query_place_id="
                + urllib.parse.quote_plus(place_id)
            )

    query = " ".join(x for x in [name, address] if x).strip()
    if query:
        return "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote_plus(query)

    return ""


def _google_am(row: dict[str, Any]) -> dict[str, bool]:
    g = row.get("google_amenities")
    if isinstance(g, dict):
        pool = bool(g.get("pool"))
        playground = bool(g.get("playground"))
        pets = bool(g.get("pets"))
        return {"pool": pool, "playground": playground, "pets": pets}
    return {"pool": False, "playground": False, "pets": False}


def clean_beach_name(raw: str) -> str:
    if not raw:
        return raw
    # Fix ALL CAPS
    parts = raw.split(',')
    name = parts[0].strip()
    distance = parts[1].strip() if len(parts) > 1 else ''

    # Title case the name
    name = name.title()

    # Clean up known Google Places bad names
    replacements = {
        'Apollo Bay Scenic Rest': 'Apollo Bay Beach',
        'Scenic Rest': 'Beach',
    }
    for bad, good in replacements.items():
        name = name.replace(bad, good)

    return f"{name}, {distance}" if distance else name


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
    text_l = text.lower()
    if "nrma treasure island" in name_l and ("dog beach" in text_l or "spit" in text_l):
        return clean_beach_name("Main Beach, 3.2 km")
    if not text and "big4 gold coast holiday park" in name_l:
        return clean_beach_name("Surfers Paradise Beach, 2.5 km")
    return clean_beach_name(text) if text else text


def comparison_supermarket_cell_text(row: dict[str, Any]) -> str:
    sn = row.get('supermarket_name') or ''
    sk = row.get('supermarket_km')

    # Also try nested cached format
    if not sn or sk is None:
        cached = row.get('nearest_supermarket_cached')
        if isinstance(cached, dict):
            sn = sn or str(cached.get('name') or '')
            if sk is None:
                sk = cached.get('km')

    if not sn:
        return '—'

    dk = format_distance_km(sk) if sk is not None else ''
    return f"{sn}, {dk}" if dk else sn


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
        raw = r.get("rating") if r.get("rating") is not None else r.get("google_rating")
        try:
            x = float(raw)
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
    top3_fixed: bool = False,
    show_best_for_line: bool = True,
) -> str:
    name = esc(display_name(row["name"]))
    href = esc(book_href(row))
    book_rel = "noopener noreferrer sponsored" if row.get("website") else "noopener noreferrer"
    best_for_html = ""
    family_score_html = ""

    photo = str(row.get("photo_url_override") or row.get("photo_url_cached") or "").strip()
    hero_img = ""
    if photo.startswith(("http", "/images/")):
        hero_img = (
            f'\n            <img class="card-hero-photo" src="{esc(photo)}" '
            f'alt="{esc(display_name(str(row.get("name") or "Holiday park")))}">'
        )
    else:
        hero_img = '\n            <div class="card-hero-photo" role="presentation"></div>'

    if show_honourable_extras:
        summary_html = ""
    else:
        summary_html = summary_html_paragraphs(row.get("summary"))

    rt, rc = google_rating_plain(row)
    meta_star = rt or "—"

    badges_html = format_google_amenity_badges(row)

    bb = comparison_beach_cell_text(row).strip()
    bs = comparison_supermarket_cell_text(row).strip()
    db = bb or "—"
    ds = bs or "—"
    distances = ""

    extra_rows = ""
    if show_honourable_extras:
        best_for_txt = str(row.get("best_for") or "—").strip() or "—"
        extra_rows = f'''
              <div class="detail-distances">
                <span><strong>Google Rating:</strong> {esc(meta_star)} {esc(str(rc or "reviews —"))}</span>
                <span><strong>Best for:</strong> {esc(best_for_txt)}</span>
                <span><strong>Kids play:</strong> {esc(str(row.get("kids_play") or "—"))}</span>
                <span><strong>Water fun:</strong> {esc(str(row.get("water_fun") or "—"))}</span>
                <span><strong>Nearest beach:</strong> {esc(db)}</span>
                <span><strong>Nearest supermarket:</strong> {esc(ds)}</span>
              </div>'''

    amen_block = ""
    if badges_html and show_honourable_extras:
        amen_block = f'\n              <div class="amenities">\n                {badges_html}\n              </div>'

    summary_block = (
        f'\n              <div class="card-summary-wrap">{summary_html}</div>'
        if summary_html
        else '\n              <div class="card-summary-wrap"></div>'
    )
    chips = []
    wf = str(row.get("water_fun") or "").strip()
    kp = str(row.get("kids_play") or "").strip()
    if wf:
        for chip in wf.split(",")[:2]:
            chip = chip.strip()
            if chip:
                chips.append(chip)
    if kp:
        for chip in kp.split(",")[:2]:
            chip = chip.strip()
            if chip:
                chips.append(chip)
    chips_html = ""
    if chips and not show_honourable_extras:
        chip_items = "".join(
            f'<span style="background:#f7f7f7;color:#444;font-size:12px;font-weight:500;padding:3px 10px;border-radius:100px;border:1px solid #ddd;white-space:nowrap;">{esc((c[0].upper()+c[1:]) if c else c)}</span>'
            for c in chips[:4]
        )
        chips_html = f'<div style="display:flex;flex-wrap:wrap;gap:5px;margin-bottom:0.75rem;">{chip_items}</div>'
    detail_meta_block = ""
    top3_class = " top3-fixed" if top3_fixed else ""
    return f"""          <article class="detail-card{top3_class}">{hero_img}
            <div class="detail-card-body">{family_score_html}
              <h3 class="park-name">{name}</h3>{summary_block}{chips_html}{best_for_html}{detail_meta_block}{amen_block}{distances}{extra_rows}
              <a class="book-btn" style="background:#0072CE;color:#fff;border:none;display:block;width:100%;text-align:center;border-radius:8px;padding:12px;font-size:13px;font-weight:700;text-decoration:none;transition:background 0.15s;" href="{href}" target="_blank" rel="{book_rel}">Book Now</a>
            </div>
          </article>
"""


def build_all_parks_slider_html(
    top3: list[dict[str, Any]], honourables: list[dict[str, Any]], *, location: str
) -> str:
    all_parks = list(top3) + list(honourables)
    if not all_parks:
        return ""

    medal_emoji = {0: "🥇", 1: "🥈", 2: "🥉"}
    medal_bg = {
        0: "background:#F5C842;color:#6b4c00;",
        1: "background:#C8D4D8;color:#3a4a50;",
        2: "background:#CD7F32;color:#fff;",
    }

    cards = []
    for idx, r in enumerate(all_parks):
        name = display_name(str(r.get("name") or ""))
        photo = str(r.get("photo_url_override") or r.get("photo_url_cached") or "").strip()
        score = r.get("family_score")
        score_text = ""
        try:
            score_text = f"{float(score):.0f}/100"
        except (TypeError, ValueError):
            pass

        photo_html = (
            f'<img src="{esc(photo)}" alt="{esc(name)}" style="width:100%;height:180px;object-fit:cover;display:block;border-radius:12px 12px 0 0;">'
            if photo.startswith(("http", "/images/"))
            else '<div style="width:100%;height:180px;background:#f7f7f7;border-radius:12px 12px 0 0;"></div>'
        )

        _logo = get_brand_logo(name, str(r.get("website") or ""))
        _logo_html = (
            f'<div style="position:absolute;bottom:8px;left:8px;background:rgba(255,255,255,0.95);'
            f'border-radius:6px;padding:3px 8px;box-shadow:0 1px 4px rgba(0,0,0,0.18);">'
            f'<img src="{esc(_logo)}" style="height:18px;width:auto;display:block;object-fit:contain;"></div>'
            if _logo else ""
        )

        if idx in medal_emoji:
            medal_html = f'<span style="position:absolute;top:10px;left:10px;display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:50%;font-weight:900;font-size:0.9rem;{medal_bg[idx]}box-shadow:0 2px 6px rgba(0,0,0,0.2);">{medal_emoji[idx]}</span>'
        else:
            medal_html = ""

        score_badge = (
            f'<span style="position:absolute;top:10px;right:10px;background:white;color:#222;font-size:12px;font-weight:700;padding:4px 10px;border-radius:100px;box-shadow:0 1px 4px rgba(0,0,0,0.15);">{esc(score_text)}</span>'
            if score_text
            else ""
        )

        best_for = str(r.get("best_for") or "").strip()
        wf = str(r.get("water_fun") or "").strip()
        kp = str(r.get("kids_play") or "").strip()
        chips = []
        tsc = r.get('top_scoring_criteria')
        if isinstance(tsc, list) and tsc:
            chips = [str(c).strip() for c in tsc if str(c).strip()][:4]
        else:
            for item in (wf + "," + kp).split(","):
                item = item.strip()
                if not item:
                    continue
                words = item.split()
                short = " ".join(words[:3])
                if short and len(chips) < 4:
                    chips.append(short)
        chips_html = "".join(
            f'<span style="background:#f7f7f7;color:#444;font-size:12px;font-weight:500;padding:3px 10px;border-radius:100px;border:1px solid #ddd;white-space:nowrap;">{esc((c[0].upper()+c[1:]) if c else c)}</span>'
            for c in chips[:4]
        )

        beach = comparison_beach_cell_text(r).strip()
        rating = r.get("rating") or r.get("google_rating") or r.get("googleRating")
        reviews = r.get("reviews") or r.get("review_count") or r.get("reviewCount")
        rating_text = ""
        reviews_text = ""
        try:
            if rating:
                rating_text = f"{float(rating):.1f}★"
        except (TypeError, ValueError):
            pass
        try:
            if reviews:
                reviews_text = f"{int(reviews):,}"
        except (TypeError, ValueError):
            pass

        powered = _parse_price(
            r.get("powered_weekday") or (r.get("prices") or {}).get("powered_weekday")
        )
        price_display = (
            f"💰 {powered}"
            if powered and powered not in {"—", "-"}
            else f'💰 <a href="{r.get("website", "#")}" target="_blank">See website</a>'
        )
        href = esc(book_href(r))
        book_rel = "noopener noreferrer sponsored" if r.get("website") else "noopener noreferrer"

        price_display_clean = powered if powered and powered not in {"—", "-"} else "See website"

        # Beach location string
        _bn = r.get("beach_name") or ""
        _bkm = r.get("beach_km")
        try: _bkm_str = f"{float(_bkm):.1f} km"
        except: _bkm_str = ""
        beach_str = f"{_bn}, {_bkm_str}".strip(", ") if (_bn or _bkm_str) else ""

        # Sort data attributes
        _score_num = 0
        try: _score_num = float(r.get("family_score") or r.get("total_score") or 0)
        except: pass
        _beach_km = r.get("beach_km") or 9999
        try: _beach_km = float(_beach_km)
        except: _beach_km = 9999
        _super_km = r.get("supermarket_km") or 9999
        try: _super_km = float(_super_km)
        except: _super_km = 9999
        _price_num = 9999
        _pw = _parse_price(
            r.get("powered_weekday") or (r.get("prices") or {}).get("powered_weekday")
        )
        import re as _re
        _price_nums = _re.findall(r"\d+", _pw)
        if _price_nums: _price_num = int(_price_nums[0])
        # Water/playground scoring — count keywords
        _water_text = str(r.get("water_fun") or "") + " " + str(r.get("top_scoring_criteria") or "") + " " + str(r.get("kids_play") or "") + " " + str(r.get("executive_summary") or "")
        _water_score = sum(1 for w in ["pool", "waterpark", "waterslide", "splash", "creek", "swim", "water", "slide", "heated pool", "aqua"] if w in _water_text.lower())
        _play_text = str(r.get("kids_play") or "") + " " + str(r.get("top_scoring_criteria") or "")
        _play_score = sum(1 for w in ["playground","pillow","jumping","flying fox","pump track","activities","games"] if w in _play_text.lower())

        card = f'''<div class="park-card" style="scroll-snap-align:start;"
  data-score="{_score_num}"
  data-beach="{_beach_km}"
  data-super="{_super_km}"
  data-price="{_price_num}"
  data-water="{_water_score}"
  data-play="{_play_score}">
  <div style="position:relative;flex-shrink:0;">
    {photo_html}
    {score_badge}
    {_logo_html}
  </div>
  <div style="padding:12px 14px 14px;flex:1;display:flex;flex-direction:column;gap:7px;">
    <div>
      <h3 style="font-size:14px;font-weight:600;color:#222;margin:0 0 2px;line-height:1.3;">{esc(name)}</h3>
      <p style="font-size:12px;color:#717171;margin:0;">{esc(beach_str)}</p>
    </div>
    <p style="font-size:13px;line-height:1.5;color:#444;margin:0;flex:1;">{esc(best_for[:90]+"..." if len(best_for)>90 else best_for)}</p>
    <div style="display:flex;flex-wrap:wrap;gap:4px;">{chips_html}</div>
    <div style="display:flex;align-items:center;justify-content:space-between;border-top:0.5px solid #eee;padding-top:10px;margin-top:2px;">
      <span style="font-size:13px;color:#222;">{price_display_clean}</span>
      <a href="{esc(href)}" target="_blank" rel="{esc(book_rel)}" style="font-size:13px;font-weight:600;color:#0072CE;text-decoration:none;">View park →</a>
    </div>
  </div>
</div>'''''''''

        cards.append(card)

    cards_joined = "\n".join(cards)
    display_location = re.sub(r"\b(Queensland|New South Wales|Victoria|South Australia|Western Australia|Tasmania|Northern Territory|Australian Capital Territory|QLD|NSW|VIC|SA|WA|TAS|NT|ACT)\b", "", location).strip().strip(",").strip()

    # State info for sticky bottom bar
    _state_upper = location.split()[-1].strip().upper()
    _state_name_map = {
        "QLD": "Queensland", "NSW": "New South Wales", "VIC": "Victoria",
        "WA": "Western Australia", "SA": "South Australia", "TAS": "Tasmania",
        "NT": "Northern Territory", "ACT": "ACT",
    }
    state_label = _state_name_map.get(_state_upper, _state_upper)
    state_anchor = f"#state-{_state_upper.lower()}"
    park_count = len(top3) + len(honourables)
    return f'''
    <section style="padding:32px 0 0;border-bottom:1px solid #eee;" aria-labelledby="all-parks-heading">
      <div style="max-width:1120px;margin:0 auto;padding:0 24px 16px;">
        <h2 id="all-parks-heading" style="font-family:'Fraunces',serif;font-weight:700;font-size:clamp(1.2rem,2.5vw,1.5rem);color:#222;letter-spacing:-0.01em;margin-bottom:4px;">{esc(display_location)} holiday parks</h2>
        <p style="font-size:14px;color:#717171;">Swipe to explore · tap a filter to reorder</p>
      </div>
      <div id="park-sort-bar" style="display:flex;gap:8px;overflow-x:auto;padding:0 24px 14px;scrollbar-width:none;">
        <button onclick="sortParks('score')" class="sort-btn active" data-sort="score">Best overall</button>
        <button onclick="sortParks('beach')" class="sort-btn" data-sort="beach">Closest to beach</button>
        <button onclick="sortParks('water')" class="sort-btn" data-sort="water">Best waterplay</button>
        <button onclick="sortParks('play')" class="sort-btn" data-sort="play">Best playground</button>
        <button onclick="sortParks('price')" class="sort-btn" data-sort="price">Best value</button>
        <button onclick="sortParks('super')" class="sort-btn" data-sort="super">Closest to shops</button>
      </div>
      <div id="parks-scroll" style="display:flex;gap:16px;overflow-x:auto;padding:4px 24px 28px;-webkit-overflow-scrolling:touch;scrollbar-width:none;scroll-snap-type:x mandatory;">
        {cards_joined}
      </div>
    </section>
    <style>
      .sort-btn {{font-size:13px;font-weight:500;padding:8px 16px;border-radius:100px;border:1px solid #ddd;background:#fff;color:#222;white-space:nowrap;cursor:pointer;transition:all 0.15s;flex-shrink:0;}}
      .sort-btn:hover {{border-color:#222;}}
      .sort-btn.active {{background:#222;color:#fff;border-color:#222;}}
      #park-sort-bar::-webkit-scrollbar {{display:none;}}
      #parks-scroll::-webkit-scrollbar {{display:none;}}
    </style>
    <script>
    function sortParks(key) {{
      const scroll = document.getElementById('parks-scroll');
      const bar = document.getElementById('park-sort-bar');
      if (!scroll) return;
      const cards = Array.from(scroll.children);
      const asc = key === 'beach' || key === 'price' || key === 'super';
      cards.sort((a, b) => {{
        const va = parseFloat(a.dataset[key] ?? 9999);
        const vb = parseFloat(b.dataset[key] ?? 9999);
        return asc ? va - vb : vb - va;
      }});
      cards.forEach(c => scroll.appendChild(c));
      scroll.scrollLeft = 0;
      bar.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
      bar.querySelector(`[data-sort="${{key}}"]`)?.classList.add('active');
    }}
    </script>
'''


def build_compare_table_html(
    top3: list[dict[str, Any]],
    honourables: list[dict[str, Any]] | None = None,
    *,
    project_dir: Path,
    short_label_fn: Any = None,
    label_location_name: str = "",
    all_park_names: list[str] | None = None,
    manual_prices: dict[str, Any] | None = None,
) -> str:
    honourables = honourables or []
    all_parks = list(top3) + list(honourables)
    if not all_parks:
        return ""

    def _compare_sort_meta(r: dict[str, Any]) -> dict[str, float]:
        def _sf(v: Any, default: float) -> float:
            try:
                if v is None:
                    return default
                return float(v)
            except (TypeError, ValueError):
                return default

        score = _sf(r.get("family_score") or r.get("total_score"), 0)
        rating = _sf(r.get("rating") or r.get("google_rating"), 0)
        beach = _sf(r.get("beach_km"), 9999)
        super_km = _sf(r.get("supermarket_km"), 9999)
        price = powered_sort_price_num(
            r, project_dir=project_dir, manual_prices=manual_prices
        )
        return {
            "family_score": score,
            "google_rating": rating,
            "beach_km": beach,
            "supermarket_km": super_km,
            "price": price,
        }

    def _park_col_attrs_th(i: int, r: dict[str, Any]) -> str:
        m = _compare_sort_meta(r)
        return (
            f'class="park-head park-col"'
            f' data-family-score="{m["family_score"]}"'
            f' data-google-rating="{m["google_rating"]}"'
            f' data-beach-km="{m["beach_km"]}"'
            f' data-supermarket-km="{m["supermarket_km"]}"'
            f' data-price="{m["price"]}"'
        )

    def _park_col_attrs_td(i: int, r: dict[str, Any]) -> str:
        m = _compare_sort_meta(r)
        return (
            f'class="park-col"'
            f' data-family-score="{m["family_score"]}"'
            f' data-google-rating="{m["google_rating"]}"'
            f' data-beach-km="{m["beach_km"]}"'
            f' data-supermarket-km="{m["supermarket_km"]}"'
            f' data-price="{m["price"]}"'
        )

    def _tag_park_td(cell: str, i: int, r: dict[str, Any]) -> str:
        attrs = _park_col_attrs_td(i, r)
        return cell.replace("<td", f"<td {attrs}", 1)

    header_cells = []
    for idx, r in enumerate(all_parks):
        if short_label_fn:
            _short = short_label_fn(
                str(r.get("park_name") or r.get("name") or ""),
                location_name=label_location_name,
                all_names=all_park_names,
            )
        else:
            _short = display_name(str(r.get("park_name") or r.get("name") or ""))
        _park_name_raw = str(r.get("park_name") or r.get("name") or "")
        _logo = get_brand_logo(_park_name_raw, str(r.get("website") or ""))
        _photo = r.get("photo_url_override") or r.get("photo_url_cached") or ""
        if _logo:
            _img = (
                f'<img src="{esc(_logo)}" style="height:30px;width:auto;max-width:80px;'
                f'object-fit:contain;display:block;margin-bottom:6px;">'
            )
        elif str(_photo).startswith(("http", "/images/")):
            _img = (
                f'<img src="{esc(_photo)}" style="width:48px;height:48px;object-fit:cover;'
                f'border-radius:8px;display:block;margin-bottom:6px;">'
            )
        else:
            _img = ""
        _park_head_html = (
            f'{_img}<span style="font-size:13px;font-weight:700;color:#222;line-height:1.3;">{esc(_short)}</span>'
        )
        header_cells.append(
            f'<th scope="col" {_park_col_attrs_th(idx, r)}>{_park_head_html}</th>'
        )
    headers_joined = "".join(header_cells)

    win_rating = compare_rating_winner_ix(all_parks)
    win_beach = compare_min_km_winners_ix(all_parks, "beach_km")
    win_super = compare_min_km_winners_ix(all_parks, "supermarket_km")

    def td_score(r: dict[str, Any]) -> str:
        score = r.get("family_score") or r.get("total_score")
        try:
            score_int = int(float(score))
            txt = str(score_int)
        except (TypeError, ValueError):
            score_int = 0
            txt = "—"
        color = "#0072CE" if score_int >= 85 else "#222"
        return (
            f'<td><div style="width:48px;height:48px;border-radius:50%;border:2px solid {color};'
            f'display:flex;align-items:center;justify-content:center;flex-direction:column;margin:0 auto;">'
            f'<span style="font-size:14px;font-weight:800;color:{color};line-height:1;">{txt}</span>'
            f'<span style="font-size:8px;font-weight:600;color:#999;text-transform:uppercase;letter-spacing:0.04em;">score</span>'
            f'</div></td>'
        )
    
    def td_price(r: dict[str, Any]) -> str:
        powered_price = _parse_price(
            r.get("powered_weekday") or (r.get("prices") or {}).get("powered_weekday")
        )
        if not powered_price or powered_price in {"—", "-"}:
            master = load_park_master(project_dir, r.get("park_name") or r.get("name") or "")
            powered_price = _parse_price(master.get("prices", {}).get("powered_weekday")) or "—"
        return f'<td><span class="cell-strong">{esc(powered_price)}</span></td>'

    def td_deals(r: dict[str, Any]) -> str:
        deals_text = r.get("deals") or ""
        if not deals_text or deals_text in {"—", "-"}:
            master = load_park_master(project_dir, r.get("park_name") or r.get("name") or "")
            deals_text = master.get("deals") or "—"
        return f'<td><span class="muted">{esc(str(deals_text))}</span></td>'

    def td_rating(i: int, r: dict[str, Any]) -> str:
        rating = r.get("rating") or r.get("google_rating") or r.get("googleRating")
        reviews = r.get("reviews") or r.get("review_count") or r.get("reviewCount")
        rt = None
        rc = None
        if rating is not None:
            try:
                rt = f"{float(rating):.1f}★"
            except (TypeError, ValueError):
                pass
        if reviews is not None:
            try:
                rc = f"{int(reviews):,}"
            except (TypeError, ValueError):
                pass
        if not rt:
            return "<td>—</td>"
        extra = f' · <span style="color:#717171;">{esc(rc)}</span>' if rc else ""
        return f'<td><span style="font-weight:600;color:#222;">{esc(rt)}</span>{extra}</td>'
    
    def td_text(r: dict[str, Any], key: str) -> str:
        val = str(r.get(key) or "").strip()
        return f'<td>{esc(val or "—")}</td>'

    def td_beach(i: int, r: dict[str, Any]) -> str:
        cx = comparison_beach_cell_text(r).strip()
        if not cx:
            return "<td>—</td>"
        return f"<td>{esc(cx)}</td>"

    def td_super(i: int, r: dict[str, Any]) -> str:
        cx = comparison_supermarket_cell_text(r).strip()
        if not cx:
            return "<td>—</td>"
        return f"<td>{esc(cx)}</td>"

    def td_book(r: dict[str, Any]) -> str:
        href = esc(book_href(r))
        rel = "noopener noreferrer sponsored" if r.get("website") else "noopener noreferrer"
        return f'<td><a class="book-btn" href="{href}" target="_blank" rel="{rel}">View park →</a></td>'

    def td_pet(r: dict[str, Any]) -> str:
        pet = str(r.get("pet_detail") or r.get("pet_friendly") or "").strip().lower()
        if any(x in pet for x in ["not pet", "no dogs", "no pets", "pet free", "pets not"]):
            return '<td><span style="color:#c0392b;">✗ No</span></td>'
        elif any(x in pet for x in ["dog", "pet", "friendly", "welcome", "allowed"]):
            return '<td style="color:#222;font-size:13px;">Yes</td>'
        return '<td><span style="color:#aaa;">—</span></td>'

    def td_wifi(r: dict[str, Any]) -> str:
        wifi = str(r.get("wifi_available") or r.get("wifi") or "").strip().lower()
        if wifi in ("yes", "true", "1"):
            return '<td style="color:#222;font-size:13px;">Yes</td>'
        elif wifi in ("no", "false", "0"):
            return '<td><span style="color:#c0392b;">✗ No</span></td>'
        return '<td><span style="color:#aaa;">—</span></td>'

    def row(label: str, cells_fn: Any) -> str:
        cells = [_tag_park_td(cells_fn(i, r), i, r) for i, r in enumerate(all_parks)]
        return f'<tr><th scope="row">{label}</th>{"".join(cells)}</tr>'

    def row_single(label: str, cells_fn: Any) -> str:
        cells = [_tag_park_td(cells_fn(r), i, r) for i, r in enumerate(all_parks)]
        return f'<tr><th scope="row">{label}</th>{"".join(cells)}</tr>'

    body_rows = [
        row_single("Family Score", td_score),
        row_single("Powered Site", td_price),
        row_single("Deals", td_deals),
        row("Google Rating", td_rating),
        row("Kids", lambda i, r: td_text(r, "kids_play")),
        row("Water", lambda i, r: td_text(r, "water_fun")),
        row("Beach", td_beach),
        row("Supermarket", td_super),
    ]
    body_rows.append(
        '<tr><th scope="row">Pets</th>'
        + "".join(_tag_park_td(td_pet(r), i, r) for i, r in enumerate(all_parks))
        + "</tr>"
    )
    body_rows.append(
        '<tr><th scope="row">Wi-Fi</th>'
        + "".join(_tag_park_td(td_wifi(r), i, r) for i, r in enumerate(all_parks))
        + "</tr>"
    )
    body_rows.append(row("Book", lambda i, r: td_book(r)))

    tbody = "\n".join(body_rows)

    return f"""
      <section class="compare-section" aria-label="Compare all parks" style="background:#fff;padding:0 0 1rem;">
        <h2>Compare all {len(all_parks)} parks</h2>
        <div class="compare-sort-wrap">
          <p class="compare-sort-label">Sort comparison by</p>
          <div class="compare-sort">
            <button type="button" class="compare-sort-btn active" data-sort="family_score" onclick="sortCompareTable(this)">Family Score</button>
            <button type="button" class="compare-sort-btn" data-sort="price" onclick="sortCompareTable(this)">Best Value</button>
            <button type="button" class="compare-sort-btn" data-sort="google_rating" onclick="sortCompareTable(this)">Google Rating</button>
            <button type="button" class="compare-sort-btn" data-sort="beach_km" onclick="sortCompareTable(this)">Closest to Beach</button>
            <button type="button" class="compare-sort-btn" data-sort="supermarket_km" onclick="sortCompareTable(this)">Closest to Supermarket</button>
          </div>
        </div>
        <div class="compare-scroll">
          <table class="compare-table" id="compare-table">
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
    hero_intro: str,
    intro_paragraph: str,
    destination_summary: str = "",
    if_we_were_booking: str = "",
    maps_api_key: str,
    faq_entries: list[dict[str, str]],
    park_count: int,
    project_dir: Path,
    loc_dir: Path,
    loc_config: dict[str, Any] | None = None,
    manual_prices: dict[str, Any] | None = None,
    why_families: list[str] | None = None,
    activities: list[dict] | None = None,
) -> str:
    loc_config = loc_config if isinstance(loc_config, dict) else {}
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

    for row in top3:
        best_for = str(row.get("best_for") or "").strip()
        if best_for.lower().startswith("best for"):
            card_desc = best_for
        else:
            card_desc = normalize_text_paragraphs(row.get("rationale_top3") or "")
        row["summary"] = card_desc
    if manual_prices is not None:
        apply_manual_prices(rows, manual_prices)
        apply_manual_prices(honourables, manual_prices)
    location_name = str(loc_config.get("hero_headline") or location).strip() or location
    year = date.today().year
    page_title = f"Best Family Holiday Parks {location_name} {year} | Reviewed & Ranked"
    parks = sorted_rows
    top_park = str(parks[0].get("park_name") or parks[0].get("name") or "") if parks else ""
    top_score = parks[0].get("total_score") if parks else ""
    if parks and top_score in (None, ""):
        top_score = parks[0].get("rank_score", "")
    total_scored_parks = park_count
    park_count = len(parks)
    top3_count = min(3, park_count)
    meta_desc = (
        f"We scored {park_count} holiday parks in {location_name}. {top_park} tops our list "
        f"with {top_score}/100. Find the best family holiday park for your next trip."
    )
    today = date.today()
    last_modified = today.isoformat()
    last_reviewed_label = today.strftime("%B %Y")

    hero_image = str(loc_config.get("hero_image") or "").strip()
    hero_stats_raw = loc_config.get("hero_stats") or []
    hero_stats: list[Any] = hero_stats_raw if isinstance(hero_stats_raw, list) else []

    if hero_image:
        header_style = (
            f"background:url({hero_image}) center/cover no-repeat !important;"
            f"padding:5rem 1.35rem 4rem;min-height:380px;"
        )
        overlay_html = "<div style='position:absolute;inset:0;background:rgba(20,40,25,0.55);z-index:0;'></div>"
        inner_style = "position:relative;z-index:1;text-align:center;width:100%;"
    else:
        header_style = "background:#0072CE;"
        overlay_html = ""
        inner_style = "text-align:center;width:100%;"

    if hero_stats:
        stats_items = "".join(
            f'<div style="text-align:center;"><span style="display:block;font-size:2rem;font-weight:700;color:#fff;">{esc(str(park_count if str(s.get("label") or "") == "Parks reviewed" else s.get("num", "")))}</span><span style="font-size:0.72rem;color:rgba(255,255,255,0.65);text-transform:uppercase;letter-spacing:0.1em;">{esc(str(s.get("label", "")))}</span></div>'
            for s in hero_stats
            if isinstance(s, dict)
        )
        stats_html = f'<div style="display:flex;justify-content:center;gap:2.5rem;margin-top:2rem;flex-wrap:wrap;">{stats_items}</div>'
    else:
        stats_html = ""

    page_h1 = str(loc_config.get("hero_headline") or location).strip() or location

    _state_upper = location.split()[-1].strip().upper()
    _state_name_map = {
        "QLD": "Queensland", "NSW": "New South Wales", "VIC": "Victoria",
        "WA": "Western Australia", "SA": "South Australia", "TAS": "Tasmania",
        "NT": "Northern Territory", "ACT": "ACT",
    }
    state_label = _state_name_map.get(_state_upper, _state_upper)

    bare_location = re.sub(
        r"\s+(QLD|NSW|VIC|SA|WA|TAS|NT|ACT)$", "", location, flags=re.IGNORECASE
    ).strip()

    _on_the_locations = [
        "Gold Coast",
        "Sunshine Coast",
        "Great Ocean Road",
        "Whitsundays",
        "Mornington Peninsula",
        "Yorke Peninsula",
        "Fleurieu Peninsula",
        "Eyre Peninsula",
    ]
    _heading_prep = (
        "on the"
        if any(loc.lower() in bare_location.lower() for loc in _on_the_locations)
        else "in"
    )

    local_knowledge = ""
    lk = intro_paragraph.strip()
    if lk:
        local_knowledge = f"""
<section class="content-section local-knowledge">
  <h2>Local Knowledge</h2>
  <p>{esc(lk)}</p>
</section>
"""

    nearby = NEARBY_LOCATIONS.get(location, [])
    nearby_html = ""
    if nearby:
        nearby_html = '<section class="content-section nearby-locations"><h2>Also worth exploring</h2><ul class="nearby-list">'
        for name, url in nearby:
            nearby_html += f'<li><a href="{esc(url)}">Family holiday parks in {esc(name)}</a></li>'
        nearby_html += "</ul></section>"

    bits: list[str] = []
    for item in faq_entries:
        if not isinstance(item, dict):
            continue
        q = esc(str(item.get("question") or "").strip())
        a = esc(str(item.get("answer") or "").strip())
        if not q:
            continue
        bits.append(
            f"""      <details>
        <summary>{q}</summary>
        <div class="faq-answer">{a or "—"}</div>
      </details>"""
        )
    faq_block = ""
    if bits:
        faq_inner = "\n".join(bits)
        faq_block = f"""
<section class="content-section faq-section">
  <h2>Frequently Asked Questions</h2>
{faq_inner}
</section>
"""

    faqs = [
        item
        for item in faq_entries
        if isinstance(item, dict) and str(item.get("question") or "").strip()
    ]
    faq_schema_html = ""
    if faqs:
        faq_schema = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": faq["question"],
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": faq["answer"],
                    },
                }
                for faq in faqs
            ],
        }
        faq_schema_html = (
            f'<script type="application/ld+json">{json.dumps(faq_schema, ensure_ascii=False)}</script>'
        )

    output_slug = output_slug_for_location(project_dir, location, loc_dir)
    local_schema = {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": page_title,
        "description": meta_desc,
        "url": f"https://familyholidayparks.com.au/{output_slug}",
        "dateModified": str(date.today()),
    }
    local_schema_html = (
        f'<script type="application/ld+json">{json.dumps(local_schema, ensure_ascii=False)}</script>'
    )

    seo_block = f"""{META_PIXEL}
{GA4_TAG}
  <meta name="description" content="{esc(meta_desc)}">
  <meta name="last-modified" content="{esc(last_modified)}">
  {faq_schema_html}
  {local_schema_html}"""

    map_section = ""

    why_families_html = ""
    _why_lines: list[str] = why_families or []
    if not _why_lines:
        why_families_file = loc_dir / "why-families.txt"
        if why_families_file.exists():
            _why_lines = [
                l.strip()
                for l in why_families_file.read_text(encoding="utf-8").splitlines()
                if l.strip()
            ]
    if _why_lines:
        items_html = "".join(f"<li>{esc(line.lstrip('- '))}</li>" for line in _why_lines)
        why_families_html = f"""
<section class="content-section why-families-section">
  <h2>Why Families Love {esc(bare_location)}</h2>
  <ul class="why-list">
    {items_html}
  </ul>
</section>
"""

    lead_magnet_html = """
<section class="content-section lead-magnet">
  <h2>Find Better Family Holidays</h2>
  <p>We compare hundreds of Australian holiday parks so you can find the right park for your family in minutes, not hours.</p>
  <form class="email-row" action="#" method="post">
    <input type="email" name="email" placeholder="Your email" required>
    <button type="submit">Join Free</button>
  </form>
</section>
"""

    display_location = re.sub(
        r"\b(Queensland|New South Wales|Victoria|South Australia|Western Australia|Tasmania|Northern Territory|Australian Capital Territory|QLD|NSW|VIC|SA|WA|TAS|NT|ACT)\b",
        "",
        location,
    ).strip().strip(",").strip()

    all_parks = list(top3) + list(honourables)
    all_parks_count = len(all_parks)
    total_google_reviews = sum(
        int(r.get("review_count") or 0)
        for r in all_parks
    )
    # Add estimated family reviews submitted via Ice Cream Campaign
    total_reviews_str = f"{total_google_reviews:,}"
    top3_vertical_parts = []
    for i, r in enumerate(all_parks):
        _name = r.get("park_name") or r.get("name") or ""
        _photo = r.get("photo_url_override") or r.get("photo_url_cached") or ""
        _score = r.get("family_score") or r.get("total_score") or 0
        try:
            _score_int = int(float(_score))
        except Exception:
            _score_int = 0
        _best_for = (r.get("summary") or r.get("best_for") or r.get("rationale_top3") or "")[:100]
        _price = ""
        _pw = r.get("powered_weekday") or (r.get("prices") or {}).get("powered_weekday") or ""
        import re as _rr
        _pn = _rr.findall(r"\d+", str(_pw))
        if _pn:
            _price = f"${_pn[0]}/night"
        _href = r.get("website") or "#"
        _loc_slug = re.sub(r"[\s-]+", "-", re.sub(r"[^a-z0-9\s-]", "", bare_location.lower())).strip("-")
        _park_slug = re.sub(r"[\s-]+", "-", re.sub(r"[^a-z0-9\s-]", "", _name.lower())).strip("-")
        _park_img_dir = project_dir / "public" / "images" / "parks" / _loc_slug / _park_slug
        _gallery_photos: list[str] = []
        if _park_img_dir.is_dir():
            for _n in range(1, 21):
                if (_park_img_dir / f"{_n}.jpg").exists():
                    _gallery_photos.append(f"/images/parks/{_loc_slug}/{_park_slug}/{_n}.jpg")
                else:
                    break
        if not _gallery_photos and str(_photo).startswith(("http", "/images/")):
            _gallery_photos.append(str(_photo))
        _pgal_id = f"pgal-{i}"
        if len(_gallery_photos) > 1:
            _gimgs_parts = []
            for _gj, _gp in enumerate(_gallery_photos):
                _lazy_attr = ' loading="lazy"' if _gj > 0 else ''
                _gimgs_parts.append(f'<img src="{esc(_gp)}" alt="{esc(_name)}" class="pgal-img"{_lazy_attr}>')
            _gimgs = "".join(_gimgs_parts)
            _gdots = "".join(
                f'<span class="pgal-dot{" active" if _gj == 0 else ""}"></span>'
                for _gj in range(len(_gallery_photos))
            )
            _img = (
                f'<div class="pgal" id="{_pgal_id}" data-cur="0" data-n="{len(_gallery_photos)}">'
                f'<div class="pgal-track">{_gimgs}</div>'
                f'<button class="pgal-btn pgal-prev" onclick="pgalMove(event,\'{_pgal_id}\',-1)" aria-label="Previous photo">&#8249;</button>'
                f'<button class="pgal-btn pgal-next" onclick="pgalMove(event,\'{_pgal_id}\',1)" aria-label="Next photo">&#8250;</button>'
                f'<div class="pgal-dots">{_gdots}</div>'
                f'</div>'
            )
        elif _gallery_photos:
            _img = f'<img src="{esc(_gallery_photos[0])}" alt="{esc(_name)}">'
        else:
            _img = '<div class="t3-img-ph"></div>'
        _rank_label = f"#{i+1} Ranked"
        _tags = (r.get("top_scoring_criteria") or [])[:3]
        _tags_html = "".join(
            f'<span class="t3-tag">{esc((t[0].upper()+t[1:]) if t else t)}</span>'
            for t in _tags
        )
        _rating = r.get("google_rating") or ""
        _reviews = r.get("review_count") or ""
        try:
            _rating_str = f"⭐ {float(_rating):.1f} · {int(_reviews):,} reviews"
        except Exception:
            _rating_str = ""
        _lat = r.get("lat") or ""
        _lng = r.get("lng") or ""

        def _sf_card(v):
            try:
                return float(v)
            except Exception:
                return None

        _beach_km = _sf_card(r.get("beach_km")) or 9999
        _super_km = _sf_card(r.get("supermarket_km")) or 9999
        _price_num = int(
            powered_sort_price_num(r, project_dir=project_dir, manual_prices=manual_prices)
        )
        _water_text = (
            str(r.get("water_fun") or "")
            + " "
            + str(r.get("top_scoring_criteria") or "")
            + " "
            + str(r.get("kids_play") or "")
            + " "
            + str(r.get("executive_summary") or "")
        )
        _water_score = sum(
            1
            for w in [
                "pool",
                "waterpark",
                "waterslide",
                "splash",
                "creek",
                "swim",
                "water",
                "slide",
                "heated pool",
                "aqua",
            ]
            if w in _water_text.lower()
        )
        _play_text = str(r.get("kids_play") or "") + " " + str(
            r.get("top_scoring_criteria") or ""
        )
        _play_score = sum(
            1
            for w in [
                "playground",
                "pillow",
                "jumping",
                "pump track",
                "activities",
                "games",
            ]
            if w in _play_text.lower()
        )
        _brand_logo = get_brand_logo(_name, str(r.get("website") or ""))
        _brand_logo_html = (
            f'<img src="{esc(_brand_logo)}" alt="" style="height:32px;width:auto;display:block;object-fit:contain;margin:2px 0 4px;">'
            if _brand_logo else ""
        )
        top3_vertical_parts.append(
            f'''<div class="t3-card" data-park-idx="{i}" data-lat="{_lat}" data-lng="{_lng}" data-score="{_score_int}" data-beach="{_beach_km}" data-super="{_super_km}" data-price_num="{_price_num}" data-water="{_water_score}" data-play="{_play_score}">
      <div class="t3-img">{_img}<div class="t3-score">{_score_int}/100</div></div>
      <div class="t3-body">
        <div class="t3-rank">{esc(_rank_label)}</div>
        {_brand_logo_html}<div class="t3-name">{esc(_name)}</div>
        <div class="t3-verdict">{esc(_best_for)}</div>
        <div class="t3-tags">{_tags_html}</div>
        <div class="t3-rating">{esc(_rating_str)}</div>
        <div class="t3-footer">
          <span class="t3-price">{esc(_price)}</span>
          <a class="t3-cta" href="{esc(_href)}" target="_blank" rel="noopener noreferrer sponsored">View park →</a>
        </div>
      </div>
    </div>''')
    top3_vertical_html = "\n".join(top3_vertical_parts)
    _first3_html = "\n".join(top3_vertical_parts[:3])
    _extra_parts = top3_vertical_parts[3:]
    _total_parks = len(top3_vertical_parts)
    if _extra_parts:
        _extra_parks_html = (
            f'<div id="extra-parks" style="display:none">\n'
            + "\n".join(_extra_parts)
            + '\n</div>\n'
            + f'<button class="see-all-btn" onclick="toggleExtraParks(this)" data-total="{_total_parks}">'
            + f'See all {_total_parks} parks</button>'
        )
    else:
        _extra_parks_html = ""
    google_maps_api_key = (maps_api_key or "").strip()
    google_maps_map_id = os.environ.get("GOOGLE_MAPS_MAP_ID", "").strip()
    print(f"[debug] google_maps_api_key = {repr(google_maps_api_key)}")
    print(f"[debug] google_maps_map_id = {repr(google_maps_map_id)}")

    import json as _json
    import re as _re_map

    def _safe_float(v):
        try:
            return float(v)
        except Exception:
            return None

    import os as _os
    import json as _json2
    import re as _re_label

    # Load optional overrides
    _map_label_overrides = {}
    _overrides_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "map_label_overrides.json")
    if _os.path.exists(_overrides_path):
        try:
            with open(_overrides_path, "r", encoding="utf-8") as _f:
                _map_label_overrides = _json2.load(_f)
        except Exception:
            pass

    def get_short_park_label(park_name, location_name="", overrides=None, all_names=None):
        park_name = str(park_name or "").strip()
        if not park_name:
            return ""
        _ov = dict(GOLD_COAST_LABEL_OVERRIDES)
        _ov.update(APOLLO_BAY_LABEL_OVERRIDES)
        _ov.update(overrides or _map_label_overrides)
        if park_name in _ov:
            return _ov[park_name]
        else:
            _brands = ["BIG4", "NRMA", "Discovery", "Ingenia", "Reflections", "Tasman", "RAC", "G'Day"]
            matched_brand = None
            for brand in _brands:
                if brand.lower() in park_name.lower():
                    matched_brand = brand
                    break
            if matched_brand:
                if all_names:
                    brand_count = sum(
                        1 for n in all_names if n and matched_brand.lower() in str(n).lower()
                    )
                    if brand_count > 1:
                        rest = _re_label.sub(matched_brand, "", park_name, flags=_re_label.IGNORECASE).strip()
                        for w in [
                            "Holiday Park", "Tourist Park", "Caravan Park", "Holiday Resort",
                            "Holiday Village", "Resort", "Village",
                        ]:
                            rest = _re_label.sub(
                                r'\b' + _re_label.escape(w) + r'\b', '', rest, flags=_re_label.IGNORECASE
                            ).strip()
                        rest = _re_label.sub(r'\s+', ' ', rest).strip().strip(',').strip()
                        first_word = rest.split()[0] if rest.split() else ""
                        label = f"{matched_brand} {first_word}".strip() if first_word else matched_brand
                    else:
                        label = matched_brand
                else:
                    label = matched_brand
            else:
                label = park_name
                for w in [
                    "Holiday Park", "Tourist Park", "Caravan Park", "Holiday Resort",
                    "Holiday Village", "Resort", "Village",
                ]:
                    label = _re_label.sub(
                        r'\b' + _re_label.escape(w) + r'\b', '', label, flags=_re_label.IGNORECASE
                    )
                label = _re_label.sub(r'\s+', ' ', label).strip().strip(',').strip()
        if not label or label.lower() == "park":
            skip = {
                "holiday", "tourist", "caravan", "resort", "village", "park",
                "family", "cabins", "camping", "the", "and", "gold", "coast",
            }
            orig_words = [w for w in park_name.split() if w.lower() not in skip]
            label = " ".join(orig_words[:2]) if orig_words else (park_name.split()[0] if park_name.split() else "")
        words = [w for w in label.split() if len(w) > 1]
        if words:
            label = " ".join(words[:2])
        if not label or label.lower() == "park":
            label = park_name.split()[0] if park_name.split() else ""
        if len(label) > 16:
            label = label[:15].rstrip() + "…"
        if label.lower() == "park":
            label = park_name.split()[0] if park_name.split() else ""
        return label

    _all_park_names = [str(r.get("park_name") or r.get("name") or "").strip() for r in all_parks]
    compare_block = build_compare_table_html(
        top3,
        honourables,
        project_dir=project_dir,
        short_label_fn=get_short_park_label,
        label_location_name=display_location,
        all_park_names=_all_park_names,
        manual_prices=manual_prices,
    )

    parks_for_map = []
    print(f"[map] total all_parks: {all_parks_count}")
    for r in all_parks:
        name = str(r.get("park_name") or r.get("name") or "").strip()
        lat, lng = get_lat_lng(r)
        included = True
        exclude_reason = ""
        if lat is None or lng is None:
            override = GOLD_COAST_COORD_OVERRIDES.get(name)
            if override:
                lat, lng = override
                exclude_reason = "used Gold Coast coord override"
            else:
                included = False
                exclude_reason = "missing lat/lng"
                print(f"MAP EXCLUDED: {name} — missing lat/lng")
        print(
            f"[map] {name}: lat={lat} lng={lng} "
            f"included={included}"
            + (f" ({exclude_reason})" if exclude_reason else "")
        )
        if not included:
            continue
        score_raw = r.get("family_score") or r.get("total_score")
        try:
            score_int = int(float(score_raw))
        except Exception:
            score_int = 0
        price_str = _parse_price(
            r.get("powered_weekday") or (r.get("prices") or {}).get("powered_weekday")
        )
        tags = (r.get("top_scoring_criteria") or [])[:3]
        # Abbreviated pin label: strip generic suffix words, keep brand or first word
        _pn_stripped = re.sub(
            r'\b(Holiday Park|Tourist Park|Caravan Park|Holiday Village|Holiday Resort|Resort)\b',
            '', name, flags=re.IGNORECASE
        )
        _pn_stripped = re.sub(r',\s*Gold Coast$', '', _pn_stripped, flags=re.IGNORECASE).strip()
        _pn_stripped = re.sub(r'\s+', ' ', _pn_stripped).strip()
        _pin_brands = ["BIG4", "NRMA", "Nobby"]
        _pin_lbl = (
            next((b for b in _pin_brands if _pn_stripped.lower().startswith(b.lower())), None)
            or (_pn_stripped.split()[0] if _pn_stripped else name.split()[0])
        )
        parks_for_map.append({
            "name": r.get("park_name") or r.get("name") or "",
            "short_name": get_short_park_label(name, location_name=display_location, all_names=_all_park_names),
            "pin_label": _pin_lbl,
            "lat": lat,
            "lng": lng,
            "score_label": f"{score_int}/100",
            "score_int": score_int,
            "photo": (
                r.get("photo_url_override")
                or r.get("photo_url_cached")
                or r.get("google_photo_url")
                or ""
            ),
            "logo": get_brand_logo(name, str(r.get("website") or "")),
            "verdict": (r.get("best_for") or "")[:100],
            "tags": tags,
            "price": price_str,
            "url": r.get("website") or "",
            "address": r.get("address") or "",
            "full_name": name,
            "maps_url": get_google_maps_url(r),
        })

    parks_json_str = _json.dumps(parks_for_map, ensure_ascii=False)
    if parks_for_map:
        map_lat = sum(float(p["lat"]) for p in parks_for_map) / len(parks_for_map)
        map_lng = sum(float(p["lng"]) for p in parks_for_map) / len(parks_for_map)
    else:
        map_lat = -27.4698
        map_lng = 153.0251

    activities_list: list[dict[str, Any]] = [
        a for a in (activities or [])
        if isinstance(a, dict) and str(a.get("name") or "").strip()
    ]
    if not activities_list:
        activities_path = loc_dir / "activities.json"
        if activities_path.exists():
            try:
                raw_activities = json.loads(activities_path.read_text(encoding="utf-8"))
                if isinstance(raw_activities, list):
                    activities_list = [
                        a for a in raw_activities
                        if isinstance(a, dict) and str(a.get("name") or "").strip()
                    ]
            except Exception:
                activities_list = []

    _act_cards_html = ""
    for act in activities_list:
        _aname = esc(act.get("name", ""))
        _adesc = esc(activity_description_display(act.get("description", "")))
        _atag = esc(act.get("tag", ""))
        _adist = esc(act.get("distance", ""))
        _aphoto = str(act.get("photo", "") or "").strip()
        _abadge = esc(str(act.get("badge", "") or "").strip())
        _maps_url = (
            "https://www.google.com/maps/search/?api=1&query="
            + urllib.parse.quote(str(act.get("name", "") or "") + " " + display_location)
        )

        if _aphoto.startswith("http"):
            _img_html = (
                f'<img src="{esc(_aphoto)}" alt="{_aname}" '
                f'style="width:100%;height:160px;object-fit:cover;display:block;">'
            )
        else:
            _img_html = '<div class="act-card-ph"></div>'

        _badge_html = f'<div class="act-badge">{_abadge}</div>' if _abadge else ""
        _tag_html = f'<span class="act-tag">{_atag}</span>' if _atag else ""

        _act_cards_html += f'''<div class="act-card">
  <div class="act-card-img">
    {_img_html}
    {_badge_html}
  </div>
  <div class="act-card-body">
    <div class="act-card-name">{_aname}</div>
    <div class="act-card-desc">{_adesc}</div>
    <div class="act-card-meta">
      {_tag_html}
      <span class="act-card-dist">{_adist}</span>
    </div>
    <a class="act-card-cta" href="{esc(_maps_url)}" target="_blank" rel="noopener noreferrer">Explore activity →</a>
  </div>
</div>'''

    activities_html = ""
    if _act_cards_html:
        activities_html = f"""
<section class="content-section activities-section">
  <h2>Top Family Activities on the {esc(display_location)}</h2>
  <div class="activities-scroll">
{_act_cards_html}
  </div>
</section>
"""

    destination_summary_html = destination_summary_section_html(
        destination_summary, bare_location
    )

    if_we_were_booking_html = if_we_were_booking_section_html(if_we_were_booking)

    font_links = '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">'

    # Build quick summary dot points from top parks
    _summary_points = []
    for i, r in enumerate(all_parks[:5]):
        _sname = get_short_park_label(
            r.get("park_name", ""),
            location_name=display_location,
            all_names=_all_park_names,
        )
        _sbest = (r.get("best_for") or "")[:70]
        _sscore = r.get("family_score") or r.get("total_score") or ""
        try:
            _sscore_int = int(float(_sscore))
        except Exception:
            _sscore_int = 0
        if _sname and _sbest:
            _summary_points.append(
                f'<li><strong>{esc(_sname)}</strong> ({_sscore_int}/100) — {esc(_sbest)}</li>'
            )

    summary_html = (
        f'''<div class="destination-summary content-section">
  <h2>The {esc(display_location)} Holiday Park Scene</h2>
  <ul class="summary-list">
    {"".join(_summary_points)}
  </ul>
</div>'''
        if _summary_points
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>{page_title}</title>
{seo_block}
{font_links}
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
:root {{
  --text: #222;
  --text-2: #717171;
  --border: #eee;
  --teal: #0072CE;
  --r: 16px;
  --nav-h: 52px;
  --page-max: 1180px;
  --font-sans: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}}
html, body {{
  font-family: var(--font-sans);
  font-size: 14px;
  line-height: 1.5;
  background: #fff;
  color: #222;
  -webkit-font-smoothing: antialiased;
}}

/* NAV */
.nav {{
  position: sticky; top: 0; z-index: 100;
  background: #fff; border-bottom: 1px solid var(--border);
  height: var(--nav-h);
  display: flex; align-items: center;
  justify-content: space-between; padding: 0 16px;
}}
.nav-back {{
  font-size: 14px; color: var(--text-2);
  text-decoration: none;
  display: flex; align-items: center; gap: 4px;
}}
.nav-back:hover {{ color: var(--text); }}
.nav-brand {{ font-size: 14px; font-weight: 600; color: var(--text); }}

/* LOCATION HEADER */
.loc-header {{
  max-width: var(--page-max);
  margin: 0 auto;
  padding: 24px 16px 12px;
  border-bottom: 1px solid var(--border);
}}
.loc-eyebrow {{
  font-size: 11px;
  font-weight: 700;
  color: #717171;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 10px;
}}
.loc-title {{
  font-family: inherit;
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.03em;
  line-height: 1.08;
  margin-bottom: 6px;
  color: #222;
}}
.loc-title-line {{ display: block; }}
.loc-sub {{
  font-size: 14px;
  color: #717171;
  line-height: 1.5;
  margin: 0;
}}
..loc-trust {{
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}}
.loc-trust-badge {{
  font-size: 12px;
  font-weight: 500;
  color: #555;
  background: #f7f7f7;
  border: 1px solid #eee;
  border-radius: 100px;
  padding: 4px 12px;
  white-space: nowrap;
}}

.top3-mobile {{
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  border-bottom: 1px solid var(--border);
  padding-bottom: 20px;
  max-width: 680px;
  margin: 0 auto;
}}
.top3-label {{
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-2);
}}
.t3-card {{
  display: flex;
  gap: 12px;
  border-radius: 12px;
  border: 1px solid var(--border);
  overflow: hidden;
  background: #fff;
  transition: box-shadow 0.2s;
}}
.t3-card:hover {{ box-shadow: 0 4px 16px rgba(0,0,0,0.08); }}
.see-all-btn {{
  display: block;
  width: 100%;
  margin: 8px 0 4px;
  padding: 13px 16px;
  background: #f5f5f5;
  border: 1px solid var(--border);
  border-radius: 12px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
  cursor: pointer;
  text-align: center;
  transition: background 0.15s;
}}
.see-all-btn:hover {{ background: #ebebeb; }}
.t3-img {{
  position: relative;
  flex-shrink: 0;
  width: 110px;
}}
.t3-img img {{
  width: 110px;
  height: 100%;
  object-fit: cover;
  display: block;
}}
.t3-img-ph {{
  width: 110px;
  height: 100%;
  background: #f5f5f5;
}}
.t3-score {{
  position: absolute;
  bottom: 8px;
  left: 8px;
  background: rgba(255,255,255,0.95);
  color: #222;
  font-size: 11px;
  font-weight: 700;
  padding: 3px 7px;
  border-radius: 100px;
}}
.t3-body {{
  padding: 12px 12px 12px 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}}
.t3-rank {{
  font-size: 10px;
  font-weight: 700;
  color: var(--teal);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}}
.t3-name {{
  font-size: 14px;
  font-weight: 700;
  color: #222;
  line-height: 1.25;
}}
.t3-verdict {{
  font-size: 12px;
  color: #555;
  line-height: 1.45;
  flex: 1;
}}
.t3-tags {{
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 2px;
}}
.t3-tag {{
  font-size: 11px;
  font-weight: 500;
  padding: 2px 8px;
  border-radius: 100px;
  background: #f7f7f7;
  color: #555;
  border: 1px solid #eee;
}}
.t3-rating {{
  font-size: 12px;
  color: var(--text-2);
  margin-top: 2px;
}}
.t3-footer {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
}}
.t3-price {{
  font-size: 12px;
  color: var(--text-2);
}}
.t3-cta {{
  font-size: 12px;
  font-weight: 600;
  color: var(--teal);
  text-decoration: none;
}}
.map-hero-strip {{
  position: sticky;
  top: var(--nav-h);
  z-index: 50;
  width: 100%;
  height: 25vh;
  min-height: 180px;
  transition: height 0.4s cubic-bezier(0.32,0.72,0,1);
  border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
  background: #f0f0f0;
}}
.map-hero-strip.map-unstuck {{
  position: relative;
  top: 0;
}}
.map-hero-strip.expanded {{
  height: 70vh;
}}
.map-expand-btn {{
  position: absolute;
  bottom: 10px;
  right: 10px;
  z-index: 10;
  background: white;
  border: 1px solid var(--border);
  border-radius: 100px;
  padding: 6px 12px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  box-shadow: 0 1px 6px rgba(0,0,0,0.12);
  font-family: inherit;
}}
.map-expand-btn.expanded svg {{
  transform: rotate(180deg);
}}
@media (min-width: 768px) {{
  .top3-mobile {{
    padding: 24px;
    gap: 14px;
  }}
  .t3-card {{
    border-radius: 14px;
  }}
  .t3-img img {{
    width: 140px;
    height: 100%;
  }}
  .t3-img {{
    width: 140px;
  }}
  .t3-img-ph {{
    width: 140px;
  }}
  .map-hero-strip.expanded {{
    height: 70vh;
  }}
  .top3-mobile {{
    max-width: 760px;
    padding: 20px 24px;
  }}
}}

/* COMPARE TABLE */
.compare-section {{
  border-top: 1px solid var(--border);
  padding-bottom: 16px;
  max-width: var(--page-max);
  margin: 0 auto;
}}
.compare-section > h2,
.map-section-hdr h2,
.activities-section h2,
.content-section h2,
.local-knowledge h2,
.destination-summary h2,
.nearby-locations h2,
.faq-section > h2,
.lead-magnet h2,
.why-families-section h2 {{
  font-family: inherit;
  font-size: 21px;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.15;
  margin-bottom: 12px;
  color: #222;
}}
.compare-section > h2 {{
  text-align: left;
  padding: 28px 16px 0;
}}
.compare-section > p,
.map-section-hdr p,
.content-section > p,
.destination-summary p,
.lead-magnet > p {{
  font-size: 14px;
  color: #717171;
  line-height: 1.5;
}}
.destination-summary p {{
  margin: 0 0 1em;
}}
.destination-summary p:last-child {{
  margin-bottom: 0;
}}
.if-we-were-booking p {{
  margin: 0 0 1em;
  font-size: 15px;
  color: #444;
  line-height: 1.65;
}}
.if-we-were-booking p:last-child {{
  margin-bottom: 0;
}}
.summary-list {{
  list-style: none;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 4px;
}}
.summary-list li {{
  font-size: 14px;
  color: var(--text-2);
  line-height: 1.6;
  padding: 12px 16px;
  background: #fafafa;
  border-radius: 10px;
  border-left: 3px solid var(--teal);
}}
.summary-list li strong {{
  color: var(--text);
  font-weight: 700;
}}
.compare-section > p {{ padding: 0 16px 14px; }}
.compare-sort-wrap {{
  padding: 0 16px;
}}
.compare-sort-label {{
  font-size: 12px;
  font-weight: 600;
  color: #717171;
  margin-bottom: 8px;
}}
.compare-sort {{
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding: 0 0 16px;
  margin-top: 0;
  scrollbar-width: none;
}}
.compare-sort::-webkit-scrollbar {{
  display: none;
}}
.compare-sort-btn {{
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: 999px;
  border: 1px solid #ddd;
  background: #fff;
  color: #222;
  cursor: pointer;
  font-family: inherit;
}}
.compare-sort-btn.active {{
  background: #222;
  color: #fff;
  border-color: #222;
}}
.compare-sort-btn:hover {{
  border-color: #222;
}}
.compare-scroll {{
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  padding: 0 16px;
}}
.compare-scroll::-webkit-scrollbar {{ height: 3px; }}
.compare-scroll::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 100px; }}
.compare-table {{ width: 100%; min-width: 600px; border-collapse: collapse; }}
.compare-table thead .park-head {{
  text-align: left;
  vertical-align: top;
  padding: 12px 12px 14px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  background: #fff;
  border-bottom: 2px solid var(--border);
  min-width: 150px;
}}
.compare-park-head {{
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: flex-start;
  min-width: 150px;
}}
.compare-park-thumb {{
  width: 72px;
  height: 52px;
  object-fit: cover;
  border-radius: 10px;
  background: #f5f5f5;
  border: 1px solid #eee;
  display: block;
  flex-shrink: 0;
}}
.compare-park-thumb-ph {{
  background: #f5f5f5;
}}
.compare-park-name {{
  font-size: 12px;
  font-weight: 700;
  color: #222;
  line-height: 1.25;
  max-width: 150px;
}}
.compare-table thead th.scope-corner {{
  min-width: 150px;
  max-width: 150px;
  position: sticky;
  left: 0;
  z-index: 3;
  background: #fff;
  border-bottom: 2px solid var(--border);
  vertical-align: top;
  padding: 12px 12px 14px;
}}
.compare-table tbody th {{
  font-size: 12px;
  font-weight: 600;
  color: #717171;
  text-align: left;
  padding: 14px 14px;
  background: #fff;
  border-bottom: 1px solid #eee;
  position: sticky;
  left: 0;
  z-index: 2;
  min-width: 150px;
  max-width: 150px;
  line-height: 1.3;
  white-space: normal;
  word-break: normal;
  overflow-wrap: normal;
  text-transform: none;
  letter-spacing: 0;
}}
.compare-table td {{
  padding: 11px 14px; border-bottom: 1px solid var(--border);
  font-size: 13px; color: var(--text);
  vertical-align: middle; line-height: 1.45; min-width: 150px;
}}
.compare-table tbody tr:hover td {{ background: #fafafa; }}
.compare-table tbody tr:hover th {{ background: #fafafa; }}
.compare-table tbody tr:last-child td,
.compare-table tbody tr:last-child th {{ border-bottom: none; }}
.score-gold, .score-silver, .score-plain {{
  background: #f7f7f7; color: #222; font-weight: 700;
  font-size: 13px; padding: 4px 10px;
  border-radius: 100px; display: inline-block; border: 1px solid #eee;
}}
.muted {{ color: var(--text-2); }}
.cell-strong {{ font-weight: 600; }}
.price-notes {{ font-size: 12px; color: var(--text-2); margin: 0; padding-left: 1rem; }}
.book-btn {{
  display: inline-block;
  background: #222;
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  padding: 10px 16px;
  border-radius: 8px;
  text-decoration: none;
  border: none;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.15s;
  width: 100%;
  text-align: center;
}}
.book-btn:hover {{ background: #000; }}

@media (max-width: 768px) {{
  .compare-table tbody th {{
    min-width: 120px;
    max-width: 120px;
    font-size: 11px;
    padding: 12px 10px;
    line-height: 1.25;
    white-space: normal;
    text-transform: none;
    letter-spacing: 0;
  }}
  .compare-table thead th.scope-corner {{
    min-width: 120px;
    max-width: 120px;
  }}
}}

/* MAP */
.map-section {{ border-top: 1px solid var(--border); }}
.map-section-hdr {{
  max-width: var(--page-max);
  margin: 0 auto;
  padding: 16px 16px 0;
}}
.map-section-label {{
  background: #222;
  color: #fff;
  padding: 16px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}}
.map-label-text {{
  display: flex;
  flex-direction: column;
  gap: 3px;
}}
.map-label-title {{
  font-family: inherit;
  font-size: 18px;
  font-weight: 700;
  color: #fff;
  letter-spacing: -0.01em;
}}
.map-label-sub {{
  font-size: 13px;
  color: rgba(255,255,255,0.65);
}}
.map-label-arrow {{
  font-size: 24px;
  color: #fff;
  opacity: 0.7;
  animation: bounce 1.5s infinite;
}}
@keyframes bounce {{
  0%, 100% {{ transform: translateY(0); }}
  50% {{ transform: translateY(4px); }}
}}
.map-wrap {{
  width: 100%;
  max-width: var(--page-max);
  margin: 0 auto;
  aspect-ratio: 16/9;
  max-height: 420px;
  padding: 0 16px 28px;
}}
#map {{ width: 100%; height: 100%; }}

/* MAP PHOTO PINS */
.map-marker-wrap {{
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
}}
.map-pin-label {{
  font-size: 11px;
  font-weight: 700;
  color: #222;
  background: rgba(255,255,255,0.96);
  padding: 2px 7px;
  border-radius: 999px;
  white-space: nowrap;
  box-shadow: 0 1px 4px rgba(0,0,0,0.12);
  max-width: 86px;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.map-photo-pin {{
  position: relative;
  width: 42px;
  height: 42px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 2px 8px rgba(0,0,0,0.22);
  cursor: pointer;
  overflow: visible;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
  flex-shrink: 0;
}}
.map-photo-pin:hover {{
  transform: scale(1.15);
  z-index: 2;
  box-shadow: 0 4px 14px rgba(0,0,0,0.28);
}}
.map-photo-pin-active {{
  transform: scale(1.15);
  z-index: 3;
  box-shadow: 0 4px 14px rgba(0,0,0,0.28);
}}
.map-photo-pin-img,
.map-photo-pin-ph {{
  width: 100%;
  height: 100%;
  border-radius: 50%;
  object-fit: cover;
  display: block;
  background: #f5f5f5;
}}
.map-photo-pin-ph {{
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1rem;
  color: #ccc;
}}
.map-photo-pin-score {{
  position: absolute;
  right: -2px;
  bottom: -2px;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  border-radius: 999px;
  background: #222;
  color: #fff;
  font-size: 9px;
  font-weight: 700;
  line-height: 18px;
  text-align: center;
  border: 1.5px solid #fff;
  box-shadow: 0 1px 4px rgba(0,0,0,0.2);
  pointer-events: none;
}}

/* ACTIVITIES */
.activities-section {{ border-top: 1px solid var(--border); }}
.activities-sub {{
  font-size: 14px; color: var(--text-2);
  margin-top: -8px; margin-bottom: 4px;
}}
.activities-scroll {{
  display: flex; gap: 16px;
  overflow-x: auto; padding: 4px 0 16px;
  scroll-snap-type: x mandatory;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
}}
.activities-scroll::-webkit-scrollbar {{ display: none; }}
.act-card {{
  flex: 0 0 260px; max-width: 260px;
  border-radius: 14px; border: 1px solid var(--border);
  overflow: hidden; background: #fff;
  scroll-snap-align: start; flex-shrink: 0;
  display: flex; flex-direction: column;
  transition: box-shadow 0.2s;
}}
.act-card:hover {{ box-shadow: 0 4px 20px rgba(0,0,0,0.08); }}
.act-card-img {{
  position: relative; flex-shrink: 0;
}}
.act-card-img img {{
  width: 100%; height: 160px;
  object-fit: cover; display: block;
}}
.act-card-ph {{
  width: 100%; height: 160px;
  background: linear-gradient(135deg, #e8f4f0 0%, #d0e8e0 100%);
}}
.act-badge {{
  position: absolute; top: 10px; left: 10px;
  background: #222; color: #fff;
  font-size: 10px; font-weight: 700;
  padding: 3px 9px; border-radius: 100px;
  letter-spacing: 0.04em; text-transform: uppercase;
}}
.act-card-body {{
  padding: 14px; display: flex;
  flex-direction: column; gap: 6px; flex: 1;
}}
.act-card-name {{
  font-size: 15px; font-weight: 700;
  color: var(--text); line-height: 1.25;
}}
.act-card-desc {{
  font-size: 13px; color: #555;
  line-height: 1.5; flex: 1;
}}
.act-card-meta {{
  display: flex; align-items: center;
  gap: 8px; flex-wrap: wrap;
}}
.act-tag {{
  font-size: 11px; font-weight: 600;
  padding: 3px 9px; border-radius: 100px;
  background: #f7f7f7; color: #555;
  border: 1px solid #eee;
}}
.act-card-dist {{
  font-size: 12px; color: var(--text-2);
}}
.act-card-cta {{
  display: block; text-align: center;
  background: #222; color: #fff;
  font-size: 13px; font-weight: 600;
  padding: 11px; border-radius: 8px;
  text-decoration: none; margin-top: 4px;
  transition: background 0.15s;
}}
.act-card-cta:hover {{ background: #000; }}
@media (max-width: 768px) {{
  .act-card {{ flex: 0 0 78vw; max-width: 78vw; }}
}}

/* CONTENT */
.content-section {{
  max-width: var(--page-max);
  margin: 0 auto;
  padding: 32px 16px;
  border-top: 1px solid var(--border);
}}
.content-section p {{ margin-bottom: 10px; }}
.faq-section {{
  padding: 28px 16px;
  border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
}}
.why-list {{ list-style: none; padding: 0; display: flex; flex-direction: column; gap: 8px; }}
.why-list li {{
  font-size: 14px; color: var(--text); padding: 11px 14px;
  background: #fafafa; border-radius: 10px;
  display: flex; align-items: center; gap: 10px;
}}
.why-list li::before {{ content: '✓'; color: var(--teal); font-weight: 700; flex-shrink: 0; }}
.nearby-list {{ list-style: none; padding: 0; display: flex; flex-direction: column; gap: 8px; }}
.nearby-list a {{
  display: flex; align-items: center; justify-content: space-between;
  padding: 13px 16px; background: #fff;
  border: 1px solid var(--border); border-radius: var(--r);
  font-size: 14px; font-weight: 500; color: var(--text); text-decoration: none;
}}
.nearby-list a:hover {{ border-color: var(--text); }}
.nearby-list a::after {{ content: '→'; color: var(--text-2); }}
details {{
  border: 1px solid var(--border); border-radius: var(--r);
  margin-bottom: 8px; background: #fff; overflow: hidden;
}}
details summary {{
  font-size: 14px; font-weight: 600; color: var(--text);
  cursor: pointer; padding: 15px 18px; list-style: none;
  display: flex; justify-content: space-between; align-items: center;
}}
details summary::-webkit-details-marker {{ display: none; }}
details summary::after {{ content: '+'; font-size: 18px; font-weight: 300; color: var(--text-2); }}
details[open] summary::after {{ content: '−'; }}
details[open] summary {{ border-bottom: 1px solid var(--border); }}
.faq-answer {{ padding: 13px 18px 16px; font-size: 14px; line-height: 1.65; color: var(--text-2); }}
.lead-magnet {{ background: #fafafa; text-align: center; padding: 40px 16px; }}
.email-row {{ display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-top: 16px; }}
.email-row input {{
  flex: 1 1 200px; padding: 12px 16px; border-radius: 8px;
  border: 1px solid var(--border); font-size: 14px;
  font-family: inherit; outline: none;
}}
.email-row input:focus {{ border-color: var(--teal); }}
.email-row button {{
  background: #222; color: #fff; border: none;
  padding: 12px 20px; font-size: 14px; font-weight: 700;
  font-family: inherit; cursor: pointer; border-radius: 8px;
  transition: background 0.15s ease;
}}
.email-row button:hover {{ background: #000; }}

/* DETAIL SHEET */
.sheet-overlay {{
  display: none;
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.4);
  z-index: 400;
}}
.sheet-overlay.open {{ display: block; }}
.sheet {{
  position: fixed;
  bottom: 0; left: 0; right: 0;
  z-index: 500;
  background: white;
  border-radius: 20px 20px 0 0;
  transform: translateY(100%);
  transition: transform 0.35s cubic-bezier(0.32,0.72,0,1);
  max-height: 85dvh;
  overflow-y: auto;
  padding-bottom: max(24px, env(safe-area-inset-bottom));
}}
.sheet.open {{ transform: translateY(0); }}
.sheet-handle {{
  display: flex; justify-content: center;
  padding: 12px 0 8px; cursor: pointer;
  flex-shrink: 0;
}}
.sheet-handle-bar {{
  width: 36px; height: 4px;
  background: #ddd; border-radius: 100px;
}}
.map-popup-thumb {{
  width: 100%;
  height: 110px;
  object-fit: cover;
  border-radius: 12px 12px 0 0;
  background: #f5f5f5;
  display: block;
}}
.map-popup-thumb-ph {{
  width: 100%;
  height: 110px;
  background: #f5f5f5;
  border-radius: 12px 12px 0 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 2rem;
  color: #ddd;
}}
.sheet-body {{ padding: 18px 20px 24px; }}
.sheet-score {{
  display: inline-block;
  font-size: 13px; font-weight: 700;
  color: var(--text); margin-bottom: 8px;
}}
.sheet-name {{
  font-family: inherit;
  font-size: 20px; font-weight: 700;
  color: #111; line-height: 1.2;
  letter-spacing: -0.02em; margin-bottom: 8px;
}}
.sheet-verdict {{
  font-size: 14px; color: #555;
  line-height: 1.65; margin-bottom: 14px;
}}
.sheet-address {{
  font-size: 13px; color: var(--text-2);
  margin-bottom: 16px;
  line-height: 1.5;
}}
.popup-address {{
  color: #0072CE;
  text-decoration: none;
  font-size: 13px;
  line-height: 1.5;
}}
.popup-address:hover {{
  text-decoration: underline;
  text-underline-offset: 3px;
}}
.sheet-footer {{
  display: flex; align-items: center;
  justify-content: space-between;
  padding-top: 16px;
  border-top: 1px solid var(--border);
}}
.sheet-price {{ font-size: 14px; color: var(--text-2); }}
.sheet-price strong {{ font-size: 16px; font-weight: 700; color: #111; }}
.sheet-cta {{
  background: #222; color: white;
  font-size: 14px; font-weight: 600;
  padding: 12px 24px; border-radius: 10px;
  text-decoration: none; transition: background 0.15s;
  white-space: nowrap;
}}
.sheet-cta:hover {{ background: #000; }}

/* FOOTER */
.site-footer-page {{
  max-width: var(--page-max);
  margin: 0 auto;
  padding: 32px 16px 40px;
  text-align: center;
  font-size: 14px;
  color: #717171;
  border-top: 1px solid var(--border);
}}
.site-footer-page img {{ height: 28px; display: block; margin: 0 auto 8px; opacity: 0.5; }}
.site-footer-page a {{ color: var(--text-2); text-decoration: none; }}

@media (min-width: 768px) {{
  .loc-header {{ padding: 32px 24px 16px; }}
  .loc-title {{ font-size: 32px; }}
  .compare-section > h2,
  .map-section-hdr h2,
  .activities-section h2,
  .content-section h2,
  .local-knowledge h2,
  .destination-summary h2,
  .nearby-locations h2,
  .faq-section > h2,
  .lead-magnet h2,
  .why-families-section h2 {{ font-size: 24px; }}
  .compare-section > h2,
  .compare-section > p {{ padding-left: 24px; padding-right: 24px; }}
  .compare-sort-wrap {{ padding-left: 24px; padding-right: 24px; }}
  .compare-scroll {{ padding: 0 24px; }}
  .content-section,
  .map-section-hdr,
  .map-wrap,
  .lead-magnet,
  .site-footer-page,
  .loc-header {{ padding-left: 24px; padding-right: 24px; }}
  .map-wrap {{ max-height: 520px; }}
  .sheet {{
    left: auto; right: 24px; bottom: 24px;
    width: 380px; border-radius: 16px;
    max-height: calc(100vh - 100px);
  }}
}}
/* PHOTO GALLERY */
.pgal {{ position: relative; width: 100%; height: 100%; overflow: hidden; }}
.pgal-track {{ display: flex; height: 100%; transition: transform 0.3s ease; will-change: transform; }}
.pgal-img {{ min-width: 100%; width: 100%; height: 100%; object-fit: cover; flex-shrink: 0; display: block; }}
.pgal-btn {{ position: absolute; top: 50%; transform: translateY(-50%); background: rgba(0,0,0,0.45); color: #fff; border: none; border-radius: 50%; width: 22px; height: 22px; display: none; align-items: center; justify-content: center; cursor: pointer; font-size: 16px; line-height: 1; padding: 0; z-index: 2; }}
.pgal:hover .pgal-btn {{ display: flex; }}
.pgal-prev {{ left: 3px; }}
.pgal-next {{ right: 3px; }}
.pgal-dots {{ position: absolute; bottom: 32px; left: 50%; transform: translateX(-50%); display: flex; gap: 4px; z-index: 2; pointer-events: none; }}
.pgal-dot {{ width: 5px; height: 5px; border-radius: 50%; background: rgba(255,255,255,0.5); transition: background 0.2s; }}
.pgal-dot.active {{ background: #fff; }}
</style>
</head>
<body>

<nav class="nav">
  <a href="/" class="nav-back">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="m15 18-6-6 6-6"/></svg>
    All locations
  </a>
  <span class="nav-brand">Family Holiday Parks</span>
</nav>

<div class="loc-header">
  <div class="loc-eyebrow">{state_label}</div>
  <h1 class="loc-title">
    <span class="loc-title-line">Best Family Holiday Parks</span>
    <span class="loc-title-line">{_heading_prep} {esc(bare_location)}</span>
  </h1>
  <p class="loc-sub">Ranked from {total_reviews_str}+ reviews and 37 data points.</p>
</div>

<div class="map-hero-strip" id="map-hero-strip">
  <div id="map" style="width:100%;height:100%;min-height:180px;"></div>
  <button class="map-expand-btn" id="map-expand-btn" onclick="toggleMapExpand()">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>
    Expand map
  </button>
</div>

<div class="top3-mobile" id="parks-list">
  {_first3_html}
  {_extra_parks_html}
</div>

{compare_block}

{if_we_were_booking_html}

{activities_html}

{faq_block}

{lead_magnet_html}

{nearby_html}

<footer class="site-footer-page">
  <img src="/images/logo.png" alt="Family Holiday Parks">
  <div>familyholidayparks.com.au · Helping Australian Families Find Better Holidays</div>
</footer>

<div class="sheet-overlay" id="sheet-overlay" onclick="closeSheet()"></div>
<div class="sheet" id="sheet">
  <div class="sheet-handle" onclick="closeSheet()">
    <div class="sheet-handle-bar"></div>
  </div>
  <div id="sheet-content"></div>
</div>

<script>
const PARKS = {parks_json_str};
let map;
let activeCardIdx = -1;
let allMarkers = [];
let allMarkerEls = [];
let showLogoPins = false;

// ── SCROLL-LINKED MAP ─────────────────────────────────────────
function initMap() {{
  const mapEl = document.getElementById('map');
  // Seed the initial center/zoom from park coords so the map never flashes a wrong location
  const _initLat = PARKS.length ? PARKS.reduce((s, p) => s + p.lat, 0) / PARKS.length : -27.98;
  const _initLng = PARKS.length ? PARKS.reduce((s, p) => s + p.lng, 0) / PARKS.length : 153.43;
  map = new google.maps.Map(mapEl, {{
    mapId: {json.dumps(google_maps_map_id)},
    center: {{ lat: _initLat, lng: _initLng }},
    zoom: 11,
    disableDefaultUI: true,
    zoomControl: true,
    gestureHandling: 'greedy',
    styles: [
      {{ featureType: 'poi', stylers: [{{ visibility: 'off' }}] }},
      {{ featureType: 'transit', stylers: [{{ visibility: 'off' }}] }},
      {{ elementType: 'labels.icon', stylers: [{{ visibility: 'off' }}] }}
    ]
  }});

  PARKS.forEach((park, i) => {{
    const el = document.createElement('div');
    el.innerHTML = renderPin(park, false, showLogoPins);
    el.style.cssText = 'cursor:pointer;transition:transform 0.2s ease;';

    const marker = new google.maps.marker.AdvancedMarkerElement({{
      map,
      position: {{ lat: park.lat, lng: park.lng }},
      content: el,
      title: park.name,
    }});

    marker.addListener('click', () => {{
      activatePin(i);
      scrollToCard(i);
      openSheet(park);
    }});

    allMarkers.push(marker);
    allMarkerEls.push(el);
  }});

  // Fit all park coords immediately — clamp result between zoom 11 and 13
  if (PARKS.length > 1) {{
    const bounds = new google.maps.LatLngBounds();
    PARKS.forEach(p => bounds.extend({{ lat: p.lat, lng: p.lng }}));
    map.fitBounds(bounds, {{ top: 40, right: 40, bottom: 40, left: 40 }});
    google.maps.event.addListenerOnce(map, 'idle', () => {{
      const z = map.getZoom();
      if (z > 13) map.setZoom(13);
      if (z < 11) map.setZoom(11);
    }});
  }} else if (PARKS.length === 1) {{
    map.setCenter({{ lat: PARKS[0].lat, lng: PARKS[0].lng }});
    map.setZoom(12);
  }}

  // Logo reveal at zoom >= 14
  google.maps.event.addListener(map, 'zoom_changed', () => {{
    showLogoPins = (map.getZoom() || 0) >= 14;
    allMarkerEls.forEach((el, i) => {{
      el.innerHTML = renderPin(PARKS[i], i === activeCardIdx, showLogoPins);
    }});
  }});

  // Init scroll observer after short delay
  setTimeout(initScrollObserver, 400);
}}

function renderPin(park, active, showLogo) {{
  const logo = park.logo && String(park.logo).startsWith('/') ? park.logo : '';
  if (active) {{
    // Active: speech bubble with full name + small teal dot anchor (no pill — avoids showing name twice)
    return `<div style="display:flex;flex-direction:column;align-items:center;transform:scale(1.25);transition:transform 0.25s cubic-bezier(0.34,1.56,0.64,1);filter:drop-shadow(0 4px 8px rgba(0,114,206,0.4));">
      <div style="position:relative;background:#0072CE;color:#fff;font-size:10px;font-weight:700;padding:3px 8px;border-radius:6px;white-space:nowrap;max-width:140px;overflow:hidden;text-overflow:ellipsis;text-align:center;margin-bottom:4px;">${{park.full_name}}<div style="position:absolute;bottom:-4px;left:50%;transform:translateX(-50%);border-left:4px solid transparent;border-right:4px solid transparent;border-top:4px solid #0072CE;"></div></div>
      <div style="width:10px;height:10px;background:#0072CE;border-radius:50%;border:2px solid #fff;"></div>
    </div>`;
  }}
  const logoEl = (showLogo && logo)
    ? `<div style="width:22px;height:22px;background:#fff;border-radius:50%;display:flex;align-items:center;justify-content:center;overflow:hidden;padding:2px;margin-bottom:2px;flex-shrink:0;"><img src="${{logo}}" style="width:100%;height:100%;object-fit:contain;display:block;"></div>`
    : '';
  return `<div style="display:flex;flex-direction:column;align-items:center;transform:scale(1);transition:transform 0.25s cubic-bezier(0.34,1.56,0.64,1);filter:drop-shadow(0 2px 4px rgba(0,0,0,0.25));">
    <div style="background:#333;color:#fff;font-size:10px;font-weight:600;padding:4px 9px;border-radius:100px;white-space:nowrap;display:flex;flex-direction:column;align-items:center;line-height:1.2;letter-spacing:0.01em;">${{logoEl}}${{park.pin_label}}</div>
  </div>`;
}}

function activatePin(idx) {{
  if (activeCardIdx === idx) return;
  activeCardIdx = idx;
  allMarkerEls.forEach((el, i) => {{
    el.innerHTML = renderPin(PARKS[i], i === idx, showLogoPins);
  }});
  // Highlight active card
  document.querySelectorAll('.t3-card').forEach((c, i) => {{
    if (i === idx) {{
      c.style.borderColor = '#0072CE';
      c.style.boxShadow = '0 0 0 2px rgba(0,114,206,0.2)';
    }} else {{
      c.style.borderColor = '#eee';
      c.style.boxShadow = 'none';
    }}
  }});
  // Pan to active park, never zoom in past 13
  if (PARKS[idx]) {{
    map.panTo({{ lat: PARKS[idx].lat, lng: PARKS[idx].lng }});
    if (map.getZoom() > 13) map.setZoom(13);
  }}
}}

function scrollToCard(idx) {{
  const cards = document.querySelectorAll('.t3-card');
  if (cards[idx]) {{
    cards[idx].scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
  }}
}}

function initScrollObserver() {{
  const cards = document.querySelectorAll('.t3-card[data-park-idx]');
  if (!cards.length || !map) return;

  const observer = new IntersectionObserver((entries) => {{
    // Find the most visible card
    let maxRatio = 0;
    let bestIdx = -1;
    entries.forEach(entry => {{
      if (entry.intersectionRatio > maxRatio) {{
        maxRatio = entry.intersectionRatio;
        bestIdx = parseInt(entry.target.dataset.parkIdx);
      }}
    }});
    if (bestIdx >= 0 && maxRatio > 0.4) {{
      activatePin(bestIdx);
    }}
  }}, {{
    root: null,
    rootMargin: '-10% 0px -40% 0px',
    threshold: [0, 0.25, 0.5, 0.75, 1.0]
  }});

  cards.forEach(card => observer.observe(card));

  // Sticky toggle + zoom-out when compare table comes into view
  const compareEl = document.querySelector('.compare-section');
  const mapStrip = document.getElementById('map-hero-strip');
  if (compareEl) {{
    const compareObserver = new IntersectionObserver((entries) => {{
      entries.forEach(entry => {{
        // Sticky toggle: unstick when compare visible, restick when scrolled back above it
        if (mapStrip) {{
          if (entry.isIntersecting) {{
            mapStrip.classList.add('map-unstuck');
          }} else if (entry.boundingClientRect.top > 0) {{
            // Compare table is below the viewport — user scrolled back up into cards
            mapStrip.classList.remove('map-unstuck');
          }}
          // If compare table scrolled past above viewport, stay unstuck
        }}
        // Zoom out to show all parks when compare comes into view
        if (entry.isIntersecting) {{
          if (map && PARKS.length > 1) {{
            const bounds = new google.maps.LatLngBounds();
            PARKS.forEach(p => bounds.extend({{ lat: p.lat, lng: p.lng }}));
            map.fitBounds(bounds, {{ top: 50, right: 50, bottom: 50, left: 50 }});
            allMarkerEls.forEach((el, i) => {{
              el.innerHTML = renderPin(PARKS[i], false, showLogoPins);
            }});
            activeCardIdx = -1;
          }}
        }}
      }});
    }}, {{
      root: null,
      rootMargin: '0px',
      threshold: 0
    }});
    compareObserver.observe(compareEl);
  }}
}}

// See all parks toggle
function toggleExtraParks(btn) {{
  const extra = document.getElementById('extra-parks');
  if (!extra) return;
  const hidden = extra.style.display === 'none';
  extra.style.display = hidden ? 'block' : 'none';
  const total = btn.dataset.total;
  btn.textContent = hidden ? 'Show fewer' : 'See all ' + total + ' parks';
}}

// Map expand toggle
function toggleMapExpand() {{
  const strip = document.getElementById('map-hero-strip');
  const btn = document.getElementById('map-expand-btn');
  const isExpanded = strip.classList.toggle('expanded');
  btn.classList.toggle('expanded', isExpanded);
  btn.innerHTML = isExpanded
    ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="10" y1="14" x2="3" y2="21"/><line x1="21" y1="3" x2="14" y2="10"/></svg> Collapse`
    : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg> Expand map`;
  setTimeout(() => {{
    if (map) {{
      google.maps.event.trigger(map, 'resize');
      if (PARKS[activeCardIdx >= 0 ? activeCardIdx : 0]) {{
        map.panTo({{ lat: PARKS[activeCardIdx >= 0 ? activeCardIdx : 0].lat, lng: PARKS[activeCardIdx >= 0 ? activeCardIdx : 0].lng }});
      }}
    }}
  }}, 420);
}}

// Sheet functions
function openSheet(park) {{
  const content = document.getElementById('sheet-content');
  if (!content) return;
  const img = park.photo && String(park.photo).startsWith('http')
    ? `<img class="sheet-img" src="${{park.photo}}" alt="${{park.name}}">`
    : `<div class="sheet-img-ph">🏕</div>`;
  const tags = (park.tags||[]).map(t =>
    `<span class="sheet-tag">${{t[0].toUpperCase()+t.slice(1)}}</span>`
  ).join('');
  const cta = park.url
    ? `<a class="sheet-cta" href="${{park.url}}" target="_blank" rel="noopener noreferrer sponsored">View park →</a>`
    : '';
  content.innerHTML = `${{img}}<div class="sheet-body">
    <div class="sheet-score">${{park.score_label}} Family Score</div>
    <div class="sheet-name">${{park.name}}</div>
    <div class="sheet-verdict">${{park.verdict}}</div>
    <div class="sheet-tags">${{tags}}</div>
    <div class="sheet-meta">
      <span class="sheet-price">${{park.price ? `<strong>${{park.price}}</strong>` : ''}}</span>
      ${{cta}}
    </div>
  </div>`;
  document.getElementById('sheet').classList.add('open');
  document.getElementById('sheet-overlay').classList.add('open');
}}

function closeSheet() {{
  document.getElementById('sheet')?.classList.remove('open');
  document.getElementById('sheet-overlay')?.classList.remove('open');
}}

function sortCards(btn, key, asc) {{
  const list = document.getElementById('parks-list');
  if (!list) return;
  const cards = Array.from(list.querySelectorAll('.t3-card'));
  cards.sort((a, b) => {{
    const va = parseFloat(a.dataset[key] ?? (asc ? 9999 : 0));
    const vb = parseFloat(b.dataset[key] ?? (asc ? 9999 : 0));
    return asc ? va - vb : vb - va;
  }});
  // Re-index after sort
  cards.forEach((c, i) => {{
    c.dataset.parkIdx = i;
    list.appendChild(c);
  }});
  document.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  // Reactivate first card
  setTimeout(() => activatePin(0), 100);
}}

document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeSheet(); }});

window.addEventListener('load', () => {{
  setTimeout(() => {{
    if (map) {{
      google.maps.event.trigger(map, 'resize');
    }}
  }}, 600);
}});

function sortCompareTable(btn) {{
  const table = document.getElementById('compare-table');
  if (!table || !btn) return;
  const sortKey = btn.dataset.sort;
  const attrMap = {{
    family_score: 'data-family-score',
    google_rating: 'data-google-rating',
    beach_km: 'data-beach-km',
    supermarket_km: 'data-supermarket-km',
    price: 'data-price',
  }};
  const dataAttr = attrMap[sortKey];
  if (!dataAttr) return;
  const asc = sortKey === 'beach_km' || sortKey === 'supermarket_km' || sortKey === 'price';
  const headerRow = table.querySelector('thead tr');
  const parkHeaders = Array.from(headerRow.querySelectorAll('th.park-col'));
  const parkCount = parkHeaders.length;
  if (!parkCount) return;
  const order = parkHeaders.map((_, i) => i);
  order.sort((a, b) => {{
    const va = parseFloat(parkHeaders[a].getAttribute(dataAttr) ?? (asc ? 9999 : 0));
    const vb = parseFloat(parkHeaders[b].getAttribute(dataAttr) ?? (asc ? 9999 : 0));
    if (sortKey === 'price') {{
      if (va !== vb) return va - vb;
      const fa = parseFloat(parkHeaders[a].getAttribute('data-family-score') ?? 0);
      const fb = parseFloat(parkHeaders[b].getAttribute('data-family-score') ?? 0);
      return fb - fa;
    }}
    return asc ? va - vb : vb - va;
  }});
  function reorderParkCells(row) {{
    const cells = Array.from(row.children);
    if (cells.length - 1 !== parkCount) return;
    const label = cells[0];
    const parkCells = cells.slice(1);
    const reordered = order.map(i => parkCells[i]);
    row.replaceChildren(label, ...reordered);
  }}
  reorderParkCells(headerRow);
  table.querySelectorAll('tbody tr').forEach(reorderParkCells);
  document.querySelectorAll('.compare-sort-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}}
function pgalMove(e, id, dir) {{
  e.stopPropagation();
  var el = document.getElementById(id);
  if (!el) return;
  var n = parseInt(el.dataset.n || '1');
  var cur = (parseInt(el.dataset.cur || '0') + dir + n) % n;
  el.dataset.cur = cur;
  el.querySelector('.pgal-track').style.transform = 'translateX(-' + (cur * 100) + '%)';
  el.querySelectorAll('.pgal-dot').forEach(function(d, i) {{ d.classList.toggle('active', i === cur); }});
}}
(function() {{
  function initGalleries() {{
    document.querySelectorAll('.pgal').forEach(function(el) {{
      var startX = null;
      el.addEventListener('touchstart', function(e) {{ startX = e.touches[0].clientX; }}, {{passive: true}});
      el.addEventListener('touchend', function(e) {{
        if (startX === null) return;
        var dx = e.changedTouches[0].clientX - startX;
        if (Math.abs(dx) > 30) pgalMove(e, el.id, dx < 0 ? 1 : -1);
        startX = null;
      }}, {{passive: true}});
    }});
  }}
  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', initGalleries);
  }} else {{
    initGalleries();
  }}
}})();
</script>

<script async defer
  src="https://maps.googleapis.com/maps/api/js?key={google_maps_api_key}&libraries=marker&callback=initMap&v=beta">
</script>

</body>
</html>"""


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


HERO_INTRO_CACHE_MARKER = (
    "# Generated by generate_page.py — delete or rerun with --fresh-copy to regenerate\n\n"
)


def load_hero_intro_cache(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    if raw.startswith("#"):
        parts = raw.split("\n\n", 1)
        if len(parts) > 1:
            return parts[1].strip()
    return raw.strip()


def fetch_claude_hero_intro(
    api_key: str,
    *,
    location_name: str,
    bare_location: str,
    park_count: int,
    parks: list[dict[str, Any]],
) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError(
            "The 'anthropic' package is required. Install with: pip install anthropic"
        ) from e

    hero_intro_prompt = f"""Write a 2-sentence hero intro for a family holiday park review page.

Location: {bare_location}
Top parks: {', '.join([p['park_name'] for p in parks[:3]])}

RULES:
- Exactly 2 sentences. No more.
- Do NOT use: "nestled", "boasts", "perfect getaway", "offers many attractions", "kilometres of coastline"
- Do NOT repeat the page title
- Do NOT mention park counts or scores

Sentence 1:
Start with "Planning a family trip to {bare_location}?" then explain what this page does — use the phrase "best family holiday parks in {bare_location}" and end with "skip hours of research and get straight to the good part"

Sentence 2:
Start with "Families love {bare_location} because" then give 2-3 specific emotional/practical reasons families visit this location. Mention real things kids care about. Be specific to this location, not generic.

Voice: parent-to-parent, Australian, warm, specific
Return plain text only. No HTML. No markdown. No line breaks between sentences."""

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": hero_intro_prompt}],
    )

    text_parts: list[str] = []
    for block_obj in message.content:
        if getattr(block_obj, "type", None) == "text":
            text_parts.append(block_obj.text)
    combined = "".join(text_parts).strip()
    return _strip_code_fence(combined)


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


def call_claude_api(api_key: str, prompt: str) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError(
            "The 'anthropic' package is required. Install with: pip install anthropic"
        ) from e

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    text_parts: list[str] = []
    for block_obj in message.content:
        if getattr(block_obj, "type", None) == "text":
            text_parts.append(block_obj.text)
    return "".join(text_parts)


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
    p.add_argument(
        "--publish",
        action="store_true",
        help="After a successful build, run git commit and git push for the project directory.",
    )
    p.add_argument(
        "--refresh-places",
        action="store_true",
        help="Re-fetch Google Places beach, supermarket, and photo data instead of using scores.json cache.",
    )
    return p.parse_args()


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def get_lat_lng(row: dict[str, Any]) -> tuple[float | None, float | None]:
    lat = (
        row.get("lat")
        or row.get("park_lat")
        or row.get("latitude")
        or (row.get("location") or {}).get("lat")
    )
    lng = (
        row.get("lng")
        or row.get("park_lng")
        or row.get("longitude")
        or (row.get("location") or {}).get("lng")
    )
    return _as_float(lat), _as_float(lng)


# Short display labels for Gold Coast parks (comparison table + map pins).
APOLLO_BAY_LABEL_OVERRIDES: dict[str, str] = {
    "Apollo Bay Holiday Park": "Apollo Bay",
    "Apollo Bay Recreation Reserve": "Recreation Reserve",
}

GOLD_COAST_LABEL_OVERRIDES: dict[str, str] = {
    "BIG4 Gold Coast Holiday Park": "BIG4",
    "Broadwater Tourist Park": "Broadwater",
    "Tallebudgera Creek Tourist Park": "Tallebudgera",
    "Ocean Beach Tourist Park": "Ocean Beach",
    "NRMA Treasure Island Holiday Resort, Gold Coast": "NRMA",
    "NRMA Treasure Island Holiday Resort": "NRMA",
    "Main Beach Tourist Park": "Main Beach",
    "Kirra Beach Tourist Park": "Kirra",
    "Burleigh Beach Tourist Park": "Burleigh",
    "Nobby Beach Holiday Village": "Nobby",
    "Jacobs Well Tourist Park": "Jacobs Well",
}

# Fixed coordinates for Gold Coast parks when lat/lng are absent from row/master.
GOLD_COAST_COORD_OVERRIDES: dict[str, tuple[float, float]] = {
    "BIG4 Gold Coast Holiday Park": (-27.9001559, 153.3159363),
    "Broadwater Tourist Park": (-27.957512, 153.410782),
    "Tallebudgera Creek Tourist Park": (-28.099885, 153.45944),
    "Ocean Beach Tourist Park": (-28.0696262, 153.4430087),
    "NRMA Treasure Island Holiday Resort, Gold Coast": (-27.9348018, 153.393697),
    "Main Beach Tourist Park": (-27.9770904, 153.4278113),
    "Kirra Beach Tourist Park": (-28.169317, 153.521834),
    "Burleigh Beach Tourist Park": (-28.0912666, 153.4551145),
    "Nobby Beach Holiday Village": (-28.061027, 153.4368609),
    "Jacobs Well Tourist Park": (-27.780752, 153.365211),
}


def load_location_config(path: Path, slug: str | None = None) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    if slug:
        entry = raw.get(slug)
        return dict(entry) if isinstance(entry, dict) else {}
    return raw


def scores_item_to_page_row(
    item: dict[str, Any], *, location: str, honourable_summary: bool = False
) -> dict[str, Any] | None:
    log(f"[debug] scores item keys: {list(item.keys())}")
    name = str(item.get("park_name") or item.get("name") or "").strip()
    if not name:
        return None
    bc = item.get("nearest_beach_cached")
    if isinstance(bc, dict):
        beach_name = str(bc.get("name") or "").strip()
        beach_km = _as_float(bc.get("km"))
    else:
        beach_name = str(
            item.get("beach_name")
            or item.get("nearest_beach_name")
            or item.get("beach")
            or item.get("nearest_beach")
            or ""
        ).strip()
        beach_km = _as_float(
            item.get("beach_km")
            or item.get("nearest_beach_km")
            or item.get("beach_distance_km")
            or item.get("distance_to_beach_km")
        )
    sc = item.get("nearest_supermarket_cached")
    if isinstance(sc, dict):
        supermarket_name = str(sc.get("name") or "").strip()
        supermarket_km = _as_float(sc.get("km"))
    else:
        supermarket_name = str(
            item.get("supermarket_name")
            or item.get("nearest_supermarket_name")
            or item.get("supermarket")
            or item.get("nearest_supermarket")
            or ""
        ).strip()
        supermarket_km = _as_float(
            item.get("supermarket_km")
            or item.get("nearest_supermarket_km")
            or item.get("supermarket_distance_km")
            or item.get("distance_to_supermarket_km")
        )
    photo = str(item.get("photo_url_override") or item.get("photo_url_cached") or item.get("photo_url") or "").strip()
    try:
        total = float(item.get("total_score") or 0)
    except (TypeError, ValueError):
        total = 0.0
    row: dict[str, Any] = {
        "name": name,
        "region_label": location,
        "address": str(item.get("address") or ""),
        "rating": None,
        "reviews": None,
        "website": str(
            item.get("website") or
            item.get("website_url") or
            item.get("websiteUrl") or
            item.get("url") or
            ""
        ).strip(),
        "maps_url": str(
            item.get("maps_url") or
            item.get("googleMapsUrl") or
            item.get("google_maps_url") or
            ""
        ).strip(),
        "beach_km": beach_km,
        "shops_km": None,
        "price_raw": None,
        "price_level": None,
        "park_lat": _as_float(item.get("lat")),
        "park_lng": _as_float(item.get("lng")),
        "_apify_place_id": str(item.get("google_place_id") or ""),
        "rationale_honourable": normalize_text_paragraphs(item.get("rationale_honourable") or ""),
        "rationale_top3": normalize_text_paragraphs(item.get("rationale_top3") or ""),
        "description": normalize_text_paragraphs(item.get("description") or ""),
        "executive_summary": str(item.get("executive_summary") or ""),
        "summary": normalize_text_paragraphs(
            (item.get("rationale_honourable") or item.get("summary") or item.get("description") or "")
            if honourable_summary
            else (
                item.get("rationale_top3")
                or item.get("summary")
                or item.get("description")
                or item.get("rationale_honourable")
                or ""
            )
        ),
        "rank_score": total,
        "family_score": item.get("total_score"),
        "classification": str(item.get("classification") or ""),
        "water_fun": str(item.get("water_fun") or ""),
        "kids_play": str(item.get("kids_play") or ""),
        "pet_detail": str(item.get("pet_detail") or ""),
        "key_phrases": item.get("key_phrases") if isinstance(item.get("key_phrases"), list) else [],
        "amenity_badges": [],
        "best_for": str(item.get("best_for") or item.get("best_suited_for") or ""),
        "top_scoring_criteria": item.get("top_scoring_criteria") or [],
        "_raw_place": {},
        "google_photo_url": photo,
        "google_amenities": {"pool": False, "playground": False, "pets": False},
        "supermarket_name": supermarket_name,
        "supermarket_km": supermarket_km,
        "beach_name": beach_name,
    }
    po_override = str(item.get("photo_url_override") or "").strip()
    if po_override:
        row["photo_url_override"] = po_override
    pc_cached = str(item.get("photo_url_cached") or "").strip()
    if pc_cached:
        row["photo_url_cached"] = pc_cached
    n_b_raw = item.get("nearest_beach_cached")
    if isinstance(n_b_raw, dict):
        row["nearest_beach_cached"] = dict(n_b_raw)
    n_s_raw = item.get("nearest_supermarket_cached")
    if isinstance(n_s_raw, dict):
        row["nearest_supermarket_cached"] = dict(n_s_raw)
    row["rating"] = (
        item.get("google_rating")
        or item.get("rating")
        or item.get("totalScore")
    )
    row["reviews"] = (
        item.get("review_count")
        or item.get("reviews")
        or item.get("reviewsCount")
    )
    return row


def load_ranked_rows_from_scores(path: Path, *, location: str) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        r = scores_item_to_page_row(item, location=location)
        if r:
            rows.append(r)
    rows.sort(key=lambda x: float(x.get("rank_score") or 0), reverse=True)
    return rows


def select_top3_from_scores(
    sorted_rows: list[dict[str, Any]], location_cfg: dict[str, Any]
) -> list[dict[str, Any]]:
    if not sorted_rows:
        return []
    override = location_cfg.get("top3_override") if isinstance(location_cfg, dict) else None
    if isinstance(override, list) and override:
        by_lower: dict[str, dict[str, Any]] = {}
        for r in sorted_rows:
            k = str(r.get("name") or "").strip().lower()
            if k:
                by_lower[k] = r
        picked: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_nm in override:
            k = str(raw_nm).strip().lower()
            r = by_lower.get(k)
            if r is None:
                log_err(f"locations-config top3_override: no score row matching {raw_nm!r}")
                continue
            nm = str(r.get("name") or "").strip()
            if nm not in seen:
                picked.append(r)
                seen.add(nm)
        for r in sorted_rows:
            if len(picked) >= 3:
                break
            nm = str(r.get("name") or "").strip()
            if nm not in seen:
                picked.append(r)
                seen.add(nm)
        top3 = picked[:3]
        for row in top3:
            log(f"[debug] {row.get('name')} rating={row.get('rating')} reviews={row.get('reviews')}")
        return top3
    top3 = sorted_rows[:3]
    for row in top3:
        log(f"[debug] {row.get('name')} rating={row.get('rating')} reviews={row.get('reviews')}")
    return top3


def update_scores_places_cache(scores_path: Path, rows: list[dict[str, Any]]) -> None:
    if not scores_path.exists():
        return
    try:
        raw = json.loads(scores_path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(raw, list):
        return
    idx_by_name: dict[str, int] = {}
    for i, item in enumerate(raw):
        if isinstance(item, dict):
            pn = str(item.get("park_name") or item.get("name") or "").strip()
            if pn:
                idx_by_name[pn] = i
    changed = False
    for row in rows:
        pn = str(row.get("name") or "").strip()
        if pn not in idx_by_name:
            continue
        item = raw[idx_by_name[pn]]
        if not isinstance(item, dict):
            continue
        # Never overwrite manually set photo overrides
        if item.get("photo_url_override"):
            item["photo_url_cached"] = item["photo_url_override"]
            changed = True
        else:
            new_photo = str(row.get("google_photo_url") or "").strip()
            if new_photo.startswith("http"):
                item["photo_url_cached"] = new_photo
                changed = True
        bn = str(row.get("beach_name") or "").strip()
        bk = row.get("beach_km")
        if bn or bk is not None:
            item["nearest_beach_cached"] = {"name": bn, "km": bk}
            changed = True
        sn = str(row.get("supermarket_name") or "").strip()
        sk = row.get("supermarket_km")
        if sn or sk is not None:
            item["nearest_supermarket_cached"] = {"name": sn, "km": sk}
            changed = True
        if row.get("rating") is not None:
            item["google_rating"] = row["rating"]
            changed = True
        if row.get("reviews") is not None:
            item["review_count"] = row["reviews"]
            changed = True
    if not changed:
        return
    tmp = scores_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, scores_path)
    log(f"[Google Places] Wrote photo_url_cached / nearest_beach_cached / nearest_supermarket_cached to {scores_path.name}")


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
        row = scores_item_to_page_row(item, location=location, honourable_summary=True)
        if not row:
            continue
        name = str(row.get("name") or "").strip()
        if name in excluded_names:
            continue
        try:
            total = float(row.get("rank_score") or 0)
        except (TypeError, ValueError):
            total = 0.0
        if total < 55:
            continue
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
            "key_phrases": item.get("key_phrases") if isinstance(item.get("key_phrases"), list) else [],
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
        return scores_item_to_page_row(item, location=location, honourable_summary=False)
    return None


def enrich_honourables_google(
    rows: list[dict[str, Any]], api_key: str, *, location: str, refresh_places: bool = False
) -> None:
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue

        # Skip if all data already cached
        if not refresh_places:
            photo_cached = str(row.get("photo_url_override") or row.get("photo_url_cached") or row.get("google_photo_url") or "").strip()
            beach_cached = row.get("nearest_beach_cached")
            super_cached = row.get("nearest_supermarket_cached")
            has_photo = photo_cached.startswith(("http", "/images/"))
            has_beach = isinstance(beach_cached, dict) and beach_cached.get("name") and beach_cached.get("km") is not None
            has_super = isinstance(super_cached, dict) and super_cached.get("name") and super_cached.get("km") is not None
            if has_photo and has_beach and has_super:
                log(f"[Google Places] Skipping honourable {name[:48]} — all data cached.")
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
            pics = detail.get("photos")
            ref = None
            if isinstance(pics, list) and pics and isinstance(pics[0], dict):
                ref = pics[0].get("photo_reference")
            google_places_photo_url = ""
            if isinstance(ref, str) and ref:
                google_places_photo_url = google_build_photo_url(api_key, ref)
            # Never overwrite manually set photo overrides
            if row.get("photo_url_override"):
                row["google_photo_url"] = row["photo_url_override"]
                row["photo_url_cached"] = row["photo_url_override"]
            else:
                row["google_photo_url"] = google_places_photo_url
                if google_places_photo_url:
                    row["photo_url_cached"] = google_places_photo_url
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


def load_location_master(loc_dir: Path) -> dict:
    """Load location-level copy fields from locations/{state}/{slug}/master.json."""
    master_path = loc_dir / "master.json"
    if master_path.exists():
        try:
            data = json.loads(master_path.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
    return {}


def save_location_master(loc_dir: Path, data: dict) -> None:
    """Atomically save location-level master.json."""
    master_path = loc_dir / "master.json"
    tmp = master_path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, master_path)


def load_park_master(project_dir: Path, park_name: str) -> dict:
    """Load master record from parks/{slug}/master.json registry."""
    def slugify(name: str) -> str:
        name = name.lower().strip()
        name = re.sub(r'[^a-z0-9\s-]', '', name)
        name = re.sub(r'[\s]+', '-', name)
        name = re.sub(r'-+', '-', name)
        return name.strip('-')
    slug = slugify(park_name)
    master_file = project_dir / "parks" / slug / "master.json"
    if master_file.exists():
        try:
            return json.loads(master_file.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {}


def main() -> int:
    if callable(load_dotenv):
        load_dotenv()
    args = parse_args()
    project_dir = Path(__file__).resolve().parent
    location = str(args.location).strip()
    if not location:
        log_err("Error: location must be non-empty.")
        return 1

    loc_dir = get_location_dir(project_dir, location)
    slug = output_slug_for_location(project_dir, location, loc_dir)
    public_dir = project_dir / "public"
    public_dir.mkdir(parents=True, exist_ok=True)
    output_path = public_dir / f"{slug}.html"
    init_location_dir(loc_dir)

    # Check if a reviewed/locked version exists for this location
    loc_slug = loc_dir.name
    review_file = project_dir / "reviews" / f"{loc_slug}.txt"
    location_is_reviewed = review_file.exists()
    if location_is_reviewed:
        log(f"✅ Reviewed location — using locked copy from reviews/{loc_slug}.txt")

    scores_path = loc_dir / "scores.json"
    local_knowledge_cache = loc_dir / "local-knowledge.txt"
    destination_summary_cache = loc_dir / "destination-summary.txt"
    faq_cache = loc_dir / "faq.json"
    photos_path = loc_dir / "photos.json"
    prices_path = loc_dir / "prices.json"
    websites_path = loc_dir / "websites.json"

    # Load location-level master.json (primary copy source)
    loc_master = load_location_master(loc_dir)

    # Load config from location folder, fall back to legacy locations-config.json
    loc_cfg: dict[str, Any] = {}
    config_path = loc_dir / "config.json"
    if config_path.exists():
        try:
            raw_cfg = json.loads(config_path.read_text(encoding="utf-8"))
            loc_cfg = raw_cfg if isinstance(raw_cfg, dict) and raw_cfg else {}
        except Exception:
            loc_cfg = {}
    if not loc_cfg:
        loc_cfg = load_location_config(project_dir / "locations-config.json", slug)
    # master.json heading/hero_image take precedence over config.json
    if loc_master.get("heading"):
        loc_cfg["hero_headline"] = loc_master["heading"]
    if loc_master.get("hero_image"):
        loc_cfg["hero_image"] = loc_master["hero_image"]

    index_path = (project_dir / args.index).resolve()
    hero_cache = loc_dir / "hero-tagline.txt"
    hero_intro_file = loc_dir / "hero-intro.txt"

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
    manual_prices = load_manual_prices(prices_path)
    manual_photos = load_manual_photos(photos_path)
    if scores_path.exists():
        log(
            f"Using {scores_path.name} for featured parks (sorted by total_score; "
            "optional top3_override from locations-config.json)."
        )
        all_score_rows = load_ranked_rows_from_scores(scores_path, location=location)
        if not all_score_rows:
            log_err(f"{scores_path.name} exists but contains no usable park rows.")
            return 1
        ranked = select_top3_from_scores(all_score_rows, loc_cfg)
        if len(ranked) < 3:
            log_err(
                f"Warning: only {len(ranked)} park(s) available for the featured top section. "
                "Add more scores or set top3_override in locations-config.json."
            )
        excluded = {str(r.get("name") or "").strip() for r in ranked if str(r.get("name") or "").strip()}
        honourables = load_honourable_mentions_from_scores(
            scores_path,
            location=location,
            excluded_names=excluded,
        )
        log(
            f"Loaded honourable mentions from {scores_path.name}: {len(honourables)} (score >= 55 and not in top 3)."
        )
        park_count = len(all_score_rows)
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

        park_count = len(ranked)

    google_maps_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    maps_embed_url = ""
    if google_maps_key:
        log("Enriching top 3 parks via Google Places (details, supermarkets, beaches, map embed)...")
        try:
            maps_embed_url = enrich_top_three_parks_google(
                ranked,
                google_maps_key,
                location=location,
                refresh_places=args.refresh_places,
            )
            if honourables:
                enrich_honourables_google(
                    honourables,
                    google_maps_key,
                    location=location,
                    refresh_places=args.refresh_places,
                )
            if scores_path.exists():
                update_scores_places_cache(scores_path, ranked[:3] + honourables)
        except Exception as e:
            log_err(f"Google Places enrichment error (continuing with partial data): {e}")
            maps_embed_url = ""
    else:
        log("GOOGLE_MAPS_API_KEY not set; skipping Google Places enrichment.")

    all_parks = ranked + honourables
    for park in all_parks:
        park_name = park.get('park_name') or park.get('name') or ''
        master = load_park_master(project_dir, park_name)
        if master:
            # Park-level fields — master wins
            for field in [
                'total_score', 'top_scoring_criteria',
                'rationale_top3', 'rationale_honourable',
                'executive_summary', 'water_fun', 'kids_play',
                'pet_detail', 'best_for', 'wifi_available', 'pet_friendly',
                'website', 'lat', 'lng', 'photo_url_cached',
                'nearest_beach_cached', 'nearest_supermarket_cached',
                'google_rating', 'review_count',
            ]:
                if master.get(field) is not None:
                    park[field] = master[field]
            # Prices and deals — master only
            prices = master.get('prices', {})
            park['powered_weekday'] = _parse_price(prices.get('powered_weekday')) or '—'
            park['deals'] = master.get('deals') or '—'
        else:
            park.setdefault('powered_weekday', '—')
            park.setdefault('deals', '—')
        # classification stays from scores.json — location specific

    if_we_were_booking = loc_master.get("if_we_were_booking") or ""
    if if_we_were_booking:
        log("Loaded If We Were Booking: master.json")

    destination_summary = loc_master.get("destination_summary") or ""
    if destination_summary:
        log("Loaded Destination Summary: master.json")
    elif destination_summary_cache.exists():
        try:
            destination_summary = destination_summary_cache.read_text(encoding="utf-8").strip()
            log(f"Loaded cached Destination Summary: {destination_summary_cache.name}")
        except OSError as e:
            log_err(f"Warning: failed to read Destination Summary cache ({e}).")

    _use_cache = location_is_reviewed or not args.fresh_copy
    intro_paragraph = (loc_master.get("local_knowledge") or "") if _use_cache else ""
    if intro_paragraph:
        log("Loaded Local Knowledge: master.json")
    elif _use_cache and local_knowledge_cache.exists():
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
                loc_master["local_knowledge"] = intro_paragraph
                save_location_master(loc_dir, loc_master)
                log(f"Saved Local Knowledge cache: {local_knowledge_cache.name}")
            except RuntimeError as e:
                log_err(f"Warning: Claude intro failed ({e}); continuing without Local Knowledge.")
            except Exception as e:
                log_err(f"Warning: Claude intro failed ({e}); continuing without Local Knowledge.")
        else:
            log("No ANTHROPIC_API_KEY set; using cached/no Local Knowledge copy.")

    hero_tagline = (loc_master.get("hero_tagline") or "") if _use_cache else ""
    if hero_tagline:
        log("Loaded hero tagline: master.json")
    elif _use_cache and hero_cache.exists():
        try:
            hero_tagline = hero_cache.read_text(encoding="utf-8").strip()
            if hero_tagline:
                loc_master["hero_tagline"] = hero_tagline
                save_location_master(loc_dir, loc_master)
            log(f"Loaded cached hero tagline: {hero_cache.name}")
        except OSError as e:
            log_err(f"Warning: failed to read hero cache ({e}); regenerating.")
    if not hero_tagline:
        if anthropic_key:
            log("Calling Claude API for hero tagline...")
            try:
                hero_tagline = fetch_claude_hero_tagline(anthropic_key, location=location)
                hero_cache.write_text(hero_tagline, encoding="utf-8")
                loc_master["hero_tagline"] = hero_tagline
                save_location_master(loc_dir, loc_master)
                log(f"Saved hero tagline cache: {hero_cache.name}")
            except RuntimeError as e:
                log_err(f"Warning: Claude hero tagline failed ({e}); using fallback line.")
            except Exception as e:
                log_err(f"Warning: Claude hero tagline failed ({e}); using fallback line.")
        else:
            log("No ANTHROPIC_API_KEY set; using cached/no hero tagline copy.")

    hero_intro = (loc_master.get("hero_intro") or "") if _use_cache else ""
    if hero_intro:
        log("Loaded hero intro: master.json")
    elif _use_cache and hero_intro_file.exists():
        hero_intro = hero_intro_file.read_text(encoding="utf-8").strip()
        log("Loaded cached hero intro: hero-intro.txt")
    if not hero_intro:
        bare_location = re.sub(r'\s+(QLD|NSW|VIC|SA|WA|TAS|NT|ACT)$', '', location, flags=re.IGNORECASE).strip()
        parks = ranked

        def get_park_name(p):
            return p.get('park_name') or p.get('name') or p.get('title') or ''

        top_park_names = ', '.join([get_park_name(p) for p in parks[:3] if get_park_name(p)])
        hero_intro_prompt = f"""Write a 2-sentence hero intro for a family holiday park review page.

Location: {bare_location}
Top parks: {top_park_names}

RULES:
- Exactly 2 sentences. No more.
- Do NOT use: "nestled", "boasts", "perfect getaway", "offers many attractions", "kilometres of coastline", "With X kilometres"
- Do NOT repeat the page title
- Do NOT mention park counts or scores

Sentence 1: Start with "Planning a family trip to {bare_location}?" then explain what this page does — use "best family holiday parks in {bare_location}" and end with "skip hours of research and get straight to the good part"

Sentence 2: Start with "Families love {bare_location} because" then give 2-3 specific emotional/practical reasons families visit. Be specific to this location. Mention real things kids care about.

Voice: parent-to-parent, Australian, warm, specific
Return plain text only. No HTML. No markdown."""

        hero_intro = call_claude_api(anthropic_key, hero_intro_prompt).strip()
        hero_intro_file.write_text(hero_intro, encoding="utf-8")
        loc_master["hero_intro"] = hero_intro
        save_location_master(loc_dir, loc_master)
        log("Generated and cached hero intro: hero-intro.txt")

    if len(ranked) >= 3:
        bf_labels = compute_best_for_labels(ranked[:3])
        for i in range(3):
            if not str(ranked[i].get("best_for") or "").strip():
                ranked[i]["best_for"] = bf_labels[i]

    if len(ranked) < 3:
        log_err("Warning: fewer than 3 parks matched — comparison table will show available parks only.")

    faq_entries: list[dict[str, str]] = []
    existing_faqs: list[dict[str, str]] = []
    faq_targets_path = loc_dir / "faq_targets.json"
    faq_targets = None
    if faq_targets_path.exists():
        try:
            faq_targets = json.loads(faq_targets_path.read_text(encoding="utf-8"))
            log("Loaded FAQ targets from faq_targets.json")
        except Exception:
            pass

    # master.json FAQ takes precedence (it was parsed from the review txt, not generated)
    if _use_cache and loc_master.get("faq"):
        faq_entries = [x for x in loc_master["faq"] if isinstance(x, dict)]
        log("Loaded FAQ: master.json")

    already_from_targets = False
    if not faq_entries and _use_cache and faq_cache.exists():
        try:
            loaded_faq = json.loads(faq_cache.read_text(encoding="utf-8"))
            if isinstance(loaded_faq, dict) and "faqs" in loaded_faq:
                existing_faqs = loaded_faq["faqs"]
                already_from_targets = loaded_faq.get("generated_from_targets", False)
            elif isinstance(loaded_faq, list):
                existing_faqs = loaded_faq
                already_from_targets = False

            if faq_targets and not already_from_targets:
                log("FAQ targets found — regenerating from targets")
            else:
                faq_entries = list(existing_faqs)
                log(f"Loaded cached FAQ: {faq_cache.name}")
        except Exception as e:
            log_err(f"Warning: failed to read FAQ cache ({e}); regenerating.")
    if not faq_entries:
        if faq_targets and anthropic_key:
            location_name = str(loc_cfg.get("hero_headline") or location).strip() or location
            local_knowledge = intro_paragraph
            parks = ranked
            if scores_path.exists():
                try:
                    loaded_scores = json.loads(scores_path.read_text(encoding="utf-8"))
                    if isinstance(loaded_scores, list):
                        parks = sorted(
                            [x for x in loaded_scores if isinstance(x, dict)],
                            key=lambda p: float(p.get("total_score") or 0),
                            reverse=True,
                        )
                except Exception:
                    pass
            all_questions = (
                faq_targets.get("high_priority", [])[:4]
                + faq_targets.get("medium_priority", [])[:3]
                + faq_targets.get("long_tail", [])[:2]
            )
            top_parks_summary = "\n".join([
                f"- {p.get('park_name') or p.get('name', '')} (score: {p.get('total_score') or p.get('rank_score', '')}/100)"
                for p in parks[:5]
            ])
            faq_prompt = f"""You are writing FAQ content for a family holiday park review website.

Location: {location_name}
Top rated parks: {", ".join([p.get('park_name','') for p in parks[:5]])}
Local knowledge: {local_knowledge[:500] if local_knowledge else "Not available"}

For each keyword phrase below, do TWO things:
1. Convert it into a natural question a parent would actually type or ask (e.g. "noosa accommodation with kids" → "What's the best accommodation in Noosa for families with kids?")
2. Write a practical 40-80 word answer using a warm Australian tone. Mention specific park names and local tips where relevant. Do NOT mention scores or numbers. Never say "I" or "we".

Keyword phrases:
{json.dumps(all_questions, ensure_ascii=False)}

Return a JSON array only, no other text:
[{{"question": "natural question here", "answer": "practical answer here"}}]
"""
            try:
                faq_response = call_claude_api(anthropic_key, faq_prompt)
                clean = faq_response.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
                faqs = json.loads(clean)
                log(f"Generated {len(faqs)} FAQ answers from targets.")
                faq_entries = [x for x in faqs if isinstance(x, dict)]
                faq_cache_data = {
                    "generated_from_targets": True,
                    "faqs": faq_entries,
                }
                faq_cache.write_text(json.dumps(faq_cache_data, indent=2, ensure_ascii=False), encoding="utf-8")
                log(f"Saved FAQ cache: {faq_cache.name}")
            except Exception as e:
                log(f"FAQ parse error: {e} — falling back to cached FAQ")
                faq_entries = existing_faqs
        elif anthropic_key:
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

    # Read why_families: master.json first, fall back to why-families.txt
    why_families_items: list[str] = (loc_master.get("why_families") or []) if _use_cache else []
    if not why_families_items:
        wf_file = loc_dir / "why-families.txt"
        if wf_file.exists():
            why_families_items = [
                ln.strip() for ln in wf_file.read_text(encoding="utf-8").splitlines() if ln.strip()
            ]

    # Read activities: master.json first, fall back to activities.json
    activities_items: list[dict] = (loc_master.get("activities") or []) if _use_cache else []
    if not activities_items:
        act_path = loc_dir / "activities.json"
        if act_path.exists():
            try:
                raw_act = json.loads(act_path.read_text(encoding="utf-8"))
                if isinstance(raw_act, list):
                    activities_items = [
                        a for a in raw_act
                        if isinstance(a, dict) and str(a.get("name") or "").strip()
                    ]
            except Exception:
                pass

    index_html = index_path.read_text(encoding="utf-8")
    apply_manual_prices(ranked, manual_prices)
    apply_manual_photos(ranked, manual_photos)
    apply_manual_photos(honourables, manual_photos)
    apply_manual_prices(honourables, manual_prices)
    park_websites = load_park_websites(websites_path)
    apply_park_websites(ranked, park_websites)
    apply_park_websites(honourables, park_websites)
    if google_maps_key:
        backfill_missing_coords(ranked[:3], api_key=google_maps_key, location=location)
        backfill_missing_coords(honourables, api_key=google_maps_key, location=location)
    document = build_page_html(
        index_html=index_html,
        rows=ranked,
        honourables=honourables,
        location=location,
        intro_paragraph=intro_paragraph,
        destination_summary=destination_summary,
        if_we_were_booking=if_we_were_booking,
        hero_tagline=hero_tagline,
        hero_intro=hero_intro,
        maps_api_key=google_maps_key,
        faq_entries=faq_entries,
        park_count=park_count,
        project_dir=project_dir,
        loc_dir=loc_dir,
        loc_config=loc_cfg,
        manual_prices=manual_prices,
        why_families=why_families_items,
        activities=activities_items,
    )

    try:
        output_path.write_text(document, encoding="utf-8")
    except OSError as e:
        log_err(f"Failed to write HTML: {e}")
        return 1

    log(f"Saved: {output_path}")

    if args.publish:
        git_commit_and_push(
            project_dir,
            message=f"Add generated holiday parks page for {location}",
        )
    else:
        log("Skipping git commit/push (use --publish to commit and push).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
