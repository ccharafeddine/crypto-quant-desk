"""Tests for the adjustable QtAds workspace (E1).

Two layers: pure validation of the layout specs (no QApplication), and a real
QtAds round-trip (perspectives, save/restore, reset) behind pytest-qt so the
docking foundation is exercised end to end.
"""

from __future__ import annotations

import pytest
import shiboken6
from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtWidgets import QLabel, QMainWindow

from cqd.ui.workspace import (
    DEFAULT_PERSPECTIVE,
    LAYOUTS,
    PANEL_KEYS,
    PRESET_NAMES,
    STATE_KEY,
    Workspace,
    validate_layout,
)

# ---------------------------------------------------------------- pure specs


def test_all_presets_are_valid_and_complete() -> None:
    for name in PRESET_NAMES:
        placements = LAYOUTS[name]
        validate_layout(placements)  # must not raise
        keys = [key for key, _area, _target in placements]
        assert sorted(keys) == sorted(PANEL_KEYS), f"{name} must place every panel once"


def test_default_perspective_is_a_shipped_preset() -> None:
    assert DEFAULT_PERSPECTIVE in PRESET_NAMES
    assert DEFAULT_PERSPECTIVE in LAYOUTS


def test_layouts_dict_matches_preset_names() -> None:
    assert set(LAYOUTS) == set(PRESET_NAMES)


def test_validate_rejects_first_target() -> None:
    with pytest.raises(ValueError):
        validate_layout([("chart", list(LAYOUTS["Trading"])[0][1], "positions")])


def test_validate_rejects_forward_target() -> None:
    center = LAYOUTS["Trading"][0][1]
    with pytest.raises(ValueError):
        validate_layout(
            [
                ("chart", center, None),
                ("risk", center, "positions"),  # positions placed later -> invalid
                ("positions", center, "chart"),
            ]
        )


def test_validate_rejects_duplicate_panel() -> None:
    center = LAYOUTS["Trading"][0][1]
    with pytest.raises(ValueError):
        validate_layout([("chart", center, None), ("chart", center, "chart")])


def test_validate_rejects_unknown_panel() -> None:
    center = LAYOUTS["Trading"][0][1]
    with pytest.raises(ValueError):
        validate_layout([("nope", center, None)])


def test_validate_rejects_bad_area() -> None:
    with pytest.raises(ValueError):
        validate_layout([("chart", 999, None)])


def test_validate_rejects_incomplete_layout() -> None:
    center = LAYOUTS["Trading"][0][1]
    with pytest.raises(ValueError):
        validate_layout([("chart", center, None)])  # only one of nine panels


# ------------------------------------------------------------- QtAds round-trip


@pytest.fixture
def make_workspace(qtbot):
    """Factory for test workspaces that force-deletes each manager on teardown.

    QtAds CDockManagers left to qtbot's deferred cleanup accumulate across the
    suite; once a few coexist (which happens sooner when pyqtgraph is also
    imported into the process), pytest-qt's post-test app.processEvents() hangs
    on them. Deleting synchronously in teardown keeps at most one manager alive
    at a time, so processEvents never has a pile to choke on.
    """
    created: list[tuple[Workspace, QMainWindow]] = []

    def _factory() -> Workspace:
        win = QMainWindow()
        qtbot.addWidget(win)
        ws = Workspace(win)
        for key in PANEL_KEYS:
            ws.add_panel(key, key.title(), QLabel(key))
        created.append((ws, win))
        return ws

    yield _factory

    for ws, win in created:
        shiboken6.delete(ws.manager)
        shiboken6.delete(win)


def _ini(tmp_path, name: str) -> QSettings:
    return QSettings(str(tmp_path / name), QSettings.Format.IniFormat)


def test_ensure_presets_creates_all_shipped_perspectives(make_workspace, tmp_path) -> None:
    ws = make_workspace()
    ws.ensure_presets(_ini(tmp_path, "s.ini"))
    assert set(PRESET_NAMES) <= set(ws.perspective_names())
    # Every panel is placed after ensure_presets settles on the default.
    assert set(ws.manager.dockWidgetsMap().keys()) == set(PANEL_KEYS)


