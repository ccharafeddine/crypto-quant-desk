"""Token-driven theme registry.

One QSS template, interpolated with a small set of design tokens, drives the
whole app. Themes differ only by their accent (and share a deep near-black base),
so switching is purely cosmetic and never touches data or computation.

Type system: every numeric/data surface (tables, metric/value labels) uses the
mono stack; everything else uses the native system UI font. Headers are heavier.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from string import Template

from PySide6.QtCore import QSettings

# Font stacks: system UI for prose/labels, mono for all numbers/data. Qt's QSS
# does not understand the CSS "-apple-system" keyword (it treats it as a literal,
# missing family and logs a slow font-alias warning at startup), so we lead with
# Qt-resolvable named families: Windows first, macOS fallbacks after.
_FONT_UI = '"Segoe UI", "Helvetica Neue", "Arial", sans-serif'
_FONT_MONO = '"Cascadia Mono", "Consolas", "Menlo", "SF Mono", monospace'


@dataclass(frozen=True)
class Theme:
    """Design tokens for one theme. Only `accent` varies across the shipped set."""

    name: str
    bg: str
    surface: str
    border: str
    text: str
    text_muted: str
    accent: str
    positive: str
    negative: str
    font_ui: str = _FONT_UI
    font_mono: str = _FONT_MONO


# Shared deep near-black base; each theme is the base plus one confident accent.
_BASE = dict(
    bg="#0B0D10",
    surface="#14171C",
    border="#232830",
    text="#E6E9EF",
    text_muted="#8A93A2",
    positive="#2FBF71",
    negative="#E5484D",
)

DEFAULT_THEME_NAME = "Slate"

THEMES: dict[str, Theme] = {
    # Cool blue - the default identity.
    "Slate": Theme(name="Slate", accent="#5B8CFF", **_BASE),
    # Bitcoin orange (the former contest-era default, debranded).
    "Amber": Theme(name="Amber", accent="#F7931A", **_BASE),
    # Cool teal.
    "Teal": Theme(name="Teal", accent="#2DD4BF", **_BASE),
}


# One shared QSS template. Tokens are ${name}; QSS uses no '$' so substitution is
# unambiguous. Numeric/data widgets get ${font_mono}; the rest get ${font_ui}.
_QSS_TEMPLATE = Template(
    """
QMainWindow, QWidget {
    background-color: ${bg};
    color: ${text};
    font-family: ${font_ui};
    font-size: 13px;
}

QMenuBar {
    background-color: ${surface};
    border-bottom: 1px solid ${border};
    padding: 3px 4px;
}
QMenuBar::item { background: transparent; padding: 4px 10px; border-radius: 4px; }
QMenuBar::item:selected { background: ${border}; }

QMenu {
    background-color: ${surface};
    border: 1px solid ${border};
    padding: 4px;
}
QMenu::item { padding: 6px 18px; border-radius: 4px; }
QMenu::item:selected { background: ${border}; color: ${text}; }
QMenu::item:checked { color: ${accent}; }
QMenu::separator { height: 1px; background: ${border}; margin: 4px 8px; }

/* Dock chrome is removed in code (custom empty title bar); panels carry their
   own header. This keeps any residual dock title text legible. */
QDockWidget { color: ${text_muted}; titlebar-close-icon: none; titlebar-normal-icon: none; }

/* Slim app header bar (lives above the docks). */
QToolBar#appHeaderBar {
    background-color: ${surface};
    border: none;
    border-bottom: 1px solid ${border};
    padding: 6px 12px;
    spacing: 10px;
}
QLabel[role="app-title"] { font-size: 14px; font-weight: 700; color: ${text}; }

/* Per-panel header row. */
#panelHeader { border-bottom: 1px solid ${border}; }
QLabel[role="panel-title"] { font-size: 14px; font-weight: 700; color: ${text}; }

QStatusBar {
    background: ${surface};
    border-top: 1px solid ${border};
    color: ${text_muted};
}
QStatusBar::item { border: none; }

