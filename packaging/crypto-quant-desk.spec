# PyInstaller spec for Crypto Quant Desk on macOS (onedir .app, unsigned).
#
# Build from the repo root:  pyinstaller packaging/crypto-quant-desk.spec
# Output: dist/Crypto Quant Desk.app  (onedir BUNDLE)
#
# Notes:
#  - onedir (not onefile): more reliable and far faster to debug for Qt apps.
#  - No kraken CLI is bundled. The app is REST/WebSocket primary on every OS; the
#    legacy CLI path is optional and never required, so the .app is self-contained
#    without it.
#  - PySide6's PyInstaller hook collects the Qt cocoa platform plugin and friends
#    automatically. keyring (macOS Keychain backend), PySide6-QtAds (compiled
#    docking extension), certifi (TLS CA bundle) and anthropic are collected
#    explicitly because the default graph misses them. The theme is generated in
#    code, so there is no .qss data file to ship.
#  - No .env or secrets are bundled. Keys live in the macOS Keychain; user data
#    lives under ~/Library/Application Support/CryptoQuantDesk.

import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

APP_VERSION = "2.0.0"

# SPECPATH is the directory containing this spec (packaging/); its parent is the
# repo root.
REPO = os.path.dirname(os.path.abspath(SPECPATH))
SRC = os.path.join(REPO, "src")
ICON = os.path.join(SPECPATH, "windows", "cqd.icns")  # shared icon source

datas = []
binaries = []
hiddenimports = ["qasync"] + collect_submodules("pyqtgraph")

# Packages PyInstaller's default graph misses (winotify is Windows-only and is
# not collected here).
for pkg in ("PySide6QtAds", "keyring", "certifi", "anthropic"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# The macOS Keychain backend loads via an entry point; name it explicitly in case
# collect_all misses the lazy import.
hiddenimports += ["keyring.backends.macOS"]

a = Analysis(
    [os.path.join(SRC, "cqd", "__main__.py")],
    pathex=[SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Trim heavy/unused stacks: nothing on the launch path needs a test runner,
    # notebooks, Tk, or Qt WebEngine.
    excludes=[
        "tkinter",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "pytest",
        "IPython",
        "notebook",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="crypto-quant-desk",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # windowed app; stderr still visible when run via the inner exe
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="crypto-quant-desk",
)

app = BUNDLE(
    coll,
    name="Crypto Quant Desk.app",
    icon=ICON if os.path.isfile(ICON) else None,
    bundle_identifier="com.ccharafeddine.cryptoquantdesk",
    version=APP_VERSION,
    info_plist={
        "CFBundleName": "Crypto Quant Desk",
        "CFBundleDisplayName": "Crypto Quant Desk",
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "NSHighResolutionCapable": True,
        # Allow the app's dark theme regardless of system appearance.
        "NSRequiresAquaSystemAppearance": False,
        "LSMinimumSystemVersion": "11.0",
    },
)
