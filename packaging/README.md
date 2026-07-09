# Packaging (macOS)

Phase 1 builds a launchable, **unsigned** `.app`. Signing, notarization, and the
`.dmg` are Phase 2.

## Build

From the repo root, in the project venv (`pip install -e ".[dev]"` installs
PyInstaller):

```bash
pyinstaller packaging/crypto-quant-desk.spec
```

Output: `dist/Crypto Quant Desk.app` (onedir bundle). Build artifacts (`build/`,
`dist/`) are gitignored.

## What the spec does

- **Entry point:** `src/cqd/__main__.py` (`python -m cqd` -> `cqd.app.run()`).
- **Mode:** `--onedir` (BUNDLE). More reliable and faster to debug than onefile
  for Qt apps.
- **Bundles the `kraken` CLI** at the bundle root (`_MEIPASS/kraken`), which is
  exactly where `KrakenClient._resolve_binary()` looks. The packaged app finds
  its own CLI with nothing installed on the user's machine. The binary is
  resolved at build time via `which kraken` (falls back to `~/.cargo/bin/kraken`);
  the build fails loudly if it is missing.
- **Qt plugins** (incl. the cocoa platform plugin) come from PySide6's built-in
  PyInstaller hook. `pyqtgraph` is collected explicitly (dynamic submodules +
  data files), and the runtime `kraken_dark.qss` stylesheet is bundled next to
  the `cqd` package so `_load_stylesheet()` resolves.
- **No secrets:** `.env` is never bundled. The app loads the user's own `.env`
  at runtime.

## Build the .dmg (Phase 2)

```bash
bash packaging/build_dmg.sh
```

This rebuilds the `.app`, strips extended-attribute detritus
(`xattr -cr`, which is what the Phase 1 ad-hoc codesign tripped on), and wraps it
in `dist/Crypto-Quant-Desk-0.2.0.dmg` with a drag-to-`/Applications` layout. It
uses `create-dmg` if installed, otherwise falls back to `hdiutil` (always present
on macOS); the script prints which it used. The `.dmg` is gitignored.

The `.dmg` is **unsigned** (no Apple Developer account yet). Signing and
notarization are still deferred.

## Install (macOS, end users)

1. Open the `.dmg` and drag **Crypto Quant Desk** to **Applications**.
2. The app is **unsigned**, so the first launch is blocked by Gatekeeper.
   Bypass it once, either:
   - **Right-click** the app in Applications -> **Open** -> **Open** (confirm), or
   - run `xattr -dr com.apple.quarantine "/Applications/Crypto Quant Desk.app"`
     then open it normally.
   After the first open, it launches normally.
3. The app needs your own Kraken API keys (read-only permissions: Query
   Funds/Orders/Trades/Ledger; never Withdraw) in your environment / `.env`.
   To explore with a sample portfolio and no keys, launch in demo mode:
   `CQD_DATA_SOURCE=demo`.

## Smoke test (no GUI required)

Run the inner executable directly so stderr is visible, in demo mode (no keys):

```bash
CQD_DATA_SOURCE=demo "dist/Crypto Quant Desk.app/Contents/MacOS/crypto-quant-desk"
```

A clean launch opens the window with no traceback and no "could not load the Qt
platform plugin" error.

## Not yet done

- App icon (`.icns`).
- Code signing (Developer ID) + notarization (needs an Apple Developer account).
  Until then the `.dmg` and `.app` are unsigned and need the Gatekeeper bypass
  above.
