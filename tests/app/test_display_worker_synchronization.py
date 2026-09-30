"""Deterministic callback/worker races; no browser, sleeps or executor threads."""
from concurrent.futures import Future
from copy import deepcopy
from types import SimpleNamespace

import pytest

from mat_viewer.app import ViewerBackend
from mat_viewer.app import callbacks_state, callbacks_view, render_worker


class ControlledPool:
    def __init__(self, **kwargs):
        self.jobs = []
        self.fail_submit = False

    def submit(self, fn, *args):
        if self.fail_submit:
            raise RuntimeError("submit failed")
        future = Future()
        self.jobs.append((future, fn, args))
        return future

    def run_next(self):
        future, fn, args = self.jobs.pop(0)
        if future.set_running_or_notify_cancel():
            try:
                result = fn(*args)
            except Exception as exc:
                future.set_exception(exc)
            else:
                future.set_result(result)
        return future

    def shutdown(self, **kwargs):
        for future, _, _ in self.jobs:
            future.cancel()


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setattr(render_worker, "_TOPOLOGY_WORKER_MODE", "thread")
    monkeypatch.setattr(render_worker, "ThreadPoolExecutor", ControlledPool)
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    yield backend
    backend.close()


class CallbackRegistry:
    def __init__(self):
        self.callbacks = {}

    def callback(self, *args, **kwargs):
        def register(fn):
            self.callbacks[fn.__name__] = fn
            return fn
        return register


def capture(backend, monkeypatch, options, *, trigger="display-options", **changes):
    registry = CallbackRegistry()
    callbacks_state.register_state_callbacks(registry, backend)
    assert "patch_fast_style_controls" not in registry.callbacks
    monkeypatch.setattr(callbacks_state, "callback_context", SimpleNamespace(
        triggered=[{"prop_id": f"{trigger}.value"}]
    ))
    state = backend.get_state()
    state.update(changes)
    queued_before = len(backend._render_worker._finalize_pool.jobs)
    emitted = registry.callbacks["capture_state"](
        state["scene_id"], state["display_mode"], options,
        state["atom_scale"], state["bond_radius"], state["minor_opacity"],
        state["material"], state["style"], state["disorder"], state["ortep_mode"],
        state["axis_scale"], state["topology_site_index"],
        ["enabled"] if state["topology_enabled"] else [],
        [], "auto", None, "viridis", "auto", None, None, None, "#BDBDBD", ["show"],
    )
    assert len(backend._render_worker._finalize_pool.jobs) == queued_before
    callbacks_view.register_view_callbacks(registry, backend)
    from dash import no_update
    assert registry.callbacks["update_view"](emitted, None, None, None) == (no_update,) * 4
    return emitted


def fake_figure(backend, state):
    payload = {
        "data": [{"type": "scatter3d", "x": [0], "y": [0], "z": [0]}],
        "layout": {"scene": {}},
    }
    backend._stamp_figure_render_metadata(payload, state)
    return SimpleNamespace(to_plotly_json=lambda: payload), None


@pytest.mark.parametrize("option", ["labels", "axes", "unit_cell_box", "hydrogens", "minor_only"])
def test_display_toggles_route_geometry_only_when_needed(backend, monkeypatch, option):
    options = set(backend.get_state()["display_options"])
    options.symmetric_difference_update({option})
    emitted = capture(backend, monkeypatch, sorted(options))
    worker = backend._render_worker
    assert emitted == backend.get_state()
    if option in {"labels", "axes"}:
        assert not worker._finalize_pool.jobs
        return
    assert len(worker._finalize_pool.jobs) == 1
    built = []

    def build(state, **kwargs):
        built.append(deepcopy(state))
        return fake_figure(backend, state)

    monkeypatch.setattr(backend, "figure_for_state", build)
    worker._finalize_pool.run_next()
    assert built == [emitted]
    event = backend.latest_figure_broadcast()
    assert event["state"] == emitted
    assert event["figure"]["layout"]["meta"]["mattervis_render"]["render_revision"] == emitted["render_revision"]
    assert not worker._pending_render


