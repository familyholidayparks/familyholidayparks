#!/usr/bin/env python3
"""
generate_homepage.py — Airbnb-style location directory homepage
Each card = one location. Photo from hero-image.txt. Data from scores.json.

Usage:
  python generate_homepage.py
  python generate_homepage.py --publish
"""
import argparse, csv, json, subprocess, sys
from pathlib import Path
from html import escape as esc

PROJECT = Path(__file__).resolve().parent
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
                        import re as _re
                        nums = _re.findall(r'\d+', str(val))
                        if nums:
                            prices.append(int(nums[0]))
                            found = True
                            break
                # Fallback: read master.json for this park
                if not found:
                    import re as _re
                    slug = p.get("slug","")
                    if not slug:
                        n = p.get("park_name","").lower()
                        n = "".join(c if c.isalnum() or c==" " else "" for c in n)
                        slug = "-".join(n.split())
                    mf = PROJECT / "parks" / slug / "master.json"
                    if mf.exists():
                        try:
                            md = json.loads(mf.read_text(encoding="utf-8"))
                            pd2 = md.get("prices") or {}
                            for key in ["powered_weekday","powered","unpowered_weekday","unpowered"]:
                                val = pd2.get(key) or md.get(key) or ""
                                if val:
                                    nums = _re.findall(r'\d+', str(val))
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


def location_card(loc):
    """Render one Airbnb-style location card."""
    name = esc(loc["name"])
    state = esc(loc["state_name"])
    url = f"/{esc(loc['page_url'])}"
    park_count = loc["park_count"]
    total_reviews = loc["total_reviews"]
    min_price = loc["min_price"]
    hero_img = loc["hero_img"]

    # Photo
    if hero_img:
        img_html = f'<img src="{esc(hero_img)}" alt="{name}" loading="lazy">'
    else:
        img_html = '<div class="ph">🏕</div>'

    # Stats line
    stats_parts = [f"{park_count} park{'s' if park_count != 1 else ''}"]
    if min_price:
        stats_parts.append(f"from ${min_price}/night")
    if total_reviews:
        stats_parts.append(f"{total_reviews:,} reviews")
    stats_line = " · ".join(stats_parts)

    price_line = f"From ${min_price}/night" if min_price else ""
    reviews_line = f"{total_reviews:,} reviews" if total_reviews else ""

    return f'''<a class="lcard" href="{url}">
  <div class="lcard-img">{img_html}</div>
  <div class="lcard-body">
    <div class="lcard-name">{name}</div>
    {f'<div class="lcard-price">{esc(price_line)}</div>' if price_line else ''}
    {f'<div class="lcard-reviews">{esc(reviews_line)}</div>' if reviews_line else ''}
  </div>
</a>'''


def row_section(title, section_id, locations, see_more_url=None):
    if not locations:
        return ""
    cards = "\n".join(location_card(loc) for loc in locations)
    return f"""
<section class="row-section" id="{section_id}">
  <div class="row-hdr">
    <h2>{esc(title)}</h2>
  </div>
  <div class="row-scroll">
    {cards}
  </div>
</section>"""


