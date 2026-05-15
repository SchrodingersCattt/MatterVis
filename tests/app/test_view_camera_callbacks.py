from __future__ import annotations

from pathlib import Path

from crystal_viewer.app import create_app
from crystal_viewer.dash_app_impl import _camera_figure_patch
from crystal_viewer.loader import build_loaded_crystal
from crystal_viewer.presets import DEFAULT_STYLE


def _outputs(callback):
    out = callback.get("output")
    items = out if isinstance(out, list) else [out]
    pairs = set()
    for item in items:
        cid = getattr(item, "component_id", None)
        prop = getattr(item, "component_property", None)
        if isinstance(cid, str) and isinstance(prop, str):
            pairs.add((cid, prop))
    return pairs


def _inputs(callback):
    return {(str(item.get("id")), item.get("property")) for item in callback.get("inputs", [])}


def test_view_buttons_patch_graph_camera_directly(tmp_path: Path):
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))

    callbacks = [
        callback
        for callback in app.callback_map.values()
        if ("view-reset", "n_clicks") in _inputs(callback)
    ]

    assert len(callbacks) == 1
    outputs = _outputs(callbacks[0])
    assert ("camera-state-store", "data") in outputs
    assert ("crystal-graph", "figure") in outputs
    assert ("fast-view-metadata", "children") in outputs


def test_projection_buttons_patch_graph_camera_directly(tmp_path: Path):
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))

    callbacks = [
        callback
        for callback in app.callback_map.values()
        if ("view-projection", "value") in _inputs(callback)
        and ("camera-state-store", "data") in _outputs(callback)
    ]

    assert len(callbacks) == 1
    outputs = _outputs(callbacks[0])
    assert ("crystal-graph", "figure") in outputs
    assert ("fast-view-metadata", "children") in outputs


def test_camera_patch_carries_viewport_aspect_contract():
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