def test_composed_callback_builds_once_and_poll_delivers_without_websocket(backend, monkeypatch):
    from dash import no_update

    options = set(backend.get_state()["display_options"])
    options.symmetric_difference_update({"axes"})
    emitted = capture(backend, monkeypatch, sorted(options))
    registry = CallbackRegistry()
    callbacks_view.register_view_callbacks(registry, backend)
    update = registry.callbacks["update_view"]
    assert update(emitted, None, None, None) == (no_update,) * 4
    assert not backend._render_worker._finalize_pool.jobs


def test_flat_ortep_worker_frame_has_http_fallback(backend, monkeypatch):
    backend.patch_state({"material": "flat", "style": "ortep"}, broadcast=False)
    state = backend.get_state()
    figure = {"data": [], "layout": {"images": [{"source": "data:image/png;base64,AA=="}]}}
    backend._stamp_figure_render_metadata(figure, state)
    monkeypatch.setattr(backend, "figure_for_state", lambda *args, **kwargs: (
        SimpleNamespace(to_plotly_json=lambda: figure), None,
    ))
    registry = CallbackRegistry()
    callbacks_view.register_view_callbacks(registry, backend)
    update = registry.callbacks["update_view"]
    update(state, None, None, None)
    backend._render_worker._finalize_pool.run_next()
    assert update(state, None, None, None, 1)[0] == figure


def test_camera_reset_invalidates_inflight_worker_frame(backend, monkeypatch):
    state = backend.get_state()

    def build(snapshot, **kwargs):
        backend.camera_action("reset", broadcast=False)
        return fake_figure(backend, snapshot)

    monkeypatch.setattr(backend, "figure_for_state", build)
    backend._render_worker.request_figure_build(state)
    backend._render_worker._finalize_pool.run_next()
    assert backend.latest_figure_broadcast() is None


@pytest.mark.parametrize("trigger,changes", [
    ("axis-scale-slider", {"axis_scale": 1.7}),
    ("minor-opacity-slider", {"minor_opacity": 0.61}),
])
def test_visual_sliders_also_queue_full_frames(backend, monkeypatch, trigger, changes):
    emitted = capture(backend, monkeypatch, backend.get_state()["display_options"], trigger=trigger, **changes)
    assert emitted == backend.get_state()
    assert not backend._render_worker._finalize_pool.jobs


def test_hydrogen_build_followed_by_labels_does_not_drop_final_frame(backend, monkeypatch):
    options = set(backend.get_state()["display_options"])
    options.symmetric_difference_update({"hydrogens"})
    first = capture(backend, monkeypatch, sorted(options))
    built = []

    def build(state, **kwargs):
        built.append(deepcopy(state))
        if len(built) == 1:
            options.symmetric_difference_update({"labels"})
            capture(backend, monkeypatch, sorted(options))
        return fake_figure(backend, state)

    monkeypatch.setattr(backend, "figure_for_state", build)
    pool = backend._render_worker._finalize_pool
    pool.run_next()  # Later toggle arrives while the hydrogen build is running.
    assert backend.latest_figure_broadcast() is None
    assert not pool.jobs
    assert backend.latest_figure_broadcast() is None
    assert not backend._render_worker._pending_render


@pytest.mark.parametrize("method", ["request_figure_build", "prewarm"])
@pytest.mark.parametrize("submit_failure", [False, True])
def test_render_failure_reports_and_allows_resubmit(backend, monkeypatch, submit_failure, method):
    worker = backend._render_worker
    pool = worker._finalize_pool
    state = backend.get_state()

    def fail(*args, **kwargs):
        raise ValueError("build failed")

    monkeypatch.setattr(backend, "figure_for_state", fail)
    pool.fail_submit = submit_failure
    result = getattr(worker, method)(state)
    if method == "request_figure_build":
        assert result is not submit_failure
    if not submit_failure:
        pool.run_next()
    assert not worker._pending_render
    error = backend.figure_broadcasts_since(0)[-1]
    assert error["type"] == "render_error"
    assert error["scene_id"] == state["scene_id"]
    assert ("submit failed" if submit_failure else "build failed") in error["error"]

    pool.fail_submit = False
    monkeypatch.setattr(backend, "figure_for_state", lambda state, **kw: fake_figure(backend, state))
    assert worker.request_figure_build(state)
    pool.run_next()
    assert backend.latest_figure_broadcast()["type"] == "figure"
    assert not worker._pending_render


