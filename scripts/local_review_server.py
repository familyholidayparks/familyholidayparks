#!/usr/bin/env python3
from __future__ import annotations

import json
import mimetypes
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from scripts.review_mvp import prepare_review, publish_payload, submit_for_moderation


PUBLIC_DIR = PROJECT_DIR / "public"


class ReviewMvpHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        clean = unquote(path.split("?", 1)[0].split("#", 1)[0])
        if clean == "/":
            clean = "/index.html"
        return str((PUBLIC_DIR / clean.lstrip("/")).resolve())

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > 250_000:
            self._json({"error": {"message": "Submission is too large."}}, 413)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json({"error": {"message": "Invalid JSON body."}}, 400)
            return

        try:
            if self.path.rstrip("/") == "/api/local-review/submit":
                self._json(submit_for_moderation(payload), 200)
                return
            if self.path.rstrip("/") == "/api/local-review/score":
                self._json(prepare_review(payload), 200)
                return
            if self.path.rstrip("/") == "/api/local-review/publish":
                self._json(publish_payload(payload), 200)
                return
        except Exception as exc:
            self._json({"error": {"message": str(exc)}}, 400)
            return

        self._json({"error": {"message": "Not found."}}, 404)

    def guess_type(self, path: str) -> str:
        if path.endswith(".js"):
            return "text/javascript"
        return mimetypes.guess_type(path)[0] or "application/octet-stream"

    def _json(self, body: dict, status: int) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8765), ReviewMvpHandler)
    print("Local review MVP running at http://127.0.0.1:8765/leave-a-review.html")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
