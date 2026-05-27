#!/usr/bin/env python3
"""
Generates public/top-rated.html — a ranked leaderboard of all scored parks.
Usage: python generate_leaderboard.py
"""
import json
import re
from pathlib import Path
from datetime import date

project_dir = Path(__file__).resolve().parent
parks_dir = project_dir / "parks"
public_dir = project_dir / "public"

STATE_LABELS = {
    "qld": "Queensland",
    "nsw": "New South Wales",
    "vic": "Victoria",
    "sa": "South Australia",
    "wa": "Western Australia",
    "tas": "Tasmania",
    "nt": "Northern Territory",
    "act": "ACT",
}

STATE_SLUGS = {
    "qld": "queensland",
    "nsw": "new-south-wales",
    "vic": "victoria",
    "sa": "south-australia",
    "wa": "western-australia",
    "tas": "tasmania",
    "nt": "northern-territory",
    "act": "act",
}

def slugify(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r'[^a-z0-9\s-]', '', name)
    name = re.sub(r'[\s]+', '-', name)
    name = re.sub(r'-+', '-', name)
    return name.strip('-')

def get_location_url(locations: list) -> tuple:
    """Get the best location label and URL for a park."""
    if not locations:
        return "", ""
    # Use first location
    loc = locations[0]
    parts = loc.split('/')
    if len(parts) == 2:
        state = parts[0]
        loc_slug = parts[1]
        state_slug = STATE_SLUGS.get(state, state)
        loc_label = loc_slug.replace('-', ' ').title()
        url = f"/{loc_slug}-{state_slug}"
        return loc_label, url
    return loc, ""

def load_all_parks() -> list:
    parks = []
    for master_file in sorted(parks_dir.glob("*/master.json")):
        try:
            data = json.loads(master_file.read_text(encoding='utf-8'))
            score = data.get('total_score')
            if score is None:
                continue
            try:
                score = float(score)
            except:
                continue
            if score < 1:
                continue
            parks.append(data)
        except Exception as e:
            print(f"Error reading {master_file}: {e}")
    parks.sort(key=lambda p: float(p.get('total_score') or 0), reverse=True)
    return parks

def get_state(locations: list) -> str:
    if not locations:
        return ""
    return locations[0].split('/')[0] if '/' in locations[0] else ""

def render_park_row(rank: int, park: dict) -> str:
    name = park.get('park_name', '')
    score = park.get('total_score', '')
    try:
        score_int = int(float(score))
    except:
        score_int = 0
    
    locations = park.get('locations', [])
    state = get_state(locations)
    loc_label, loc_url = get_location_url(locations)
    
    google_rating = park.get('google_rating')
    review_count = park.get('review_count')
    website = park.get('website') or ''
    powered = park.get('prices', {}).get('powered_weekday') or '—'
    pets = park.get('pet_friendly')
    wifi = park.get('wifi_available')
    
    # Score colour
    if score_int >= 80:
        score_class = "score-gold"
        medal = "🥇"
    elif score_int >= 65:
        score_class = "score-silver"
        medal = "🥈"
    elif score_int >= 50:
        score_class = "score-bronze"
        medal = "🥉"
    else:
        score_class = "score-low"
        medal = ""

    rating_str = f"{google_rating}★ · {review_count:,}" if google_rating and review_count else "—"
    
    pets_str = '<span class="yes">✓</span>' if pets else '<span class="no">✗</span>'
    wifi_str = '<span class="yes">✓</span>' if wifi else '<span class="no">—</span>'
    
    loc_html = f'<a href="{loc_url}" class="loc-link">{loc_label}</a>' if loc_url else loc_label

    photo = park.get('photo_url_cached') or ''
    thumb_html = f'<img src="{photo}" style="width:48px;height:36px;object-fit:cover;border-radius:4px;" loading="lazy">' if photo else '<div style="width:48px;height:36px;background:#eee;border-radius:4px;"></div>'
    state_label = STATE_LABELS.get(state, state.upper()) if state else '—'
    
    book_btn = f'<a href="{website}" target="_blank" rel="noopener" class="book-btn">Book</a>' if website else '—'

    return f'''<tr data-state="{state}" data-state-label="{state_label}">
  <td class="rank">#{rank}</td>
  <td class="thumb">{thumb_html}</td>
  <td class="park-name">{name}</td>
  <td class="location">{loc_html}</td>
  <td class="state-col">{state_label}</td>
  <td class="score"><span class="{score_class}">{score_int}/100</span></td>
  <td class="rating">{rating_str}</td>
  <td class="powered">{powered}</td>
  <td class="pets">{pets_str}</td>
  <td class="wifi">{wifi_str}</td>
  <td class="book">{book_btn}</td>
</tr>'''

