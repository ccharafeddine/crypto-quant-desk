# Packaging

Windows is the primary target (`packaging/windows/`). macOS artifacts
(`crypto-quant-desk.spec`, `build_dmg.sh`) are kept for the cross-platform build
and documented further down.

---

# Packaging (Windows)

Produces a per-user installer: `dist/installer/crypto-quant-desk-2.0.0-setup.exe`.
Two steps, both from the repo root in the project venv (`pip install -e ".[dev]"`
installs PyInstaller).

## 1. Build the app bundle

```powershell
pyinstaller packaging\windows\cqd.spec
```

Output: `dist\crypto-quant-desk\crypto-quant-desk.exe` (onedir). Build artifacts
(`build/`, `dist/`) are gitignored.

## 2. Build the installer

Needs [Inno Setup 6](https://jrsoftware.org/isdl.php) (`ISCC.exe`):

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\windows\installer.iss
```

Output: `dist\installer\crypto-quant-desk-2.0.0-setup.exe`.

## What the Windows spec does

- **Entry point:** `src/cqd/__main__.py` (`python -m cqd` -> `cqd.app.run()`).
- **Mode:** `--onedir`, windowed (`console=False`).
- **No kraken CLI** is bundled: Windows is REST/WebSocket primary and the CLI has
  no Windows build (it is only ever reached optionally through WSL).
- **Explicit collection** for the packages PyInstaller's default graph misses:
  `PySide6QtAds` (compiled docking extension), `keyring` + `keyring.backends.Windows`
  (Credential Manager), `winotify` (toasts), `certifi` (TLS CA bundle for httpx),
  and `anthropic`. Qt plugins come from PySide6's built-in hook; `pyqtgraph` is
  collected as dynamic submodules. The theme is generated in code, so there is no
  `.qss` data file to ship.
- **Icon + version resource:** `cqd.ico` and `version_info.txt`. Regenerate the
  icon (Slate-theme candlesticks, drawn with PySide6, no extra tooling) with
  `python packaging\windows\make_icon.py`.
- **No secrets:** `.env` is never bundled. API keys live in Windows Credential
  Manager; user data lives under `%LOCALAPPDATA%\CryptoQuantDesk`.

## Install / uninstall (Windows, end users)

1. Run the setup `.exe`. It installs **per-user** (no admin) under
   `%LOCALAPPDATA%\Programs\Crypto Quant Desk`, adds a Start-menu shortcut
   (desktop shortcut optional), and can launch on finish.
2. On first run, open **File > Settings** and add your Kraken API keys
   (read-only: Query Funds/Orders/Trades/Ledger; **never** Withdraw) and,
   optionally, an Anthropic key for the AI analyst.
3. To explore with a sample portfolio and no keys, the app defaults to demo data
   until keys are entered.
4. **Uninstall** removes only the program files. Your data under
   `%LOCALAPPDATA%\CryptoQuantDesk` and your keys in Credential Manager are left
   in place, so a reinstall picks up where you left off.

## Smoke test (Windows)

Run the built exe in demo mode from a console (a clean launch opens the window
with no traceback and no "could not load the Qt platform plugin" error):

```powershell
$env:CQD_DATA_SOURCE = "demo"; .\dist\crypto-quant-desk\crypto-quant-desk.exe
```

---

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
- **No kraken CLI** is bundled: the app is REST/WebSocket primary and the CLI is
  optional and never required.
- **Qt plugins** (incl. the cocoa platform plugin) come from PySide6's built-in
  PyInstaller hook. `keyring` (macOS Keychain backend), `PySide6-QtAds`, `certifi`
  and `anthropic` are collected explicitly; `pyqtgraph` is collected as dynamic
  submodules. The theme is generated in code, so no `.qss` data file ships.
- **Icon:** `packaging/windows/cqd.icns` (the shared icon source, regenerate with
  `python packaging/windows/make_icon.py`).
- **No secrets:** `.env` is never bundled. Keys live in the macOS Keychain; user
  data lives under `~/Library/Application Support/CryptoQuantDesk`.

## Build the .dmg (Phase 2)

```bash
bash packaging/build_dmg.sh
```

This rebuilds the `.app`, strips extended-attribute detritus
(`xattr -cr`, which is what the Phase 1 ad-hoc codesign tripped on), and wraps it
in `dist/Crypto-Quant-Desk-2.0.0.dmg` with a drag-to-`/Applications` layout. It
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
3. On first run, open **File > Settings** and add your Kraken API keys
   (read-only: Query Funds/Orders/Trades/Ledger; **never** Withdraw), stored in
   the macOS Keychain, and optionally an Anthropic key for the AI analyst. The
   app defaults to demo data until keys are entered.

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