def build():
    print("Loading locations...")
    locations = load_locations()
    print(f"  {len(locations)} locations loaded")

    # Sort by review count (most popular first) for top row
    by_reviews = sorted(locations, key=lambda l: l["total_reviews"], reverse=True)
    # Top row: most reviewed locations with hero images
    top_locs = [l for l in by_reviews if l["hero_img"]][:12]
    if len(top_locs) < 6:
        top_locs = by_reviews[:12]

    # Track which locations are already in top row
    top_slugs = {l["slug"] for l in top_locs}

    # Build all rows
    rows_html = row_section("Popular locations", "popular", top_locs)

    # State rows — exclude those already in top row
    by_state = {}
    for loc in locations:
        s = loc["state"]
        by_state.setdefault(s, []).append(loc)

    for state in STATE_ORDER:
        if state not in by_state:
            continue
        state_locs = sorted(by_state[state], key=lambda l: l["total_reviews"], reverse=True)
        # Don't show ones already in top row
        state_locs_filtered = [l for l in state_locs if l["slug"] not in top_slugs]
        if not state_locs_filtered:
            state_locs_filtered = state_locs  # show all if nothing left
        if not state_locs_filtered:
            continue
        state_name = STATE_NAMES.get(state, state.upper())
        rows_html += row_section(
            f"{state_name}",
            f"state-{state}",
            state_locs_filtered[:10],
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Family Holiday Parks — Australia's Best Rated Family Holiday Parks</title>
<meta name="description" content="Find the best family holiday parks in Australia. 500+ parks scored across every state.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,wght@0,700;1,600&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<script async src="https://www.googletagmanager.com/gtag/js?id=G-VVPFY2WRM1"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','G-VVPFY2WRM1');</script>
<script>!function(f,b,e,v,n,t,s){{if(f.fbq)return;n=f.fbq=function(){{n.callMethod?n.callMethod.apply(n,arguments):n.queue.push(arguments)}};if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}}(window,document,'script','https://connect.facebook.net/en_US/fbevents.js');fbq('init','909873062100576');fbq('track','PageView');</script>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --teal:#0072CE;--teal-h:#005fa8;
  --text:#222;--text2:#717171;
  --border:#DDDDDD;--bg:#fff;--bg2:#F7F7F7;
  --r:12px;
}}
html{{scroll-behavior:smooth}}
body{{font-family:'DM Sans',sans-serif;background:var(--bg);color:var(--text);-webkit-font-smoothing:antialiased}}

