#!/usr/bin/env python3
"""
Universal location updater. Reads a structured .txt review file and applies all updates.

Usage:
  python update_location.py gold-coast-qld
  python update_location.py gold-coast-qld --publish
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path

project_dir = Path(__file__).resolve().parent
reviews_dir = project_dir / "reviews"
reviews_dir.mkdir(exist_ok=True)


def _parse_price(val) -> str:
    """Safely extract display price from string or dict."""
    if not val:
        return ""
    if isinstance(val, dict):
        return val.get("display") or (val.get("price") and f"${val['price']}/night") or "—"
    if isinstance(val, str):
        text = val.strip()
        if "display" in text and (text.startswith("{") or text.startswith("${")):
            match = re.search(r"""['"]display['"]\s*:\s*['"]([^'"]+)['"]""", text)
            if match:
                return match.group(1).strip()
    return str(val)


STATE_ABBR_LOWER = {
    "QLD": "qld", "NSW": "nsw", "VIC": "vic", "SA": "sa",
    "WA": "wa", "TAS": "tas", "NT": "nt", "ACT": "act",
}
STATE_NAMES = {
    "QLD": "queensland", "NSW": "new-south-wales", "VIC": "victoria",
    "SA": "south-australia", "WA": "western-australia", "TAS": "tasmania",
    "NT": "northern-territory", "ACT": "act",
}


def lookup_csv_row(slug_or_location: str) -> dict | None:
    """Resolve a CLI slug or location name to a locations.csv row."""
    csv_path = project_dir / "locations.csv"
    if not csv_path.exists():
        return None
    key = slug_or_location.strip().lower()
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row_slug = row.get("slug", "").strip().lower()
            row_loc = row.get("location", "").strip().lower()
            state = row.get("state", "").strip().upper()
            state_lower = STATE_ABBR_LOWER.get(state, state.lower())
            state_full = STATE_NAMES.get(state, state.lower())
            if key in {
                row_slug,
                row_loc,
                f"{row_loc} {state.lower()}",
                f"{row_slug}-{state_lower}",
                f"{row_slug}-{state_full}",
            }:
                return row
    return None


def output_slug_for_row(row: dict) -> str:
    slug = row.get("slug", "").strip()
    state = row.get("state", "").strip().upper()
    return f"{slug}-{STATE_NAMES.get(state, state.lower())}"


def resolve_review_path(slug_arg: str) -> Path:
    """Find review .txt from CLI slug, csv slug, or slug-state variants."""
    candidates = [reviews_dir / f"{slug_arg.strip()}.txt"]
    row = lookup_csv_row(slug_arg)
    if row:
        csv_slug = row.get("slug", "").strip()
        state = row.get("state", "").strip().upper()
        state_lower = STATE_ABBR_LOWER.get(state, state.lower())
        candidates.extend([
            reviews_dir / f"{csv_slug}.txt",
            reviews_dir / f"{csv_slug}-{state_lower}.txt",
        ])
    key = slug_arg.strip().lower()
    for state_abbr, suffix in STATE_NAMES.items():
        if key.endswith(f"-{suffix}"):
            base = key[: -(len(suffix) + 1)]
            candidates.append(reviews_dir / f"{base}.txt")
            state_lower = STATE_ABBR_LOWER.get(state_abbr, "")
            if state_lower:
                candidates.append(reviews_dir / f"{base}-{state_lower}.txt")
            break
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if path.exists():
            return path
    return candidates[0]


def slugify(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r'[^a-z0-9\s-]', '', name)
    name = re.sub(r'[\s]+', '-', name)
    return re.sub(r'-+', '-', name).strip('-')


def get_location_dir(location: str) -> Path | None:
    import csv
    csv_path = project_dir / "locations.csv"
    state_map = {"QLD":"qld","NSW":"nsw","VIC":"vic","SA":"sa","WA":"wa","TAS":"tas","NT":"nt","ACT":"act"}

    # Strip state suffix for CSV lookup (e.g. "Gold Coast QLD" -> "Gold Coast")
    bare = re.sub(
        r'\s+(QLD|NSW|VIC|SA|WA|TAS|NT|ACT)$', '', location.strip(), flags=re.IGNORECASE
    ).strip()

    loc_key = location.strip().lower()
    bare_key = bare.lower()

    if csv_path.exists():
        with open(csv_path, encoding='utf-8') as f:
            for row in csv.DictReader(f):
                row_loc = row.get("location", "").strip().lower()
                if row_loc == loc_key or row_loc == bare_key:
                    state = state_map.get(row.get("state","").strip().upper(), "other")
                    slug = row.get("slug","").strip()
                    if slug:
                        return project_dir / "locations" / state / slug
    return None


def parse_review_file(path: Path) -> dict:
    text = path.read_text(encoding='utf-8')
    sections = {}
    current = None
    current_lines = []

    for line in text.splitlines():
        m = re.match(r'^([A-Z][A-Z\s&]+):\s*$', line.strip())
        if m:
            if current:
                sections[current] = '\n'.join(current_lines).strip()
            current = m.group(1).strip()
            current_lines = []
        elif current:
            current_lines.append(line)

    if current:
        sections[current] = '\n'.join(current_lines).strip()

    return sections


def parse_pipe_table(text: str) -> list:
    rows = []
    for line in text.splitlines():
        line = line.strip().lstrip('-').strip()
        if '|' in line:
            parts = line.split('|', 1)
            key = parts[0].strip()
            val = parts[1].strip()
            if key:
                rows.append((key, val))
    return rows


def extract_image_url(url: str) -> str:
    match = re.search(r'(https://lh3\.googleusercontent\.com[^\s!"\'&>]+)', url)
    if match:
        img = match.group(1)
        img = img.replace('%2F', '/').replace('%3D', '=').replace('%3F', '?').replace('%26', '&')
        img = re.split(r'[\s"\'<>]', img)[0]
        img = re.sub(r'w\d+-h\d+', 'w800-h600', img)
        return img
    if 'lh3.googleusercontent.com' in url or 'dynamic-media' in url:
        return re.sub(r'w\d+-h\d+', 'w800-h600', url.strip())
    return url.strip()


def apply_updates(sections: dict, review_text: str = "", publish: bool = False):
    location = sections.get('LOCATION', '').strip()
    if not location:
        print("ERROR: LOCATION section missing.")
        sys.exit(1)

    print(f"\n[location] Updating: {location}")

    loc_dir = get_location_dir(location)
    if not loc_dir:
        print(f"ERROR: Could not find location directory for '{location}'")
        sys.exit(1)

    scores_path = loc_dir / "scores.json"
    if not scores_path.exists():
        print(f"ERROR: No scores.json at {scores_path}")
        sys.exit(1)

    scores = json.loads(scores_path.read_text(encoding='utf-8'))
    scores_by_name = {p.get('park_name', ''): p for p in scores}
    parks_dir = project_dir / "parks"

    # Load or create location-level master.json
    loc_master_path = loc_dir / "master.json"
    loc_master: dict = {}
    if loc_master_path.exists():
        try:
            raw_master = json.loads(loc_master_path.read_text(encoding='utf-8'))
            if isinstance(raw_master, dict):
                loc_master = raw_master
        except Exception:
            loc_master = {}

    def save_master(name: str, updates: dict):
        slug = slugify(name)
        master_file = parks_dir / slug / "master.json"
        if master_file.exists():
            data = json.loads(master_file.read_text(encoding='utf-8'))
            data.update(updates)
            master_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
        else:
            print(f"  [warning]  NOT FOUND: {slug}")

    changed = set()

    # HEADING
    if 'HEADING' in sections:
        heading = sections['HEADING'].strip()
        config_path = loc_dir / "config.json"
        config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
        config['hero_headline'] = heading
        config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding='utf-8')
        loc_master['heading'] = heading
        print(f"  [ok] Heading updated")

    # HERO IMAGE
    if 'HERO IMAGE' in sections:
        img_url = extract_image_url(sections['HERO IMAGE'].strip())
        config_path = loc_dir / "config.json"
        config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
        config['hero_image'] = img_url.replace('w800-h600', 'w1600-h900')
        config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding='utf-8')
        loc_master['hero_image'] = img_url.replace('w800-h600', 'w1600-h900')
        print(f"  [ok] Hero image updated")

    # HERO INTRO
    if 'HERO INTRO' in sections:
        hero_intro = sections['HERO INTRO'].strip()
        (loc_dir / "hero-intro.txt").write_text(hero_intro, encoding='utf-8')
        loc_master['hero_intro'] = hero_intro
        print(f"  [ok] Hero intro updated")

    # WHY FAMILIES LOVE
    if 'WHY FAMILIES LOVE' in sections:
        lines = [l.strip().lstrip('-').strip() for l in sections['WHY FAMILIES LOVE'].splitlines() if l.strip().lstrip('-').strip()]
        (loc_dir / "why-families.txt").write_text('\n'.join(lines), encoding='utf-8')
        loc_master['why_families'] = lines
        print(f"  [ok] Why families love: {len(lines)} bullets")

    # LOCAL KNOWLEDGE
    if 'LOCAL KNOWLEDGE' in sections:
        local_knowledge = sections['LOCAL KNOWLEDGE'].strip()
        (loc_dir / "local-knowledge.txt").write_text(local_knowledge, encoding='utf-8')
        loc_master['local_knowledge'] = local_knowledge
        print(f"  [ok] Local knowledge updated")

    # DESTINATION SUMMARY
    dest_match = re.search(
        r'^DESTINATION SUMMARY:\s*\n(.*?)(?=\n[A-Z][A-Z\s&]+:\s*$|\Z)',
        review_text,
        re.MULTILINE | re.DOTALL,
    )
    if dest_match:
        dest_text = dest_match.group(1).strip()
        (loc_dir / "destination-summary.txt").write_text(dest_text, encoding='utf-8')
        loc_master['destination_summary'] = dest_text
        print(f"  [ok] Destination summary updated")
    elif 'DESTINATION SUMMARY' in sections:
        dest_text = sections['DESTINATION SUMMARY'].strip()
        (loc_dir / "destination-summary.txt").write_text(dest_text, encoding='utf-8')
        loc_master['destination_summary'] = dest_text
        print(f"  [ok] Destination summary updated")

    # FAQ
    if 'FAQ' in sections:
        faq_text = sections['FAQ'].strip()
        faqs = []
        current_q = None
        current_a_lines = []
        for line in faq_text.splitlines():
            line = line.strip()
            if line.startswith('Q:'):
                if current_q and current_a_lines:
                    faqs.append({'question': current_q, 'answer': ' '.join(current_a_lines).strip()})
                current_q = line[2:].strip()
                current_a_lines = []
            elif line.startswith('A:'):
                current_a_lines = [line[2:].strip()]
            elif current_a_lines is not None and line:
                current_a_lines.append(line)
        if current_q and current_a_lines:
            faqs.append({'question': current_q, 'answer': ' '.join(current_a_lines).strip()})
        if faqs:
            faq_data = {'generated_from_targets': True, 'faqs': faqs}
            (loc_dir / "faq.json").write_text(json.dumps(faq_data, indent=2, ensure_ascii=False), encoding='utf-8')
            loc_master['faq'] = faqs
            print(f"  [ok] FAQ: {len(faqs)} questions")

    # PARK CARDS
    if 'PARK CARDS' in sections:
        for name, best_for in parse_pipe_table(sections['PARK CARDS']):
            if name in scores_by_name:
                scores_by_name[name]['best_for'] = best_for
                save_master(name, {'best_for': best_for})
                changed.add(name)
        print(f"  [ok] Park cards updated")

    # TAGS
    if 'TAGS' in sections:
        for name, tags_str in parse_pipe_table(sections['TAGS']):
            tags = [t.strip() for t in tags_str.split(',') if t.strip()]
            if name in scores_by_name:
                scores_by_name[name]['top_scoring_criteria'] = tags
                save_master(name, {'top_scoring_criteria': tags})
                changed.add(name)
        print(f"  [ok] Tags updated")

    # KIDS PLAY
    if 'KIDS PLAY' in sections:
        for name, kids_str in parse_pipe_table(sections['KIDS PLAY']):
            if name in scores_by_name:
                scores_by_name[name]['kids_play'] = kids_str.strip()
                save_master(name, {'kids_play': kids_str.strip()})
                changed.add(name)
        print(f"  [ok] Kids play updated")

    # WATER FUN
    if 'WATER FUN' in sections:
        for name, water_str in parse_pipe_table(sections['WATER FUN']):
            if name in scores_by_name:
                scores_by_name[name]['water_fun'] = water_str.strip()
                save_master(name, {'water_fun': water_str.strip()})
                changed.add(name)
        print(f"  [ok] Water fun updated")

    # PHOTOS
    if 'PHOTOS' in sections:
        for name, url in parse_pipe_table(sections['PHOTOS']):
            img = extract_image_url(url)
            if name in scores_by_name:
                scores_by_name[name]['photo_url_override'] = img
                scores_by_name[name]['photo_url_cached'] = img
                save_master(name, {'photo_url_override': img, 'photo_url_cached': img})
                changed.add(name)
                print(f"    [photo] {name}")
        print(f"  [ok] Photos updated")

    # ADDRESSES
    if 'ADDRESSES' in sections:
        for name, address in parse_pipe_table(sections['ADDRESSES']):
            if name in scores_by_name:
                scores_by_name[name]['address'] = address
                save_master(name, {'address': address})
                changed.add(name)
        print(f"  [ok] Addresses updated")

    # WEBSITES
    if 'WEBSITES' in sections:
        for name, url in parse_pipe_table(sections['WEBSITES']):
            if name in scores_by_name:
                scores_by_name[name]['website'] = url
                save_master(name, {'website': url})
                changed.add(name)
        print(f"  [ok] Websites updated")

    # PRICES — do not write placeholder dashes to master (prices.json is source of truth)
    prices_path = loc_dir / "prices.json"
    prices_data: dict = {}
    if prices_path.exists():
        try:
            raw_prices = json.loads(prices_path.read_text(encoding='utf-8'))
            prices_data = raw_prices if isinstance(raw_prices, dict) else {}
        except Exception:
            prices_data = {}

    if 'PRICES' in sections:
        for name, price_str in parse_pipe_table(sections['PRICES']):
            parts = price_str.split('|', 1)
            powered = _parse_price(prices_data.get(name, parts[0].strip()))
            deals = parts[1].strip() if len(parts) > 1 else ''
            if powered in ('—', '-', ''):
                print(f"    [$] {name}: — (skipped master update)")
                continue
            slug = slugify(name)
            master_file = parks_dir / slug / "master.json"
            if master_file.exists():
                data = json.loads(master_file.read_text(encoding='utf-8'))
                data['prices'] = {'powered_weekday': powered}
                if deals:
                    data['deals'] = deals
                master_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
                print(f"    [$] {name}: {powered}")
        print(f"  [ok] Prices updated")

    # SUPERMARKETS
    if 'SUPERMARKETS' in sections:
        for name, super_str in parse_pipe_table(sections['SUPERMARKETS']):
            parts = super_str.split(',')
            super_name = parts[0].strip()
            try:
                super_km = float(parts[1].strip().replace('km','').strip()) if len(parts) > 1 else None
            except ValueError:
                super_km = None
            if name in scores_by_name:
                scores_by_name[name]['supermarket_name'] = super_name
                scores_by_name[name]['supermarket_km'] = super_km
                scores_by_name[name]['nearest_supermarket_cached'] = {'name': super_name, 'km': super_km}
                changed.add(name)
        print(f"  [ok] Supermarkets updated")

    # ACTIVITIES
    if 'ACTIVITIES' in sections:
        activities = []
        for line in sections['ACTIVITIES'].splitlines():
            line = line.strip()
            if not line or '|' not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            activity = {
                "name": parts[0] if len(parts) > 0 else "",
                "description": parts[1] if len(parts) > 1 else "",
                "tag": parts[2] if len(parts) > 2 else "",
                "distance": parts[3] if len(parts) > 3 else "",
                "photo": parts[4] if len(parts) > 4 else "",
                "badge": parts[5] if len(parts) > 5 else "",
            }
            if activity["name"]:
                activities.append(activity)
        (loc_dir / "activities.json").write_text(
            json.dumps(activities, indent=2, ensure_ascii=False), encoding='utf-8'
        )
        loc_master['activities'] = activities
        print(f"  [ok] Activities updated: {len(activities)} activities")

    # COORDS
    if 'COORDS' in sections:
        for name, coords_str in parse_pipe_table(sections['COORDS']):
            parts = coords_str.split(',')
            if len(parts) == 2 and name in scores_by_name:
                try:
                    lat, lng = float(parts[0].strip()), float(parts[1].strip())
                    scores_by_name[name]['lat'] = lat
                    scores_by_name[name]['lng'] = lng
                    save_master(name, {'lat': lat, 'lng': lng})
                    changed.add(name)
                except ValueError:
                    print(f"  [warning]  Bad coords for {name}")
        print(f"  [ok] Coords updated")

    # Save location master.json
    loc_master_path.write_text(json.dumps(loc_master, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"  [ok] Location master.json saved ({len(loc_master)} fields)")

    # Save scores.json
    scores_path.write_text(json.dumps(scores, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"\n  [saved] Saved scores.json ({len(changed)} parks updated)")

    # Generate page
    print(f"\n  [building] Generating page for {location}...")
    cmd = [sys.executable, str(project_dir / "generate_page.py"), location,
           "--index", str(project_dir / "public" / "index.html")]
    if publish:
        cmd.append("--publish")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(cmd, cwd=project_dir, env=env)
    if result.returncode != 0:
        print("  [error] Page generation failed")
    else:
        print("  [done] Done!")


def main():
    parser = argparse.ArgumentParser(description='Apply location review updates from a .txt file')
    parser.add_argument('slug', help='Review file slug e.g. gold-coast-qld')
    parser.add_argument('--publish', action='store_true', help='Commit and push after generating')
    args = parser.parse_args()

    review_file = resolve_review_path(args.slug)
    csv_row = lookup_csv_row(args.slug)
    canonical_slug = csv_row.get("slug", "").strip() if csv_row else args.slug
    canonical_state = csv_row.get("state", "").strip().upper() if csv_row else ""
    expected_output = (
        f"public/{output_slug_for_row(csv_row)}.html" if csv_row else "unknown"
    )

    if not review_file.exists():
        print(f"ERROR: Review file not found: {review_file}")
        print(f"Create it at: {review_file}")
        sys.exit(1)

    canonical_output = project_dir / expected_output if csv_row else None
    state_lower = STATE_ABBR_LOWER.get(canonical_state, canonical_state.lower()) if canonical_state else ""
    stale_abbr_path = project_dir / "public" / f"{canonical_slug}-{state_lower}.html" if canonical_slug else None
    print(f"[slug]    canonical: {canonical_slug}")
    print(f"[state]   canonical: {canonical_state}")
    print(f"[review]  path: {review_file.relative_to(project_dir).as_posix()}  exists: {'yes' if review_file.exists() else 'no'}")
    if canonical_output and stale_abbr_path:
        print(f"[output]  expected: {expected_output}  exists: {'yes' if canonical_output.exists() else 'no'}")
        print(f"[stale]   public/{canonical_slug}-{state_lower}.html  exists: {'yes' if stale_abbr_path.exists() else 'no'}")
        match = canonical_output.exists() and canonical_output != stale_abbr_path
        print(f"[status]  match: {'yes' if match else 'no'}")

    review_text = review_file.read_text(encoding='utf-8')
    sections = parse_review_file(review_file)
    apply_updates(sections, review_text=review_text, publish=args.publish)


if __name__ == '__main__':
    main()
