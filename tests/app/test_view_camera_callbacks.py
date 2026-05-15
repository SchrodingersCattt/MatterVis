from __future__ import annotations

from pathlib import Path

from crystal_viewer.app import create_app


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
