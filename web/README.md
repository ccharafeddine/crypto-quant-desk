# Live demo (cqd.chafic.me)

A static page that runs the desktop app's pure engine in the browser with
[Pyodide](https://pyodide.org). The page fetches Kraken **public** market data
(daily OHLC and AssetPairs) straight from `api.kraken.com`, and the engine
computes the Risk page, the Risk lab and the Signals backtest from it.

- **No keys and no account.** The page calls only `/0/public/` endpoints.
- **No order path.** The bundle contains `cqd/engine`, the two data modules it
  needs (`normalize`, `crash_history`) and `cqd/web`. It contains nothing from
  `trading/`, `ui/`, the REST/WebSocket clients or credentials.
  `scripts/build_web_demo.py` refuses to build if any of that, or any
  private-endpoint string, gets in, and `tests/test_web_demo.py` pins this.
- **Strict CSP** (`_headers`): scripts may load only from the site and the
  Pyodide CDN, and network requests may go only to the site, the CDN and
  `api.kraken.com`.

## Files

| File | Role |
|---|---|
| `index.html`, `style.css` | Page and Graphite styling |
| `app.js` | Fetches Kraken public data, renders tables and SVG charts |
| `worker.js` | Loads Pyodide 314.0.7 plus numpy/pandas/pydantic and the engine bundle in a Web Worker |
| `_headers` | Cloudflare Pages headers (CSP and friends) |

## Build, serve, check

```bash
python scripts/build_web_demo.py              # -> web/dist (about 250 KB plus the Pyodide CDN)
python scripts/serve_web_demo.py 8787         # serves dist with the _headers CSP
python scripts/check_web_demo.py http://127.0.0.1:8787/ --shot demo.png
```

`check_web_demo.py` loads the page in headless Chrome. It fails on any page
error, any console error, a request to a host other than the site, the CDN and
Kraken, or a Kraken path outside `/0/public/`. It prints the load timings.

## Deploy (only with approval)

```bash
CQD_DEPLOY_APPROVED=yes scripts/deploy_web_demo.sh --confirm cqd.chafic.me
```

The script runs the demo tests, rebuilds and publishes with
`wrangler pages deploy`. It refuses to run without both guards or with
uncommitted changes. The custom domain is attached once in the Cloudflare
dashboard; the script header has the steps.
