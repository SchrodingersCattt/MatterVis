from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from dash import no_update

from mat_viewer.app import callbacks_view, create_app
from mat_viewer.app.dash_impl import _camera_figure_patch
from mat_viewer.loader import build_loaded_crystal
from mat_viewer.presets import DEFAULT_STYLE


SY_CIF = Path("scripts/data/SY.cif")


from _layout_helpers import (  # noqa: E402  shared helpers
    callback_inputs as _inputs,
    callback_outputs as _outputs,
)


def test_view_buttons_submit_camera_update_message(tmp_path: Path):
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))

    callbacks = [
        callback
        for callback in app.callback_map.values()
        if ("view-reset", "n_clicks") in _inputs(callback)
    ]

    assert len(callbacks) == 1
    outputs = _outputs(callbacks[0])
    assert ("camera-state-store", "data") in outputs
    assert ("view-update-store", "data") in outputs
    assert ("fast-view-metadata", "children") in outputs


def test_camera_reset_restores_default_zoom_after_a_deep_zoom(tmp_path: Path):
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    backend = app.crystal_backend
    scene_id = backend.active_scene_id()
    default = backend.default_camera(backend.get_state(scene_id))
    zoomed = {
        "eye": {"x": 0.08, "y": 0.06, "z": 0.04},
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "up": {"x": 0.0, "y": 1.0, "z": 0.0},
    }
    backend.patch_state({"camera": zoomed}, scene_id=scene_id, broadcast=False)

    reset = backend.camera_action("reset", scene_id=scene_id, broadcast=False)

    assert reset == default
    assert reset["eye"] != zoomed["eye"]


def test_projection_buttons_submit_camera_update_message(tmp_path: Path):
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))

    callbacks = [
        callback
        for callback in app.callback_map.values()
        if ("view-projection", "value") in _inputs(callback)
        and ("camera-state-store", "data") in _outputs(callback)
    ]

    assert len(callbacks) == 1
    outputs = _outputs(callbacks[0])
    assert ("view-update-store", "data") in outputs
    assert ("fast-view-metadata", "children") in outputs


def _callback(app, name):
    return next(
        spec["callback"].__wrapped__
        for spec in app.callback_map.values()
        if spec.get("callback") and spec["callback"].__name__ == name
    )


def test_projection_radio_follows_the_selected_tab(tmp_path: Path, monkeypatch):
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    try:
        backend = app.crystal_backend
        scene_a = backend.active_scene_id()
        scene_b = backend.create_scene(
            structure=backend.get_state()["structure"],
            label="Second",
        )["id"]
        backend.set_projection("orthographic", scene_id=scene_a, broadcast=False)
        backend.set_projection("perspective", scene_id=scene_b, broadcast=False)
        sync = _callback(app, "sync_view_projection_from_state")
        stale = {"scene_id": scene_a, "projection": "orthographic"}

        monkeypatch.setattr(
            callbacks_view,
            "callback_context",
            SimpleNamespace(
                triggered_id="scene-tabs",
                triggered=[{"prop_id": "scene-tabs.value"}],
            ),
        )
        assert sync(stale, scene_b) == "perspective"

        monkeypatch.setattr(
            callbacks_view,
            "callback_context",
            SimpleNamespace(
                triggered_id="agent-state-store",
                triggered=[
                    {"prop_id": "agent-state-store.data"},
                    {"prop_id": "scene-tabs.value"},
                ],
            ),
        )
        assert sync(stale, scene_b) == "perspective"

        monkeypatch.setattr(
            callbacks_view,
            "callback_context",
            SimpleNamespace(
                triggered_id="agent-state-store",
                triggered=[{"prop_id": "agent-state-store.data"}],
            ),
        )
        assert sync(stale, scene_b) is no_update
        assert sync({"scene_id": scene_b, "projection": "orthographic"}, scene_b) == "perspective"

        # A same-scene snapshot may predate a newer camera command. Its
        # projection and revision cannot roll the radio back either.
        old_state = backend.get_state(scene_b)
        backend.set_projection("orthographic", scene_id=scene_b, broadcast=False)
        assert backend.get_state(scene_b)["camera_revision"] > old_state["camera_revision"]
        assert sync(old_state, scene_b) == "orthographic"

        # Untagged or deleted scenes cannot supply a radio value for the
        # selected scene, even if their projection happens to be valid.
        for payload in (None, {}, {"projection": "perspective"}, stale):
            assert sync(payload, None) is no_update
            assert sync(payload, "deleted-scene") is no_update
        assert sync({"projection": "perspective"}, scene_b) is no_update
    finally:
        app.close_extensions()
        app.crystal_backend.close()


def test_camera_capture_also_notifies_flat_ortep_render_path(tmp_path: Path):
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    callbacks = [
        callback
        for callback in app.callback_map.values()
        if ("camera-capture-store", "data") in _inputs(callback)
        and ("camera-state-store", "data") in _outputs(callback)
    ]
    assert len(callbacks) == 1
    assert ("agent-state-store", "data") in _outputs(callbacks[0])
    assert ("view-update-store", "data") in _outputs(callbacks[0])


def test_camera_patch_carries_viewport_aspect_contract():
    if not SY_CIF.exists():
        pytest.skip("local SY CIF fixture is not present")
    bundle = build_loaded_crystal(name="SY", cif_path="scripts/data/SY.cif", title="SY")
    style = {
        **DEFAULT_STYLE,
        **bundle.scene.get("style", {}),
        "display_mode": "unit_cell",
        "show_axes": False,
        "show_axis_key": False,
    }
    camera = {"eye": {"x": 1.25, "y": 1.25, "z": 1.25}, "center": {"x": 0, "y": 0, "z": 0}}

    patch = _camera_figure_patch(bundle.scene, style, camera)
    operations = {
        tuple(operation["location"]): operation["params"]["value"]
        for operation in patch.to_plotly_json()["operations"]
    }

    assert operations[("layout", "scene", "camera")] == camera
    assert operations[("layout", "scene", "aspectmode")] == "manual"
    assert ("layout", "scene", "aspectratio") in operations
    assert ("layout", "scene", "xaxis") in operations
    assert ("layout", "scene", "yaxis") in operations
    assert ("layout", "scene", "zaxis") in operations
