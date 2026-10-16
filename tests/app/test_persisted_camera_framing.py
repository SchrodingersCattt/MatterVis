"""A stored camera that misses the scene must not survive a restart or a drag."""

from __future__ import annotations

import json
from pathlib import Path

from mat_viewer.app import ViewerBackend, _camera_store_payload, _persist_relayout_camera
from mat_viewer.render.screen_projection import camera_frames_points
from mat_viewer.render.viewport import _plotly_camera_from_scene
from mat_viewer.render.viewport_spec import resolve_viewport


NACL = Path("benchmarks/structures/ionic_nacl.cif")
OFFSCREEN = {
    "eye": {"x": 0.04, "y": 0.02, "z": 0.01},
    "center": {"x": 0.0, "y": 1.6, "z": 0.0},
    "up": {"x": 0.0, "y": 0.0, "z": 1.0},
    "projection": {"type": "perspective"},
}
# A close eye at (0.5, 0.5, 0.5) leaves only one formula-unit centre inside
# the frustum, so the span test treats it as a missed scene. This nudge
# still shows both atoms.
FRAMED_ZOOM = {
    "eye": {"x": 1.3, "y": 1.2, "z": 1.1},
    "center": {"x": 0.02, "y": -0.01, "z": 0.0},
    "up": {"x": 0.0, "y": 0.0, "z": 1.0},
    "projection": {"type": "perspective"},
}