def test_cancelled_request_cleans_pending_and_can_retry(backend, monkeypatch):
    worker = backend._render_worker
    state = backend.get_state()
    worker.request_figure_build(state)
    assert worker._finalize_pool.jobs[0][0].cancel()
    assert not worker._pending_render
    monkeypatch.setattr(backend, "figure_for_state", lambda state, **kw: fake_figure(backend, state))
    worker.request_figure_build(state)
    worker._finalize_pool.run_next()  # Cancelled job cannot run.
    assert backend.latest_figure_broadcast() is None
    worker._finalize_pool.run_next()
    assert backend.latest_figure_broadcast()["type"] == "figure"


@pytest.mark.parametrize("action", ["close", "delete", "stale"])
@pytest.mark.parametrize("fail", [False, True])
def test_obsolete_running_job_cannot_publish_frame_or_error(backend, monkeypatch, action, fail):
    worker = backend._render_worker
    state = backend.get_state()

    def build(snapshot, **kwargs):
        if action == "close":
            worker.shutdown()
        elif action == "delete":
            backend.scene_store.scenes.pop(state["scene_id"])
        else:
            backend.patch_state({"axis_scale": 1.9}, broadcast=False)
        if fail:
            raise ValueError("obsolete failure")
        return fake_figure(backend, snapshot)

    monkeypatch.setattr(backend, "figure_for_state", build)
    worker.request_figure_build(state)
    worker._finalize_pool.run_next()
    assert backend.figure_broadcasts_since(0) == []
    assert not worker._pending_render
    if action == "close":
        assert worker.request_figure_build(state) is False


def test_request_snapshot_is_detached_from_callers_mutable_state(backend, monkeypatch):
    state = backend.get_state()
    expected = deepcopy(state)
    worker = backend._render_worker
    worker.request_figure_build(state)
    state["display_options"].append("not-a-real-option")
    built = []

    def build(snapshot, **kwargs):
        built.append(snapshot)
        return fake_figure(backend, snapshot)

    monkeypatch.setattr(backend, "figure_for_state", build)
    worker._finalize_pool.run_next()
    assert built == [expected]


@pytest.mark.parametrize("failure", ["submit", "finalize-submit", "compute", "cancel"])
def test_topology_failure_cleans_pending_and_reports_relevant_errors(backend, monkeypatch, failure):
    worker = backend._render_worker
    state = backend.get_state()
    context = {
        "cache_key": ("test",), "bundle": None, "scene": {},
        "effective_specs": [], "site_index": 0, "cutoff": 3.0,
        "structure": state["structure"],
    }

    def compute(payload):
        if failure == "compute":
            raise ValueError("topology failed")
        return None

    monkeypatch.setattr(render_worker, "compute_topology_geometry_payload", compute)
    if failure == "submit":
        worker._compute_pool.fail_submit = True
        worker._finalize_pool.fail_submit = True
    worker.request_topology(state, context)
    if failure == "cancel":
        worker._compute_pool.jobs[0][0].cancel()
    elif failure != "submit":
        worker._finalize_pool.fail_submit = failure == "finalize-submit"
        worker._compute_pool.run_next()
        if failure == "compute":
            worker._finalize_pool.run_next()
    assert not worker._pending
    events = backend.figure_broadcasts_since(0)
    if failure == "cancel":
        assert events == []
    else:
        assert events[-1]["type"] == "render_error"
        assert events[-1]["scene_id"] == state["scene_id"]
    worker._compute_pool.fail_submit = False
    worker._finalize_pool.fail_submit = False
    assert worker.request_topology(state, context)
