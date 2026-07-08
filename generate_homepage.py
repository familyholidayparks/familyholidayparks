#!/usr/bin/env python3
"""
generate_homepage.py — Airbnb-style location directory homepage
Each card = one location. Photo from hero-image.txt. Data from scores.json.

Usage:
  python generate_homepage.py
  python generate_homepage.py --publish
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from html import escape as esc

PROJECT = Path(__file__).resolve().parent
PROJECT_DIR = PROJECT
LOCATIONS_CSV = PROJECT / "locations.csv"
LOCATION_COORDS_CSV = PROJECT / "location_coords.csv"
LOCATIONS_DIR = PROJECT / "locations"
OUTPUT = PROJECT / "public" / "index.html"
REVIEWS_DIR = PROJECT / "reviews"

STATE_NAMES = {
    "qld": "Queensland", "nsw": "New South Wales", "vic": "Victoria",
    "wa": "Western Australia", "sa": "South Australia", "tas": "Tasmania",
    "nt": "Northern Territory", "act": "ACT",
}
STATE_ORDER = ["qld", "nsw", "vic", "wa", "sa", "tas", "nt", "act"]
STATE_URL = {
    "qld": "queensland", "nsw": "new-south-wales", "vic": "victoria",
    "wa": "western-australia", "sa": "south-australia", "tas": "tasmania",
    "nt": "northern-territory", "act": "act",
}


def load_locations():
    """Load all locations from CSV + enrich from scores.json + hero-image.txt."""
    locations = []

    if not LOCATIONS_CSV.exists():
        print("ERROR: locations.csv not found")
        return locations

    with open(LOCATIONS_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            loc_name = row.get("location", "").strip()
            state = row.get("state", "").strip().lower()
            slug = row.get("slug", "").strip()
            if not (loc_name and state and slug):
                continue

            loc_dir = LOCATIONS_DIR / state / slug
            scores_file = loc_dir / "scores.json"
            if not scores_file.exists():
                continue

            try:
                parks = json.loads(scores_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            if not parks:
                continue

            # ── Stats from scores.json ──
            park_count = len(parks)
            total_reviews = 0
            prices = []
            for p in parks:
                # Reviews
                rv = p.get("review_count") or p.get("reviewCount") or 0
                try:
                    total_reviews += int(rv)
                except (TypeError, ValueError):
                    pass
                # Prices — check prices dict, direct keys, and master.json
                price_data = p.get("prices") or {}
                found = False
                for key in ["powered_weekday", "powered", "unpowered_weekday", "unpowered"]:
                    val = price_data.get(key) or p.get(key) or ""
                    if val:
                        nums = re.findall(r"\d+", str(val))
                        if nums:
                            prices.append(int(nums[0]))
                            found = True
                            break
                # Fallback: read master.json for this park
                if not found:
                    park_slug = p.get("slug", "")
                    if not park_slug:
                        n = p.get("park_name", "").lower()
                        n = "".join(c if c.isalnum() or c == " " else "" for c in n)
                        park_slug = "-".join(n.split())
                    mf = PROJECT / "parks" / park_slug / "master.json"
                    if mf.exists():
                        try:
                            md = json.loads(mf.read_text(encoding="utf-8"))
                            pd2 = md.get("prices") or {}
                            for key in ["powered_weekday", "powered", "unpowered_weekday", "unpowered"]:
                                val = pd2.get(key) or md.get(key) or ""
                                if val:
                                    nums = re.findall(r"\d+", str(val))
                                    if nums:
                                        prices.append(int(nums[0]))
                                        break
                        except Exception:
                            pass

            min_price = min(prices) if prices else None

            # ── Hero image — read from config.json hero_image ──
            hero_img = ""
            config_file = loc_dir / "config.json"
            if config_file.exists():
                try:
                    config = json.loads(config_file.read_text(encoding="utf-8"))
                    hero_img = config.get("hero_image", "").strip()
                except Exception:
                    pass
            # Fallback: best park photo from scores.json
            if not hero_img:
                for p in sorted(parks, key=lambda x: x.get("total_score", 0), reverse=True):
                    photo = p.get("photo_url_override") or p.get("photo_url_cached") or ""
                    if photo:
                        hero_img = photo
                        break

            # ── Page URL ──
            state_full = STATE_URL.get(state, state)
            page_url = f"{slug}-{state_full}"

            locations.append({
                "name": loc_name,
                "state": state,
                "state_name": STATE_NAMES.get(state, state.upper()),
                "slug": slug,
                "page_url": page_url,
                "park_count": park_count,
                "total_reviews": total_reviews,
                "min_price": min_price,
                "hero_img": hero_img,
            })

    return locations


def _truncate_blurb(text, max_len=100):
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0]
    return cut if cut else text[:max_len]


def _extract_hero_intro(content):
    if "HERO INTRO:" not in content:
        return ""
    after = content.split("HERO INTRO:", 1)[1]
    section_end = re.search(r"\n[A-Z][A-Z0-9 /\-]+:\s*\n", after)
    if section_end:
        after = after[: section_end.start()]
    parts = []
    for line in after.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts.append(stripped)
    return _truncate_blurb(" ".join(parts), 100)


def _load_location_blurb(slug, state_lower):
    for name in (f"{slug}-{state_lower}.txt", f"{slug}.txt"):
        path = REVIEWS_DIR / name
        if not path.exists():
            continue
        try:
            blurb = _extract_hero_intro(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if blurb:
            return blurb
    return ""


def _park_sort_score(p):
    try:
        return float(p.get("total_score") or p.get("family_score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _resolve_card_photo(top_park, loc, slug, state_lower):
    """Resolve card photo mirroring apply_manual_photos() priority in generate_page.py:
    1. photo_url_override starting with /images/ (local downloaded file, highest priority)
    2. photo_url_override as an HTTP URL
    3. photos.json entry (only if no local file exists)
    4. photo_url_cached
    5. config.json hero_image
    6. hero_img from load_locations fallback
    """
    top_name = (top_park.get("park_name") or top_park.get("name") or "Unknown").strip()

    override = str(top_park.get("photo_url_override") or "").strip()

    # 1. Local downloaded file — highest priority
    if override.startswith("/images/"):
        return override, "photo_url_override", top_name

    # 2. Remote photo_url_override
    if override.startswith("http"):
        return override, "photo_url_override", top_name

    # 3. photos.json — only reached if no local file and no override URL
    photos_file = PROJECT_DIR / "locations" / state_lower / slug / "photos.json"
    if photos_file.exists():
        try:
            photos = json.loads(photos_file.read_text(encoding="utf-8"))
            name_lc = top_name.lower()
            photos_url = str(next((v for k, v in photos.items() if k.lower() == name_lc), "") or "").strip()
            if photos_url:
                return photos_url, "photo_url_override", top_name
        except Exception:
            pass

    # 4. photo_url_cached
    cached = str(top_park.get("photo_url_cached") or "").strip()
    if cached:
        return cached, "photo_url_cached", top_name

    # 5. config.json hero_image
    config_file = PROJECT_DIR / "locations" / state_lower / slug / "config.json"
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text(encoding="utf-8"))
            config_hero = (config.get("hero_image") or "").strip()
            if config_hero:
                return config_hero, "hero_image", top_name
        except Exception:
            pass

    # 6. hero_img from load_locations
    hero_img = (loc.get("hero_img") or "").strip()
    if hero_img:
        return hero_img, "hero_image", top_name

    return "", "none", top_name


def build_map_and_card_locations(all_locations):
    """Build enriched location data for map pins and vertical cards."""
    location_coords = {}
    if LOCATION_COORDS_CSV.exists():
        with open(LOCATION_COORDS_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                slug_key = (row.get("slug") or "").strip()
                if not slug_key:
                    continue
                try:
                    lat_val = float(row.get("lat") or 0)
                    lng_val = float(row.get("lng") or 0)
                    if lat_val and lng_val:
                        location_coords[slug_key] = (lat_val, lng_val)
                except (TypeError, ValueError):
                    pass

    map_locations = []
    card_locations = []
    photo_debug_lines = []
    missing_photos = 0

    for loc in all_locations:
        slug = loc.get("slug", "")
        location_name = loc.get("name", "")
        state = loc.get("state", "")

        state_lower = state.lower()
        scores_path = PROJECT_DIR / "locations" / state_lower / slug / "scores.json"

        parks = []
        if scores_path.exists():
            try:
                data = json.loads(scores_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    parks = sorted(data, key=_park_sort_score, reverse=True)
            except Exception:
                pass

        if not parks:
            continue

        top_park = parks[0] if parks else {}
        hero_image, photo_field, top_park_name = _resolve_card_photo(
            top_park, loc, slug, state_lower
        )
        photo_debug_lines.append(
            f"[homepage] card photo source: {location_name} -> {top_park_name} -> {photo_field}"
        )
        if not hero_image:
            missing_photos += 1

        scores = []
        for p in parks[:3]:
            try:
                scores.append(float(p.get("total_score") or p.get("family_score") or 0))
            except (TypeError, ValueError):
                pass
        avg_score = int(sum(scores) / len(scores)) if scores else 0

        total_reviews = 0
        for p in parks:
            try:
                total_reviews += int(float(p.get("review_count") or 0))
            except (TypeError, ValueError):
                pass

        min_price = 9999
        for p in parks:
            pw = p.get("powered_weekday") or (p.get("prices") or {}).get("powered_weekday") or ""
            nums = re.findall(r"\d+", str(pw))
            if nums:
                try:
                    price = int(nums[0])
                    if price < min_price:
                        min_price = price
                except (TypeError, ValueError):
                    pass
        if min_price == 9999 and loc.get("min_price"):
            min_price = loc["min_price"]
        price_str = f"From ${min_price}/night" if min_price < 9999 else ""

        lat = None
        lng = None
        for p in parks:
            try:
                plat = float(p.get("lat") or 0)
                plng = float(p.get("lng") or 0)
                if plat and plng:
                    lat = plat
                    lng = plng
                    break
            except (TypeError, ValueError):
                pass

        if (not lat or not lng) and slug in location_coords:
            lat, lng = location_coords[slug]

        state_full = STATE_URL.get(state.lower(), state.lower())
        url = f"/{slug}-{state_full}"

        blurb = _load_location_blurb(slug, state_lower)

        location_data = {
            "name": location_name,
            "state": loc.get("state_name") or STATE_NAMES.get(state.lower(), state.upper()),
            "slug": slug,
            "url": url,
            "hero": hero_image,
            "score": avg_score,
            "parks": len(parks),
            "reviews": total_reviews,
            "price": price_str,
            "blurb": blurb,
            "lat": lat,
            "lng": lng,
        }

        if lat and lng:
            map_locations.append(location_data)
        card_locations.append(location_data)

    card_locations.sort(key=lambda x: x["score"], reverse=True)
    return map_locations, card_locations, photo_debug_lines, missing_photos


def build_location_cards_html(card_locations):
    def _price_num(loc):
        m = re.search(r"(\d+)", str(loc.get("price") or ""))
        return int(m.group(1)) if m else 9999

    def _render_lcard(loc):
        _name = esc(loc["name"])
        _state = esc(loc["state"])
        _url = esc(loc["url"])
        _hero = loc["hero"]
        _score = loc["score"]
        _parks = loc["parks"]
        _reviews = f"{loc['reviews']:,}" if loc["reviews"] else ""
        _price = esc(loc["price"])
        _blurb = esc(loc["blurb"])

        _img = (
            f'<img src="{esc(_hero)}" alt="{_name}" loading="lazy">'
            if str(_hero).startswith(("http", "/"))
            else '<div class="lcard-img-ph"></div>'
        )
        _score_html = f'<span class="lcard-score">{_score}/100</span>' if _score else ""
        _reviews_html = (
            f'<span class="lcard-meta-item">{_reviews} reviews</span>' if _reviews else ""
        )
        _price_html = f'<span class="lcard-meta-item">{_price}</span>' if _price else ""
        _parks_html = f'<span class="lcard-meta-item">{_parks} parks</span>'

        return f'''<a class="lcard" href="{_url}" data-slug="{esc(loc["slug"])}">
  <div class="lcard-img-wrap">
    {_img}
    {_score_html}
  </div>
  <div class="lcard-body">
    <div class="lcard-header">
      <div>
        <div class="lcard-name">{_name}</div>
        <div class="lcard-state">{_state}</div>
      </div>
    </div>
    <div class="lcard-blurb">{_blurb}</div>
    <div class="lcard-meta">
      {_parks_html}
      {_reviews_html}
      {_price_html}
    </div>
  </div>
</a>'''

    def _render_compact(loc):
        _name = esc(loc["name"])
        _url = esc(loc["url"])
        _hero = loc["hero"]
        _score = loc["score"]
        _parks = loc["parks"]
        _reviews = loc["reviews"] or 0
        _price = esc(loc["price"])

        _img = (
            f'<img src="{esc(_hero)}" alt="{_name}" loading="lazy">'
            if str(_hero).startswith(("http", "/"))
            else '<div class="lcard-img-ph"></div>'
        )
        _score_html = f'<span class="compact-score">{_score}</span>' if _score else ""
        _meta_bits = [f"{_parks} parks"]
        if _reviews:
            _meta_bits.append(f"{_reviews:,} reviews")
        if _price:
            _meta_bits.append(_price)
        _meta = " · ".join(_meta_bits)

        return f'''<a class="lcard lcard--compact" href="{_url}" data-slug="{esc(loc["slug"])}" data-score="{loc["score"] or 0}" data-price="{_price_num(loc)}" data-reviews="{_reviews}" data-name="{_name}">
  <div class="compact-img">{_img}</div>
  <div class="compact-body">
    <div class="compact-name">{_name}</div>
    <div class="compact-meta">{_meta}</div>
  </div>
  {_score_html}
</a>'''

    sort_bar = '''<div class="sort-bar">
      <span class="sort-bar-label">Sort by</span>
      <button type="button" class="sort-pill active" onclick="sortState(this,'score')">Family score</button>
      <button type="button" class="sort-pill" onclick="sortState(this,'price')">Price</button>
      <button type="button" class="sort-pill" onclick="sortState(this,'reviews')">Most reviewed</button>
      <button type="button" class="sort-pill" onclick="sortState(this,'name')">A–Z</button>
    </div>'''

    state_key_by_name = {name: code for code, name in STATE_NAMES.items()}
    by_state = {code: [] for code in STATE_ORDER}
    for loc in card_locations:
        code = state_key_by_name.get(loc["state"])
        if code:
            by_state[code].append(loc)

    total = len(card_locations)
    total_label = f"{total} holiday park destination{'s' if total != 1 else ''}"
    location_cards_html = f'''<div class="locations-header">
  <span class="locations-total">{total_label}</span>
</div>'''

    for code in STATE_ORDER:
        locs = sorted(by_state[code], key=lambda x: x["score"], reverse=True)
        if not locs:
            continue

        state_label = STATE_NAMES[code]
        state_slug = STATE_URL[code]
        count = len(locs)
        count_label = f"{count} destination{'s' if count != 1 else ''}"

        location_cards_html += f'''<div class="state-group" data-state="{esc(state_slug)}">
  <div class="state-heading">
    <span class="state-label">{esc(state_label)}</span>
    <span class="state-count">{count_label}</span>
  </div>
  <div class="state-cards">'''

        for loc in locs[:3]:
            location_cards_html += _render_lcard(loc)

        location_cards_html += "</div>"

        if count > 3:
            location_cards_html += f'''<div class="state-overflow" style="display:none">
  {sort_bar}
  <div class="compact-list">'''
            for loc in locs:
                location_cards_html += _render_compact(loc)
            location_cards_html += f'''</div>
  </div>
  <button class="see-all-btn" onclick="toggleState(this)">
    See all {count} {esc(state_label)} destinations →
  </button>'''

        location_cards_html += "</div>"

    return location_cards_html


def build(*, google_maps_api_key: str = "", google_maps_map_id: str = ""):
    all_locations = load_locations()
    print(f"[homepage] locations loaded: {len(all_locations)}")

    map_locations, card_locations, photo_debug_lines, missing_photos = build_map_and_card_locations(
        all_locations
    )
    print(f"[homepage] map pins: {len(map_locations)}")
    for line in photo_debug_lines:
        print(line)
    print(f"[homepage] missing photos: {missing_photos}")

    map_locations_json = json.dumps(map_locations, ensure_ascii=False)
    location_cards_html = build_location_cards_html(card_locations)

    total_parks = sum(int(l.get("parks") or 0) for l in card_locations)
    total_reviews = sum(int(l.get("reviews") or 0) for l in card_locations)
    total_destinations = len(card_locations)
    reviews_label = f"{total_reviews:,}" if total_reviews else "300,000+"

    google_maps_api_key = (google_maps_api_key or "").strip()
    google_maps_map_id = (google_maps_map_id or "").strip()

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>Family Holiday Parks — Australia's Best Holiday Park Guide</title>
<meta name="description" content="Compare Australia's best family holiday parks. Ranked from 300,000+ real family reviews.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
:root {{
  --text: #222;
  --text-2: #717171;
  --border: #eee;
  --teal: #0072CE;
  --r: 12px;
  --nav-h: 60px;
}}
html, body {{
  font-family: 'Inter', -apple-system, sans-serif;
  background: #fff;
  color: var(--text);
  -webkit-font-smoothing: antialiased;
}}

/* NAV */
.nav {{
  position: sticky;
  top: 0;
  z-index: 100;
  background: #fff;
  border-bottom: 1px solid var(--border);
  height: var(--nav-h);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  gap: 16px;
}}
.nav-logo img {{
  height: 28px; width: auto; display: block;
}}
.nav-search {{
  flex: 1;
  display: flex; align-items: center; gap: 8px;
  background: #fff; border: 1px solid #ccc;
  border-radius: 100px; padding: 10px 16px;
  cursor: pointer;
  box-shadow: 0 1px 5px rgba(0,0,0,0.06);
  transition: border-color 0.15s, box-shadow 0.15s;
}}
.nav-search:hover {{ border-color: #999; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
.nav-search svg {{ flex-shrink: 0; }}
.nav-search span {{ font-size: 14px; color: var(--text-2); }}

/* HERO */
.hero {{
  padding: 30px 20px 18px;
  max-width: 1120px;
  margin: 0 auto;
}}
.hero h1 {{
  font-family: 'Fraunces', serif;
  font-size: 27px;
  font-weight: 700;
  line-height: 1.15;
  letter-spacing: -0.01em;
  color: var(--text);
  max-width: 560px;
}}
.hero p {{
  margin-top: 8px;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text-2);
  max-width: 560px;
}}
/* COMPACT EXPLORER (expanded state) */
.sort-bar {{
  display: flex;
  align-items: center;
  gap: 8px;
  overflow-x: auto;
  padding: 12px 20px 10px;
  scrollbar-width: none;
}}
.sort-bar::-webkit-scrollbar {{ display: none; }}
.sort-bar-label {{
  font-size: 12px;
  color: var(--text-2);
  flex-shrink: 0;
}}
.sort-pill {{
  flex-shrink: 0;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  color: var(--text);
  background: #fff;
  border: 1px solid #ccc;
  border-radius: 100px;
  padding: 6px 13px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}}
.sort-pill.active {{
  background: #222;
  border-color: #222;
  color: #fff;
}}
.compact-list {{
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 0 20px 8px;
}}
.lcard--compact {{
  flex-direction: row;
  align-items: center;
  gap: 12px;
  padding: 8px 12px 8px 8px;
  border-radius: 12px;
}}
.lcard--compact .compact-img {{
  width: 56px;
  height: 56px;
  border-radius: 10px;
  overflow: hidden;
  flex-shrink: 0;
  background: #f5f5f5;
}}
.lcard--compact .compact-img img {{
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}}
.lcard--compact .lcard-img-ph {{
  width: 100%;
  height: 100%;
}}
.compact-body {{
  flex: 1;
  min-width: 0;
}}
.compact-name {{
  font-size: 14px;
  font-weight: 700;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.compact-meta {{
  font-size: 12px;
  color: var(--text-2);
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.compact-score {{
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 700;
  color: var(--text);
  border: 1.5px solid #222;
  border-radius: 50%;
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
}}


/* STATE CHIPS */
.state-chips {{
  position: sticky;
  top: var(--nav-h);
  z-index: 90;
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding: 8px 20px 10px;
  background: #fff;
  border-bottom: 1px solid var(--border);
  scrollbar-width: none;
}}
.state-chips::-webkit-scrollbar {{ display: none; }}
.state-chips button {{
  flex-shrink: 0;
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  background: #f7f7f7;
  border: 1px solid var(--border);
  border-radius: 100px;
  padding: 8px 16px;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}}
.state-chips button:hover {{ background: #efefef; border-color: #ccc; }}
.state-chips button.active {{
  background: #222;
  border-color: #222;
  color: #fff;
}}

/* SEARCH OVERLAY */
.search-overlay {{
  position: fixed;
  inset: 0;
  z-index: 300;
  background: #fff;
  display: flex;
  flex-direction: column;
}}
.search-overlay[hidden] {{ display: none; }}
.search-bar {{
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border);
}}
.search-bar input {{
  flex: 1;
  border: none;
  outline: none;
  font-family: inherit;
  font-size: 16px;
  color: var(--text);
  background: transparent;
}}
.search-close {{
  background: #f7f7f7;
  border: 1px solid var(--border);
  border-radius: 50%;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
}}
.search-results {{
  flex: 1;
  overflow-y: auto;
  padding: 8px 0 40px;
}}
.search-hint {{
  padding: 14px 20px 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-2);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}}
.search-row {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 13px 20px;
  text-decoration: none;
  color: inherit;
  border-bottom: 1px solid #f5f5f5;
}}
.search-row:hover {{ background: #fafafa; }}
.search-row-name {{ font-size: 15px; font-weight: 600; color: var(--text); }}
.search-row-state {{ font-size: 12px; color: var(--text-2); margin-top: 1px; }}
.search-row-meta {{ font-size: 12px; color: var(--text-2); white-space: nowrap; }}
.search-empty {{ padding: 24px 20px; font-size: 14px; color: var(--text-2); }}

/* MAP */
.map-strip {{
  position: sticky;
  top: calc(var(--nav-h) + 48px);
  z-index: 50;
  width: 100%;
  height: 28vh;
  min-height: 180px;
  border-bottom: 1px solid var(--border);
  background: #f0f0f0;
  transition: height 0.4s cubic-bezier(0.32,0.72,0,1);
}}
.map-strip.expanded {{ height: 70vh; }}
#map {{ width: 100%; height: 100%; }}
.map-expand-btn {{
  position: absolute; bottom: 10px; right: 10px; z-index: 10;
  background: white; border: 1px solid var(--border);
  border-radius: 100px; padding: 6px 14px;
  font-size: 12px; font-weight: 600; color: var(--text);
  cursor: pointer; display: flex; align-items: center; gap: 6px;
  box-shadow: 0 1px 6px rgba(0,0,0,0.12); font-family: inherit;
}}

/* LOCATIONS LIST */
.locations-section {{
  padding: 0;
}}
.locations-header {{
  padding: 18px 20px 0;
}}
.locations-total {{
  font-size: 13px;
  color: var(--text-2);
}}
.state-group {{
  border-bottom: 2px solid var(--border);
  scroll-margin-top: calc(var(--nav-h) + 48px + 28vh + 10px);
  padding-bottom: 4px;
}}
.state-heading {{
  display: flex; align-items: baseline;
  justify-content: space-between;
  padding: 20px 20px 12px;
}}
.state-label {{
  font-family: 'Fraunces', serif;
  font-size: 19px; font-weight: 700; color: var(--text);
}}
.state-count {{ font-size: 12px; color: var(--text-2); }}
.see-all-btn {{
  display: block;
  margin: 4px 20px 18px;
  width: calc(100% - 40px);
  padding: 13px 16px;
  background: #fff;
  border: 1px solid #ccc;
  border-radius: 12px;
  font-family: 'Inter', sans-serif;
  font-size: 13px; font-weight: 600; color: var(--text);
  text-align: center; cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}}
.see-all-btn:hover {{ background: #fafafa; border-color: #999; }}

/* LOCATION CARD */
.state-cards {{
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 0 20px 8px;
}}
.lcard {{
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  border-radius: 14px;
  overflow: hidden;
  text-decoration: none;
  color: inherit;
  background: #fff;
  transition: box-shadow 0.2s, border-color 0.2s;
}}
.lcard:hover {{ box-shadow: 0 6px 20px rgba(0,0,0,0.09); }}
.lcard--active {{
  border-color: var(--teal);
  box-shadow: 0 0 0 1px var(--teal), 0 6px 20px rgba(0,114,206,0.12);
}}
.lcard-img-wrap {{
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 10;
  background: #f5f5f5;
}}
.lcard-img-wrap img {{
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}}
.lcard-img-ph {{
  width: 100%;
  height: 100%;
  background: #f5f5f5;
}}
.lcard-score {{
  position: absolute; bottom: 10px; left: 10px;
  background: rgba(255,255,255,0.95);
  color: var(--text); font-size: 12px; font-weight: 700;
  padding: 4px 9px; border-radius: 100px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.15);
}}
.lcard-body {{
  padding: 13px 16px 15px;
  display: flex; flex-direction: column; gap: 4px;
  flex: 1; min-width: 0;
}}
.lcard-header {{
  display: flex; align-items: flex-start;
  justify-content: space-between; gap: 8px;
}}
.lcard-name {{
  font-size: 16px; font-weight: 700;
  color: var(--text); line-height: 1.25;
}}
.lcard-state {{
  font-size: 11px; font-weight: 600;
  color: var(--teal); text-transform: uppercase;
  letter-spacing: 0.06em;
}}
.lcard-blurb {{
  font-size: 13px; color: #555;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}}
.lcard-meta {{
  display: flex; flex-wrap: wrap; gap: 8px;
  margin-top: 3px;
}}
.lcard-meta-item {{
  font-size: 12px; color: var(--text-2);
}}

@keyframes pinFadeIn {{
  from {{ opacity: 0; transform: translateY(3px); }}
  to   {{ opacity: 1; transform: translateY(0); }}
}}

@keyframes cardSlideUp {{
  from {{ opacity: 0; transform: translateX(-50%) translateY(12px); }}
  to   {{ opacity: 1; transform: translateX(-50%) translateY(0); }}
}}

/* FOOTER */
/* EMAIL SIGNUP */
.signup-band {{
  max-width: 560px;
  margin: 0 auto;
  padding: 44px 20px 50px;
  text-align: center;
}}
.signup-heading {{
  font-family: 'Fraunces', serif;
  font-size: 22px;
  font-weight: 700;
  margin-bottom: 6px;
}}
.signup-sub {{
  font-size: 13px;
  color: var(--text-2);
  line-height: 1.6;
  margin-bottom: 18px;
}}
.signup-row {{
  display: flex;
  gap: 8px;
}}
.signup-row input {{
  flex: 1;
  min-width: 0;
  font-family: inherit;
  font-size: 15px;
  color: var(--text);
  padding: 13px 16px;
  border: 1px solid #ccc;
  border-radius: var(--r);
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
  -webkit-appearance: none;
}}
.signup-row input:focus {{
  border-color: var(--teal);
  box-shadow: 0 0 0 3px rgba(0,114,206,0.12);
}}
.signup-btn {{
  flex-shrink: 0;
  font-family: inherit;
  font-size: 14px;
  font-weight: 600;
  color: #fff;
  background: var(--teal);
  border: none;
  border-radius: var(--r);
  padding: 13px 22px;
  cursor: pointer;
  transition: background 0.15s, opacity 0.15s;
}}
.signup-btn:hover {{ background: #005da8; }}
.signup-btn:disabled {{ opacity: 0.6; cursor: default; }}
.signup-msg {{
  font-size: 13px;
  margin-top: 12px;
  min-height: 18px;
  color: var(--text-2);
}}
.signup-msg.ok {{ color: var(--teal); font-weight: 600; }}
.signup-msg.err {{ color: #d93025; }}

.site-footer {{
  padding: 28px 20px 100px;
  text-align: center; font-size: 13px;
  color: var(--text-2); border-top: 1px solid var(--border);
}}
.site-footer img {{
  height: 28px; display: block;
  margin: 0 auto 8px; opacity: 0.5;
}}

@media (min-width: 768px) {{
  .hero {{ padding: 44px 24px 22px; }}
  .hero h1 {{ font-size: 38px; }}
  .hero p {{ font-size: 15px; }}
  .locations-section {{ max-width: 1120px; margin: 0 auto; }}
  .locations-header {{ padding: 22px 24px 0; }}
  .state-heading {{ padding: 24px 24px 14px; }}
  .state-cards {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 16px;
    padding: 0 24px 10px;
  }}
  .sort-bar {{ padding: 14px 24px 10px; }}
  .compact-list {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    padding: 0 24px 10px;
  }}
  .see-all-btn {{ margin: 6px 24px 20px; width: calc(100% - 48px); }}
  .lcard-body {{ padding: 14px 16px 16px; }}
}}
</style>
</head>
<body>

<nav class="nav">
  <a href="/" class="nav-logo"><img src="/images/logo.png" alt="Family Holiday Parks"></a>
  <div class="nav-search" onclick="openSearch()" role="button" tabindex="0" aria-label="Search locations">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#717171" stroke-width="2.5" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
    <span>Where do you want to go?</span>
  </div>
</nav>

<div class="search-overlay" id="search-overlay" hidden>
  <div class="search-bar">
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#717171" stroke-width="2.5" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
    <input id="search-input" type="text" placeholder="Search destinations or states" autocomplete="off" oninput="renderSearchResults(this.value)">
    <button class="search-close" type="button" onclick="closeSearch()" aria-label="Close search">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#222" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
    </button>
  </div>
  <div class="search-results" id="search-results"></div>
</div>

<header class="hero">
  <h1>Find your family's next favourite holiday park</h1>
  <p>{total_parks} parks across {total_destinations} Australian destinations, ranked from {reviews_label} real family reviews.</p>
</header>

<div class="state-chips" id="state-chips" role="navigation" aria-label="Jump to state">
  <button type="button" data-slug="queensland" data-name="Queensland" onclick="goToState(this)">QLD</button>
  <button type="button" data-slug="new-south-wales" data-name="New South Wales" onclick="goToState(this)">NSW</button>
  <button type="button" data-slug="victoria" data-name="Victoria" onclick="goToState(this)">VIC</button>
  <button type="button" data-slug="western-australia" data-name="Western Australia" onclick="goToState(this)">WA</button>
  <button type="button" data-slug="south-australia" data-name="South Australia" onclick="goToState(this)">SA</button>
  <button type="button" data-slug="tasmania" data-name="Tasmania" onclick="goToState(this)">TAS</button>
  <button type="button" data-slug="northern-territory" data-name="Northern Territory" onclick="goToState(this)">NT</button>
  <button type="button" data-slug="act" data-name="ACT" onclick="goToState(this)">ACT</button>
</div>

<div class="map-strip" id="map-strip">
  <div id="map"></div>
  <button class="map-expand-btn" id="map-expand-btn" onclick="toggleMap()">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>
    Expand map
  </button>
</div>

<div class="locations-section">
  {location_cards_html}
</div>

<section class="signup-band" aria-label="Email signup">
  <h2 class="signup-heading">Find better family holidays</h2>
  <p class="signup-sub">Get the best family holiday park deals and new destination guides before anyone else.</p>
  <div class="signup-row" id="signup-row">
    <input type="email" id="signup-email" placeholder="Your email" autocomplete="email">
    <button type="button" class="signup-btn" id="signup-btn" onclick="submitSignup()">Join free</button>
  </div>
  <div class="signup-msg" id="signup-msg"></div>
</section>

<footer class="site-footer">
  <img src="/images/logo.png" alt="Family Holiday Parks">
  <div>familyholidayparks.com.au · Helping Australian Families Find Better Holidays</div>
</footer>

<script>
const LOCATIONS = {map_locations_json};
const EMAIL_SIGNUP_WEBHOOK_URL = 'EMAIL_SIGNUP_WEBHOOK_URL_PLACEHOLDER';

async function submitSignup() {{
  const input = document.getElementById('signup-email');
  const btn = document.getElementById('signup-btn');
  const msg = document.getElementById('signup-msg');
  const email = input.value.trim();
  msg.className = 'signup-msg';
  if (!/^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(email)) {{
    msg.textContent = 'Please enter a valid email address.';
    msg.classList.add('err');
    return;
  }}
  btn.disabled = true;
  btn.textContent = 'Joining...';
  try {{
    await fetch(EMAIL_SIGNUP_WEBHOOK_URL, {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ email: email, source: 'homepage' }})
    }});
    msg.textContent = "You're in. We'll be in touch.";
    msg.classList.add('ok');
    input.value = '';
  }} catch (err) {{
    msg.textContent = "Something went wrong. Please try again.";
    msg.classList.add('err');
  }}
  btn.disabled = false;
  btn.textContent = 'Join free';
}}

document.getElementById('signup-email').addEventListener('keydown', (e) => {{
  if (e.key === 'Enter') submitSignup();
}});
const MAP_CENTER = {{ lat: -26.0, lng: 131.0 }};
const MAP_ZOOM_DESKTOP = 4;
const MAP_ZOOM_MOBILE = 3;
let map;
const markersBySlug = {{}};
let activeSlug = null;
let isExploreMode = false;
const cardVisibility = new Map();

function defaultMapZoom() {{
  return window.innerWidth < 768 ? MAP_ZOOM_MOBILE : MAP_ZOOM_DESKTOP;
}}

function escapeHtml(s) {{
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}}

function plainPinContent() {{
  const wrap = document.createElement('div');
  wrap.style.cursor = 'pointer';
  wrap.innerHTML = `<div style="
    width: 10px;
    height: 10px;
    background: #0072CE;
    border-radius: 50%;
    border: 2px solid #fff;
    box-shadow: 0 1px 4px rgba(0,0,0,0.3);
    cursor: pointer;
    transition: all 0.15s;
  "></div>`;
  return wrap;
}}

function labelPinContent(name, photo) {{
  // Active pin — identical language to the location page: teal speech bubble + tail + teal dot
  const wrap = document.createElement('div');
  wrap.style.cursor = 'pointer';
  wrap.innerHTML = `<div style="display:flex;flex-direction:column;align-items:center;transform:scale(1.25);transition:transform 0.25s cubic-bezier(0.34,1.56,0.64,1);filter:drop-shadow(0 4px 8px rgba(0,114,206,0.4));">
    <div style="position:relative;background:#0072CE;color:#fff;font-family:'Inter',sans-serif;font-size:10px;font-weight:700;padding:3px 8px;border-radius:6px;white-space:nowrap;max-width:140px;overflow:hidden;text-overflow:ellipsis;text-align:center;margin-bottom:4px;">${{escapeHtml(name)}}<div style="position:absolute;bottom:-4px;left:50%;transform:translateX(-50%);border-left:4px solid transparent;border-right:4px solid transparent;border-top:4px solid #0072CE;"></div></div>
    <div style="width:10px;height:10px;background:#0072CE;border-radius:50%;border:2px solid #fff;"></div>
  </div>`;
  return wrap;
}}

function explorePinContent(name) {{
  // State-zoom pin — identical language to the location page default: dark pill, white text
  const wrap = document.createElement('div');
  wrap.style.cssText = 'cursor:pointer;animation:pinFadeIn 0.2s ease;';
  wrap.innerHTML = `<div style="display:flex;flex-direction:column;align-items:center;transform:scale(1);transition:transform 0.25s cubic-bezier(0.34,1.56,0.64,1);filter:drop-shadow(0 2px 4px rgba(0,0,0,0.25));">
    <div style="background:#333;color:#fff;font-family:'Inter',sans-serif;font-size:10px;font-weight:600;padding:4px 9px;border-radius:100px;white-space:nowrap;max-width:120px;overflow:hidden;text-overflow:ellipsis;line-height:1.2;letter-spacing:0.01em;">${{escapeHtml(name)}}</div>
  </div>`;
  return wrap;
}}

function refreshAllPins() {{
  Object.entries(markersBySlug).forEach(([slug, entry]) => {{
    if (slug === activeSlug) {{
      entry.marker.content = labelPinContent(entry.loc.name, entry.loc.hero);
    }} else if (isExploreMode) {{
      entry.marker.content = explorePinContent(entry.loc.name);
    }} else {{
      entry.marker.content = plainPinContent();
    }}
  }});
}}

function syncScrollState() {{
  let bestCard = null;
  let bestRatio = 0;
  cardVisibility.forEach((ratio, card) => {{
    if (ratio > bestRatio) {{
      bestRatio = ratio;
      bestCard = card;
    }}
  }});

  document.querySelectorAll('.lcard').forEach(card => {{
    card.classList.toggle('lcard--active', card === bestCard && bestRatio > 0.1);
  }});

  const prevActiveSlug = activeSlug;
  activeSlug = bestCard && bestRatio > 0.1 ? bestCard.dataset.slug : null;
  refreshAllPins();

  if (activeSlug !== prevActiveSlug && activeSlug && Date.now() >= chipLock) {{
    const entry = markersBySlug[activeSlug];
    if (entry && map) {{
      const latOffset = window.innerWidth < 768 ? 2.5 : 2.0;
      map.panTo({{ lat: entry.loc.lat - latOffset, lng: entry.loc.lng }});
    }}
  }}
}}

function toggleState(btn) {{
  const group = btn.closest('.state-group');
  const overflow = group.querySelector('.state-overflow');
  if (!overflow) return;
  const open = overflow.style.display !== 'none';
  overflow.style.display = open ? 'none' : 'block';
  const state = group.dataset.state;
  const total = overflow.querySelectorAll('.lcard--compact').length;
  btn.textContent = open
    ? `See all ${{total}} ${{state.charAt(0).toUpperCase() + state.slice(1)}} destinations →`
    : `Show fewer →`;
}}

function sortState(btn, key) {{
  const overflow = btn.closest('.state-overflow');
  const list = overflow.querySelector('.compact-list');
  overflow.querySelectorAll('.sort-pill').forEach(p => p.classList.toggle('active', p === btn));
  const rows = Array.from(list.children);
  const num = (el, attr) => parseFloat(el.dataset[attr]) || 0;
  rows.sort((a, b) => {{
    if (key === 'score') return num(b, 'score') - num(a, 'score');
    if (key === 'price') return num(a, 'price') - num(b, 'price');
    if (key === 'reviews') return num(b, 'reviews') - num(a, 'reviews');
    return (a.dataset.name || '').localeCompare(b.dataset.name || '');
  }});
  rows.forEach(r => list.appendChild(r));
}}

let chipLock = 0;

function goToState(btn) {{
  const slug = btn.dataset.slug;
  const stateName = btn.dataset.name;
  document.querySelectorAll('.state-chips button').forEach(b => b.classList.toggle('active', b === btn));
  chipLock = Date.now() + 1600;
  const pts = LOCATIONS.filter(l => l.state === stateName && l.lat && l.lng);
  if (pts.length && map) {{
    const b = new google.maps.LatLngBounds();
    pts.forEach(p => b.extend({{ lat: p.lat, lng: p.lng }}));
    map.fitBounds(b, {{ top: 30, right: 30, bottom: 30, left: 30 }});
  }}
  scrollToState(slug);
}}

function scrollToState(slug) {{
  const el = document.querySelector(`.state-group[data-state="${{slug}}"]`);
  if (el) el.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
}}

/* ── SEARCH ── */
function openSearch() {{
  const ov = document.getElementById('search-overlay');
  ov.hidden = false;
  document.body.style.overflow = 'hidden';
  const input = document.getElementById('search-input');
  input.value = '';
  renderSearchResults('');
  setTimeout(() => input.focus(), 50);
}}

function closeSearch() {{
  document.getElementById('search-overlay').hidden = true;
  document.body.style.overflow = '';
}}

function renderSearchResults(query) {{
  const box = document.getElementById('search-results');
  const q = query.trim().toLowerCase();
  let matches;
  let hint;
  if (!q) {{
    matches = [...LOCATIONS].sort((a, b) => (b.score || 0) - (a.score || 0)).slice(0, 8);
    hint = 'Top destinations';
  }} else {{
    matches = LOCATIONS.filter(l =>
      (l.name || '').toLowerCase().includes(q) ||
      (l.state || '').toLowerCase().includes(q)
    ).slice(0, 20);
    hint = matches.length ? `${{matches.length}} result${{matches.length === 1 ? '' : 's'}}` : '';
  }}
  if (!matches.length) {{
    box.innerHTML = '<div class="search-empty">No destinations found. Try a place name or state, like "Broome" or "QLD".</div>';
    return;
  }}
  const rows = matches.map(l => {{
    const meta = [l.score ? `${{l.score}}/100` : '', l.parks ? `${{l.parks}} parks` : ''].filter(Boolean).join(' · ');
    return `<a class="search-row" href="${{escapeHtml(l.url)}}">
      <div>
        <div class="search-row-name">${{escapeHtml(l.name)}}</div>
        <div class="search-row-state">${{escapeHtml(l.state)}}</div>
      </div>
      <div class="search-row-meta">${{meta}}</div>
    </a>`;
  }}).join('');
  box.innerHTML = `<div class="search-hint">${{hint}}</div>` + rows;
}}

document.addEventListener('keydown', (e) => {{
  if (e.key === 'Escape') closeSearch();
}});

function closePinCard() {{
  const existing = document.getElementById('pin-card');
  if (existing) existing.remove();
}}

function showPinCard(loc) {{
  closePinCard();
  const card = document.createElement('div');
  card.id = 'pin-card';
  card.style.cssText = `
    position:absolute;
    bottom:12px;
    left:50%;
    transform:translateX(-50%);
    width:calc(100% - 32px);
    max-width:340px;
    background:#fff;
    border-radius:12px;
    box-shadow:0 4px 24px rgba(0,0,0,0.18);
    z-index:10;
    overflow:hidden;
    animation:cardSlideUp 0.22s cubic-bezier(0.34,1.56,0.64,1);
    cursor:default;
  `;
  const img = loc.hero && loc.hero.startsWith('http')
    ? `<img src="${{loc.hero}}" style="width:100%;height:110px;object-fit:cover;display:block;">`
    : '';
  const score = loc.score ? `<span style="background:#222;color:#fff;font-size:11px;font-weight:700;padding:2px 7px;border-radius:100px;">${{loc.score}}/100</span>` : '';
  const price = loc.price ? `<span style="color:#717171;font-size:12px;">${{loc.price}}</span>` : '';
  const reviews = loc.reviews ? `<span style="color:#717171;font-size:12px;">${{loc.reviews.toLocaleString()}} reviews</span>` : '';
  const parks = loc.parks ? `<span style="color:#717171;font-size:12px;">${{loc.parks}} parks</span>` : '';
  card.innerHTML = `
    ${{img}}
    <div style="padding:12px 14px 14px;">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
        <div>
          <div style="font-family:'Inter',sans-serif;font-size:14px;font-weight:700;color:#222;">${{escapeHtml(loc.name)}}</div>
          <div style="font-family:'Inter',sans-serif;font-size:11px;font-weight:600;color:#0072CE;text-transform:uppercase;letter-spacing:0.05em;margin-top:1px;">${{escapeHtml(loc.state)}}</div>
        </div>
        ${{score}}
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;">
        ${{parks}}${{reviews ? '<span style="color:#ddd;">·</span>' : ''}}${{reviews}}${{price ? '<span style="color:#ddd;">·</span>' : ''}}${{price}}
      </div>
      <a href="${{loc.url}}" style="
        display:block;
        background:#0072CE;
        color:#fff;
        font-family:'Inter',sans-serif;
        font-size:13px;
        font-weight:700;
        text-align:center;
        padding:10px;
        border-radius:8px;
        text-decoration:none;
      ">View destination →</a>
    </div>
    <button onclick="closePinCard()" style="
      position:absolute;top:8px;right:8px;
      background:rgba(0,0,0,0.45);
      border:none;border-radius:50%;
      width:26px;height:26px;
      color:#fff;font-size:14px;line-height:1;
      cursor:pointer;display:flex;align-items:center;justify-content:center;
    ">×</button>
  `;
  document.getElementById('map-strip').appendChild(card);
  card.addEventListener('click', e => e.stopPropagation());
}}

function initScrollObserver() {{
  const cards = document.querySelectorAll('.lcard');
  if (!cards.length) return;

  const observer = new IntersectionObserver((entries) => {{
    entries.forEach(entry => {{
      cardVisibility.set(entry.target, entry.intersectionRatio);
    }});
    syncScrollState();
  }}, {{
    root: null,
    rootMargin: '-35% 0px -35% 0px',
    threshold: [0, 0.1, 0.5, 1.0],
  }});

  cards.forEach(card => {{
    cardVisibility.set(card, 0);
    observer.observe(card);
  }});
}}

function initMap() {{
  map = new google.maps.Map(document.getElementById('map'), {{
    mapId: {json.dumps(google_maps_map_id)},
    center: MAP_CENTER,
    zoom: MAP_ZOOM_DESKTOP,
    disableDefaultUI: true,
    zoomControl: true,
    gestureHandling: 'cooperative',
    styles: [
      {{ featureType: 'poi', stylers: [{{ visibility: 'off' }}] }},
      {{ featureType: 'transit', stylers: [{{ visibility: 'off' }}] }},
      {{ elementType: 'labels.icon', stylers: [{{ visibility: 'off' }}] }}
    ]
  }});

  if (window.innerWidth < 768) {{
    map.setZoom(MAP_ZOOM_MOBILE);
    map.setCenter({{ lat: -26.0, lng: 131.0 }});
  }}

  LOCATIONS.forEach(loc => {{
    if (!loc.lat || !loc.lng) return;

    const marker = new google.maps.marker.AdvancedMarkerElement({{
      map,
      position: {{ lat: loc.lat, lng: loc.lng }},
      content: plainPinContent(),
      title: loc.name,
    }});

    markersBySlug[loc.slug] = {{ marker, loc }};

    marker.addListener('click', () => {{
      showPinCard(loc);
    }});
  }});

  map.addListener('click', () => {{
    closePinCard();
  }});

  map.addListener('zoom_changed', () => {{
    const z = map.getZoom();
    isExploreMode = z >= 5;
    refreshAllPins();
  }});

  initScrollObserver();
}}

function toggleMap() {{
  const strip = document.getElementById('map-strip');
  const btn = document.getElementById('map-expand-btn');
  const expanded = strip.classList.toggle('expanded');
  btn.innerHTML = expanded
    ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="10" y1="14" x2="3" y2="21"/><line x1="21" y1="3" x2="14" y2="10"/></svg> Collapse`
    : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg> Expand map`;
  setTimeout(() => {{
    if (!map) return;
    google.maps.event.trigger(map, 'resize');
    if (expanded) {{
      map.setCenter({{ lat: -26.0, lng: 131.0 }});
      map.setZoom(defaultMapZoom());
    }}
  }}, 420);
}}
</script>

<script async defer
  src="https://maps.googleapis.com/maps/api/js?key={google_maps_api_key}&libraries=marker&callback=initMap&v=beta">
</script>

</body>
</html>"""

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html, encoding="utf-8")
    print(f"Saved: {OUTPUT}")
    print(f"   {len(card_locations)} location cards generated")
    print(f"   {len(map_locations)} map pins generated")


def main():
    from dotenv import load_dotenv

    load_dotenv()
    google_maps_api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    google_maps_map_id = os.environ.get("GOOGLE_MAPS_MAP_ID", "")

    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    build(
        google_maps_api_key=google_maps_api_key,
        google_maps_map_id=google_maps_map_id,
    )
    if args.publish:
        print("Running: git add -A")
        subprocess.run(["git", "add", "-A"], cwd=PROJECT)
        r = subprocess.run(
            ["git", "commit", "-m", "Regenerate homepage"],
            cwd=PROJECT,
            capture_output=True,
            text=True,
        )
        out = r.stdout.strip()
        print(out or "Nothing to commit.")
        if "nothing to commit" not in out.lower():
            subprocess.run(["git", "push"], cwd=PROJECT)
            print("Pushed")


if __name__ == "__main__":
    main()
