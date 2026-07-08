#!/usr/bin/env python
"""
Replace /images/parks/... local paths in scores.json with R2 public URLs.

Before: /images/parks/gold-coast/big4-gold-coast-holiday-park/1.jpg
After:  https://pub-778b7b706f1649f3be2e5a13474b6d3c.r2.dev/parks/gold-coast/big4-gold-coast-holiday-park/1.jpg

Usage:
    python update_scores_r2_urls.py
    python update_scores_r2_urls.py --dry-run
"""
import argparse
import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
R2_BASE_URL = "https://pub-778b7b706f1649f3be2e5a13474b6d3c.r2.dev"
LOCAL_PREFIX = "/images/parks/"
R2_PREFIX = f"{R2_BASE_URL}/parks/"


def local_to_r2(path: str) -> str:
    """Convert a /images/parks/... local path to its R2 URL."""
    return R2_PREFIX + path[len(LOCAL_PREFIX):]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    updated_files = 0
    updated_parks = 0

    for scores_path in sorted(PROJECT.glob("locations/*/*/scores.json")):
        parks = json.loads(scores_path.read_text(encoding="utf-8"))
        changed = False
        for park in parks:
            for field in ("photo_url_override", "photo_url_cached"):
                val = str(park.get(field) or "").strip()
                if val.startswith(LOCAL_PREFIX):
                    new_val = local_to_r2(val)
                    if not args.dry_run:
                        park[field] = new_val
                    else:
                        print(f"  {scores_path.parent.name}: {val}")
                        print(f"    → {new_val}")
                    changed = True
                    updated_parks += 1

        if changed and not args.dry_run:
            scores_path.write_text(
                json.dumps(parks, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            updated_files += 1

    if args.dry_run:
        print(f"\n[dry-run] Would update {updated_parks} park entries across scores.json files.")
    else:
        print(f"Updated {updated_parks} park entries in {updated_files} scores.json files.")


if __name__ == "__main__":
    main()