def render_rows(parks):
    rows = []
    for i, park in enumerate(parks, 1):
        rows.append(render_park_row(i, park))
    return ''.join(rows)

def generate():
    parks = load_all_parks()
    print(f"Loaded {len(parks)} parks")

    today = date.today().strftime("%B %Y")

    rows_html = render_rows(parks)

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Top Rated Family Holiday Parks in Australia 2026 | Family Holiday Parks</title>
  <meta name="description" content="The best family holiday and caravan parks in Australia ranked by real family scores. {len(parks)} parks scored. Pools, playgrounds, powered sites, cabins and school holiday accommodation compared. No sponsored rankings.">

  <script async src="https://www.googletagmanager.com/gtag/js?id=G-VVPFY2WRM1"></script>
  <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){{dataLayer.push(arguments);}}
    gtag('js', new Date());
    gtag('config', 'G-VVPFY2WRM1');
  </script>

  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,700;1,9..144,400&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">

  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --deep: #3F5F47;
      --leaf: #6B8F71;
      --sand: #E8DCCB;
      --cream: #F7F5F0;
      --light-green: #EAF2EC;
      --dark: #1E2D21;
      --text: #2C2C2C;
      --muted: #666;
    }}
    body {{ font-family: 'DM Sans', sans-serif; background: var(--cream); color: var(--text); -webkit-font-smoothing: antialiased; }}

    /* NAV */
    .nav {{ background: var(--dark); padding: 1rem 2rem; display: flex; justify-content: space-between; align-items: center; }}
    .nav-logo {{ font-family: 'Fraunces', serif; font-size: 1.1rem; font-weight: 700; color: white; text-decoration: none; }}
    .nav-sub {{ font-size: 0.65rem; color: rgba(255,255,255,0.5); letter-spacing: 0.06em; text-transform: uppercase; }}
    .nav-back {{ color: rgba(255,255,255,0.6); font-size: 0.85rem; text-decoration: none; }}
    .nav-back:hover {{ color: white; }}

    /* HEADER */
    .page-header {{ position: relative; background-image: url('https://images.unsplash.com/photo-1778694276593-1ba7fd0213cc?w=1600&q=80&fit=crop'); background-size: cover; background-position: center; color: white; padding: 5rem 2rem 4rem; text-align: center; }}
    .page-header::before {{ content: ''; position: absolute; inset: 0; background: linear-gradient(to bottom, rgba(20,38,23,0.65) 0%, rgba(20,38,23,0.55) 100%); }}
    .page-header-inner {{ position: relative; z-index: 1; max-width: 720px; margin: 0 auto; }}
    .page-header h1 {{ font-family: 'Fraunces', serif; font-size: clamp(2rem, 5vw, 3.5rem); font-weight: 700; margin-bottom: 1rem; line-height: 1.1; }}
    .hero-sub {{ color: rgba(255,255,255,0.85); font-size: clamp(1rem, 2vw, 1.15rem); max-width: 560px; margin: 0 auto 1.25rem; line-height: 1.6; }}
    .hero-trust {{ font-size: 0.78rem; color: rgba(255,255,255,0.5); letter-spacing: 0.06em; text-transform: uppercase; }}

    /* FILTERS */
    .filters {{ background: white; padding: 1.25rem 2rem; border-bottom: 1px solid rgba(63,95,71,0.1); position: sticky; top: 0; z-index: 50; display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }}
    .filter-label {{ font-size: 0.75rem; color: var(--muted); font-weight: 500; margin-right: 0.25rem; text-transform: uppercase; letter-spacing: 0.06em; }}
    .filter-btn {{ padding: 0.4rem 1rem; border-radius: 100px; border: 1.5px solid rgba(63,95,71,0.2); background: white; font-size: 0.82rem; font-weight: 500; color: var(--text); cursor: pointer; transition: all 0.15s; font-family: 'DM Sans', sans-serif; }}
    .filter-btn:hover {{ border-color: var(--deep); color: var(--deep); }}
    .filter-btn.active {{ background: var(--deep); color: white; border-color: var(--deep); }}
    .search-input {{ padding: 0.4rem 1rem; border-radius: 100px; border: 1.5px solid rgba(63,95,71,0.2); font-size: 0.82rem; font-family: 'DM Sans', sans-serif; outline: none; min-width: 180px; margin-left: auto; }}
    .search-input:focus {{ border-color: var(--deep); }}

    .popular-searches {{ padding: 0.75rem 2rem; background: var(--cream); border-bottom: 1px solid rgba(63,95,71,0.08); display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }}
    .ps-label {{ font-size: 0.75rem; color: var(--muted); font-weight: 500; margin-right: 0.25rem; }}
    .ps-tag {{ background: white; border: 1px solid rgba(63,95,71,0.15); border-radius: 100px; padding: 0.3rem 0.875rem; font-size: 0.8rem; color: var(--deep); cursor: pointer; font-family: 'DM Sans', sans-serif; transition: all 0.15s; }}
    .ps-tag:hover {{ background: var(--light-green); border-color: var(--deep); }}

    /* TABLE */
    .table-wrap {{ overflow-x: auto; padding: 1.5rem 2rem 4rem; max-width: 1400px; margin: 0 auto; }}
    .results-count {{ font-size: 0.82rem; color: var(--muted); margin-bottom: 1rem; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 16px; overflow: hidden; box-shadow: 0 2px 12px rgba(63,95,71,0.07); }}
    thead {{ background: var(--deep); color: white; }}
    th {{ padding: 0.875rem 1rem; text-align: left; font-size: 0.75rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; white-space: nowrap; }}
    td {{ padding: 0.75rem 1rem; font-size: 0.85rem; border-bottom: 1px solid rgba(63,95,71,0.06); vertical-align: middle; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: var(--light-green); }}
    tr.hidden {{ display: none; }}

    .rank {{ font-family: 'Fraunces', serif; font-weight: 700; color: var(--muted); font-size: 0.9rem; width: 48px; }}
    .park-name {{ font-weight: 600; color: var(--text); min-width: 180px; }}
    .location {{ min-width: 130px; }}
    .loc-link {{ color: var(--deep); text-decoration: none; font-size: 0.82rem; }}
    .loc-link:hover {{ text-decoration: underline; }}
    .score {{ width: 90px; }}
    .score-gold {{ background: #FFF3CD; color: #7D5A00; padding: 0.2rem 0.6rem; border-radius: 6px; font-weight: 700; font-size: 0.82rem; white-space: nowrap; }}
    .score-silver {{ background: #F0F0F0; color: #555; padding: 0.2rem 0.6rem; border-radius: 6px; font-weight: 700; font-size: 0.82rem; white-space: nowrap; }}
    .score-bronze {{ background: #FDE8D8; color: #8B4513; padding: 0.2rem 0.6rem; border-radius: 6px; font-weight: 700; font-size: 0.82rem; white-space: nowrap; }}
    .score-low {{ background: #F5F5F5; color: #999; padding: 0.2rem 0.6rem; border-radius: 6px; font-weight: 600; font-size: 0.82rem; white-space: nowrap; }}
    .rating {{ color: var(--muted); font-size: 0.82rem; white-space: nowrap; }}
    .powered {{ font-size: 0.82rem; color: var(--text); white-space: nowrap; }}
    .yes {{ color: var(--leaf); font-weight: 700; }}
    .no {{ color: #ccc; }}
    .book-btn {{ background: var(--deep); color: white; padding: 0.35rem 0.875rem; border-radius: 6px; text-decoration: none; font-size: 0.78rem; font-weight: 600; white-space: nowrap; transition: opacity 0.2s; }}
    .book-btn:hover {{ opacity: 0.85; }}

    /* FOOTER */
    footer {{ background: var(--dark); padding: 2rem; text-align: center; }}
    footer p {{ color: rgba(255,255,255,0.3); font-size: 0.78rem; }}
    footer a {{ color: rgba(255,255,255,0.4); text-decoration: none; }}

    @media (max-width: 768px) {{
      .table-wrap {{ padding: 1rem; }}
      .filters {{ padding: 1rem; }}
      th, td {{ padding: 0.6rem 0.75rem; }}
      .search-input {{ margin-left: 0; width: 100%; }}
    }}
  </style>
</head>
<body>

<nav class="nav">
  <a href="/" class="nav-logo">
    Family Holiday Parks
    <span class="nav-sub" style="display:block">Holiday Parks Ranked By Families, For Families</span>
  </a>
  <a href="/" class="nav-back">← Back to home</a>
</nav>

<div class="page-header">
  <div class="page-header-inner">
    <h1>Australia's Best Family Holiday Parks</h1>
    <p class="hero-sub">Real family scores, reviews and kid-friendly features like pools, playgrounds, jumping pillows and powered sites.</p>
    <p class="hero-trust">{len(parks)} parks scored &bull; No sponsored rankings &bull; Updated {today}</p>
  </div>
</div>

<div class="filters">
  <span class="filter-label">Filter:</span>
  <button class="filter-btn active" onclick="filterState('all')">All Australia</button>
  <button class="filter-btn" onclick="filterState('qld')">QLD</button>
  <button class="filter-btn" onclick="filterState('nsw')">NSW</button>
  <button class="filter-btn" onclick="filterState('vic')">VIC</button>
  <button class="filter-btn" onclick="filterState('sa')">SA</button>
  <button class="filter-btn" onclick="filterState('wa')">WA</button>
  <button class="filter-btn" onclick="filterState('tas')">TAS</button>
  <button class="filter-btn" onclick="filterState('nt')">NT</button>
  <input type="text" class="search-input" placeholder="Search parks..." oninput="searchParks(this.value)">
</div>

<div class="popular-searches">
  <span class="ps-label">Popular searches:</span>
  <button class="ps-tag" onclick="searchParks('caravan')">Family caravan parks</button>
  <button class="ps-tag" onclick="searchParks('pool')">Caravan parks with pools</button>
  <button class="ps-tag" onclick="searchParks('big4')">Best caravan parks for kids</button>
  <button class="ps-tag" onclick="searchParks('')">Holiday parks with powered sites</button>
</div>

<div class="table-wrap">
  <p class="results-count" id="results-count">{len(parks)} parks</p>
  <table id="leaderboard">
    <thead>
      <tr>
        <th>Rank</th>
        <th>Photo</th>
        <th>Park</th>
        <th>Location</th>
        <th>State</th>
        <th>Family Score</th>
        <th>Google Rating</th>
        <th>Powered Site</th>
        <th>Pets</th>
        <th>WiFi</th>
        <th>Book</th>
      </tr>
    </thead>
    <tbody id="table-body">
{rows_html}
    </tbody>
  </table>
</div>

<footer>
  <p><a href="/">Family Holiday Parks</a> · familyholidayparks.com.au · No sponsored rankings</p>
</footer>

<script>
  let currentState = 'all';
  let currentSearch = '';

  function filterState(state) {{
    currentState = state;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    event.target.classList.add('active');
    applyFilters();
  }}

  function searchParks(val) {{
    currentSearch = val.toLowerCase();
    applyFilters();
  }}

  function applyFilters() {{
    const rows = document.querySelectorAll('#table-body tr');
    let visible = 0;
    let rank = 1;

    rows.forEach(row => {{
      const state = row.dataset.state;
      const name = row.querySelector('.park-name')?.textContent.toLowerCase() || '';
      const loc = row.querySelector('.location')?.textContent.toLowerCase() || '';

      const stateMatch = currentState === 'all' || state === currentState;
      const searchMatch = !currentSearch || name.includes(currentSearch) || loc.includes(currentSearch);

      if (stateMatch && searchMatch) {{
        row.classList.remove('hidden');
        row.querySelector('.rank').textContent = '#' + rank++;
        visible++;
      }} else {{
        row.classList.add('hidden');
      }}
    }});

    document.getElementById('results-count').textContent = visible + ' parks';
  }}
</script>

</body>
</html>'''

    output = public_dir / "top-rated.html"
    output.write_text(html, encoding='utf-8')
    print(f"Generated: {output}")
    print(f"URL: https://familyholidayparks.com.au/top-rated")

if __name__ == '__main__':
    generate()
