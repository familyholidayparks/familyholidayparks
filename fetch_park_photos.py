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
PLACES_TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
MAX_PHOTOS = 20
PHOTO_MAXWIDTH = 1200

R2_BUCKET = "fhp-park-photos"
R2_BASE_URL = "https://pub-778b7b706f1649f3be2e5a13474b6d3c.r2.dev"


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
        if not isinstance(entry, dict):
            continue
        if (entry.get("title") or "").lower() == park_name.lower():
            # Direct placeId field (Apify format)
            if entry.get("placeId"):
                return entry["placeId"]
            # URL-embedded place_id
            url = entry.get("url") or ""
            m = re.search(r"query_place_id=([^&\s]+)", url)
            if m:
                return urllib.parse.unquote(m.group(1))
    return None


def text_search_place_id(api_key: str, park_name: str, location_hint: str = "Australia") -> str | None:
    """Look up a place_id via Text Search when approved-parks.json has no place_id."""
    query = f"{park_name} {location_hint}"
    url = (
        f"{PLACES_TEXT_SEARCH_URL}"
        f"?query={urllib.parse.quote(query, safe='')}"
        f"&type=campground"
        f"&key={urllib.parse.quote(api_key, safe='')}"
    )
    data = get_json(url)
    if not data:
        return None
    results = data.get("results") or []
    if not results:
        return None
    return results[0].get("place_id") or None


def upload_to_r2(api_token: str, account_id: str, local_path: Path, r2_key: str) -> tuple[bool, str]:
    """Upload a local file to R2. Returns (success, message)."""
    if not api_token or not account_id:
        return False, "no R2 credentials"
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
        f"/r2/buckets/{R2_BUCKET}/objects/{r2_key}"
    )
    try:
        data = local_path.read_bytes()
        req = urllib.request.Request(
            url, data=data, method="PUT",
            headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "image/jpeg",
                "Content-Length": str(len(data)),
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            result = json.loads(body) if body else {}
            if result.get("success") is False:
                return False, f"R2 API error: {result.get('errors')}"
        return True, f"{len(data) // 1024} KB"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def find_loc_dir(slug: str) -> Path | None:
    for state_dir in sorted((PROJECT / "locations").iterdir()):
        if state_dir.is_dir() and (state_dir / slug).is_dir():
            return state_dir / slug
    return None


def park_score(p: dict) -> float:
    try:
        return float(p.get("total_score") or p.get("family_score") or 0)
    except (TypeError, ValueError):
        return 0.0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Google Places photos for all parks in a location."
    )
    parser.add_argument("slug", help="Location slug (e.g. gold-coast)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be downloaded")
    parser.add_argument(
        "--top-only",
        action="store_true",
        help="Only process the single highest-scored park (saves API quota)",
    )
    args = parser.parse_args()

    load_env()
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY") or os.environ.get("MAPS_API_KEY") or ""
    if not api_key:
        print("ERROR: GOOGLE_MAPS_API_KEY not found in .env")
        sys.exit(1)
    r2_account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    r2_api_token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    use_r2 = bool(r2_account_id and r2_api_token)
    if use_r2:
        print(f"[R2] uploads enabled → {R2_BASE_URL}/parks/{args.slug}/...")
    else:
        print("[R2] no credentials — photo_url_override will use local /images/ path")

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
    approved = (_approved_raw.get("parks") or []) if isinstance(_approved_raw, dict) else (_approved_raw or [])

    # Sort by score descending so --top-only picks the right park
    parks = sorted(parks, key=park_score, reverse=True)
    # Keep full list for saving; working_parks may be a subset
    all_parks = parks
    working_parks = parks[:1] if args.top_only else parks

    if args.top_only and working_parks:
        print(f"[--top-only] processing 1 park: {working_parks[0].get('park_name') or working_parks[0].get('name')}")

    parks_dir = PROJECT / "public" / "images" / "parks" / args.slug
    scores_changed = False

    for park in working_parks:
        name = park.get("park_name") or park.get("name") or ""
        park_slug = slugify(name)
        park_dir = parks_dir / park_slug

        print(f"\n[{name}]", flush=True)

        place_id = extract_place_id(approved, name)
        if not place_id:
            print(f"  no place_id in approved-parks.json — trying Text Search API...", flush=True)
            place_id = text_search_place_id(api_key, name, "Australia")
            if place_id:
                print(f"  place_id (via text search): {place_id}")
            else:
                print("  SKIP: place_id not found via text search either")
                continue
        else:
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
            print("  all slots filled — skipping download")
            slot1 = park_dir / "1.jpg"
            if use_r2 and slot1.exists():
                r2_key = f"parks/{args.slug}/{park_slug}/1.jpg"
                r2_url = f"{R2_BASE_URL}/{r2_key}"
                if park.get("photo_url_override") != r2_url:
                    ok, msg = upload_to_r2(r2_api_token, r2_account_id, slot1, r2_key)
                    if ok:
                        park["photo_url_override"] = r2_url
                        scores_changed = True
                        print(f"  uploaded to R2 + set photo_url_override: {r2_url}")
                    else:
                        print(f"  R2 upload FAIL: {msg}")
            else:
                local_path_str = f"/images/parks/{args.slug}/{park_slug}/1.jpg"
                if park.get("photo_url_override") != local_path_str:
                    park["photo_url_override"] = local_path_str
                    scores_changed = True
                    print(f"  set photo_url_override: {local_path_str}")
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
                # Upload to R2 immediately after download
                if use_r2:
                    r2_key = f"parks/{args.slug}/{park_slug}/{slot}.jpg"
                    r2_ok, r2_msg = upload_to_r2(r2_api_token, r2_account_id, dest, r2_key)
                    r2_status = f" r2={'OK' if r2_ok else 'FAIL:' + r2_msg}"
                else:
                    r2_status = ""
                print(f" OK ({kb} KB, {src}){r2_status}")
                any_downloaded = True
            else:
                print(f" FAIL: {err}")

        # Set photo_url_override to slot 1 if slot 1 now exists
        slot1 = park_dir / "1.jpg"
        if slot1.exists() or 1 in existing_slots:
            if use_r2:
                r2_key = f"parks/{args.slug}/{park_slug}/1.jpg"
                r2_url = f"{R2_BASE_URL}/{r2_key}"
                if park.get("photo_url_override") != r2_url:
                    park["photo_url_override"] = r2_url
                    scores_changed = True
                    print(f"  set photo_url_override: {r2_url}")
            else:
                local_path_str = f"/images/parks/{args.slug}/{park_slug}/1.jpg"
                if park.get("photo_url_override") != local_path_str:
                    park["photo_url_override"] = local_path_str
                    scores_changed = True
                    print(f"  set photo_url_override: {local_path_str}")

    if scores_changed and not args.dry_run:
        scores_path.write_text(
            json.dumps(all_parks, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        r2_count = sum(1 for p in all_parks if str(p.get("photo_url_override", "")).startswith(R2_BASE_URL))
        local_count = sum(1 for p in all_parks if str(p.get("photo_url_override", "")).startswith("/images/"))
        print(f"\nUpdated scores.json — R2 URLs: {r2_count}, local paths: {local_count}, total parks: {len(all_parks)}")

    print("\nDone.")


if __name__ == "__main__":
    main()
