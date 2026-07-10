"""Generate the Windows app icon (cqd.ico) and a PNG preview.

Uses PySide6 (already a dependency) so no extra build tooling is needed. The mark
is an ascending candlestick trio in the Slate theme's identity colors - the same
near-black canvas and accent blue the app ships with - so the taskbar/titlebar
icon matches the running app.

Run from the repo root:  python packaging/windows/make_icon.py
Writes cqd.ico (Windows), cqd.icns (macOS bundle), and cqd.png (README preview)
into packaging/windows/.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen  # noqa: E402

# Slate theme tokens (kept in sync with src/cqd/ui/theme).
BG = "#0B0D10"
BORDER = "#333B47"
ACCENT = "#5B8CFF"
GREEN = "#2FBF71"

HERE = os.path.dirname(os.path.abspath(__file__))


def _candle(p: QPainter, cx: float, top: float, bottom: float, body_top: float,
            body_bottom: float, half: float, color: QColor) -> None:
    """One candlestick: a thin wick with a rounded body centered on cx."""
    p.setPen(QPen(color, half * 0.55, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(int(cx), int(top), int(cx), int(bottom))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawRoundedRect(
        QRectF(cx - half, body_top, half * 2, body_bottom - body_top), half * 0.6, half * 0.6
    )


def render(size: int = 256) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # Rounded-square canvas with a hairline border.
    pad = size * 0.06
    radius = size * 0.22
    canvas = QRectF(pad, pad, size - 2 * pad, size - 2 * pad)
    p.setBrush(QBrush(QColor(BG)))
    p.setPen(QPen(QColor(BORDER), max(1.0, size / 128)))
    p.drawRoundedRect(canvas, radius, radius)

    # Three ascending candles: two accent-blue, the leader green (an uptrend).
    half = size * 0.055
    xs = [size * 0.34, size * 0.5, size * 0.66]
    specs = [
        # (wick_top, wick_bottom, body_top, body_bottom, color)
        (0.44, 0.74, 0.50, 0.68, ACCENT),
        (0.34, 0.70, 0.42, 0.60, ACCENT),
        (0.24, 0.60, 0.30, 0.50, GREEN),
    ]
    for cx, (wt, wb, bt, bb, color) in zip(xs, specs):
        _candle(p, cx, size * wt, size * wb, size * bt, size * bb, half, QColor(color))

    p.end()
    return img


def main() -> int:
    icon = render(256)
    ico_path = os.path.join(HERE, "cqd.ico")
    png_path = os.path.join(HERE, "cqd.png")
    icns_path = os.path.join(HERE, "cqd.icns")  # macOS bundle icon
    if not icon.save(ico_path, "ICO"):
        print(f"failed to write {ico_path}", file=sys.stderr)
        return 1
    icon.save(png_path, "PNG")
    icon.save(icns_path, "ICNS")
    print(f"wrote {ico_path}, {png_path}, {icns_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