/* ── NAV ── */
.nav{{position:sticky;top:0;z-index:100;background:rgba(255,255,255,0.97);border-bottom:1px solid var(--border);backdrop-filter:blur(8px)}}
.nav-inner{{max-width:1280px;margin:0 auto;padding:0 24px;height:72px;display:flex;align-items:center;justify-content:space-between;gap:16px}}
.nav-logo img{{height:36px;width:auto;display:block;flex-shrink:0}}
.nav-search{{flex:1;max-width:420px;background:#fff;border:1.5px solid var(--border);border-radius:100px;padding:10px 16px;display:flex;align-items:center;gap:10px;box-shadow:0 1px 6px rgba(0,0,0,0.08);cursor:pointer;transition:box-shadow 0.15s}}
.nav-search:hover{{box-shadow:0 2px 12px rgba(0,0,0,0.12);border-color:var(--text)}}
.nav-search svg{{width:15px;height:15px;color:var(--text);flex-shrink:0}}
.nav-search-text{{font-size:14px;color:var(--text2);white-space:nowrap}}
.nav-right{{display:flex;align-items:center;gap:8px;flex-shrink:0}}
.nav-link{{font-size:14px;font-weight:500;color:var(--text);text-decoration:none;padding:8px 12px;border-radius:8px;transition:background 0.15s;white-space:nowrap}}
.nav-link:hover{{background:var(--bg2)}}
.nav-btn{{font-size:14px;font-weight:700;color:white;background:var(--teal);text-decoration:none;padding:10px 20px;border-radius:100px;transition:background 0.15s;white-space:nowrap}}
.nav-btn:hover{{background:var(--teal-h)}}

/* ── MOBILE SEARCH BAR (replaces nav on mobile) ── */
.mobile-search{{display:none;padding:10px 16px;background:#fff;border-bottom:1px solid var(--border)}}
.mobile-search-bar{{background:#fff;border:1.5px solid var(--border);border-radius:100px;padding:10px 16px;display:flex;align-items:center;gap:10px;box-shadow:0 1px 6px rgba(0,0,0,0.08)}}
.mobile-search-bar svg{{width:16px;height:16px;color:var(--text);flex-shrink:0}}
.mobile-search-bar input{{border:none;outline:none;font-size:14px;color:var(--text);font-family:inherit;width:100%;background:transparent}}
.mobile-search-bar input::placeholder{{color:var(--text2)}}

/* ── BOTTOM NAV (mobile only) ── */
.bottom-nav{{display:none;position:fixed;bottom:0;left:0;right:0;z-index:200;background:#fff;border-top:1px solid var(--border);padding:8px 0 max(16px, env(safe-area-inset-bottom))}}
.bottom-nav-inner{{display:flex;justify-content:space-around;align-items:center;max-width:480px;margin:0 auto}}
.bnav-btn{{display:flex;flex-direction:column;align-items:center;gap:3px;font-size:10px;font-weight:500;color:var(--text2);text-decoration:none;cursor:pointer;padding:4px 16px;border:none;background:none;font-family:inherit}}
.bnav-btn.active{{color:var(--teal)}}
.bnav-btn svg{{width:24px;height:24px}}
.bnav-btn .ice{{font-size:20px;line-height:1.2}}

/* ── STATE TABS ── */
.tabs{{border-bottom:1px solid var(--border);padding:0 24px;display:flex;gap:0;overflow-x:auto;scrollbar-width:none;background:var(--bg)}}
.tabs::-webkit-scrollbar{{display:none}}
.tab{{padding:14px 16px;font-size:14px;font-weight:500;color:var(--text2);border-bottom:2px solid transparent;white-space:nowrap;cursor:pointer;transition:color 0.15s;text-decoration:none;display:block}}
.tab:hover{{color:var(--text)}}
.tab.active{{color:var(--text);border-bottom-color:var(--text);font-weight:600}}
.tab .tab-full{{display:inline}}
.tab .tab-abbr{{display:none}}

/* ── ROWS ── */
.row-section{{padding:32px 0 0;border-bottom:1px solid var(--border)}}
.row-hdr{{max-width:1280px;margin:0 auto;padding:0 24px;display:flex;align-items:baseline;justify-content:space-between;margin-bottom:16px}}
.row-hdr h2{{font-family:'Fraunces',serif;font-size:clamp(1.1rem,2vw,1.45rem);font-weight:700;color:var(--text);letter-spacing:-0.01em}}
.see-more{{font-size:14px;font-weight:600;color:var(--text);text-decoration:underline;text-underline-offset:2px;text-decoration-color:var(--border);white-space:nowrap}}
.see-more:hover{{text-decoration-color:var(--text)}}
.row-scroll{{display:flex;gap:20px;overflow-x:auto;padding:4px 24px 32px;scrollbar-width:none;scroll-snap-type:x mandatory;-webkit-overflow-scrolling:touch}}
.row-scroll::-webkit-scrollbar{{display:none}}

/* ── LOCATION CARD ── */
.lcard{{flex:0 0 240px;min-width:240px;text-decoration:none;color:inherit;scroll-snap-align:start;cursor:pointer}}
.lcard-img{{border-radius:var(--r);overflow:hidden;aspect-ratio:20/19;background:var(--bg2);margin-bottom:10px;position:relative}}
.lcard-img img{{width:100%;height:100%;object-fit:cover;display:block;transition:transform 0.35s ease}}
.lcard:hover .lcard-img img{{transform:scale(1.04)}}
.ph{{width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:2.5rem;color:var(--border);background:var(--bg2)}}
.lcard-body{{padding:0 2px}}
.lcard-name{{font-size:15px;font-weight:600;color:var(--text);line-height:1.3;margin-bottom:2px}}
.lcard-price{{font-size:13px;color:var(--text);font-weight:500;margin-bottom:1px}}
.lcard-reviews{{font-size:13px;color:var(--text2)}}

/* ── FOOTER ── */
.footer{{background:var(--bg2);border-top:1px solid var(--border);padding:48px 24px 0}}
.footer-inner{{max-width:1280px;margin:0 auto}}
.footer-cols{{display:grid;grid-template-columns:1.6fr 1fr 1fr 1fr;gap:40px;padding-bottom:40px;border-bottom:1px solid var(--border)}}
.footer-brand p{{font-size:13px;color:var(--text2);line-height:1.65;margin-top:12px;max-width:260px}}
.footer-brand .contact{{font-size:13px;color:var(--text);font-weight:500;margin-top:12px;text-decoration:none;display:block}}
.footer-brand .contact:hover{{color:var(--teal)}}
.footer-col h3{{font-size:11px;font-weight:700;color:var(--text);text-transform:uppercase;letter-spacing:0.07em;margin-bottom:14px}}
.footer-col ul{{list-style:none}}
.footer-col li{{margin-bottom:9px}}
.footer-col a{{font-size:13px;color:var(--text);text-decoration:none}}
.footer-col a:hover{{text-decoration:underline;color:var(--teal)}}
.footer-bottom{{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;padding:20px 0 28px}}
.footer-copy{{font-size:13px;color:var(--text2)}}
.footer-social{{display:flex;gap:16px}}
.footer-social a{{font-size:13px;color:var(--text);font-weight:500;text-decoration:none}}
.footer-social a:hover{{color:var(--teal)}}

@media(max-width:768px){{
  /* Hide desktop nav, show mobile search */
  .nav-logo,.nav-search,.nav-right{{display:none}}
  .nav-inner{{height:0;padding:0;overflow:hidden}}
  .mobile-search{{display:block}}
  /* Show bottom nav */
  .bottom-nav{{display:block}}
  body{{padding-bottom:72px}}
  /* Tabs abbreviate */
  .tabs{{padding:0 12px}}
  .tab{{padding:11px 11px;font-size:13px}}
  .tab .tab-full{{display:none}}
  .tab .tab-abbr{{display:inline}}
  /* Cards */
  .row-section{{padding:24px 0 0}}
  .row-hdr{{padding:0 16px}}
  .row-scroll{{padding:4px 16px 20px;gap:14px}}
  .lcard{{flex:0 0 170px;min-width:170px}}
  .lcard-name{{font-size:14px}}
  /* Footer */
  .footer{{padding:32px 16px 24px}}
  .footer-cols{{grid-template-columns:1fr 1fr;gap:20px}}
  .footer-brand{{grid-column:1/-1}}
}}
@media(max-width:480px){{
  .lcard{{flex:0 0 155px;min-width:155px}}
  .footer-cols{{grid-template-columns:1fr}}
  .footer-brand{{grid-column:auto}}
}}
</style>
</head>
<body>

<!-- DESKTOP NAV -->
<nav class="nav">
  <div class="nav-inner">
    <a href="/" class="nav-logo"><img src="/images/logo.png" alt="Family Holiday Parks"></a>
    <div class="nav-search">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
      <span class="nav-search-text">Search holiday parks...</span>
    </div>
    <div class="nav-right">
      <a href="/top-rated" class="nav-link">Top rated</a>
      <a href="/icecream" class="nav-link">Leave a review</a>
    </div>
  </div>
</nav>

<!-- MOBILE SEARCH BAR -->
<div class="mobile-search">
  <div class="mobile-search-bar">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
    <input type="text" placeholder="Search holiday parks..." autocomplete="off">
  </div>
</div>

<!-- STATE TABS -->
<div class="tabs">
  <a class="tab active" href="/"><span class="tab-full">All</span><span class="tab-abbr">All</span></a>
  <a class="tab" href="#state-qld"><span class="tab-full">Queensland</span><span class="tab-abbr">QLD</span></a>
  <a class="tab" href="#state-nsw"><span class="tab-full">New South Wales</span><span class="tab-abbr">NSW</span></a>
  <a class="tab" href="#state-vic"><span class="tab-full">Victoria</span><span class="tab-abbr">VIC</span></a>
  <a class="tab" href="#state-wa"><span class="tab-full">Western Australia</span><span class="tab-abbr">WA</span></a>
  <a class="tab" href="#state-sa"><span class="tab-full">South Australia</span><span class="tab-abbr">SA</span></a>
  <a class="tab" href="#state-tas"><span class="tab-full">Tasmania</span><span class="tab-abbr">TAS</span></a>
  <a class="tab" href="#state-nt"><span class="tab-full">Northern Territory</span><span class="tab-abbr">NT</span></a>
</div>

{rows_html}

<!-- FOOTER -->
<footer class="footer">
  <div class="footer-inner">
    <div class="footer-cols">
      <div class="footer-brand">
        <img src="/images/logo.png" alt="Family Holiday Parks" style="height:32px;opacity:0.8">
        <p>Australia's family holiday park guide.<br>500+ parks scored and ranked across every state — by families, for families.</p>
        <a href="mailto:pm@familyholidayparks.com.au" class="contact">pm@familyholidayparks.com.au</a>
      </div>
      <div class="footer-col">
        <h3>Browse by state</h3>
        <ul>
          <li><a href="#state-qld">Queensland</a></li>
          <li><a href="#state-nsw">New South Wales</a></li>
          <li><a href="#state-vic">Victoria</a></li>
          <li><a href="#state-wa">Western Australia</a></li>
          <li><a href="#state-sa">South Australia</a></li>
          <li><a href="#state-tas">Tasmania</a></li>
          <li><a href="#state-nt">Northern Territory</a></li>
        </ul>
      </div>
      <div class="footer-col">
        <h3>Discover</h3>
        <ul>
          <li><a href="/top-rated">Top rated parks</a></li>
          <li><a href="/icecream">Leave a review</a></li>
          <li><a href="/top-rated">Parks with waterparks</a></li>
          <li><a href="/top-rated">Pet friendly parks</a></li>
          <li><a href="/top-rated">Beach holiday parks</a></li>
        </ul>
      </div>
      <div class="footer-col">
        <h3>About</h3>
        <ul>
          <li><a href="/top-rated">How we score parks</a></li>
          <li><a href="mailto:pm@familyholidayparks.com.au">For park owners</a></li>
          <li><a href="mailto:pm@familyholidayparks.com.au">Contact us</a></li>
        </ul>
      </div>
    </div>
    <div class="footer-bottom">
      <span class="footer-copy">© 2025 Family Holiday Parks · familyholidayparks.com.au</span>
      <div class="footer-social">
        <a href="https://instagram.com/familyholidayparks" target="_blank" rel="noopener">Instagram</a>
        <a href="https://facebook.com/familyholidayparks" target="_blank" rel="noopener">Facebook</a>
      </div>
    </div>
  </div>
</footer>

<!-- BOTTOM NAV (mobile only) -->
<nav class="bottom-nav" aria-label="Mobile navigation">
  <div class="bottom-nav-inner">
    <a href="/" class="bnav-btn active" id="bnav-explore">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
      Explore
    </a>
    <a href="#popular" class="bnav-btn" id="bnav-top">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
      Popular
    </a>
    <a href="/icecream" class="bnav-btn" id="bnav-review">
      <div class="ice"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg></div>
      Create Story
    </a>
  </div>
</nav>

<script>
// Highlight active tab on scroll
const sections = document.querySelectorAll('.row-section[id^="state-"]');
const tabs = document.querySelectorAll('.tab');
window.addEventListener('scroll', () => {{
  let current = '';
  sections.forEach(s => {{
    if (window.scrollY >= s.offsetTop - 120) current = s.id;
  }});
  tabs.forEach(t => {{
    t.classList.toggle('active', t.getAttribute('href') === '#' + current || (!current && t.getAttribute('href') === '/'));
  }});
}}, {{passive: true}});

// Bottom nav active state
const path = window.location.pathname;
if (path.includes('top-rated')) {{
  document.getElementById('bnav-top')?.classList.add('active');
  document.getElementById('bnav-explore')?.classList.remove('active');
}} else if (path.includes('icecream')) {{
  document.getElementById('bnav-review')?.classList.add('active');
  document.getElementById('bnav-explore')?.classList.remove('active');
}}
</script>

</body>
</html>"""

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html, encoding="utf-8")
    print(f"✅ Saved: {OUTPUT}")
    print(f"   {len(locations)} location cards generated")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    build()
    if args.publish:
        print("Running: git add -A")
        subprocess.run(["git", "add", "-A"], cwd=PROJECT)
        r = subprocess.run(["git", "commit", "-m", "Regenerate homepage"], cwd=PROJECT, capture_output=True, text=True)
        out = r.stdout.strip()
        print(out or "Nothing to commit.")
        if "nothing to commit" not in out.lower():
            subprocess.run(["git", "push"], cwd=PROJECT)
            print("Pushed ✅")

if __name__ == "__main__":
    main()
