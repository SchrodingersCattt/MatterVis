from __future__ import annotations

import math
from pathlib import Path

from crystal_viewer.app.dash_impl import ViewerBackend
from crystal_viewer.loader import build_loaded_crystal
from crystal_viewer.render.viewport import _plotly_camera_from_scene


def _backend() -> ViewerBackend:
    backend = ViewerBackend(
        preset_path=str(Path(".local") / "missing-test-preset.json"),
        names=[],
    )
    backend.bundles["SY"] = build_loaded_crystal(
        name="SY",
        cif_path="scripts/data/SY.cif",
        title="SY",
    )
    backend.catalog["SY"] = {"path": "scripts/data/SY.cif", "title": "SY"}
    backend.structure_names = ["SY"]
    # Keep tests independent of any persisted interactive scene tab.
    backend.scene_store.active_id = None
    backend.current_state = backend.default_state("SY")
    backend.current_state["display_mode"] = "formula_unit"
    return backend


def _camera_close(actual: dict, expected: dict, *, tol: float = 1e-9) -> bool:
    for group in ("eye", "center", "up"):
        for axis in ("x", "y", "z"):
            if not math.isclose(
                float(actual.get(group, {}).get(axis, 0.0)),
                float(expected.get(group, {}).get(axis, 0.0)),
                rel_tol=tol,
                abs_tol=tol,
            ):
                return False
    actual_projection = (actual.get("projection") or {}).get("type")
    expected_projection = (expected.get("projection") or {}).get("type")
    return actual_projection == expected_projection


def test_plain_camera_patch_preserves_camera():
    backend = _backend()
    custom_camera = {
        "eye": {"x": 0.2, "y": 1.4, "z": 1.1},
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "up": {"x": 0.0, "y": 0.0, "z": 1.0},
        "projection": {"type": "perspective"},
    }

    state = backend.patch_state({"camera": custom_camera}, broadcast=False)
    fig, _ = backend.figure_for_state(state)

    actual = fig.layout.scene.camera.to_plotly_json()
    assert _camera_close(actual, custom_camera)
    assert state["camera"] == custom_camera


def test_display_signature_change_drops_stored_camera():
    backend = _backend()
    custom_camera = {
        "eye": {"x": 0.2, "y": 1.4, "z": 1.1},
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "up": {"x": 0.0, "y": 0.0, "z": 1.0},
        "projection": {"type": "perspective"},
    }
    with_camera = backend.patch_state({"camera": custom_camera}, broadcast=False)
    previous_revision = int(with_camera.get("camera_revision", 0) or 0)

    state = backend.patch_state({"display_mode": "unit_cell"}, broadcast=False)
    fig, _ = backend.figure_for_state(state)
    scene = backend.scene_for_state(state)
    expected = _plotly_camera_from_scene(
        scene,
        backend.style_for_state(state, scene=scene),
    )
    actual = fig.layout.scene.camera.to_plotly_json()

    assert state["camera"] is None
    assert int(state.get("camera_revision", 0) or 0) == previous_revision + 1
    assert not _camera_close(actual, custom_camera)
    assert _camera_close(actual, expected)

