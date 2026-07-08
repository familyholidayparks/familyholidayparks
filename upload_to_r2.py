#!/usr/bin/env python
"""
Upload all local park photos to Cloudflare R2.

Source:  public/images/parks/**/*.jpg
R2 key:  parks/{location}/{park}/{n}.jpg
R2 URL:  https://pub-778b7b706f1649f3be2e5a13474b6d3c.r2.dev/parks/...

Usage:
    python upload_to_r2.py
    python upload_to_r2.py --dry-run
"""
import argparse
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
BUCKET = "fhp-park-photos"
R2_BASE_URL = "https://pub-778b7b706f1649f3be2e5a13474b6d3c.r2.dev"
WORKERS = 8
MAX_RETRIES = 3


def load_env() -> None:
    env_path = PROJECT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def upload_file(api_token: str, account_id: str, local_path: Path, r2_key: str, dry_run: bool) -> tuple[bool, str]:
    """Upload one file to R2. Returns (success, message)."""
    r2_url_api = (
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
        f"/r2/buckets/{BUCKET}/objects/{r2_key}"
    )

    if dry_run:
        return True, f"[dry-run] would upload → {r2_key}"

    data = local_path.read_bytes()
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                r2_url_api,
                data=data,
                method="PUT",
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
                    errors = result.get("errors") or []
                    return False, f"API error: {errors}"
            return True, f"OK ({len(data) // 1024} KB)"
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")[:200]
            if attempt < MAX_RETRIES and e.code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            return False, f"HTTP {e.code}: {err_body}"
        except Exception as e:
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
                continue
            return False, f"{type(e).__name__}: {e}"

    return False, "max retries exceeded"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_env()
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not api_token:
        print("ERROR: CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN must be in .env")
        raise SystemExit(1)

    parks_root = PROJECT / "public" / "images" / "parks"
    if not parks_root.exists():
        print(f"ERROR: {parks_root} does not exist")
        raise SystemExit(1)

    # Collect all files
    all_files = sorted(parks_root.rglob("*.jpg")) + sorted(parks_root.rglob("*.jpeg")) + sorted(parks_root.rglob("*.png"))
    all_files = sorted(set(all_files))

    print(f"Files to upload: {len(all_files)}")
    print(f"Bucket: {BUCKET}")
    print(f"Workers: {WORKERS}")
    if args.dry_run:
        print("[DRY RUN — no files will be uploaded]")
    print()

    # Build work list: (local_path, r2_key)
    work = []
    for f in all_files:
        # public/images/parks/gold-coast/big4/.../1.jpg → parks/gold-coast/big4/.../1.jpg
        rel = f.relative_to(PROJECT / "public" / "images")
        r2_key = rel.as_posix()  # forward slashes
        work.append((f, r2_key))

    success_count = 0
    fail_count = 0
    failures = []

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {
            pool.submit(upload_file, api_token, account_id, lp, key, args.dry_run): (lp, key)
            for lp, key in work
        }
        done = 0
        for future in as_completed(futures):
            lp, key = futures[future]
            done += 1
            ok, msg = future.result()
            if ok:
                success_count += 1
                if done % 50 == 0 or done <= 5:
                    print(f"  [{done:>3}/{len(work)}] {key} — {msg}")
            else:
                fail_count += 1
                failures.append((key, msg))
                print(f"  [{done:>3}/{len(work)}] FAIL {key} — {msg}")

    print()
    print("=" * 60)
    print(f"Uploaded: {success_count}/{len(work)}")
    if failures:
        print(f"Failed:   {fail_count}")
        for key, msg in failures:
            print(f"  {key}: {msg}")
    else:
        print("All files uploaded successfully.")
    print()
    print(f"Public URL base: {R2_BASE_URL}/parks/...")


if __name__ == "__main__":
    main()
