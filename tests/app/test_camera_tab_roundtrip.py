"""A drag that does not bump camera_revision must still be the delivered pose."""

from __future__ import annotations

import json
from pathlib import Path

from mat_viewer.app import ViewerBackend


NACL = Path("benchmarks/structures/ionic_nacl.cif")
FRAMED = {
    "eye": {"x": 1.3, "y": 1.2, "z": 1.1},
    "center": {"x": 0.02, "y": -0.01, "z": 0.0},
    "up": {"x": 0.0, "y": 0.0, "z": 1.0},
    "projection": {"type": "perspective"},
}
OFFSCREEN = {
    "eye": {"x": 0.04, "y": 0.02, "z": 0.01},
    "center": {"x": 0.0, "y": 1.6, "z": 0.0},
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


def _eye(figure: dict) -> dict:
    return figure["layout"]["scene"]["camera"]["eye"]


def test_published_figure_is_not_rewritten_by_a_later_orbit(tmp_path: Path):
    """The journal returns the camera that was broadcast.

    An orbit stores a new eye without a new revision. That must not
    replace the published snapshot. The browser paints the later eye
    from ``camera-state-store``.
    """
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        state = backend.get_state(scene_id)
        published = {
            "data": [{"type": "scatter3d", "x": [0], "y": [0], "z": [0]}],
            "layout": {"scene": {"camera": {"eye": {"x": 1, "y": 1, "z": 1}}}},
        }
        backend.broadcast_figure(scene_id=scene_id, figure=published, state=state)
        revision = int(state.get("camera_revision", 0) or 0)
        backend.patch_state({"camera": FRAMED}, scene_id=scene_id, broadcast=False)
        stored = backend.get_state(scene_id)
        assert int(stored["camera_revision"]) == revision
        assert stored["camera"]["eye"]["x"] == FRAMED["eye"]["x"]
        delivered = backend.latest_figure_broadcast(scene_id)
        assert delivered is not None
        assert _eye(delivered["figure"])["x"] == 1
        assert delivered["figure"] == published
    finally:
        backend.close()


def test_delivered_figure_follows_a_refit_of_an_offscreen_camera(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        state = backend.get_state(scene_id)
        figure, topology = backend.figure_for_state(state)
        backend.broadcast_figure(
            scene_id=scene_id,
            figure=figure.to_plotly_json(),
            topology_data=topology,
            state=state,
        )
        backend.patch_state({"camera": OFFSCREEN}, scene_id=scene_id, broadcast=False)
        refit = backend.refit_scene_camera(scene_id)
        assert refit["camera"]["center"]["y"] != OFFSCREEN["center"]["y"]
        # The refit advances camera_revision, so the pre-refit journal
        # frame is no longer current and must not be painted.
        assert backend.latest_figure_broadcast(scene_id) is None
        figure, topology = backend.figure_for_state(refit)
        painted = figure.to_plotly_json()["layout"]["scene"]["camera"]
        assert painted["center"]["y"] != OFFSCREEN["center"]["y"]
        assert painted["center"]["y"] == refit["camera"]["center"]["y"]
        assert painted["eye"]["x"] == refit["camera"]["eye"]["x"]
        backend.broadcast_figure(
            scene_id=scene_id,
            figure=figure.to_plotly_json(),
            topology_data=topology,
            state=refit,
        )
        delivered = backend.latest_figure_broadcast(scene_id)
        assert delivered is not None
        assert _eye(delivered["figure"])["x"] == refit["camera"]["eye"]["x"]
    finally:
        backend.close()
