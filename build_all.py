#!/usr/bin/env python3
"""
Overnight batch scoring and page generation.
Scores all locations that have an approved-parks.json file.
Usage: python build_all.py
"""
from __future__ import annotations
import csv
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))
from score_parks import get_location_dir, log, log_err

project_dir = Path(__file__).resolve().parent

state_names = {
    "QLD": "queensland",
    "NSW": "new-south-wales",
    "VIC": "victoria",
    "SA": "south-australia",
    "WA": "western-australia",
    "TAS": "tasmania",
    "NT": "northern-territory",
    "ACT": "act",
}


def process_location(location: str, loc_dir: Path, output_slug: str) -> tuple[str, str, str]:
    """Score and generate page for one location. Returns (location, status, detail)."""
    scores_path = loc_dir / "scores.json"

    # Score if needed
    if not scores_path.exists():
        try:
            result = subprocess.run(
                [sys.executable, "score_parks.py", location, "--auto"],
                cwd=project_dir,
                timeout=3600,
            )
            if result.returncode != 0:
                return (location, "FAILED", "scoring failed")
        except subprocess.TimeoutExpired:
            return (location, "TIMEOUT", "scoring timed out")
        except Exception as e:
            return (location, "ERROR", str(e))

    # Generate page
    try:
        result = subprocess.run(
            [sys.executable, "generate_page.py", location],
            cwd=project_dir,
            timeout=300,
        )
        if result.returncode != 0:
            return (location, "FAILED", "page generation failed")
        return (location, "PUBLISHED", f"public/{output_slug}.html")
    except subprocess.TimeoutExpired:
        return (location, "TIMEOUT", "page generation timed out")
    except Exception as e:
        return (location, "ERROR", str(e))


def main() -> None:
    log_path = project_dir / "build-log.txt"
    results = []
    start_time = datetime.now()

    # Load all locations from CSV
    locations = []
    with open(project_dir / "locations.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            loc_name = row["location"].strip()
            state = row["state"].strip()
            location_str = f"{loc_name} {state}"
            loc_dir = get_location_dir(project_dir, location_str)
            approved_path = loc_dir / "approved-parks.json"
            if approved_path.exists():
                slug = row.get("slug", "").strip()
                state_suffix = state_names.get(state, state.lower())
                output_slug = f"{slug}-{state_suffix}"
                locations.append((location_str, loc_dir, output_slug))

    log(f"Found {len(locations)} locations ready to process.")
    log(f"Running with 3 parallel workers...")

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(process_location, loc, loc_dir, output_slug): loc
            for loc, loc_dir, output_slug in locations
        }
        for future in as_completed(futures):
            location = futures[future]
            try:
                loc, status, detail = future.result()
                line = f"[{datetime.now().strftime('%H:%M:%S')}] {status.upper():10} {loc} {detail}"
                print(line)
                results.append(line)
            except Exception as e:
                line = f"[{datetime.now().strftime('%H:%M:%S')}] ERROR      {location} {str(e)}"
                print(line)
                results.append(line)

    log("Publishing all pages...")
    subprocess.run(
        ["git", "add", "-A"],
        cwd=project_dir,
    )
    subprocess.run(
        ["git", "commit", "-m", f"Overnight build: {len(locations)} locations"],
        cwd=project_dir,
    )
    subprocess.run(
        ["git", "push"],
        cwd=project_dir,
    )
    log("All pages published.")

    # Write build log
    duration = datetime.now() - start_time
    summary = [
        f"\n{'='*60}",
        f"BUILD COMPLETE: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Duration: {duration}",
        f"Locations processed: {len(locations)}",
        f"{'='*60}",
    ] + results

    log_path.write_text("\n".join(summary), encoding="utf-8")
    log(f"\nBuild log saved to {log_path.name}")
    log("✅ Done.")

if __name__ == "__main__":
    main()
