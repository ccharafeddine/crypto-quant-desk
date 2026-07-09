# PyInstaller spec for Crypto Quant Desk (Phase 1: launchable .app, unsigned).
#
# Build from the repo root:  pyinstaller packaging/crypto-quant-desk.spec
# Output: dist/Crypto Quant Desk.app  (onedir BUNDLE)
#
# Notes:
#  - onedir (not onefile): more reliable and far faster to debug for Qt apps.
#  - The kraken CLI is bundled at the _MEIPASS root as "kraken"; that is exactly
#    what KrakenClient._resolve_binary() looks for (os.path.join(_MEIPASS,
#    "kraken")), so the packaged app finds its own CLI with nothing installed.
#  - PySide6's PyInstaller hook collects the Qt platform plugin (libqcocoa) and
#    friends automatically; we add explicit collection only for the dynamic
#    packages (pyqtgraph) and the runtime data file the app reads (the QSS).
#  - No .env or secrets are bundled. The app reads the user's own .env at runtime.

import os
import shutil

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# SPECPATH is the directory containing this spec (packaging/); its parent is the
# repo root.
REPO = os.path.dirname(os.path.abspath(SPECPATH))
SRC = os.path.join(REPO, "src")

# Resolve the kraken binary (PATH first, then the known Cargo location). Fail
# the build loudly if it's missing rather than shipping a broken bundle.
KRAKEN = shutil.which("kraken") or os.path.expanduser("~/.cargo/bin/kraken")
if not os.path.isfile(KRAKEN):
    raise SystemExit(f"kraken binary not found (looked at {KRAKEN}); cannot bundle.")

# Runtime data file the app loads via Path(__file__).parent/ui/theme/...; must be
# placed alongside the collected cqd package or _load_stylesheet() crashes.
QSS = os.path.join(SRC, "cqd", "ui", "theme", "kraken_dark.qss")

binaries = [
    (KRAKEN, "."),  # -> _MEIPASS/kraken, matching _resolve_binary()
]

datas = [
    (QSS, "cqd/ui/theme"),
] + collect_data_files("pyqtgraph")

hiddenimports = ["qasync"] + collect_submodules("pyqtgraph")

a = Analysis(
    [os.path.join(SRC, "cqd", "__main__.py")],
    pathex=[SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Trim heavy/unused stacks to keep the Phase 1 bundle lean. These are not on
    # the app's launch path (no notebooks, no test runner, no Qt WebEngine).
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "pytest", "IPython", "notebook"],
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
    icon=None,  # Phase 1: no icon yet (added in the ship pass)
    bundle_identifier="com.ccharafeddine.cryptoquantdesk",
    version="0.2.0",
    info_plist={
        "CFBundleName": "Crypto Quant Desk",
        "CFBundleDisplayName": "Crypto Quant Desk",
        "CFBundleShortVersionString": "0.2.0",
        "CFBundleVersion": "0.2.0",
        "NSHighResolutionCapable": True,
        # Allow the app's dark theme regardless of system appearance.
        "NSRequiresAquaSystemAppearance": False,
        "LSMinimumSystemVersion": "11.0",
    },
)
