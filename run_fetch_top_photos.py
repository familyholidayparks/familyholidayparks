#!/usr/bin/env python
"""
Runner: fetch the top-ranked park's photo for every location in locations.csv.
Only processes the single highest-scoring park per location (--top-only mode).

Skip logic:
  - Skip if top park's photo_url_override already starts with /images/ (local file exists)
  - Skip if scores.json doesn't exist for the location

Usage:
    python run_fetch_top_photos.py
    python run_fetch_top_photos.py --dry-run
"""
import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
STATE_DIR = {"QLD": "qld", "NSW": "nsw", "VIC": "vic", "WA": "wa",
             "SA": "sa", "TAS": "tas", "NT": "nt", "ACT": "act"}
DELAY_SECONDS = 1.5  # between locations — polite to Google API


def park_score(p):
    try:
        return float(p.get("total_score") or p.get("family_score") or 0)
    except (TypeError, ValueError):
        return 0.0


def find_loc_dir(slug):
    for state_dir in sorted((PROJECT / "locations").iterdir()):
        if state_dir.is_dir() and (state_dir / slug).is_dir():
            return state_dir / slug
    return None


def top_park_has_local_photo(slug):
    loc_dir = find_loc_dir(slug)
    if not loc_dir:
        return False
    scores_path = loc_dir / "scores.json"
    if not scores_path.exists():
        return False
    try:
        parks = json.loads(scores_path.read_text(encoding="utf-8"))
        parks = sorted(parks, key=park_score, reverse=True)
        if not parks:
            return False
        top = parks[0]
        override = str(top.get("photo_url_override") or "").strip()
        return override.startswith("/images/")
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would run without calling the API")
    args = parser.parse_args()

    locations = []
    with open(PROJECT / "locations.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            slug = row.get("slug", "").strip()
            state = row.get("state", "").strip().upper()
            if slug and state in STATE_DIR:
                locations.append((slug, state))

    print(f"Loaded {len(locations)} locations from locations.csv\n")

    results = {"success": [], "skipped": [], "failed": []}

    for i, (slug, state) in enumerate(locations, 1):
        prefix = f"[{i:>3}/{len(locations)}] {slug}"

        loc_dir = find_loc_dir(slug)
        if not loc_dir:
            print(f"{prefix} — FAIL: no location directory")
            results["failed"].append((slug, "no location directory"))
            continue

        scores_path = loc_dir / "scores.json"
        if not scores_path.exists():
            print(f"{prefix} — FAIL: no scores.json")
            results["failed"].append((slug, "no scores.json"))
            continue

        if top_park_has_local_photo(slug):
            print(f"{prefix} — SKIPPED: top park already has local file")
            results["skipped"].append(slug)
            continue

        if args.dry_run:
            print(f"{prefix} — [dry-run] would run: python fetch_park_photos.py {slug} --top-only")
            continue

        print(f"{prefix} — running fetch...", flush=True)
        cmd = [sys.executable, str(PROJECT / "fetch_park_photos.py"), slug, "--top-only"]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                cwd=str(PROJECT), timeout=120
            )
            output = result.stdout.strip()
            if result.returncode != 0:
                err = (result.stderr or "").strip()
                print(f"  FAIL (exit {result.returncode}): {err[:200]}")
                results["failed"].append((slug, f"exit {result.returncode}: {err[:100]}"))
            else:
                # Check if we actually got a local photo now
                if top_park_has_local_photo(slug):
                    print(f"  SUCCESS — local photo set")
                    results["success"].append(slug)
                else:
                    # Scan output for clues
                    lines = output.splitlines()
                    fail_line = next((l for l in lines if "FAIL" in l or "SKIP" in l or "no place_id" in l or "no photos" in l), "")
                    reason = fail_line.strip() if fail_line else "no local photo after run"
                    print(f"  FAIL: {reason}")
                    results["failed"].append((slug, reason))
            # Print subprocess output indented for debug
            for line in output.splitlines():
                print(f"  | {line}")
        except subprocess.TimeoutExpired:
            print(f"  FAIL: timeout")
            results["failed"].append((slug, "timeout"))
        except Exception as e:
            print(f"  FAIL: {e}")
            results["failed"].append((slug, str(e)))

        time.sleep(DELAY_SECONDS)

    # ── Final tally ────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("FINAL TALLY")
    print("=" * 60)
    print(f"  Success (new local photo):  {len(results['success'])}")
    print(f"  Skipped (already local):    {len(results['skipped'])}")
    print(f"  Failed:                     {len(results['failed'])}")

    if results["success"]:
        print(f"\nSuccesses:")
        for s in results["success"]:
            print(f"  {s}")

    if results["failed"]:
        print(f"\nFailures:")
        for slug, reason in results["failed"]:
            print(f"  {slug}: {reason}")


if __name__ == "__main__":
    main()