/* ---- Tables and all numeric data: mono, subtle row separators ---- */
QTableWidget, QTableView {
    background-color: ${bg};
    alternate-background-color: ${bg};
    gridline-color: ${border};
    selection-background-color: ${accent};
    selection-color: ${bg};
    border: none;
    font-family: ${font_mono};
    outline: none;
}
QTableWidget::item, QTableView::item {
    padding: 7px 10px;
    border-bottom: 1px solid ${border};  /* thin row separator, not heavy slabs */
}
QTableView::item:hover { background-color: ${surface}; }
QHeaderView { background-color: ${surface}; }
QHeaderView::section {
    background-color: ${surface};
    color: ${text_muted};
    border: none;
    border-bottom: 2px solid ${border};
    padding: 9px 10px;
    font-family: ${font_ui};
    font-weight: 700;
}
QTableCornerButton::section { background-color: ${surface}; border: none; }

QPushButton {
    background-color: ${surface};
    color: ${text};
    border: 1px solid ${border};
    border-radius: 5px;
    padding: 6px 14px;
}
QPushButton:hover { border-color: ${accent}; }
QPushButton:pressed { background-color: ${border}; }
QPushButton:default { border-color: ${accent}; }
/* Buttons embedded in table rows: compact, or the row height clips the text. */
QPushButton[role="table-action"] {
    padding: 1px 8px;
    font-size: 11px;
    min-height: 0;
}

QLineEdit, QTextEdit, QPlainTextEdit {
    background-color: ${surface};
    color: ${text};
    border: 1px solid ${border};
    border-radius: 5px;
    padding: 7px;
    selection-background-color: ${accent};
    selection-color: ${bg};
    font-family: ${font_mono};
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus { border-color: ${accent}; }
QLineEdit:disabled { color: ${text_muted}; background-color: ${bg}; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: ${border}; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: ${accent}; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: ${border}; border-radius: 5px; min-width: 24px; }
QScrollBar::handle:horizontal:hover { background: ${accent}; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* ---- Role labels ---- */
QLabel[role="title"] {
    font-size: 16px;
    font-weight: 700;
    color: ${text};
    padding-bottom: 2px;
}
QLabel[role="subtitle"] { font-size: 12px; color: ${text_muted}; }
QLabel[role="footnote"] { font-size: 11px; color: ${text_muted}; }
QLabel[role="metric-label"] { font-size: 11px; color: ${text_muted}; }
QLabel[role="metric-value"] {
    font-size: 22px;
    font-weight: 600;
    color: ${text};
    font-family: ${font_mono};
}
/* Small tinted pill - accent text + accent border on surface, never a fill. */
QLabel[role="badge"] {
    background-color: ${surface};
    color: ${accent};
    border: 1px solid ${accent};
    border-radius: 9px;
    padding: 2px 9px;
    font-size: 11px;
    font-weight: 700;
}
"""
)


def build_qss(theme: Theme) -> str:
    """Interpolate a theme's tokens into the shared QSS template."""
    tokens = asdict(theme)
    tokens.pop("name", None)
    return _QSS_TEMPLATE.substitute(tokens)


def get_theme(name: str | None) -> Theme:
    """Resolve a theme by name, falling back to the default for unknown names."""
    return THEMES.get(name or "", THEMES[DEFAULT_THEME_NAME])


def default_theme() -> Theme:
    return THEMES[DEFAULT_THEME_NAME]


# ---- Persistence (QSettings) ----


def _settings() -> QSettings:
    return QSettings("crypto-quant-desk", "cqd")


def load_theme_name() -> str:
    """Persisted theme name, defaulting to Slate when unset/unknown."""
    name = _settings().value("theme", DEFAULT_THEME_NAME)
    name = str(name) if name is not None else DEFAULT_THEME_NAME
    return name if name in THEMES else DEFAULT_THEME_NAME


def save_theme_name(name: str) -> None:
    if name in THEMES:
        _settings().setValue("theme", name)
