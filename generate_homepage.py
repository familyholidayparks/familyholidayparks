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
LOCATIONS_DIR = PROJECT / "locations"
OUTPUT = PROJECT / "public" / "index.html"

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


def _park_sort_score(p):
    try:
        return float(p.get("total_score") or p.get("family_score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _resolve_card_photo(top_park, loc, slug, state_lower):
    """Top-ranked park photo; config hero_image and load_locations hero_img are last resort only."""
    top_name = (top_park.get("park_name") or top_park.get("name") or "Unknown").strip()

    config_hero = ""
    config_file = PROJECT_DIR / "locations" / state_lower / slug / "config.json"
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text(encoding="utf-8"))
            config_hero = (config.get("hero_image") or "").strip()
        except Exception:
            pass

    for field_name, val in [
        ("photo_url_override", top_park.get("photo_url_override")),
        ("photo_url_cached", top_park.get("photo_url_cached")),
        ("image_url", top_park.get("image_url")),
        ("photo", top_park.get("photo")),
    ]:
        if val and str(val).strip():
            return str(val).strip(), field_name, top_name

    if config_hero:
        return config_hero, "hero_image", top_name

    hero_img = (loc.get("hero_img") or "").strip()
    if hero_img:
        return hero_img, "hero_image", top_name

    return "", "none", top_name


def build_map_and_card_locations(all_locations):
    """Build enriched location data for map pins and vertical cards."""
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

        state_full = STATE_URL.get(state.lower(), state.lower())
        url = f"/{slug}-{state_full}"

        blurb = (top_park.get("best_for") or "")[:80]

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
    location_cards_html = ""
    for loc in card_locations:
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
            f'<img src="{esc(_hero)}" alt="{_name}">'
            if str(_hero).startswith("http")
            else '<div class="lcard-img-ph">🏕</div>'
        )
        _score_html = f'<span class="lcard-score">{_score}/100</span>' if _score else ""
        _reviews_html = (
            f'<span class="lcard-meta-item">{_reviews} reviews</span>' if _reviews else ""
        )
        _price_html = f'<span class="lcard-meta-item">{_price}</span>' if _price else ""
        _parks_html = f'<span class="lcard-meta-item">{_parks} parks</span>'

        location_cards_html += f'''<a class="lcard" href="{_url}" data-slug="{esc(loc["slug"])}">
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
  height: 36px; width: auto; display: block;
}}
.nav-search {{
  flex: 1; max-width: 400px;
  display: flex; align-items: center; gap: 8px;
  background: #f7f7f7; border: 1px solid var(--border);
  border-radius: 100px; padding: 9px 16px;
  cursor: pointer;
}}
.nav-search svg {{ flex-shrink: 0; }}
.nav-search span {{ font-size: 14px; color: var(--text-2); }}

/* HERO TEXT */
.hero-text {{
  padding: 24px 20px 20px;
  border-bottom: 1px solid var(--border);
  max-width: 680px;
}}
.hero-text p {{
  font-size: 15px;
  line-height: 1.65;
  color: var(--text-2);
}}
.hero-text strong {{
  color: var(--text);
  font-weight: 600;
}}

/* MAP */
.map-strip {{
  position: sticky;
  top: var(--nav-h);
  z-index: 50;
  width: 100%;
  height: 35vh;
  min-height: 220px;
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
  padding: 16px 20px 12px;
  border-bottom: 1px solid var(--border);
  display: flex; align-items: baseline;
  justify-content: space-between;
}}
.locations-header h2 {{
  font-family: 'Fraunces', serif;
  font-size: 18px; font-weight: 700;
  color: var(--text); letter-spacing: -0.01em;
}}
.locations-header span {{
  font-size: 13px; color: var(--text-2);
}}

/* LOCATION CARD */
.lcard {{
  display: flex; gap: 0;
  border-bottom: 1px solid var(--border);
  text-decoration: none; color: inherit;
  transition: background 0.15s;
  background: #fff;
}}
.lcard:hover {{ background: #fafafa; }}
.lcard-img-wrap {{
  position: relative; flex-shrink: 0;
  width: 120px;
}}
.lcard-img-wrap img {{
  width: 120px; height: 100%;
  min-height: 110px;
  object-fit: cover; display: block;
}}
.lcard-img-ph {{
  width: 120px; min-height: 110px;
  background: #f5f5f5;
  display: flex; align-items: center;
  justify-content: center; font-size: 2rem;
  color: #ddd;
}}
.lcard-score {{
  position: absolute; bottom: 8px; left: 8px;
  background: rgba(255,255,255,0.95);
  color: var(--text); font-size: 11px; font-weight: 700;
  padding: 3px 8px; border-radius: 100px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.15);
}}
.lcard-body {{
  padding: 14px 16px;
  display: flex; flex-direction: column; gap: 5px;
  flex: 1; min-width: 0;
}}
.lcard-header {{
  display: flex; align-items: flex-start;
  justify-content: space-between; gap: 8px;
}}
.lcard-name {{
  font-size: 15px; font-weight: 700;
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
  margin-top: 2px;
}}
.lcard-meta-item {{
  font-size: 12px; color: var(--text-2);
}}

/* FOOTER */
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
  .lcard-img-wrap {{ width: 180px; }}
  .lcard-img-wrap img {{ width: 180px; min-height: 130px; }}
  .lcard-img-ph {{ width: 180px; min-height: 130px; }}
  .lcard-name {{ font-size: 16px; }}
  .map-strip {{ height: 45vh; }}
  .hero-text {{ padding: 32px 24px 24px; }}
  .locations-header {{ padding: 20px 24px 14px; }}
  .lcard-body {{ padding: 16px 20px; }}
}}
</style>
</head>
<body>

<nav class="nav">
  <a href="/" class="nav-logo"><img src="/images/logo.png" alt="Family Holiday Parks"></a>
  <div class="nav-search">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#717171" stroke-width="2.5" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
    <span>Search locations...</span>
  </div>
</nav>

<div class="hero-text">
  <p>Family Holiday Parks helps families book with confidence by comparing Australia's best holiday parks for caravans and motorhomes using 300,000+ real family reviews.</p>
</div>

<div class="map-strip" id="map-strip">
  <div id="map"></div>
  <button class="map-expand-btn" id="map-expand-btn" onclick="toggleMap()">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>
    Expand map
  </button>
</div>

<div class="locations-section">
  <div class="locations-header">
    <h2>All locations</h2>
    <span>{len(card_locations)} destinations</span>
  </div>
  {location_cards_html}
</div>

<footer class="site-footer">
  <img src="/images/logo.png" alt="Family Holiday Parks">
  <div>familyholidayparks.com.au · Helping Australian Families Find Better Holidays</div>
</footer>

<script>
const LOCATIONS = {map_locations_json};
const MAP_CENTER = {{ lat: -27.0, lng: 133.0 }};
const MAP_ZOOM_DESKTOP = 4;
const MAP_ZOOM_MOBILE = 3;
let map;
const pinEls = {{}};

function defaultMapZoom() {{
  return window.innerWidth < 768 ? MAP_ZOOM_MOBILE : MAP_ZOOM_DESKTOP;
}}

function resetAllPins() {{
  Object.values(pinEls).forEach(el => {{
    el.style.background = '#222';
    el.style.borderColor = '#222';
    el.style.transform = 'scale(1)';
  }});
}}

function highlightPin(slug) {{
  resetAllPins();
  const el = pinEls[slug];
  if (!el) return;
  el.style.background = '#0072CE';
  el.style.borderColor = '#0072CE';
  el.style.transform = 'scale(1.15)';
}}

function initMap() {{
  map = new google.maps.Map(document.getElementById('map'), {{
    mapId: {json.dumps(google_maps_map_id)},
    center: MAP_CENTER,
    zoom: MAP_ZOOM_DESKTOP,
    disableDefaultUI: true,
    zoomControl: true,
    gestureHandling: 'greedy',
    styles: [
      {{ featureType: 'poi', stylers: [{{ visibility: 'off' }}] }},
      {{ featureType: 'transit', stylers: [{{ visibility: 'off' }}] }},
      {{ elementType: 'labels.icon', stylers: [{{ visibility: 'off' }}] }}
    ]
  }});

  if (window.innerWidth < 768) {{
    map.setZoom(MAP_ZOOM_MOBILE);
  }}

  LOCATIONS.forEach(loc => {{
    if (!loc.lat || !loc.lng) return;

    const pin = document.createElement('div');
    pin.style.cssText = `
      background:#222;
      border-radius:8px;
      padding:5px 10px;
      box-shadow:0 2px 8px rgba(0,0,0,0.25);
      border:2px solid #222;
      cursor:pointer;
      transition:all 0.15s;
      white-space:nowrap;
      font-family:'Inter',sans-serif;
      max-width:140px;
      transform:scale(1);
    `;
    pin.innerHTML = `<div style="font-size:11px;font-weight:600;color:#fff;overflow:hidden;text-overflow:ellipsis;">${{loc.name}}</div>`;

    pinEls[loc.slug] = pin;

    const marker = new google.maps.marker.AdvancedMarkerElement({{
      map,
      position: {{ lat: loc.lat, lng: loc.lng }},
      content: pin,
      title: loc.name,
    }});

    marker.addListener('click', () => {{
      map.panTo({{ lat: loc.lat, lng: loc.lng }});
      window.location.href = loc.url;
    }});
  }});

  document.querySelectorAll('.lcard').forEach(card => {{
    card.addEventListener('mouseenter', () => highlightPin(card.dataset.slug));
    card.addEventListener('mouseleave', resetAllPins);
  }});
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
      map.setCenter(MAP_CENTER);
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