def test_apply_layout_preserves_all_panels(make_workspace, tmp_path) -> None:
    ws = make_workspace()
    ws.ensure_presets(_ini(tmp_path, "s.ini"))
    for name in PRESET_NAMES:
        ws.apply_layout(name)
        assert set(ws.manager.dockWidgetsMap().keys()) == set(PANEL_KEYS)


def test_state_save_restore_roundtrip(make_workspace, tmp_path) -> None:
    ws = make_workspace()
    ws.ensure_presets(_ini(tmp_path, "s.ini"))
    ws.apply_layout("Analysis")
    settings = _ini(tmp_path, "state.ini")
    ws.save_state(settings)
    # Mutate away, then restore should succeed and keep every panel.
    ws.apply_layout("Monitor")
    assert ws.restore_state(settings) is True
    assert set(ws.manager.dockWidgetsMap().keys()) == set(PANEL_KEYS)


def test_restore_absent_state_returns_false(make_workspace, tmp_path) -> None:
    ws = make_workspace()
    ws.ensure_presets(_ini(tmp_path, "s.ini"))
    assert ws.restore_state(_ini(tmp_path, "empty.ini")) is False


def test_restore_corrupt_state_falls_back_without_crashing(make_workspace, tmp_path) -> None:
    ws = make_workspace()
    ws.ensure_presets(_ini(tmp_path, "s.ini"))
    bad = _ini(tmp_path, "bad.ini")
    bad.setValue(STATE_KEY, QByteArray(b"not a valid layout blob"))
    assert ws.restore_state(bad) is False
    # Workspace is still intact after a rejected restore (AC10.2).
    assert set(ws.manager.dockWidgetsMap().keys()) == set(PANEL_KEYS)


def test_save_and_delete_custom_perspective(make_workspace, tmp_path) -> None:
    ws = make_workspace()
    settings = _ini(tmp_path, "s.ini")
    ws.ensure_presets(settings)
    ws.save_perspective("MyView", settings)
    assert "MyView" in ws.perspective_names()
    assert ws.custom_perspectives() == ["MyView"]  # presets excluded
    ws.delete_perspective("MyView", settings)
    assert "MyView" not in ws.perspective_names()


def test_reset_returns_to_default_with_all_panels(make_workspace, tmp_path) -> None:
    ws = make_workspace()
    ws.ensure_presets(_ini(tmp_path, "s.ini"))
    ws.apply_layout("Monitor")
    ws.reset()
    assert set(ws.manager.dockWidgetsMap().keys()) == set(PANEL_KEYS)


def test_apply_theme_over_all_themes_does_not_raise(make_workspace, tmp_path) -> None:
    from cqd.ui.theme import THEMES, build_qtads_qss

    ws = make_workspace()
    ws.ensure_presets(_ini(tmp_path, "s.ini"))
    for theme in THEMES.values():
        ws.apply_theme(build_qtads_qss(theme))
    # The shipped default (with the button icons) stays layered underneath ours.
    assert ws._ads_default_qss in ws.manager.styleSheet()


def test_stale_panelset_discards_saved_state(make_workspace, tmp_path) -> None:
    # A layout saved under a different panel set must not be restored, so a
    # newly added panel isn't left unplaced.
    from cqd.ui.workspace import VERSION_KEY

    ws = make_workspace()
    settings = _ini(tmp_path, "s.ini")
    ws.ensure_presets(settings)
    ws.save_state(settings)
    assert ws.restore_state(settings) is True  # current panel set restores
    settings.setValue(VERSION_KEY, "some|older|panelset")
    assert ws.restore_state(settings) is False  # stale panel set is ignored


def test_toggle_actions_cover_every_panel(make_workspace) -> None:
    ws = make_workspace()
    actions = ws.toggle_actions()
    assert len(actions) == len(PANEL_KEYS)
    assert [a.text() for a in actions] == [k.title() for k in PANEL_KEYS]
