"""Token-driven theme registry: Graphite.

One QSS template, interpolated with a small set of design tokens, drives the
whole app. Graphite is a near-black graphite ground with ONE amber accent
(#F59E0B, the logo's candle). Green/red are reserved for profit and loss; buy
and sell are neutral side labels; `warning` is a separate, muted ochre so a
caution never reads as the brand accent. A high-contrast variant keeps the
same roles with stronger text and borders for accessibility. Switching is
purely cosmetic and never touches data or computation.

Type system: Inter (bundled, OFL) for UI text and JetBrains Mono (bundled, OFL)
for every numeric/data surface, so figures are tabular and columns line up.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from string import Template

from PySide6.QtCore import QSettings

# Bundled families lead (registered by cqd.ui.assets.load_fonts at startup);
# platform fallbacks follow. Qt's QSS does not understand "-apple-system", so
# only Qt-resolvable named families are listed.
_FONT_UI = '"Inter", "Segoe UI", "Helvetica Neue", "Arial", sans-serif'
_FONT_MONO = '"JetBrains Mono", "Cascadia Mono", "Consolas", "Menlo", monospace'


@dataclass(frozen=True)
class Theme:
    """Design tokens for one theme.

    Elevation is a ramp on the graphite canvas: `bg` (window) < `nav` (left
    rail) < `surface` (card) < `surface_raised` (headers, hover) < `elevated`
    (dialogs, tooltips). `accent_fg` is the text colour ON an accent fill.
    """

    name: str
    bg: str
    nav: str
    surface: str
    surface_raised: str
    elevated: str
    border: str
    border_strong: str
    text: str
    text_muted: str
    text_faint: str
    accent: str
    accent_fg: str
    accent_dim: str
    positive: str
    negative: str
    warning: str
    font_ui: str = _FONT_UI
    font_mono: str = _FONT_MONO


DEFAULT_THEME_NAME = "Graphite"

THEMES: dict[str, Theme] = {
    "Graphite": Theme(
        name="Graphite",
        bg="#0E1114",
        nav="#101418",
        surface="#12161A",
        surface_raised="#171C22",
        elevated="#1D232A",
        border="#22272E",
        border_strong="#2D333B",
        text="#E6EDF3",
        text_muted="#8B949E",
        text_faint="#636E7B",
        accent="#F59E0B",
        accent_fg="#111111",
        accent_dim="#6B4A0E",
        positive="#3FB950",
        negative="#F47067",
        warning="#C69026",
    ),
    # Accessibility: same roles, brighter text, heavier borders, purer P&L hues.
    "Graphite High Contrast": Theme(
        name="Graphite High Contrast",
        bg="#000000",
        nav="#05070A",
        surface="#0A0D10",
        surface_raised="#141A20",
        elevated="#1C232B",
        border="#4A5560",
        border_strong="#7D8995",
        text="#FFFFFF",
        text_muted="#C9D1D9",
        text_faint="#A0AAB4",
        accent="#FFB020",
        accent_fg="#000000",
        accent_dim="#A86E00",
        positive="#56D364",
        negative="#FF7B72",
        warning="#E3B341",
    ),
}

#: Names shipped before Graphite map onto it so a saved choice never breaks.
_LEGACY_NAMES = {"Slate": "Graphite", "Amber": "Graphite", "Teal": "Graphite"}


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
QToolTip { background: ${elevated}; color: ${text}; border: 1px solid ${border_strong}; padding: 4px 6px; }

QMenuBar {
    background-color: ${surface};
    border-bottom: 1px solid ${border};
    padding: 2px 4px;
    color: ${text_muted};
}
QMenuBar::item { background: transparent; padding: 3px 10px; border-radius: 3px; }
QMenuBar::item:selected { background: ${surface_raised}; color: ${text}; }

QMenu {
    background-color: ${elevated};
    border: 1px solid ${border_strong};
    padding: 4px;
}
QMenu::item { padding: 6px 18px; border-radius: 3px; }
QMenu::item:selected { background: ${surface_raised}; color: ${text}; }
QMenu::item:checked { color: ${accent}; }
QMenu::separator { height: 1px; background: ${border}; margin: 4px 8px; }

QDockWidget { color: ${text_muted}; titlebar-close-icon: none; titlebar-normal-icon: none; }

/* ---- App shell: header strip, KPI strip, left nav ---- */
QToolBar#appHeaderBar {
    background-color: ${surface};
    border: none;
    border-bottom: 1px solid ${border};
    padding: 4px 12px;
    spacing: 10px;
}
QLabel[role="app-title"] { font-size: 13px; font-weight: 600; color: ${text}; }
QLabel[role="header-key"] { font-size: 12px; color: ${text_faint}; }
QLabel[role="header-value"] { font-size: 13px; font-weight: 600; color: ${text}; font-family: ${font_mono}; }
QLabel[tone="pos"] { color: ${positive}; }
QLabel[tone="neg"] { color: ${negative}; }
QLabel[tone="accent"] { color: ${accent}; }
QLabel[tone="warn"] { color: ${warning}; }
QLabel[tone="muted"] { color: ${text_muted}; }

#kpiStrip { background: ${surface}; border: 1px solid ${border}; }
#kpiCell { border-right: 1px solid ${border}; background: transparent; }
QLabel[role="kpi-label"] {
    font-size: 10px; font-weight: 600; color: ${text_faint};
    text-transform: uppercase; letter-spacing: 0.6px;
}
QLabel[role="kpi-value"] { font-size: 17px; font-weight: 500; color: ${text}; font-family: ${font_mono}; }
QLabel[role="kpi-value"][tone="pos"] { color: ${positive}; }
QLabel[role="kpi-value"][tone="neg"] { color: ${negative}; }
QLabel[role="kpi-sub"] { font-size: 11px; color: ${text_faint}; }

QLabel[role="page-title"] { font-size: 18px; font-weight: 600; color: ${text}; }
QLabel[role="page-sub"] { font-size: 12px; color: ${text_faint}; }

#navRail { background: ${nav}; border: none; border-right: 1px solid ${border}; }
QLabel[role="nav-section"] {
    font-size: 10px; font-weight: 600; color: ${text_faint};
    text-transform: uppercase; letter-spacing: 0.8px; padding: 12px 16px 4px 16px;
}
QPushButton[role="nav-item"] {
    background: transparent; border: none; border-left: 2px solid transparent;
    border-radius: 0; color: ${text_muted}; text-align: left; padding: 5px 14px;
}
QPushButton[role="nav-item"]:hover { color: ${text}; background: ${surface}; }
QPushButton[role="nav-item"]:checked {
    color: ${text}; background: ${surface_raised}; border-left: 2px solid ${accent};
}

/* Per-panel header row: the card's section label. */
#panelHeader { border-bottom: 1px solid ${border}; }
QLabel[role="panel-title"] {
    font-size: 11px;
    font-weight: 600;
    color: ${text_muted};
    text-transform: uppercase;
    letter-spacing: 0.6px;
}

QStatusBar {
    background: ${surface};
    border-top: 1px solid ${border};
    color: ${text_faint};
    font-size: 11px;
}
QStatusBar::item { border: none; }

/* ---- Tabs ---- */
QTabWidget::pane { border: 1px solid ${border}; }
QTabBar { background: transparent; }
QTabBar::tab {
    background: transparent;
    color: ${text_muted};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 5px 12px;
    margin-right: 2px;
}
QTabBar::tab:selected { color: ${text}; border-bottom: 2px solid ${accent}; }
QTabBar::tab:hover:!selected { color: ${text}; }
QTabBar::tab:bottom:selected { border-bottom: none; border-top: 2px solid ${accent}; }

QListWidget {
    background-color: ${bg};
    color: ${text};
    border: 1px solid ${border};
    border-radius: 3px;
    font-family: ${font_mono};
}
QListWidget::item { padding: 3px 8px; }
QListWidget::item:selected { background: ${surface_raised}; color: ${text}; }

/* ---- Tables and numeric data: mono (tabular), hairline row separators ---- */
QTableWidget, QTableView {
    background-color: ${surface};
    alternate-background-color: ${surface};
    gridline-color: ${border};
    selection-background-color: ${surface_raised};
    selection-color: ${text};
    border: none;
    font-family: ${font_mono};
    font-size: 12px;
    outline: none;
}
QTableWidget::item, QTableView::item {
    padding: 4px 8px;
    border-bottom: 1px solid ${border};
}
QTableView::item:hover { background-color: ${surface_raised}; }
QHeaderView { background-color: ${surface}; }
QHeaderView::section {
    background-color: ${surface};
    color: ${text_faint};
    border: none;
    border-bottom: 1px solid ${border};
    padding: 5px 8px;
    font-family: ${font_ui};
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
}
QTableCornerButton::section { background-color: ${surface}; border: none; }

QPushButton {
    background-color: transparent;
    color: ${text};
    border: 1px solid ${border_strong};
    border-radius: 3px;
    padding: 5px 12px;
}
QPushButton:hover { border-color: ${text_faint}; background-color: ${surface_raised}; }
QPushButton:pressed { background-color: ${elevated}; }
QPushButton:default, QPushButton[role="primary"] {
    background-color: ${accent}; border-color: ${accent}; color: ${accent_fg}; font-weight: 600;
}
QPushButton[role="primary"]:hover { background-color: ${accent}; border-color: ${text}; }
QPushButton:disabled { color: ${text_faint}; border-color: ${border}; background: transparent; }
QPushButton[role="table-action"] {
    padding: 1px 8px;
    font-size: 11px;
    min-height: 0;
}

QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: ${bg};
    color: ${text};
    border: 1px solid ${border_strong};
    border-radius: 3px;
    padding: 5px 7px;
    selection-background-color: ${accent_dim};
    selection-color: ${text};
    font-family: ${font_mono};
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus { border-color: ${accent}; }
QLineEdit:disabled { color: ${text_faint}; background-color: ${surface}; }
QComboBox QAbstractItemView { background: ${elevated}; border: 1px solid ${border_strong}; }

QCheckBox::indicator, QRadioButton::indicator { width: 13px; height: 13px; }

QScrollBar:vertical { background: transparent; width: 9px; margin: 0; }
QScrollBar::handle:vertical { background: ${border_strong}; border-radius: 4px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: ${text_faint}; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 9px; margin: 0; }
QScrollBar::handle:horizontal { background: ${border_strong}; border-radius: 4px; min-width: 24px; }
QScrollBar::handle:horizontal:hover { background: ${text_faint}; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* ---- Role labels ---- */
QLabel[role="title"] {
    font-size: 16px;
    font-weight: 600;
    color: ${text};
    padding-bottom: 2px;
}
QLabel[role="subtitle"] { font-size: 12px; color: ${text_muted}; }
QLabel[role="footnote"] { font-size: 11px; color: ${text_faint}; }
QLabel[role="empty-state"] { font-size: 12px; color: ${text_muted}; padding: 10px 0; }
QLabel[role="metric-label"] { font-size: 10px; color: ${text_faint}; text-transform: uppercase; letter-spacing: 0.5px; }
QLabel[role="metric-value"] {
    font-size: 18px;
    font-weight: 500;
    color: ${text};
    font-family: ${font_mono};
}
QLabel[role="stream-state"] { font-size: 11px; font-weight: 600; padding-right: 6px; }
QLabel[role="stream-state"][streamState="live"] { color: ${positive}; }
QLabel[role="stream-state"][streamState="delayed"] { color: ${warning}; }
QLabel[role="stream-state"][streamState="offline"] { color: ${negative}; }

/* Badges/chips: a hairline outline, never a fill. */
QLabel[role="badge"] {
    background-color: transparent;
    color: ${accent};
    border: 1px solid ${accent_dim};
    border-radius: 2px;
    padding: 1px 7px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.6px;
}
QLabel[role="badge"][kind="neutral"] { color: ${text_muted}; border-color: ${border_strong}; }
QLabel[role="badge"][kind="live"] { color: ${negative}; border-color: ${negative}; }
QLabel[role="badge"][kind="go"] { color: ${positive}; border-color: ${positive}; }
QLabel[role="badge"][kind="warn"] { color: ${warning}; border-color: ${warning}; }
"""
)