def _backend(tmp_path: Path) -> ViewerBackend:
    local = tmp_path / ".local"
    local.mkdir(parents=True, exist_ok=True)
    (local / "catalog.local.json").write_text(
        json.dumps(
            {
                "structures": {
                    "ionic_nacl": {
                        "cif_path": str(NACL.resolve()),
                        "title": "ionic_nacl",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return ViewerBackend(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
    )


def _points_and_viewport(backend: ViewerBackend, scene_id: str):
    state = backend.get_state(scene_id)
    scene = backend.scene_for_state(state)
    style = backend.style_for_state(state, scene=scene)
    viewport = resolve_viewport(scene, style)
    points = [atom["cart"] for atom in scene.get("draw_atoms") or []]
    camera = _plotly_camera_from_scene(scene, style)
    return points, viewport, camera


def _shows_atoms(backend: ViewerBackend, scene_id: str) -> bool:
    points, viewport, camera = _points_and_viewport(backend, scene_id)
    return camera_frames_points(
        camera,
        points,
        ranges=viewport.ranges,
        aspectratio=viewport.aspectratio,
    )


def test_unit_cell_nacl_default_shows_both_atoms(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        state = backend.get_state(scene_id)
        # Current inorganic defaults use the complete conventional cell.
        assert state["display_mode"] == "unit_cell"
        points, _, _ = _points_and_viewport(backend, scene_id)
        assert len(points) >= 2
        assert _shows_atoms(backend, scene_id)
    finally:
        backend.close()


def test_restart_replaces_camera_that_misses_the_scene(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state(
            {"camera": OFFSCREEN},
            scene_id=scene_id,
            broadcast=False,
        )
        assert not _shows_atoms(backend, scene_id)
        backend.flush_scene_store()
    finally:
        backend.close()

    restarted = _backend(tmp_path)
    try:
        assert _shows_atoms(restarted, scene_id)
        restored = restarted.get_state(scene_id)["camera"]
        assert restored["center"]["y"] != OFFSCREEN["center"]["y"]
        restarted.camera_action("reset", scene_id=scene_id, broadcast=False)
        assert _shows_atoms(restarted, scene_id)
    finally:
        restarted.close()


def test_restart_keeps_a_zoom_that_still_shows_the_atoms(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state({"camera": FRAMED_ZOOM}, scene_id=scene_id, broadcast=False)
        assert _shows_atoms(backend, scene_id)
        backend.flush_scene_store()
    finally:
        backend.close()

    restarted = _backend(tmp_path)
    try:
        assert _shows_atoms(restarted, scene_id)
        restored = restarted.get_state(scene_id)["camera"]
        assert restored["eye"]["x"] == FRAMED_ZOOM["eye"]["x"]
        assert restored["center"]["y"] == FRAMED_ZOOM["center"]["y"]
    finally:
        restarted.close()


def _persist(backend: ViewerBackend, scene_id: str, camera: dict) -> dict:
    return _persist_relayout_camera(
        backend,
        {"scene_id": scene_id, "relayout": {"scene.camera": camera}},
        _camera_store_payload(scene_id, backend.get_state(scene_id).get("camera")),
        now=10.0,
        last_commit=0.0,
        min_interval_s=0.25,
    )


def test_orthographic_unit_cell_drag_that_misses_is_refit(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state(
            {"display_mode": "unit_cell", "projection": "orthographic"},
            scene_id=scene_id,
            broadcast=False,
        )
        before = backend.get_state(scene_id)
        missed = {
            "eye": {"x": 0.0, "y": 0.0, "z": 50.0},
            "center": {"x": 20.0, "y": 20.0, "z": 20.0},
            "up": {"x": 0.0, "y": 0.0, "z": 1.0},
            "projection": {"type": "orthographic"},
        }
        result = _persist(backend, scene_id, missed)
        assert result is not None
        assert result["refit"] is True
        assert result["view_update"]["payload"]["command"] == "refit"
        assert result["view_update"]["update_kind"] == "camera"
        state = backend.get_state(scene_id)
        assert state["projection"] == "orthographic"
        assert state["camera"]["projection"]["type"] == "orthographic"
        assert state["camera"]["center"]["x"] != missed["center"]["x"]
        assert int(state["camera_revision"]) == int(before["camera_revision"]) + 1
        assert _shows_atoms(backend, scene_id)
    finally:
        backend.close()


def test_drag_that_still_frames_is_stored_unchanged(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state(
            {"display_mode": "unit_cell", "projection": "orthographic"},
            scene_id=scene_id,
            broadcast=False,
        )
        before = backend.get_state(scene_id)
        nudged = {
            "eye": {"x": 1.3, "y": 1.2, "z": 1.1},
            "center": {"x": 0.02, "y": -0.01, "z": 0.0},
            "up": {"x": 0.0, "y": 0.0, "z": 1.0},
            "projection": {"type": "orthographic"},
        }
        result = _persist(backend, scene_id, nudged)
        assert result is not None
        assert result["refit"] is False
        assert result["view_update"] is None
        state = backend.get_state(scene_id)
        assert state["camera"]["eye"]["x"] == nudged["eye"]["x"]
        assert state["camera"]["center"]["y"] == nudged["center"]["y"]
        assert state["projection"] == "orthographic"
        assert int(state["camera_revision"]) == int(before["camera_revision"])
        assert _shows_atoms(backend, scene_id)
    finally:
        backend.close()


def test_figure_stamps_a_framing_camera_without_rewriting_state(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        missed = {
            "eye": {"x": 0.0, "y": 0.0, "z": 50.0},
            "center": {"x": 20.0, "y": 20.0, "z": 20.0},
            "up": {"x": 0.0, "y": 0.0, "z": 1.0},
            "projection": {"type": "orthographic"},
        }
        backend.patch_state(
            {
                "camera": missed,
                "display_mode": "unit_cell",
                "projection": "orthographic",
            },
            scene_id=scene_id,
            broadcast=False,
        )
        assert not _shows_atoms(backend, scene_id)
        state = backend.get_state(scene_id)
        fig, _ = backend.figure_for_state(state)
        assert backend.get_state(scene_id)["camera"]["center"] == missed["center"]
        rendered = fig.layout.scene.camera.to_plotly_json()
        points, viewport, _camera = _points_and_viewport(backend, scene_id)
        assert camera_frames_points(
            rendered,
            points,
            ranges=viewport.ranges,
            aspectratio=viewport.aspectratio,
            projection="orthographic",
        )
        assert rendered["projection"]["type"] == "orthographic"
    finally:
        backend.close()


def test_display_capture_refits_a_camera_the_new_scope_cannot_show(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        missed = {
            "eye": {"x": 0.0, "y": 0.0, "z": 50.0},
            "center": {"x": 20.0, "y": 20.0, "z": 20.0},
            "up": {"x": 0.0, "y": 0.0, "z": 1.0},
            "projection": {"type": "orthographic"},
        }
        backend.patch_state(
            {
                "camera": missed,
                "display_mode": "unit_cell",
                "projection": "orthographic",
            },
            scene_id=scene_id,
            broadcast=False,
        )
        assert not _shows_atoms(backend, scene_id)
        state = backend.refit_scene_camera(scene_id)
        assert state["projection"] == "orthographic"
        assert state["camera"]["projection"]["type"] == "orthographic"
        assert state["camera"]["center"]["y"] != missed["center"]["y"]
        assert _shows_atoms(backend, scene_id)
    finally:
        backend.close()


def test_reset_recovers_an_offscreen_camera(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state({"camera": OFFSCREEN}, scene_id=scene_id, broadcast=False)
        assert not _shows_atoms(backend, scene_id)
        backend.camera_action("reset", scene_id=scene_id, broadcast=False)
        assert _shows_atoms(backend, scene_id)
        assert backend.get_state(scene_id)["camera"]["center"] == {
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
        }
    finally:
        backend.close()
