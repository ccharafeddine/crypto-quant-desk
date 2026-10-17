"""Serve web/dist locally with the same headers Cloudflare Pages applies.

    python scripts/serve_web_demo.py [PORT] [DIR]

Only the global `/*` block of `_headers` is applied (enough to test the CSP).
"""

from __future__ import annotations

import functools
import http.server
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def global_headers(dist: Path) -> list[tuple[str, str]]:
    out, on = [], False
    for line in (dist / "_headers").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")):
            on = line.strip() == "/*"
            continue
        if on and ":" in line:
            k, v = line.strip().split(":", 1)
            out.append((k.strip(), v.strip()))
    return out


class Handler(http.server.SimpleHTTPRequestHandler):
    extra: list[tuple[str, str]] = []

    def end_headers(self) -> None:
        for k, v in self.extra:
            self.send_header(k, v)
        super().end_headers()

    def log_message(self, *args) -> None:  # keep test output quiet
        pass


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    dist = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "web" / "dist"
    Handler.extra = global_headers(dist)
    handler = functools.partial(Handler, directory=str(dist))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as srv:
        print(f"serving {dist} on http://127.0.0.1:{port}", flush=True)
        srv.serve_forever()


if __name__ == "__main__":
    main()