def build_qss(theme: Theme) -> str:
    """Interpolate a theme's tokens into the shared QSS template."""
    tokens = asdict(theme)
    tokens.pop("name", None)
    return _QSS_TEMPLATE.substitute(tokens)


# One QtAds template, themed from the same tokens. Kept separate from the app
# QSS because it is applied to the CDockManager (layered over the manager's
# shipped default stylesheet, which carries the button icons), not the app.
_QTADS_QSS_TEMPLATE = Template(
    """
/* ---- QtAds workspace chrome, themed over the shipped default ---- */
ads--CDockContainerWidget { background: ${bg}; }
ads--CDockContainerWidget ads--CDockSplitter::handle { background: ${bg}; }
ads--CDockContainerWidget ads--CDockSplitter::handle:hover { background: ${accent_dim}; }

/* Each docked group is a flat card on the graphite canvas. */
ads--CDockAreaWidget {
    background: ${surface};
    border: 1px solid ${border};
    border-radius: 0;
}
ads--CDockAreaTitleBar {
    background: ${surface};
    border-bottom: 1px solid ${border};
    padding: 0 4px;
}

ads--CDockWidgetTab {
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 4px 10px;
}
ads--CDockWidgetTab[activeTab="true"] { border-bottom: 2px solid ${accent}; }
ads--CDockWidgetTab QLabel {
    color: ${text_faint}; font-size: 11px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.5px;
}
ads--CDockWidgetTab[activeTab="true"] QLabel { color: ${text}; }

ads--CTitleBarButton { background: transparent; border: none; padding: 3px; border-radius: 3px; }
ads--CTitleBarButton:hover { background: ${surface_raised}; }

ads--CFloatingDockContainer {
    background: ${bg};
    border: 1px solid ${border_strong};
}
"""
)


