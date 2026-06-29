#!/usr/bin/env python
"""
Download park photos from scores.json and save them locally under public/images/parks/.

Usage:
    python download_park_photos.py --slug gold-coast
    python download_park_photos.py --all
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT = Path(__file__).resolve().parent
LOCATIONS_DIR = PROJECT / "locations"
PUBLIC_DIR = PROJECT / "public"
REPORT_FILE = PROJECT / "download-photos-report.md"

# Checked in priority order — photo_url_override is first because generate_page.py
# treats it as the highest-quality source.
PHOTO_FIELDS = [
    "photo_url_override",
    "photo",
    "photo_url",
    "photo_url_cached",
    "image_url",
]


def slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[\s-]+", "-", s).strip("-")
    return s


def find_photo_url(park: dict) -> str:
    """Return the first http URL found across PHOTO_FIELDS, or ''."""
    for field in PHOTO_FIELDS:
        val = park.get(field)
        if val and isinstance(val, str) and val.startswith("http"):
            return val
    return ""


def already_local(park: dict) -> bool:
    """True if any photo field already holds a local /images/ path."""
    for field in PHOTO_FIELDS:
        val = park.get(field)
        if val and isinstance(val, str) and val.startswith("/images/"):
            return True
    return False


def download(url: str, dest: Path) -> tuple[bool, str]:
    try:
        req = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                "Referer": "https://www.google.com/",
            },
        )
        with urlopen(req, timeout=20) as resp:
            data = resp.read()
        if len(data) < 1000:
            return False, f"response too small ({len(data)} bytes — likely a redirect or error page)"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True, ""
    except HTTPError as e:
        return False, f"HTTP {e.code} {e.reason}"
    except URLError as e:
        return False, f"URLError: {e.reason}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def find_scores_path(slug: str) -> Path | None:
    for state_dir in sorted(LOCATIONS_DIR.iterdir()):
        if not state_dir.is_dir():
            continue
        candidate = state_dir / slug / "scores.json"
        if candidate.exists():
            return candidate
    return None


def process_location(slug: str) -> tuple[list[str], dict]:
    scores_path = find_scores_path(slug)
    if scores_path is None:
        return [f"\n## {slug}\n\nERROR: No scores.json found.\n"], {}

    try:
        parks = json.loads(scores_path.read_text(encoding="utf-8"))
    except Exception as e:
        return [f"\n## {slug}\n\nERROR reading scores.json: {e}\n"], {}

    lines: list[str] = [f"\n## {slug}\n"]
    stats = {"success": 0, "skip_local": 0, "skip_no_url": 0, "fail": 0}
    changed = False

    for park in parks:
        name = park.get("park_name") or park.get("name") or "Unknown"
        park_slug = slugify(name)

        if already_local(park):
            lines.append(f"- SKIP (already local): {name}")
            stats["skip_local"] += 1
            continue

        url = find_photo_url(park)
        if not url:
            lines.append(f"- SKIP (no URL): {name}")
            stats["skip_no_url"] += 1
            continue

        dest = PUBLIC_DIR / "images" / "parks" / slug / park_slug / "1.jpg"
        local_path = f"/images/parks/{slug}/{park_slug}/1.jpg"

        print(f"  Downloading: {name}", flush=True)
        ok, err = download(url, dest)

        if ok:
            park["photo_url_cached"] = local_path
            changed = True
            lines.append(f"- OK: {name} -> {local_path}")
            stats["success"] += 1
        else:
            lines.append(f"- FAIL: {name} -- {err}")
            lines.append(f"  URL: {url}")
            stats["fail"] += 1

    if changed:
        scores_path.write_text(
            json.dumps(parks, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    lines.append(
        f"\nSummary: {stats['success']} downloaded, "
        f"{stats['skip_local'] + stats['skip_no_url']} skipped "
        f"({stats['skip_local']} already local, {stats['skip_no_url']} no URL), "
        f"{stats['fail']} failed"
    )
    return lines, stats


def get_all_slugs() -> list[str]:
    slugs = []
    for state_dir in sorted(LOCATIONS_DIR.iterdir()):
        if not state_dir.is_dir():
            continue
        for loc_dir in sorted(state_dir.iterdir()):
            if not loc_dir.is_dir():
                continue
            if (loc_dir / "scores.json").exists():
                slugs.append(loc_dir.name)
    return slugs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download park photos from scores.json and save locally."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--slug", help="Location slug (e.g. gold-coast)")
    group.add_argument("--all", action="store_true", help="Process all locations")
    args = parser.parse_args()

    slugs = [args.slug] if args.slug else get_all_slugs()

    report_lines: list[str] = [
        "# Park Photo Download Report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    total: dict[str, int] = {"success": 0, "skip_local": 0, "skip_no_url": 0, "fail": 0}

    for slug in slugs:
        print(f"\n[{slug}]", flush=True)
        lines, stats = process_location(slug)
        report_lines.extend(lines)
        for k in total:
            total[k] += stats.get(k, 0)

    if len(slugs) > 1:
        report_lines += [
            "",
            "---",
            "## Overall Total",
            "",
            f"- Downloaded: {total['success']}",
            f"- Skipped (already local): {total['skip_local']}",
            f"- Skipped (no URL): {total['skip_no_url']}",
            f"- Failed: {total['fail']}",
            "",
        ]

    report_text = "\n".join(report_lines)
    REPORT_FILE.write_text(report_text, encoding="utf-8")

    print(f"\n{'='*60}")
    print(report_text)
    print(f"\nReport saved to: {REPORT_FILE}")


if __name__ == "__main__":
    main()
