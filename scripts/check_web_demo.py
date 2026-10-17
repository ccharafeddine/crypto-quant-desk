"""Load the built demo in headless Chrome and report load timings.

    python scripts/check_web_demo.py [URL] [--shot out.png]

Needs Playwright (`pip install playwright`) and Chrome or `playwright install
chromium`. Fails if the page reports an error, if any console error appears,
or if a request goes anywhere other than the page itself, the Pyodide CDN and
Kraken's public API.
"""

from __future__ import annotations

import json
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ALLOWED_HOSTS = {"127.0.0.1", "localhost", "cdn.jsdelivr.net", "api.kraken.com"}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    url = args[0] if args else "http://127.0.0.1:8787/"
    shot = sys.argv[sys.argv.index("--shot") + 1] if "--shot" in sys.argv else None
    errors, hosts, kraken_paths = [], set(), []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception:  # noqa: BLE001 - fall back to Playwright's bundled Chromium
            browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        def on_request(req):
            u = urlparse(req.url)
            hosts.add(u.hostname)
            if u.hostname == "api.kraken.com":
                kraken_paths.append(u.path)

        page.on("request", on_request)
        t0 = time.perf_counter()
        page.goto(url)
        # Poll with evaluate (CDP, not page eval): wait_for_function would need
        # 'unsafe-eval', which the demo's CSP rightly refuses.
        timings: dict = {}
        while time.perf_counter() - t0 < 180:
            timings = page.evaluate("() => window.__cqdTimings || {}")
            if timings.get("done") or timings.get("error"):
                break
            page.wait_for_timeout(250)
        wall = time.perf_counter() - t0
        kpis = page.inner_text("#kpis")
        if shot:
            page.screenshot(path=shot, full_page=True)
        browser.close()

    bad_hosts = sorted(h for h in hosts if h not in ALLOWED_HOSTS)
    bad_paths = sorted({p for p in kraken_paths if not p.startswith("/0/public/")})
    report = {
        "url": url,
        "wall_s": round(wall, 2),
        "timings": timings,
        "hosts": sorted(hosts),
        "kraken_paths": sorted(set(kraken_paths)),
        "console_errors": errors,
        "kpis": kpis.replace("\n", " | "),
    }
    print(json.dumps(report, indent=2))
    failed = timings.get("error") or errors or bad_hosts or bad_paths
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
