"""Validate dispatch dependencies, not just a successful /_dash-layout GET."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from dash import no_update

from mat_viewer.app import create_app
from mat_viewer.app import callbacks_view
from _layout_helpers import find_component, layout_ids, walk_layout


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("MATTERVIS_PREWARM", "0")
    viewer = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    try:
        yield viewer
    finally:
        viewer.close_extensions()
        viewer.crystal_backend.close()


def _assert_dependencies_mounted(app, layout):
    components = {
        node.id: node for node in walk_layout(layout)
        if isinstance(getattr(node, "id", None), str)
    }
    errors = []
    for callback in app.callback_map.values():
        dependencies = list(callback.get("inputs", [])) + list(callback.get("state", []))
        outputs = callback["output"]
        for output in outputs if isinstance(outputs, list) else [outputs]:
            dependencies.append({"id": output.component_id, "property": output.component_property})
        for dependency in dependencies:
            cid, prop = dependency["id"], dependency["property"]
            # ALL/MATCH row controls are intentionally conditional. Fixed IDs
            # must exist in the actual layout, not merely validation_layout.
            if isinstance(cid, str) and cid.startswith("{"):
                cid = json.loads(cid)
            if isinstance(cid, dict):
                continue
            if dependency.get("allow_optional") and cid not in components:
                continue
            if cid not in components:
                errors.append(f"{cid}.{prop}: missing component")
            elif prop not in components[cid]._prop_names:
                errors.append(f"{cid}.{prop}: unsupported property")
    assert not errors, "\n".join(sorted(set(errors)))


def _callback(app, name):
    return next(
        spec["callback"].__wrapped__ for spec in app.callback_map.values()
        if "callback" in spec and spec["callback"].__name__ == name
    )


def test_initial_layout_has_all_fixed_callback_dependencies(app):
    assert not app.config.suppress_callback_exceptions
    _assert_dependencies_mounted(app, app.layout())


@pytest.mark.parametrize("target", [
    None, {}, {"kind": "_close"}, {"kind": "unknown"},
    {"kind": "_global", "action": "select_all"},
    {"kind": "atom", "payload": {"label": "C1", "element": "C"}},
    {"kind": "bond", "payload": {"label_pair": "C1-C2"}},
    {"kind": "polyhedron", "payload": {"fragment_label": "M1"}},
])
def test_conditional_menu_keeps_callback_dependencies_mounted(app, target):
    layout = app.layout()
    menu = find_component(layout, "rightclick-menu")
    menu.children, menu.style, menu.className = _callback(app, "render_rightclick_menu")(target)
    _assert_dependencies_mounted(app, layout)
    ids = [node.id for node in walk_layout(layout) if isinstance(getattr(node, "id", None), str)]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("missing_id", ["rcm-action-hide", "polyhedra-controls"])
def test_dependency_check_detects_original_missing_components(app, missing_id):
    layout = app.layout()
    find_component(layout, missing_id).id = "removed-for-regression-test"
    with pytest.raises(AssertionError, match=missing_id):
        _assert_dependencies_mounted(app, layout)


def test_polyhedra_gate_controls_editor_not_toggle(app):
    layout = app.layout()
    controls = find_component(layout, "polyhedra-controls")
    assert {"polyhedra-add-btn", "polyhedra-rows-container"} <= layout_ids(controls)
    assert "topology-toggle" not in layout_ids(controls)
    gate = _callback(app, "gate_polyhedra_controls")
    assert gate([]) == {"display": "none"}
    assert gate(["enabled"]) == {}


@pytest.mark.parametrize("trigger,value,action", [
    ("rcm-action-hide", 0, None),
    ("rcm-action-hide", 1, "hide"),
    ("rightclick-target", None, "select_all"),
])
def test_menu_mount_is_not_a_click_but_buttons_and_shortcuts_dispatch(
    app, monkeypatch, trigger, value, action,
):
    target = {"kind": "atom", "payload": {"label": "C1"}}
    if trigger == "rightclick-target":
        target["action"] = action
    monkeypatch.setattr(callbacks_view, "callback_context", SimpleNamespace(
        triggered_id=trigger,
        triggered=[{"prop_id": f"{trigger}.n_clicks", "value": value}],
    ))
    dispatch = Mock()
    monkeypatch.setattr(callbacks_view, "_dispatch_rightclick_action", dispatch)
    result = _callback(app, "apply_rightclick_action")(
        *([value] + [0] * 10), target, app.crystal_backend.active_scene_id(),
    )
    if action is None:
        dispatch.assert_not_called()
        assert result == (no_update, no_update)
    else:
        dispatch.assert_called_once()
        assert dispatch.call_args.args[2] == action
        assert result[1]["kind"] == "_close"