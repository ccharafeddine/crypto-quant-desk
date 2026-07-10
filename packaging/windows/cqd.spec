# PyInstaller spec for Crypto Quant Desk on Windows (onedir, windowed).
#
# Build from the repo root:  pyinstaller packaging/windows/cqd.spec
# Output: dist/crypto-quant-desk/crypto-quant-desk.exe  (onedir)
#
# Windows differs from the macOS spec in three ways:
#  - No kraken binary is bundled. Windows is REST/WebSocket primary; the CLI has
#    no Windows build and is only ever reached optionally via WSL, so the bundle
#    must not require it.
#  - keyring, PySide6-QtAds and winotify need explicit collection: keyring's
#    Windows Credential Manager backend and QtAds' compiled extension are not on
#    PyInstaller's default graph.
#  - The theme is generated in code (build_qss), so there is NO .qss data file to
#    ship; the only runtime data comes from third-party packages (pyqtgraph,
#    certifi for TLS).
#
# No .env or secrets are bundled. Keys live in Windows Credential Manager and are
# read at runtime; user data lives under %LOCALAPPDATA%\CryptoQuantDesk.

import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

APP_NAME = "crypto-quant-desk"
APP_VERSION = "2.0.0"

# SPECPATH is packaging/windows/; the repo root is two levels up.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(SPECPATH)))
SRC = os.path.join(REPO, "src")
ICON = os.path.join(SPECPATH, "cqd.ico")
VERSION_FILE = os.path.join(SPECPATH, "version_info.txt")

datas = []
binaries = []
hiddenimports = ["qasync"] + collect_submodules("pyqtgraph")

# Packages PyInstaller's default graph misses. collect_all pulls their data
# files, compiled libs and submodules in one shot.
for pkg in ("PySide6QtAds", "keyring", "winotify", "certifi", "anthropic"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# The Windows Credential Manager backend loads via an entry point, so name it and
# its win32 dependency explicitly in case collect_all misses the lazy import.
hiddenimports += ["keyring.backends.Windows", "win32ctypes.core"]

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
    name=APP_NAME,
    debug=False,
    strip=False,
    upx=False,
    console=False,  # windowed GUI app
    icon=ICON if os.path.isfile(ICON) else None,
    version=VERSION_FILE if os.path.isfile(VERSION_FILE) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)
