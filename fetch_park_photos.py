#!/usr/bin/env python
"""
Fetch up to 10 Google Places photos per park and save them locally.

Owner photos (empty html_attributions) fill first slots; contributor photos fill remaining.
Existing files are never overwritten — only missing slots are filled.
photo_url_override in scores.json is set to the /images/parks/.../1.jpg local path.

Usage:
    python fetch_park_photos.py gold-coast
    python fetch_park_photos.py gold-coast --dry-run
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
PLACES_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
PLACES_PHOTO_URL = "https://maps.googleapis.com/maps/api/place/photo"
MAX_PHOTOS = 20
PHOTO_MAXWIDTH = 1200


def load_env() -> None:
    env_path = PROJECT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[\s-]+", "-", s).strip("-")
    return s


def get_json(url: str) -> dict | None:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "familyholidayparks-fetcher/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as e:
        print(f"  [api error] {e}", flush=True)
        return None


def fetch_place_photos(api_key: str, place_id: str) -> list[dict]:
    """Return raw photo objects from Place Details (up to 10)."""
    url = (
        f"{PLACES_DETAILS_URL}"
        f"?place_id={urllib.parse.quote(place_id, safe='')}"
        f"&fields=photos"
        f"&key={urllib.parse.quote(api_key, safe='')}"
    )
    data = get_json(url)
    if not data:
        return []
    result = data.get("result")
    if not isinstance(result, dict):
        return []
    return result.get("photos") or []


def is_owner_photo(photo: dict) -> bool:
    """True when html_attributions is empty — indicates owner/Google upload, not a user."""
    attribs = photo.get("html_attributions") or []
    return len(attribs) == 0


def build_photo_fetch_url(api_key: str, ref: str) -> str:
    return (
        f"{PLACES_PHOTO_URL}"
        f"?maxwidth={PHOTO_MAXWIDTH}"
        f"&photo_reference={urllib.parse.quote(ref, safe='')}"
        f"&key={urllib.parse.quote(api_key, safe='')}"
    )


def download_image(url: str, dest: Path) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        if len(data) < 1000:
            return False, f"response too small ({len(data)} bytes)"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True, ""
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, f"URLError: {e.reason}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def extract_place_id(approved_parks: list[dict], park_name: str) -> str | None:
    for entry in approved_parks:
        if (entry.get("title") or "").lower() == park_name.lower():
            url = entry.get("url") or ""
            m = re.search(r"query_place_id=([^&\s]+)", url)
            if m:
                return urllib.parse.unquote(m.group(1))
    return None


def find_loc_dir(slug: str) -> Path | None:
    for state_dir in sorted((PROJECT / "locations").iterdir()):
        if state_dir.is_dir() and (state_dir / slug).is_dir():
            return state_dir / slug
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Google Places photos for all parks in a location."
    )
    parser.add_argument("slug", help="Location slug (e.g. gold-coast)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be downloaded")
    args = parser.parse_args()

    load_env()
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY") or os.environ.get("MAPS_API_KEY") or ""
    if not api_key:
        print("ERROR: GOOGLE_MAPS_API_KEY not found in .env")
        sys.exit(1)

    loc_dir = find_loc_dir(args.slug)
    if not loc_dir:
        print(f"ERROR: no location directory found for slug '{args.slug}'")
        sys.exit(1)

    scores_path = loc_dir / "scores.json"
    approved_path = loc_dir / "approved-parks.json"

    if not scores_path.exists():
        print(f"ERROR: {scores_path} not found")
        sys.exit(1)

    parks = json.loads(scores_path.read_text(encoding="utf-8"))
    _approved_raw = json.loads(approved_path.read_text(encoding="utf-8")) if approved_path.exists() else {}
    approved = _approved_raw.get("parks") if isinstance(_approved_raw, dict) else _approved_raw or []

    parks_dir = PROJECT / "public" / "images" / "parks" / args.slug
    scores_changed = False

    for park in parks:
        name = park.get("park_name") or park.get("name") or ""
        park_slug = slugify(name)
        park_dir = parks_dir / park_slug

        print(f"\n[{name}]", flush=True)

        place_id = extract_place_id(approved, name)
        if not place_id:
            print("  SKIP: no place_id in approved-parks.json")
            continue
        print(f"  place_id: {place_id}")

        # Determine which slots are already filled
        existing_slots: set[int] = set()
        if park_dir.exists():
            for f in park_dir.glob("*.jpg"):
                try:
                    existing_slots.add(int(f.stem))
                except ValueError:
                    pass

        missing_slots = [n for n in range(1, MAX_PHOTOS + 1) if n not in existing_slots]

        if existing_slots:
            print(f"  existing: {sorted(existing_slots)}")

        if not missing_slots:
            print("  all 10 slots filled — skipping download")
            local_path = f"/images/parks/{args.slug}/{park_slug}/1.jpg"
            if park.get("photo_url_override") != local_path:
                park["photo_url_override"] = local_path
                scores_changed = True
                print(f"  set photo_url_override: {local_path}")
            continue

        print(f"  fetching photo list from Places API...", flush=True)
        raw_photos = fetch_place_photos(api_key, place_id)

        if not raw_photos:
            print("  FAIL: no photos returned by Places API")
            continue

        owner = [p for p in raw_photos if is_owner_photo(p)]
        contrib = [p for p in raw_photos if not is_owner_photo(p)]
        ordered = owner + contrib
        print(f"  {len(raw_photos)} photos total — {len(owner)} owner, {len(contrib)} contributor")

        if args.dry_run:
            for i, slot in enumerate(missing_slots):
                if i >= len(ordered):
                    print(f"  [dry-run] slot {slot}: no photo available")
                    break
                src = "owner" if ordered[i] in owner else "contributor"
                ref = (ordered[i].get("photo_reference") or "")[:40]
                print(f"  [dry-run] slot {slot}: {src} — ref={ref}...")
            continue

        # Download into missing slots
        any_downloaded = False
        for i, slot in enumerate(missing_slots):
            if i >= len(ordered):
                print(f"  slot {slot}: no more photos available")
                break
            photo = ordered[i]
            ref = photo.get("photo_reference") or ""
            if not ref:
                print(f"  slot {slot}: no photo_reference, skipping")
                continue
            url = build_photo_fetch_url(api_key, ref)
            dest = park_dir / f"{slot}.jpg"
            print(f"  slot {slot}: downloading...", end="", flush=True)
            ok, err = download_image(url, dest)
            if ok:
                kb = dest.stat().st_size // 1024
                src = "owner" if photo in owner else "contrib"
                print(f" OK ({kb} KB, {src})")
                any_downloaded = True
            else:
                print(f" FAIL: {err}")

        # Set photo_url_override to slot 1 if slot 1 now exists
        slot1 = park_dir / "1.jpg"
        if slot1.exists() or 1 in existing_slots:
            local_path = f"/images/parks/{args.slug}/{park_slug}/1.jpg"
            if park.get("photo_url_override") != local_path:
                park["photo_url_override"] = local_path
                scores_changed = True
                print(f"  set photo_url_override: {local_path}")

    if scores_changed and not args.dry_run:
        scores_path.write_text(
            json.dumps(parks, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nUpdated scores.json ({sum(1 for p in parks if str(p.get('photo_url_override','')).startswith('/images/'))}/{len(parks)} parks with local photos)")

    print("\nDone.")


if __name__ == "__main__":
    main()
