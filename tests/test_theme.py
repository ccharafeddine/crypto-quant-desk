"""Tests for the token-driven theme registry (pure, no GUI loop)."""

from cqd.ui.theme import (
    DEFAULT_THEME_NAME,
    THEMES,
    Theme,
    build_qss,
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
