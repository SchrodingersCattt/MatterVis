"""Cross-scene patches and the figure journal must not deadlock or leak."""

from __future__ import annotations

import threading

import pytest

from mat_viewer.app import ViewerBackend


def _backend(tmp_path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    return backend


def test_patch_of_inactive_scene_does_not_replace_the_active_state(tmp_path):
    backend = _backend(tmp_path)
    try:
        active = backend.active_scene_id()
        other = backend.create_scene(structure=backend.get_state(active)["structure"], label="Other")["id"]
        backend.set_active_scene(active)
        active_before = backend.get_state(active)
        backend.patch_state({"display_mode": "unit_cell", "atom_scale": 1.7}, scene_id=other)
        assert backend.current_state["scene_id"] == active
        assert backend.get_state(active)["display_mode"] == active_before["display_mode"]
        assert backend.get_state(active)["atom_scale"] == active_before["atom_scale"]
        assert backend.get_state(other)["display_mode"] == "unit_cell"
        assert backend.get_state(other)["atom_scale"] == pytest.approx(1.7)
        pending = backend.pending_state
        assert pending is None or pending.get("scene_id") == active
    finally:
        backend.close()


def test_reset_keeps_the_active_projection(tmp_path):
    backend = _backend(tmp_path)
    try:
        backend.set_projection("orthographic", broadcast=False)
        canonical = backend.default_camera()
        zoomed = dict(backend.get_camera())
        zoomed["eye"] = {axis: float(zoomed["eye"][axis]) * 0.25 for axis in ("x", "y", "z")}
        backend.set_camera(zoomed, broadcast=False)
        reset = backend.camera_action("reset", broadcast=False)
        assert reset["projection"]["type"] == "orthographic"
        assert backend.get_state()["projection"] == "orthographic"
        for axis in ("x", "y", "z"):
            assert reset["eye"][axis] == pytest.approx(canonical["eye"][axis])
    finally:
        backend.close()


def test_journal_read_does_not_deadlock_with_state_patch(tmp_path):
    backend = _backend(tmp_path)
    try:
        state = backend.get_state()
        figure = {
            "data": [{"type": "scatter3d", "x": [0], "y": [0], "z": [0]}],
            "layout": {"scene": {"camera": state.get("camera") or {}}},
        }
        backend.broadcast_figure(scene_id=state["scene_id"], figure=figure, state=state)
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def writer():
            try:
                barrier.wait(timeout=5)
                for index in range(1000):
                    eye = 1.0 + (index % 5) * 0.01
                    backend.patch_state(
                        {"camera": {"eye": {"x": eye, "y": 1.0, "z": 1.0}, "center": {"x": 0, "y": 0, "z": 0}, "up": {"x": 0, "y": 0, "z": 1}}},
                        broadcast=True,
                    )
            except BaseException as exc:  # noqa: BLE001 - the assertion reports it
                errors.append(exc)

        def reader():
            try:
                barrier.wait(timeout=5)
                for _ in range(1000):
                    backend.figure_broadcasts_since(0)
                    backend.latest_figure_broadcast()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for thread in threads:
            thread.start()
        # A loaded CI worker shares the machine with the rest of the suite.
        # Thirty seconds is enough for the thousand-iteration race and still
        # fails if the two locks are taken in opposite orders.
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive(), "state patch and journal read deadlocked"
        assert errors == []
    finally:
        backend.close()
