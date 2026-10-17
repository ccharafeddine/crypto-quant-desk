"""Bundled brand and type assets: the logo, the app icon, and the two fonts.

Files live next to this module so they ship in the wheel and the PyInstaller
bundle alike (the spec adds this folder as data). Inter and JetBrains Mono are
under the SIL Open Font License (texts in fonts/).
"""

from __future__ import annotations

from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent
FONT_FILES = ("Inter.ttf", "JetBrainsMono.ttf")


def asset_path(name: str) -> Path:
    """Absolute path of a bundled asset (raises if it is missing)."""
    path = ASSETS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def load_fonts() -> list[str]:
    """Register the bundled fonts with Qt; returns the family names loaded.

    Safe to call more than once and without a display (Qt dedupes). A font
    that fails to load is skipped: the QSS stacks fall back to system faces.
    """
    from PySide6.QtGui import QFontDatabase

    families: list[str] = []
    for name in FONT_FILES:
        font_id = QFontDatabase.addApplicationFont(str(ASSETS_DIR / "fonts" / name))
        if font_id >= 0:
            families.extend(QFontDatabase.applicationFontFamilies(font_id))
    return families


def app_icon():
    """The app icon (window title bar, taskbar, dock) from the vector logo."""
    from PySide6.QtGui import QIcon

    return QIcon(str(asset_path("logo.svg")))


def mark_pixmap(size: int):
    """The bare mark (no tile) rendered crisply at `size` px for the header."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer

    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    QSvgRenderer(str(asset_path("mark.svg"))).render(painter)
    painter.end()
    return pix
