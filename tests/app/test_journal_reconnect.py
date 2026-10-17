"""A reconnect that missed more than the journal cap reconciles to the latest frame."""

from __future__ import annotations

from mat_viewer.app.factory import create_app


def test_journal_gap_over_32_events_keeps_only_the_latest_window():
    app = create_app()
    backend = app.crystal_backend
    try:
        scene_id = backend.active_scene_id()
        state = backend.get_state(scene_id)
        figure = {
            "data": [{"type": "scatter3d", "x": [0.0], "y": [0.0], "z": [0.0]}],
            "layout": {"scene": {"camera": state.get("camera") or {}}},
        }
        for index in range(40):
            backend.broadcast_figure(
                scene_id=scene_id,
                figure=figure,
                state=backend.get_state(scene_id),
                reason=f"frame-{index}",
            )
        replay = backend.figure_broadcasts_since(1)
        assert replay
        assert len(replay) <= 32
        assert int(replay[0]["figure_seq"]) > 2
        latest = backend.latest_figure_broadcast(scene_id)
        assert latest is not None
        assert int(replay[-1]["figure_seq"]) == int(latest["figure_seq"])
        assert replay[-1]["scene_id"] == scene_id
    finally:
        backend.close()
