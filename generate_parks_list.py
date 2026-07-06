#!/usr/bin/env python3
"""Generate public/parks-list.json from holiday_parks_master.csv.

Run from the project root:
    python generate_parks_list.py

Keeps the review form's park autocomplete in sync with the master CSV.
"""

import csv
import json
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
CSV_PATH = PROJECT_DIR / "holiday_parks_master.csv"
OUT_PATH = PROJECT_DIR / "public" / "parks-list.json"


def main() -> None:
    if not CSV_PATH.exists():
        raise SystemExit(f"ERROR: {CSV_PATH} not found")

    parks = []
    with CSV_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("Park Name") or "").strip()
            if not name:
                continue
            parks.append({
                "name": name,
                "town": (row.get("Town/Suburb") or "").strip(),
                "state": (row.get("State") or "").strip(),
                "brand": (row.get("Chain/Brand") or "").strip(),
            })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(parks, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(OUT_PATH)

    print(f"Wrote {len(parks)} parks to {OUT_PATH}")


if __name__ == "__main__":
    main()
