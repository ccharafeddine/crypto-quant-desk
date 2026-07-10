"""Tests for the token-driven theme registry (pure, no GUI loop)."""

from cqd.ui.theme import (
    DEFAULT_THEME_NAME,
    THEMES,
    Theme,
    build_qss,
    build_qtads_qss,
    default_theme,
    get_theme,
)


def test_registry_has_the_three_themes() -> None:
    assert set(THEMES) == {"Slate", "Amber", "Teal"}
    assert all(isinstance(t, Theme) for t in THEMES.values())


def test_default_is_slate() -> None:
    assert DEFAULT_THEME_NAME == "Slate"
    assert default_theme().name == "Slate"
    assert default_theme().accent == "#5B8CFF"


def test_get_theme_falls_back_to_default() -> None:
    assert get_theme("nope").name == "Slate"
    assert get_theme(None).name == "Slate"
    assert get_theme("Teal").name == "Teal"


def test_build_qss_amber_contains_orange_and_mono() -> None:
    qss = build_qss(THEMES["Amber"])
    assert qss.strip()  # non-empty
    assert "#F7931A" in qss  # the Bitcoin-orange accent
    assert "Cascadia Mono" in qss  # mono type system for data widgets


def test_each_theme_builds_with_its_accent() -> None:
    for theme in THEMES.values():
        qss = build_qss(theme)
        assert theme.accent in qss
        # No unresolved template tokens left behind.
        assert "${" not in qss


def test_themes_share_base_differ_only_by_accent() -> None:
    accents = {t.accent for t in THEMES.values()}
    assert len(accents) == 3  # distinct accents
    bases = {(t.bg, t.surface, t.border, t.text) for t in THEMES.values()}
    assert len(bases) == 1  # one shared base


def test_elevation_ramp_is_a_distinct_ordered_set() -> None:
    # The four-step canvas ramp must be four different values so cards read.
    t = default_theme()
    ramp = [t.bg, t.surface, t.surface_raised, t.elevated]
    assert len(set(ramp)) == 4
    assert t.border != t.border_strong


def test_build_qss_uses_elevation_tokens() -> None:
    qss = build_qss(default_theme())
    for token in (default_theme().surface_raised, default_theme().border_strong):
        assert token in qss


def test_build_qtads_qss_resolves_and_targets_ads_selectors() -> None:
    for theme in THEMES.values():
        qss = build_qtads_qss(theme)
        assert qss.strip()  # non-empty
        assert "${" not in qss  # every token resolved
        assert "ads--CDockWidgetTab" in qss  # actually styles QtAds chrome
        assert theme.accent in qss  # active-tab edge carries the accent
