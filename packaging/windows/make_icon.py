"""Generate the app icons from the vector logo (src/cqd/ui/assets/logo.svg).

Uses PySide6 only (Qt's ico/icns image writers), so no extra build tooling is
needed. Writes into packaging/windows/:

- cqd.ico  - Windows, multi-size (16-256 px)
- cqd.icns - macOS bundle icon (the shared spec points here)
- cqd.png  - 512 px preview for the README

Run from the repo root:  python packaging/windows/make_icon.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

HERE = Path(__file__).resolve().parent
LOGO = HERE.parents[1] / "src" / "cqd" / "ui" / "assets" / "logo.svg"


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    QSvgRenderer(str(LOGO)).render(painter)
    painter.end()
    return img


def main() -> int:
    _app = QGuiApplication(sys.argv)
    # Qt's ICO writer stores the image it is handed; 256 px is the largest
    # entry Windows reads and it downsamples for the small sizes.
    ok = render(256).save(str(HERE / "cqd.ico"), "ICO")
    ok &= render(1024).save(str(HERE / "cqd.icns"), "ICNS")
    ok &= render(512).save(str(HERE / "cqd.png"), "PNG")
    print("wrote cqd.ico, cqd.icns, cqd.png" if ok else "icon export failed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