def build_qtads_qss(theme: Theme) -> str:
    """Theme string for the QtAds dock manager, from the same tokens as the app.

    Applied on top of the manager's shipped default stylesheet (which sets the
    button icons) so ours wins by coming last while the icons survive.
    """
    tokens = asdict(theme)
    tokens.pop("name", None)
    return _QTADS_QSS_TEMPLATE.substitute(tokens)


def get_theme(name: str | None) -> Theme:
    """Resolve a theme by name (legacy names map to Graphite; unknown -> default)."""
    name = _LEGACY_NAMES.get(name or "", name or "")
    return THEMES.get(name, THEMES[DEFAULT_THEME_NAME])


def default_theme() -> Theme:
    return THEMES[DEFAULT_THEME_NAME]


# ---- Persistence (QSettings) ----


def _settings() -> QSettings:
    return QSettings("crypto-quant-desk", "cqd")


def load_theme_name() -> str:
    """Persisted theme name, defaulting to Graphite when unset/unknown/legacy."""
    name = _settings().value("theme", DEFAULT_THEME_NAME)
    name = str(name) if name is not None else DEFAULT_THEME_NAME
    name = _LEGACY_NAMES.get(name, name)
    return name if name in THEMES else DEFAULT_THEME_NAME


def save_theme_name(name: str) -> None:
    if name in THEMES:
        _settings().setValue("theme", name)
