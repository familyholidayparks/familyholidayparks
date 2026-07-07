#!/usr/bin/env python3
"""
generate_leaderboard.py — builds public/best-holiday-parks-australia.html

Australia's highest-scored family holiday parks, ranked nationally.
Reads live data from locations/{state}/{slug}/scores.json (+ prices.json,
master.json) so the page updates whenever the enrichment pipeline runs.

Usage:
    python generate_leaderboard.py              # build the page
    python generate_leaderboard.py --snapshot   # also write today's score
                                                # snapshot for future trends
    python generate_leaderboard.py --publish    # build, commit and push

Trend column:
    Snapshots live in history/leaderboard/YYYY-MM-DD.json as
    {"<park key>": <score>, ...}. The build compares current scores to the
    most recent snapshot from a previous day. With no prior snapshot every
    park shows "New". Run --snapshot weekly/monthly to activate trends.
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
LOCATIONS_DIR = PROJECT_DIR / "locations"
PUBLIC_DIR = PROJECT_DIR / "public"
HISTORY_DIR = PROJECT_DIR / "history" / "leaderboard"
OUTPUT_NAME = "best-holiday-parks-australia.html"

SSR_ROWS = 50          # rows rendered as real HTML for SEO / first paint
PAGE_CHUNK = 50        # rows appended per "Show more"
JSONLD_ITEMS = 25      # parks in ItemList structured data

STATE_LABELS = {
    "qld": "QLD", "nsw": "NSW", "vic": "VIC", "wa": "WA",
    "sa": "SA", "tas": "TAS", "nt": "NT", "act": "ACT",
}
STATE_URL = {
    "qld": "queensland", "nsw": "new-south-wales", "vic": "victoria",
    "wa": "western-australia", "sa": "south-australia", "tas": "tasmania",
    "nt": "northern-territory", "act": "act",
}


def esc(s) -> str:
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def norm_name(name: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", "", str(name or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def as_float(v):
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def parse_price(entry) -> tuple:
    """Return (display, numeric) from a prices.json value."""
    if isinstance(entry, dict):
        disp = str(entry.get("display") or "").strip()
        num = as_float(entry.get("price"))
        if not disp and num:
            disp = f"${int(num)}/night"
        return disp, num
    if isinstance(entry, (int, float)):
        return f"${int(entry)}/night", float(entry)
    if isinstance(entry, str) and entry.strip():
        m = re.search(r"(\d+)", entry)
        return entry.strip(), (float(m.group(1)) if m else None)
    return "", None


def facility_chips(item: dict) -> list:
    """Derive up to 3 honest facility chips from scores.json text fields."""
    chips = []
    water = str(item.get("water_fun") or "").lower()
    kids = str(item.get("kids_play") or "").lower()

    if any(w in water for w in ("waterpark", "water park", "slide")):
        chips.append("Waterpark")
    elif "splash" in water:
        chips.append("Splash park")
    elif "pool" in water:
        chips.append("Pool")

    if "jumping pillow" in kids or "bouncing pillow" in kids:
        chips.append("Jumping pillow")
    elif "playground" in kids:
        chips.append("Playground")
    elif "kids club" in kids:
        chips.append("Kids club")

    beach = item.get("nearest_beach_cached")
    beach_km = None
    if isinstance(beach, dict):
        beach_km = as_float(beach.get("km"))
    if beach_km is None:
        beach_km = as_float(item.get("beach_km"))
    if beach_km is not None and beach_km <= 1.0:
        chips.append("Walk to beach")
    elif beach_km is not None and beach_km <= 5.0:
        chips.append(f"Beach {beach_km:.0f} km")

    pet = str(item.get("pet_detail") or "").lower()
    if len(chips) < 3 and ("yes" in pet or "welcome" in pet or "friendly" in pet):
        chips.append("Pet friendly")

    return chips[:3]


def latest_snapshot() -> dict:
    """Most recent snapshot from a previous day (empty dict if none)."""
    if not HISTORY_DIR.exists():
        return {}
    today_name = f"{date.today().isoformat()}.json"
    snaps = sorted(
        [p for p in HISTORY_DIR.glob("*.json") if p.name != today_name],
        reverse=True,
    )
    for snap in snaps:
        try:
            data = json.loads(snap.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {}


def load_parks() -> list:
    """Walk every location, collect parks, dedupe, rank nationally."""
    seen: dict = {}
    if not LOCATIONS_DIR.exists():
        print(f"ERROR: {LOCATIONS_DIR} not found", file=sys.stderr)
        return []

    for state_dir in sorted(LOCATIONS_DIR.iterdir()):
        if not state_dir.is_dir() or state_dir.name not in STATE_LABELS:
            continue
        state = state_dir.name
        for loc_dir in sorted(state_dir.iterdir()):
            scores_path = loc_dir / "scores.json"
            if not scores_path.exists():
                continue
            try:
                raw = json.loads(scores_path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  skip {scores_path}: {e}", file=sys.stderr)
                continue
            if not isinstance(raw, list):
                continue

            updated = date.fromtimestamp(scores_path.stat().st_mtime)
            prices_raw = {}
            prices_path = loc_dir / "prices.json"
            if prices_path.exists():
                try:
                    prices_raw = json.loads(prices_path.read_text(encoding="utf-8"))
                    if not isinstance(prices_raw, dict):
                        prices_raw = {}
                except Exception:
                    prices_raw = {}
            prices_norm = {norm_name(k): v for k, v in prices_raw.items()}

            loc_slug = loc_dir.name
            loc_label = loc_slug.replace("-", " ").title()
            loc_url = f"/{loc_slug}-{STATE_URL[state]}"

            for item in raw:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("park_name") or item.get("name") or "").strip()
                score = as_float(item.get("total_score"))
                if not name or score is None or score <= 0:
                    continue

                rating = as_float(
                    item.get("google_rating") or item.get("rating") or item.get("totalScore")
                )
                reviews = item.get("review_count") or item.get("reviews") or 0
                try:
                    reviews = int(reviews)
                except (TypeError, ValueError):
                    reviews = 0

                price_disp, price_num = parse_price(prices_norm.get(norm_name(name)))

                park = {
                    "name": name,
                    "score": round(score),
                    "rating": rating,
                    "reviews": reviews,
                    "price_disp": price_disp,
                    "price_num": price_num,
                    "chips": facility_chips(item),
                    "state": state,
                    "loc_label": loc_label,
                    "loc_url": loc_url,
                    "updated": updated.isoformat(),
                }

                key = norm_name(name)
                prev = seen.get(key)
                # Dedupe: keep the record with the most complete data
                if prev is None or (reviews, score) > (prev["reviews"], prev["score"]):
                    seen[key] = park

    parks = sorted(
        seen.values(),
        key=lambda p: (-p["score"], -p["reviews"], p["name"].lower()),
    )

    snapshot = latest_snapshot()
    for i, p in enumerate(parks):
        p["rank"] = i + 1
        prior = as_float(snapshot.get(norm_name(p["name"])))
        if prior is None:
            p["trend"] = "new"
            p["delta"] = 0
        else:
            diff = p["score"] - prior
            if diff >= 1:
                p["trend"] = "up"
            elif diff <= -1:
                p["trend"] = "down"
            else:
                p["trend"] = "steady"
            p["delta"] = round(diff)
    return parks


def write_snapshot(parks: list) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    out = HISTORY_DIR / f"{date.today().isoformat()}.json"
    data = {norm_name(p["name"]): p["score"] for p in parks}
    out.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"Snapshot written: {out} ({len(data)} parks)")


# ---------------------------------------------------------------- rendering

TREND_SVG = {
    "up": '<svg width="10" height="10" viewBox="0 0 10 10"><path d="M5 1 L9 7 L1 7 Z" fill="#0072CE"/></svg>',
    "down": '<svg width="10" height="10" viewBox="0 0 10 10"><path d="M5 9 L1 3 L9 3 Z" fill="#b3261e"/></svg>',
    "steady": '<svg width="10" height="10" viewBox="0 0 10 10"><rect x="1" y="4" width="8" height="2" rx="1" fill="#999"/></svg>',
}


def trend_pill(p: dict) -> str:
    t = p["trend"]
    if t == "new":
        return '<span class="trend trend-new">New</span>'
    label = {"up": f"+{p['delta']}", "down": str(p["delta"]), "steady": "="}[t]
    return f'<span class="trend trend-{t}">{TREND_SVG[t]}{esc(label)}</span>'


def fmt_updated(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
        return d.strftime("%-d %b" if sys.platform != "win32" else "%#d %b")
    except Exception:
        return "—"


def render_row(p: dict) -> str:
    rank_cls = " lb-rank--top" if p["rank"] <= 3 else ""
    google = (
        f'{p["rating"]:.1f} · {p["reviews"]:,}'
        if p["rating"] and p["reviews"]
        else "—"
    )
    chips = "".join(f'<span class="lb-chip">{esc(c)}</span>' for c in p["chips"])
    price = esc(p["price_disp"]) if p["price_disp"] else "—"
    return f'''<a class="lb-row" href="{esc(p["loc_url"])}" data-state="{esc(p["state"])}">
  <span class="lb-rank{rank_cls}">{p["rank"]}</span>
  <span class="lb-park">
    <span class="lb-name">{esc(p["name"])}</span>
    <span class="lb-loc">{esc(p["loc_label"])}, {esc(STATE_LABELS[p["state"]])}</span>
    <span class="lb-chips">{chips}</span>
  </span>
  {trend_pill(p)}
  <span class="lb-score">{p["score"]}</span>
  <span class="lb-google">{google}</span>
  <span class="lb-price">{price}</span>
  <span class="lb-updated">{fmt_updated(p["updated"])}</span>
</a>'''


CSS = r"""
:root {
  --text: #222; --text-2: #717171; --border: #eee;
  --teal: #0072CE; --r: 12px; --nav-h: 60px; --ctrl-h: 96px;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Inter', sans-serif; color: var(--text); background: #fff; line-height: 1.5; }

.nav {
  position: sticky; top: 0; z-index: 100; height: var(--nav-h);
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 20px; background: #fff; border-bottom: 1px solid var(--border);
}
.nav-logo img { height: 28px; display: block; }
.nav-link { font-size: 14px; font-weight: 600; color: var(--teal); text-decoration: none; }
.nav-link:hover { text-decoration: underline; }

.lb-header { max-width: 900px; margin: 0 auto; padding: 30px 20px 18px; }
.lb-header h1 {
  font-family: 'Fraunces', serif; font-size: 27px; font-weight: 700;
  line-height: 1.15; letter-spacing: -0.01em; margin-bottom: 10px;
}
.lb-intro { font-size: 14px; color: var(--text-2); line-height: 1.65; max-width: 640px; }
.lb-stamp { font-size: 12px; color: var(--text-2); margin-top: 10px; }
.lb-stamp strong { color: var(--text); font-weight: 600; }

.lb-controls {
  position: sticky; top: var(--nav-h); z-index: 90;
  background: #fff; border-bottom: 1px solid var(--border);
  padding: 8px 0 10px;
}
.lb-controls-inner { max-width: 900px; margin: 0 auto; padding: 0 20px; }
.chip-row { display: flex; gap: 8px; overflow-x: auto; scrollbar-width: none; padding-bottom: 8px; }
.chip-row::-webkit-scrollbar { display: none; }
.chip-row button {
  flex-shrink: 0; font-family: inherit; font-size: 13px; font-weight: 600;
  color: var(--text); background: #f7f7f7; border: 1px solid var(--border);
  border-radius: 100px; padding: 7px 15px; cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.chip-row button:hover { background: #efefef; border-color: #ccc; }
.chip-row button.active { background: #222; border-color: #222; color: #fff; }
.sort-row { display: flex; gap: 8px; align-items: center; overflow-x: auto; scrollbar-width: none; }
.sort-row::-webkit-scrollbar { display: none; }
.sort-label { font-size: 12px; color: var(--text-2); flex-shrink: 0; }
.sort-row button {
  flex-shrink: 0; font-family: inherit; font-size: 12px; font-weight: 600;
  color: var(--text); background: #fff; border: 1px solid #ccc;
  border-radius: 100px; padding: 6px 13px; cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.sort-row button.active { background: #222; border-color: #222; color: #fff; }

.lb-list { max-width: 900px; margin: 0 auto; padding: 14px 20px 30px; }
.lb-colhead {
  display: none; font-size: 11px; font-weight: 600; color: var(--text-2);
  text-transform: uppercase; letter-spacing: 0.05em;
  padding: 0 14px 8px;
}
.lb-row {
  display: grid;
  grid-template-columns: 40px 1fr 62px 52px;
  grid-template-areas: "rank park trend score";
  gap: 4px 12px; align-items: center;
  padding: 13px 14px; margin-bottom: 8px;
  border: 1px solid var(--border); border-radius: var(--r);
  text-decoration: none; color: var(--text);
  transition: border-color 0.15s, box-shadow 0.15s;
}
.lb-row:hover { border-color: #ccc; box-shadow: 0 2px 10px rgba(0,0,0,0.06); }
.lb-rank {
  grid-area: rank;
  font-family: 'Fraunces', serif; font-size: 16px; font-weight: 700;
  color: var(--text-2); text-align: center;
}
.lb-rank--top {
  color: #fff; background: var(--teal); border-radius: 50%;
  width: 32px; height: 32px; display: flex; align-items: center;
  justify-content: center; font-size: 15px; margin: 0 auto;
}
.lb-park { grid-area: park; min-width: 0; }
.lb-name { display: block; font-size: 14px; font-weight: 700; }
.lb-loc { display: block; font-size: 12px; color: var(--text-2); margin-top: 1px; }
.lb-chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 5px; }
.lb-chip {
  font-size: 10.5px; font-weight: 600; color: var(--text-2);
  border: 1px solid var(--border); border-radius: 100px; padding: 2px 8px;
  white-space: nowrap;
}
.lb-score {
  grid-area: score;
  font-size: 13px; font-weight: 700; color: var(--text);
  border: 1.5px solid #222; border-radius: 50%;
  width: 38px; height: 38px; display: flex;
  align-items: center; justify-content: center; margin: 0 auto;
}
.trend {
  grid-area: trend;
  display: inline-flex; align-items: center; justify-content: center; gap: 4px;
  font-size: 11px; font-weight: 700; border-radius: 100px;
  padding: 3px 9px; white-space: nowrap; justify-self: center;
}
.trend-up { color: var(--teal); background: #f0f7ff; }
.trend-down { color: #b3261e; background: #fdf0ef; }
.trend-steady { color: #777; background: #f5f5f5; }
.trend-new { color: var(--text-2); border: 1px solid var(--border); }
.lb-google, .lb-price, .lb-updated { display: none; font-size: 12.5px; color: var(--text-2); }

.lb-more {
  display: block; width: 100%; max-width: 900px; margin: 0 auto 40px;
  font-family: inherit; font-size: 14px; font-weight: 600;
  color: var(--text); background: #fff; border: 1px solid #ccc;
  border-radius: 100px; padding: 13px; cursor: pointer;
  transition: background 0.15s;
}
.lb-more:hover { background: #fafafa; }
.lb-empty { text-align: center; font-size: 14px; color: var(--text-2); padding: 30px 0; }

.lb-method { max-width: 900px; margin: 0 auto; padding: 0 20px 44px; }
.lb-method h2 { font-family: 'Fraunces', serif; font-size: 19px; margin-bottom: 8px; }
.lb-method p { font-size: 13px; color: var(--text-2); line-height: 1.7; max-width: 640px; }

.site-footer {
  border-top: 1px solid var(--border); padding: 26px 20px;
  text-align: center; font-size: 12px; color: var(--text-2);
}
.site-footer a { color: var(--teal); text-decoration: none; }

@media (min-width: 768px) {
  .lb-header h1 { font-size: 32px; }
  .lb-colhead {
    display: grid;
    grid-template-columns: 40px 1fr 62px 52px 96px 92px 66px;
    gap: 12px;
  }
  .lb-row {
    grid-template-columns: 40px 1fr 62px 52px 96px 92px 66px;
    grid-template-areas: "rank park trend score google price updated";
  }
  .lb-google { display: block; grid-area: google; }
  .lb-price { display: block; grid-area: price; }
  .lb-updated { display: block; grid-area: updated; }
}
"""

JS = r"""
const PAGE_CHUNK = __PAGE_CHUNK__;
const ALL_PARKS = __PARKS_JSON__;
const STATE_LABELS = __STATE_LABELS__;

let activeState = 'all';
let activeSort = 'score';
let shown = __SSR_ROWS__;

const listEl = document.getElementById('lb-list');
const moreBtn = document.getElementById('lb-more');

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

const TREND_SVG = {
  up: '<svg width="10" height="10" viewBox="0 0 10 10"><path d="M5 1 L9 7 L1 7 Z" fill="#0072CE"/></svg>',
  down: '<svg width="10" height="10" viewBox="0 0 10 10"><path d="M5 9 L1 3 L9 3 Z" fill="#b3261e"/></svg>',
  steady: '<svg width="10" height="10" viewBox="0 0 10 10"><rect x="1" y="4" width="8" height="2" rx="1" fill="#999"/></svg>'
};

function trendHtml(p) {
  if (p.trend === 'new') return '<span class="trend trend-new">New</span>';
  const label = p.trend === 'up' ? '+' + p.delta : (p.trend === 'down' ? String(p.delta) : '=');
  return '<span class="trend trend-' + p.trend + '">' + TREND_SVG[p.trend] + esc(label) + '</span>';
}

function fmtUpdated(iso) {
  try {
    const d = new Date(iso + 'T00:00:00');
    return d.toLocaleDateString('en-AU', { day: 'numeric', month: 'short' });
  } catch (e) { return '—'; }
}

function rowHtml(p) {
  const topCls = p.rank <= 3 ? ' lb-rank--top' : '';
  const google = (p.rating && p.reviews)
    ? p.rating.toFixed(1) + ' · ' + p.reviews.toLocaleString() : '—';
  const chips = p.chips.map(c => '<span class="lb-chip">' + esc(c) + '</span>').join('');
  const price = p.price_disp ? esc(p.price_disp) : '—';
  return '<a class="lb-row" href="' + esc(p.loc_url) + '" data-state="' + esc(p.state) + '">' +
    '<span class="lb-rank' + topCls + '">' + p.rank + '</span>' +
    '<span class="lb-park"><span class="lb-name">' + esc(p.name) + '</span>' +
    '<span class="lb-loc">' + esc(p.loc_label) + ', ' + esc(STATE_LABELS[p.state]) + '</span>' +
    '<span class="lb-chips">' + chips + '</span></span>' +
    trendHtml(p) +
    '<span class="lb-score">' + p.score + '</span>' +
    '<span class="lb-google">' + google + '</span>' +
    '<span class="lb-price">' + price + '</span>' +
    '<span class="lb-updated">' + fmtUpdated(p.updated) + '</span></a>';
}

function currentSet() {
  let set = activeState === 'all'
    ? ALL_PARKS.slice()
    : ALL_PARKS.filter(p => p.state === activeState);
  if (activeSort === 'price') {
    set.sort((a, b) => (a.price_num || 99999) - (b.price_num || 99999));
  } else if (activeSort === 'reviews') {
    set.sort((a, b) => (b.reviews || 0) - (a.reviews || 0));
  } else if (activeSort === 'rating') {
    set.sort((a, b) => (b.rating || 0) - (a.rating || 0) || (b.reviews || 0) - (a.reviews || 0));
  } else {
    set.sort((a, b) => a.rank - b.rank);
  }
  return set;
}

function render() {
  const set = currentSet();
  const slice = set.slice(0, shown);
  listEl.innerHTML = slice.length
    ? slice.map(rowHtml).join('')
    : '<div class="lb-empty">No parks match this filter yet.</div>';
  const remaining = set.length - slice.length;
  if (remaining > 0) {
    moreBtn.style.display = '';
    moreBtn.textContent = 'Show ' + Math.min(PAGE_CHUNK, remaining) + ' more (' + remaining + ' remaining)';
  } else {
    moreBtn.style.display = 'none';
  }
}

document.querySelectorAll('#state-chips button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('#state-chips button').forEach(b => b.classList.toggle('active', b === btn));
    activeState = btn.dataset.state;
    shown = PAGE_CHUNK;
    render();
  });
});

document.querySelectorAll('#sort-pills button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('#sort-pills button').forEach(b => b.classList.toggle('active', b === btn));
    activeSort = btn.dataset.sort;
    shown = PAGE_CHUNK;
    render();
  });
});

moreBtn.addEventListener('click', () => {
  shown += PAGE_CHUNK;
  render();
});
"""


def build_page(parks: list) -> str:
    today = date.today()
    total = len(parks)
    states_present = sorted({p["state"] for p in parks}, key=lambda s: list(STATE_LABELS).index(s))
    total_reviews = sum(p["reviews"] for p in parks)

    ssr_rows = "".join(render_row(p) for p in parks[:SSR_ROWS])

    chips = ['<button class="active" data-state="all">All Australia</button>']
    for s in states_present:
        chips.append(f'<button data-state="{s}">{STATE_LABELS[s]}</button>')

    itemlist = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": "Best Family Holiday Parks in Australia",
        "itemListOrder": "https://schema.org/ItemListOrderDescending",
        "numberOfItems": min(JSONLD_ITEMS, total),
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": p["rank"],
                "name": p["name"],
                "url": f"https://familyholidayparks.com.au{p['loc_url']}",
            }
            for p in parks[:JSONLD_ITEMS]
        ],
    }

    parks_json = json.dumps(
        [
            {
                "rank": p["rank"], "name": p["name"], "score": p["score"],
                "rating": p["rating"], "reviews": p["reviews"],
                "price_disp": p["price_disp"], "price_num": p["price_num"],
                "chips": p["chips"], "state": p["state"],
                "loc_label": p["loc_label"], "loc_url": p["loc_url"],
                "updated": p["updated"], "trend": p["trend"], "delta": p["delta"],
            }
            for p in parks
        ],
        separators=(",", ":"),
    )

    js = (
        JS.replace("__PAGE_CHUNK__", str(PAGE_CHUNK))
        .replace("__PARKS_JSON__", parks_json)
        .replace("__STATE_LABELS__", json.dumps(STATE_LABELS))
        .replace("__SSR_ROWS__", str(min(SSR_ROWS, total)))
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>The {total} Best Family Holiday Parks in Australia ({today.year}) | Family Holiday Parks</title>
<meta name="description" content="Australia's family holiday parks ranked by Family Score. {total} parks compared on entertainment, nature, value, cleanliness and more, from {total_reviews:,} real reviews. Updated {today.strftime('%B %Y')}. No sponsored rankings.">
<link rel="canonical" href="https://familyholidayparks.com.au/best-holiday-parks-australia">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<script type="application/ld+json">{json.dumps(itemlist, separators=(",", ":"))}</script>
<style>{CSS}</style>
</head>
<body>

<nav class="nav">
  <a href="/" class="nav-logo"><img src="/images/logo.png" alt="Family Holiday Parks"></a>
  <a href="/" class="nav-link">Explore locations &rarr;</a>
</nav>

<header class="lb-header">
  <h1>Australia's Best Family Holiday Parks</h1>
  <p class="lb-intro">Every park below is scored out of 100 across seven categories that matter to families: entertainment, nature, value, cleanliness, site size, sentiment and location, built from {total_reviews:,} real guest reviews. Nobody pays to be on this list.</p>
  <p class="lb-stamp"><strong>{total} parks ranked</strong> &middot; Data updated {today.strftime('%d %B %Y')}</p>
</header>

<div class="lb-controls">
  <div class="lb-controls-inner">
    <div class="chip-row" id="state-chips">{''.join(chips)}</div>
    <div class="sort-row" id="sort-pills">
      <span class="sort-label">Sort by</span>
      <button class="active" data-sort="score">Family score</button>
      <button data-sort="price">Price</button>
      <button data-sort="reviews">Most reviewed</button>
      <button data-sort="rating">Google rating</button>
    </div>
  </div>
</div>

<main class="lb-list-wrap">
  <div class="lb-list">
    <div class="lb-colhead">
      <span></span><span>Park</span><span style="text-align:center">Trend</span><span style="text-align:center">Score</span><span>Google</span><span>From</span><span>Updated</span>
    </div>
    <div id="lb-list">{ssr_rows}</div>
  </div>
  <button class="lb-more" id="lb-more" type="button">Show more</button>
</main>

<section class="lb-method">
  <h2>How the ranking works</h2>
  <p>Each park's Family Score weighs entertainment (20), nature (20), value (15), cleanliness (15), site size (10), guest sentiment (10) and location (10). Scores update as new review data flows in, and the Trend column tracks each park's movement between updates. Rankings are national: filtering by state keeps each park's Australia-wide rank so you always know where it truly stands.</p>
</section>

<footer class="site-footer">
  <a href="/">familyholidayparks.com.au</a> &middot; Australia's family holiday park guide
</footer>

<script>{js}</script>
</body>
</html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true", help="write today's score snapshot for trend tracking")
    ap.add_argument("--publish", action="store_true", help="git add/commit/push after building")
    args = ap.parse_args()

    parks = load_parks()
    if not parks:
        print("No parks found — nothing to build.", file=sys.stderr)
        return 1
    print(f"Loaded {len(parks)} unique parks across {len({p['state'] for p in parks})} states")

    if args.snapshot:
        write_snapshot(parks)

    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    out = PUBLIC_DIR / OUTPUT_NAME
    tmp = out.with_suffix(".html.tmp")
    tmp.write_text(build_page(parks), encoding="utf-8")
    tmp.replace(out)
    print(f"Wrote {out}")

    if args.publish:
        subprocess.run(["git", "add", "-A"], cwd=PROJECT_DIR, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Rebuild leaderboard page"],
            cwd=PROJECT_DIR, check=False,
        )
        subprocess.run(["git", "push"], cwd=PROJECT_DIR, check=True)
        print("Published.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
