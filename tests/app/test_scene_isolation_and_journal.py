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


def test_selection_commit_enqueues_render_without_the_backend_lock(tmp_path):
    """A click commit must not hold the backend lock while enqueueing a frame.

    The render worker publishes by taking its own lock and then reading
    backend state. Holding both in the opposite order deadlocks shutdown.
    """

    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        revision = backend.get_state(scene_id).get("target_revision", 0)
        worker = backend._render_worker
        entered = threading.Event()
        finished_read = threading.Event()
        errors: list[object] = []
        arm = {"on": False}

        def request(state, force=False):
            if not arm["on"]:
                return True
            depth = int(getattr(backend._backend_lock_tls, "depth", 0) or 0)
            if depth != 0:
                errors.append(f"backend lock depth {depth}")
            entered.set()
            if not finished_read.wait(timeout=3):
                errors.append("state read did not finish")
            return True

        worker.request_figure_build = request
        assert backend._commit_selection_event(
            {
                "targets": [{"kind": "atom", "id": "atom:a"}],
                "mode": "replace",
                "target_revision": revision,
                "ts": 10,
            },
            scene_id,
        )

        def reader():
            try:
                if not entered.wait(timeout=3):
                    errors.append("render enqueue did not start")
                    return
                if not worker._lock.acquire(timeout=2):
                    errors.append("worker lock stayed busy")
                    return
                try:
                    done = threading.Event()

                    def read():
                        try:
                            backend.get_state(scene_id)
                        except Exception as exc:  # noqa: BLE001
                            errors.append(exc)
                        finally:
                            done.set()

                    threading.Thread(target=read).start()
                    if not done.wait(timeout=2):
                        errors.append("get_state blocked while the worker lock was held")
                finally:
                    worker._lock.release()
            finally:
                finished_read.set()

        arm["on"] = True
        thread = threading.Thread(target=reader)
        thread.start()
        try:
            assert backend._commit_selection_event(
                {"mode": "clear", "targets": [], "target_revision": revision, "ts": 20},
                scene_id,
            )
        finally:
            finished_read.set()
            thread.join(timeout=3)
        assert not thread.is_alive()
        assert errors == []
        assert backend.get_selection(scene_id)["targets"] == []
    finally:
        backend.close()


def test_render_publish_does_not_hold_the_worker_lock(tmp_path, monkeypatch):
    backend = _backend(tmp_path)
    try:
        state = backend.get_state()
        worker = backend._render_worker
        observed: dict[str, bool] = {}

        class _Figure:
            def to_plotly_json(self):
                return {
                    "data": [{"type": "scatter3d", "x": [0], "y": [0], "z": [0]}],
                    "layout": {"scene": {}},
                }

        def figure_for_state(*_args, **_kwargs):
            return _Figure(), None

        def broadcast_figure(**_kwargs):
            def other():
                got = worker._lock.acquire(blocking=False)
                if got:
                    worker._lock.release()
                observed["free"] = got

            probe = threading.Thread(target=other)
            probe.start()
            probe.join(timeout=2)
            return {"type": "figure"}

        monkeypatch.setattr(backend, "figure_for_state", figure_for_state)
        monkeypatch.setattr(backend, "broadcast_figure", broadcast_figure)
        assert worker.request_figure_build(state)
        deadline = threading.Event()
        for _ in range(100):
            if "free" in observed:
                break
            deadline.wait(0.02)
        assert observed.get("free") is True
    finally:
        backend.close()
