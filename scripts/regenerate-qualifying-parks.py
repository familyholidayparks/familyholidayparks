"""Regenerate PARKS array in qualifying-parks.html from holiday_parks_master.csv."""

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "holiday_parks_master.csv"
HTML_PATH = ROOT / "qualifying-parks.html"


def main():
    parks = []
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("Park Name") or "").strip()
            if not name:
                continue
            parks.append(
                {
                    "name": name,
                    "suburb": (row.get("Town/Suburb") or "").strip(),
                    "state": (row.get("State") or "").strip(),
                    "chain": (row.get("Chain/Brand") or "").strip(),
                }
            )

    parks.sort(key=lambda p: p["name"].lower())
    count = len(parks)
    parks_js = "const PARKS = " + json.dumps(parks, ensure_ascii=False) + ";"

    html = HTML_PATH.read_text(encoding="utf-8")
    html, n = re.subn(r"const PARKS = \[.*?\];", parks_js, html, count=1, flags=re.DOTALL)
    if n != 1:
        raise SystemExit("Could not find PARKS array in qualifying-parks.html")

    html = re.sub(
        r'(<span class="stat-number" id="totalCount">)\d+(</span>)',
        rf"\g<1>{count}\g<2>",
        html,
    )
    html = re.sub(
        r'(id="resultsCount">)Showing all \d+ parks',
        rf"\g<1>Showing all {count} parks",
        html,
    )

    HTML_PATH.write_text(html, encoding="utf-8")
    print(f"Updated {count} parks in qualifying-parks.html")


if __name__ == "__main__":
    main()
