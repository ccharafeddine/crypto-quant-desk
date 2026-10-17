"""Tests for the Graphite theme registry (pure, no GUI loop)."""

import re

from cqd.ui.theme import (
    DEFAULT_THEME_NAME,
    THEMES,
    Theme,
    build_qss,
    build_qtads_qss,
    default_theme,
    get_theme,
)


def _luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_registry_is_graphite_and_its_high_contrast_variant() -> None:
    assert set(THEMES) == {"Graphite", "Graphite High Contrast"}
    assert all(isinstance(t, Theme) for t in THEMES.values())


def test_default_is_graphite_with_the_logo_amber() -> None:
    assert DEFAULT_THEME_NAME == "Graphite"
    t = default_theme()
    assert (t.name, t.accent, t.bg, t.surface) == ("Graphite", "#F59E0B", "#0E1114", "#12161A")


def test_legacy_and_unknown_names_resolve_to_graphite() -> None:
    for name in ("Slate", "Amber", "Teal", "nope", None):
        assert get_theme(name).name == "Graphite"
    assert get_theme("Graphite High Contrast").name == "Graphite High Contrast"


def test_one_accent_and_distinct_semantic_roles() -> None:
    for t in THEMES.values():
        roles = {t.accent, t.positive, t.negative, t.warning}
        assert len(roles) == 4  # warning is not the accent; P&L hues are their own


def test_text_contrast_meets_wcag() -> None:
    for t in THEMES.values():
        assert _contrast(t.text, t.bg) >= 7.0
        assert _contrast(t.text_muted, t.surface) >= 4.5
        assert _contrast(t.accent_fg, t.accent) >= 4.5  # label on the primary button
    hc, g = THEMES["Graphite High Contrast"], THEMES["Graphite"]
    assert _contrast(hc.text_muted, hc.surface) > _contrast(g.text_muted, g.surface)


def test_each_theme_builds_fully_resolved_qss() -> None:
    for theme in THEMES.values():
        qss = build_qss(theme)
        assert theme.accent in qss
        assert "${" not in qss
        assert '"Inter"' in qss and '"JetBrains Mono"' in qss


def test_qss_has_no_gradients() -> None:
    for theme in THEMES.values():
        assert "gradient" not in build_qss(theme).lower()
        assert "gradient" not in build_qtads_qss(theme).lower()


def test_only_the_accent_is_saturated_in_chrome() -> None:
    # Every colour literal in the stylesheet is a token: nothing hard-coded.
    tokens = {
        v for k, v in vars(default_theme()).items() if isinstance(v, str) and v.startswith("#")
    }
    literals = set(re.findall(r"#[0-9A-Fa-f]{6}\b", build_qss(default_theme())))
    assert literals <= tokens


def test_elevation_ramp_is_a_distinct_ordered_set() -> None:
    t = default_theme()
    ramp = [t.bg, t.nav, t.surface, t.surface_raised, t.elevated]
    assert len(set(ramp)) == 5
    lum = [_luminance(c) for c in ramp]
    assert lum == sorted(lum)
    assert t.border != t.border_strong


def test_build_qtads_qss_resolves_and_targets_ads_selectors() -> None:
    for theme in THEMES.values():
        qss = build_qtads_qss(theme)
        assert "${" not in qss
        assert "ads--CDockWidgetTab" in qss
        assert theme.accent in qss
