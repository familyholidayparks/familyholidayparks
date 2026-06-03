#!/usr/bin/env python3
"""
Universal location updater. Reads a structured .txt review file and applies all updates.

Usage:
  python update_location.py gold-coast-qld
  python update_location.py gold-coast-qld --publish
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

project_dir = Path(__file__).resolve().parent
reviews_dir = project_dir / "reviews"
reviews_dir.mkdir(exist_ok=True)


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


def apply_updates(sections: dict, publish: bool = False):
    location = sections.get('LOCATION', '').strip()
    if not location:
        print("ERROR: LOCATION section missing.")
        sys.exit(1)

    print(f"\n📍 Updating: {location}")

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

    def save_master(name: str, updates: dict):
        slug = slugify(name)
        master_file = parks_dir / slug / "master.json"
        if master_file.exists():
            data = json.loads(master_file.read_text(encoding='utf-8'))
            data.update(updates)
            master_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
        else:
            print(f"  ⚠️  NOT FOUND: {slug}")

    changed = set()

    # HEADING
    if 'HEADING' in sections:
        config_path = loc_dir / "config.json"
        config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
        config['hero_headline'] = sections['HEADING'].strip()
        config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding='utf-8')
        print(f"  ✓ Heading updated")

    # HERO IMAGE
    if 'HERO IMAGE' in sections:
        img_url = extract_image_url(sections['HERO IMAGE'].strip())
        config_path = loc_dir / "config.json"
        config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
        config['hero_image'] = img_url.replace('w800-h600', 'w1600-h900')
        config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding='utf-8')
        print(f"  ✓ Hero image updated")

    # HERO INTRO
    if 'HERO INTRO' in sections:
        (loc_dir / "hero-intro.txt").write_text(sections['HERO INTRO'].strip(), encoding='utf-8')
        print(f"  ✓ Hero intro updated")

    # WHY FAMILIES LOVE
    if 'WHY FAMILIES LOVE' in sections:
        lines = [l.strip().lstrip('-').strip() for l in sections['WHY FAMILIES LOVE'].splitlines() if l.strip().lstrip('-').strip()]
        (loc_dir / "why-families.txt").write_text('\n'.join(lines), encoding='utf-8')
        print(f"  ✓ Why families love: {len(lines)} bullets")

    # LOCAL KNOWLEDGE
    if 'LOCAL KNOWLEDGE' in sections:
        (loc_dir / "local-knowledge.txt").write_text(sections['LOCAL KNOWLEDGE'].strip(), encoding='utf-8')
        print(f"  ✓ Local knowledge updated")

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
            print(f"  ✓ FAQ: {len(faqs)} questions")

    # PARK CARDS
    if 'PARK CARDS' in sections:
        for name, best_for in parse_pipe_table(sections['PARK CARDS']):
            if name in scores_by_name:
                scores_by_name[name]['best_for'] = best_for
                save_master(name, {'best_for': best_for})
                changed.add(name)
        print(f"  ✓ Park cards updated")

    # TAGS
    if 'TAGS' in sections:
        for name, tags_str in parse_pipe_table(sections['TAGS']):
            tags = [t.strip() for t in tags_str.split(',') if t.strip()]
            if name in scores_by_name:
                scores_by_name[name]['top_scoring_criteria'] = tags
                save_master(name, {'top_scoring_criteria': tags})
                changed.add(name)
        print(f"  ✓ Tags updated")

    # PHOTOS
    if 'PHOTOS' in sections:
        for name, url in parse_pipe_table(sections['PHOTOS']):
            img = extract_image_url(url)
            if name in scores_by_name:
                scores_by_name[name]['photo_url_override'] = img
                scores_by_name[name]['photo_url_cached'] = img
                save_master(name, {'photo_url_override': img, 'photo_url_cached': img})
                changed.add(name)
                print(f"    📸 {name}")
        print(f"  ✓ Photos updated")

    # ADDRESSES
    if 'ADDRESSES' in sections:
        for name, address in parse_pipe_table(sections['ADDRESSES']):
            if name in scores_by_name:
                scores_by_name[name]['address'] = address
                save_master(name, {'address': address})
                changed.add(name)
        print(f"  ✓ Addresses updated")

    # WEBSITES
    if 'WEBSITES' in sections:
        for name, url in parse_pipe_table(sections['WEBSITES']):
            if name in scores_by_name:
                scores_by_name[name]['website'] = url
                save_master(name, {'website': url})
                changed.add(name)
        print(f"  ✓ Websites updated")

    # PRICES
    if 'PRICES' in sections:
        for name, price_str in parse_pipe_table(sections['PRICES']):
            parts = price_str.split('|', 1)
            powered = parts[0].strip()
            deals = parts[1].strip() if len(parts) > 1 else ''
            slug = slugify(name)
            master_file = parks_dir / slug / "master.json"
            if master_file.exists():
                data = json.loads(master_file.read_text(encoding='utf-8'))
                data['prices'] = {'powered_weekday': powered}
                if deals:
                    data['deals'] = deals
                master_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
                print(f"    💰 {name}: {powered}")
        print(f"  ✓ Prices updated")

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
        print(f"  ✓ Supermarkets updated")

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
                    print(f"  ⚠️  Bad coords for {name}")
        print(f"  ✓ Coords updated")

    # Save scores.json
    scores_path.write_text(json.dumps(scores, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"\n  💾 Saved scores.json ({len(changed)} parks updated)")

    # Generate page
    print(f"\n  🔨 Generating page for {location}...")
    cmd = [sys.executable, str(project_dir / "generate_page.py"), location]
    if publish:
        cmd.append("--publish")
    result = subprocess.run(cmd, cwd=project_dir)
    if result.returncode != 0:
        print("  ❌ Page generation failed")
    else:
        print("  ✅ Done!")


def main():
    parser = argparse.ArgumentParser(description='Apply location review updates from a .txt file')
    parser.add_argument('slug', help='Review file slug e.g. gold-coast-qld')
    parser.add_argument('--publish', action='store_true', help='Commit and push after generating')
    args = parser.parse_args()

    review_file = reviews_dir / f"{args.slug}.txt"
    if not review_file.exists():
        print(f"ERROR: Review file not found: {review_file}")
        print(f"Create it at: {review_file}")
        sys.exit(1)

    sections = parse_review_file(review_file)
    apply_updates(sections, publish=args.publish)


if __name__ == '__main__':
    main()
