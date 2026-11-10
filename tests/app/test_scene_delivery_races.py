"""Scene-scoped publication races with a held worker and no wall-clock sleeps.

The pool double queues figure jobs until ``run_next``. A regression here is a
job that never appears, a stale revision that becomes the live frame, or one
scene's callback writing the other scene.
"""
from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from dash import no_update

from mat_viewer.app import ViewerBackend, callbacks_state, callbacks_view, render_worker
from mat_viewer.app.scene_controls import (
    _POLL_CLIENT_CONTROL_INDEX,
    _scene_control_values,
)


class ControlledPool:
    def __init__(self, **kwargs):
        self.jobs = []

    def submit(self, fn, *args):
        future = Future()
        self.jobs.append((future, fn, args))
        return future

    def run_next(self):
        future, fn, args = self.jobs.pop(0)
        if future.set_running_or_notify_cancel():
            try:
                result = fn(*args)
            except Exception as exc:  # noqa: BLE001 - the pool records every job failure
                future.set_exception(exc)
            else:
                future.set_result(result)
        return future

    def shutdown(self, **kwargs):
        for future, _, _ in self.jobs:
            future.cancel()


class CallbackRegistry:
    def __init__(self):
        self.callbacks = {}

    def callback(self, *args, **kwargs):
        def register(fn):
            self.callbacks[fn.__name__] = fn
            return fn

        return register


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setattr(render_worker, "_TOPOLOGY_WORKER_MODE", "thread")
    monkeypatch.setattr(render_worker, "ThreadPoolExecutor", ControlledPool)
    viewer = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    yield viewer
    viewer.close()


def _figure(backend, state):
    payload = {
        "data": [{"type": "scatter3d", "x": [0], "y": [0], "z": [0]}],
        "layout": {"scene": {"camera": {"eye": {"x": 1, "y": 1, "z": 1}}}},
    }
    backend._stamp_figure_render_metadata(payload, state)
    return SimpleNamespace(to_plotly_json=lambda: payload), None


def _queued_scenes(worker):
    return [
        record.get("scene_id")
        for record in worker.task_status().values()
        if record.get("status") == "queued"
    ]


def test_duplicate_scene_schedules_a_complete_frame(backend):
    original = backend.active_scene_id()
    duplicate = backend.duplicate_scene(original)
    assert duplicate["id"] != original
    assert duplicate["id"] in _queued_scenes(backend._render_worker)


def test_superseded_build_requeues_the_live_revision_and_drops_the_stale_one(backend, monkeypatch):
    scene_id = backend.active_scene_id()
    monkeypatch.setattr(backend, "figure_for_state", lambda snapshot, **kwargs: _figure(backend, snapshot))
    state = backend.get_state(scene_id)
    assert backend._render_worker.request_figure_build(state)
    # A complete-frame edit that never reached the scheduler. Display and
    # overlay edits bump the same clock and must not be requeued here.
    backend.scene_store.patch_scene(scene_id, {"atom_scale": 1.45}, save=False)
    live_revision = backend.render_revision(scene_id) + 1
    backend._render_revisions[str(scene_id)] = live_revision

    ignored = backend.broadcast_figure(
        scene_id=scene_id,
        figure={
            "data": [{"type": "scatter3d", "x": [0], "y": [0], "z": [0]}],
            "layout": {"scene": {}},
        },
        state=state,
        reason="stale-dash",
    )
    assert ignored["type"] == "figure_ignored"
    assert str(ignored["reason"]).startswith("stale")
    assert backend.latest_figure_broadcast(scene_id) is None

    backend._render_worker._finalize_pool.run_next()
    assert scene_id in _queued_scenes(backend._render_worker)

    backend._render_worker._finalize_pool.run_next()
    event = backend.latest_figure_broadcast(scene_id)
    assert event is not None
    assert event["type"] == "figure"
    assert event["scene_id"] == scene_id
    assert event["render_revision"] == live_revision


def test_older_publication_identity_is_not_requeued(backend, monkeypatch):
    scene_id = backend.active_scene_id()
    monkeypatch.setattr(backend, "figure_for_state", lambda snapshot, **kwargs: _figure(backend, snapshot))
    backend._render_revisions[str(scene_id)] = 2
    state = backend.get_state(scene_id)
    assert state["render_revision"] == 2
    assert backend._render_worker.request_figure_build(state)
    backend._render_revisions[str(scene_id)] = 1

    backend._render_worker._finalize_pool.run_next()
    assert scene_id not in _queued_scenes(backend._render_worker)
    assert backend.latest_figure_broadcast(scene_id) is None


def test_camera_advance_during_an_inflight_build_still_publishes(backend, monkeypatch):
    scene_id = backend.active_scene_id()
    monkeypatch.setattr(backend, "figure_for_state", lambda snapshot, **kwargs: _figure(backend, snapshot))
    state = backend.get_state(scene_id)
    assert backend._render_worker.request_figure_build(state)
    queued = len(backend._render_worker._finalize_pool.jobs)
    moved = backend.patch_state(
        {
            "camera": {
                "eye": {"x": 2.0, "y": 0.2, "z": 0.2},
                "center": {"x": 0, "y": 0, "z": 0},
                "up": {"x": 0, "y": 0, "z": 1},
            },
            "camera_revision": int(state.get("camera_revision", 0) or 0) + 1,
        },
        scene_id=scene_id,
        broadcast=False,
    )
    assert moved["camera_revision"] > state["camera_revision"]
    assert len(backend._render_worker._finalize_pool.jobs) == queued

    backend._render_worker._finalize_pool.run_next()
    assert scene_id in _queued_scenes(backend._render_worker)
    backend._render_worker._finalize_pool.run_next()
    event = backend.latest_figure_broadcast(scene_id)
    assert event["render_revision"] == moved["render_revision"]
    assert event["state"]["camera_revision"] == moved["camera_revision"]


def test_duplicate_scenes_do_not_share_a_plotly_ui_revision(backend):
    original = backend.active_scene_id()
    duplicate = backend.duplicate_scene(original)["id"]
    style_a = backend.style_for_state(backend.get_state(original))
    style_b = backend.style_for_state(backend.get_state(duplicate))
    assert str(original) in style_a["uirevision"]
    assert str(duplicate) in style_b["uirevision"]
    assert style_a["uirevision"] != style_b["uirevision"]


def _sidebar(state):
    values = _scene_control_values(state)
    return [values[index] for index in _POLL_CLIENT_CONTROL_INDEX]


def _capture(capture, monkeypatch, backend, scene_id, client_id, axis_scale):
    monkeypatch.setattr(
        callbacks_state,
        "callback_context",
        SimpleNamespace(triggered=[{"prop_id": "axis-scale-slider.value"}]),
    )
    state = backend.get_state(scene_id)
    return capture(
        scene_id,
        state["display_mode"],
        state["display_options"],
        state["atom_scale"],
        state["bond_radius"],
        state["minor_opacity"],
        state["material"],
        state["style"],
        state["disorder"],
        state["ortep_mode"],
        axis_scale,
        state["topology_site_index"],
        ["enabled"] if state["topology_enabled"] else [],
        [],
        "auto",
        None,
        "viridis",
        "auto",
        None,
        None,
        None,
        "#BDBDBD",
        ["show"],
        None,
        1,
        client_id,
    )


def test_each_browser_keeps_its_own_filled_scene(backend, monkeypatch):
    scene_a = backend.active_scene_id()
    scene_b = backend.create_scene(structure=backend.get_state()["structure"], label="Second")["id"]
    scale_a = backend.get_state(scene_a)["axis_scale"]
    scale_b = backend.get_state(scene_b)["axis_scale"]
    registry = CallbackRegistry()
    callbacks_state.register_state_callbacks(registry, backend)
    monkeypatch.setattr(
        callbacks_state,
        "callback_context",
        SimpleNamespace(triggered=[{"prop_id": "scene-tabs.value"}]),
    )
    sync = registry.callbacks["sync_agent_state"]
    capture = registry.callbacks["capture_state"]
    sidebar_a = _sidebar(backend.get_state(scene_a))
    sidebar_b = _sidebar(backend.get_state(scene_b))
    sync(None, None, None, scene_a, None, None, *sidebar_a, 1, "delivery-browser-a")
    sync(None, None, None, scene_b, None, None, *sidebar_b, 1, "delivery-browser-b")

    rejected = _capture(
        capture, monkeypatch, backend, scene_a, "delivery-browser-b", scale_a + 0.25
    )
    assert rejected == (no_update, no_update, no_update)
    assert backend.get_state(scene_a)["axis_scale"] == scale_a
    assert backend.get_state(scene_b)["axis_scale"] == scale_b

    applied = _capture(
        capture, monkeypatch, backend, scene_a, "delivery-browser-a", scale_a + 0.25
    )
    assert applied[0] is not no_update
    assert backend.get_state(scene_a)["axis_scale"] == scale_a + 0.25
    assert backend.get_state(scene_b)["axis_scale"] == scale_b


def test_delayed_view_callback_classifies_against_its_own_scene(backend, monkeypatch):
    scene_a = backend.active_scene_id()
    scene_b = backend.create_scene(structure=backend.get_state()["structure"], label="Second")["id"]
    registry = CallbackRegistry()
    callbacks_view.register_view_callbacks(registry, backend)
    update = registry.callbacks["update_view"]
    update({"scene_id": scene_a}, None, None)
    update({"scene_id": scene_b}, None, None)
    requested = []

    def spy(state, force=False):
        requested.append(str(state.get("scene_id") or ""))
        return False

    monkeypatch.setattr(backend._render_worker, "request_figure_build", spy)
    camera_b = backend.get_state(scene_b)["camera"]
    backend.patch_state(
        {
            "camera": {
                "eye": {"x": 0.2, "y": 0.2, "z": 2.0},
                "center": {"x": 0, "y": 0, "z": 0},
                "up": {"x": 0, "y": 0, "z": 1},
            }
        },
        scene_id=scene_a,
        broadcast=False,
    )
    update({"scene_id": scene_a}, None, None)

    assert requested == []
    assert backend.get_state(scene_b)["camera"] == camera_b


def test_first_look_at_a_scene_still_requests_its_frame(backend, monkeypatch):
    scene_a = backend.active_scene_id()
    scene_b = backend.create_scene(structure=backend.get_state()["structure"], label="Second")["id"]
    registry = CallbackRegistry()
    callbacks_view.register_view_callbacks(registry, backend)
    update = registry.callbacks["update_view"]
    update({"scene_id": scene_a}, None, None)
    backend.patch_state(
        {
            "camera": {
                "eye": {"x": 0.4, "y": 0.4, "z": 1.6},
                "center": {"x": 0, "y": 0, "z": 0},
                "up": {"x": 0, "y": 0, "z": 1},
            }
        },
        scene_id=scene_b,
        broadcast=False,
    )
    assert backend._last_update_kind_by_scene[scene_b] == "camera"
    requested = []

    def spy(state, force=False):
        requested.append(str(state.get("scene_id") or ""))
        return False

    monkeypatch.setattr(backend._render_worker, "request_figure_build", spy)
    update({"scene_id": scene_b}, None, None)
    assert requested == [scene_b]


def _mounted_figure(scene_id, *, title, projection="perspective", render_revision=0, camera_revision=0):
    return {
        "data": [{"type": "scatter3d", "x": [0], "y": [0], "z": [0]}],
        "layout": {
            "title": {"text": title},
            "scene": {
                "camera": {
                    "eye": {"x": 1, "y": 1, "z": 1},
                    "projection": {"type": projection},
                }
            },
            "meta": {
                "mattervis_render": {
                    "scene_id": scene_id,
                    "scene_generation": 0,
                    "render_revision": render_revision,
                    "camera_revision": camera_revision,
                }
            },
        },
    }


def _drain_figure_jobs(worker, limit=8):
    for _ in range(limit):
        if not worker._finalize_pool.jobs:
            return
        worker._finalize_pool.run_next()


def test_other_client_receives_mounted_scene_after_camera_change(backend, monkeypatch):
    """Client B must get S from the journal when B's figure is still T.

    The process-wide per-scene snapshot can already classify S as a camera
    change because client A visited it. That must not skip the frame for a
    caller whose mounted figure is a different scene.
    """
    scene_s = backend.active_scene_id()
    state_s = backend.get_state(scene_s)
    scene_t = backend.create_scene(
        structure=state_s["structure"], label="Client T"
    )["id"]
    def _state_figure(snapshot, **kwargs):
        payload = {
            "data": [{"type": "scatter3d", "x": [0], "y": [0], "z": [0]}],
            "layout": {
                "title": {"text": snapshot.get("scene_label") or snapshot.get("structure") or ""},
                "scene": {
                    "camera": {
                        "eye": {"x": 1.0, "y": 1.0, "z": 1.0},
                        "up": {"x": 0, "y": 0, "z": 1},
                        "center": {"x": 0, "y": 0, "z": 0},
                        "projection": {"type": snapshot.get("projection") or "perspective"},
                    },
                    "uirevision": f"{snapshot.get('scene_id')}__{snapshot.get('camera_revision')}",
                },
            },
        }
        backend._stamp_figure_render_metadata(payload, snapshot)
        return SimpleNamespace(to_plotly_json=lambda: payload), None

    monkeypatch.setattr(backend, "figure_for_state", _state_figure)
    registry = CallbackRegistry()
    callbacks_view.register_view_callbacks(registry, backend)
    update = registry.callbacks["update_view"]
    update._last_state_by_scene = {}
    update({"scene_id": scene_s}, _mounted_figure(scene_s, title=state_s.get("scene_label") or "S"), None)
    backend.set_projection("orthographic", scene_id=scene_s, broadcast=False)
    live = backend.get_state(scene_s)
    _drain_figure_jobs(backend._render_worker)
    event = backend.latest_figure_broadcast(scene_s)
    assert event is not None
    assert event["state"]["camera_revision"] == live["camera_revision"]
    assert event["render_revision"] == live["render_revision"]

    result = update(
        {"scene_id": scene_s},
        _mounted_figure(scene_t, title="Client T"),
        None,
    )
    figure = result[0]
    assert figure is not no_update
    render = figure["layout"]["meta"]["mattervis_render"]
    camera = figure["layout"]["scene"]["camera"]
    projection = camera.get("projection")
    projection_name = projection.get("type") if isinstance(projection, dict) else projection
    assert render["scene_id"] == scene_s
    assert render["render_revision"] == live["render_revision"]
    assert render["camera_revision"] == live["camera_revision"]
    assert projection_name == "orthographic"
    assert figure["layout"]["title"]["text"] == (live.get("scene_label") or live.get("structure"))


def test_mounted_caller_keeps_a_camera_change_on_the_local_path(backend, monkeypatch):
    scene_s = backend.active_scene_id()
    label = backend.get_state(scene_s).get("scene_label") or "S"
    registry = CallbackRegistry()
    callbacks_view.register_view_callbacks(registry, backend)
    update = registry.callbacks["update_view"]
    update._last_state_by_scene = {}
    update({"scene_id": scene_s}, _mounted_figure(scene_s, title=label), None)
    backend.set_projection("orthographic", scene_id=scene_s, broadcast=False)
    requested = []

    def spy(state, force=False):
        requested.append(str(state.get("scene_id") or ""))
        return False

    monkeypatch.setattr(backend._render_worker, "request_figure_build", spy)
    result = update({"scene_id": scene_s}, _mounted_figure(scene_s, title=label), None)
    assert result == (no_update,) * 4
    assert requested == []


def test_reordered_projection_store_cannot_change_the_other_scene(backend, monkeypatch):
    scene_a = backend.active_scene_id()
    scene_b = backend.create_scene(structure=backend.get_state()["structure"], label="Second")["id"]
    backend.set_projection("orthographic", scene_id=scene_a, broadcast=False)
    backend.set_projection("perspective", scene_id=scene_b, broadcast=False)
    registry = CallbackRegistry()
    callbacks_view.register_view_callbacks(registry, backend)
    sync = registry.callbacks["sync_view_projection_from_state"]
    monkeypatch.setattr(
        callbacks_view,
        "callback_context",
        SimpleNamespace(
            triggered_id="agent-state-store",
            triggered=[{"prop_id": "agent-state-store.data"}],
        ),
    )

    current = sync({"scene_id": scene_b, "projection": "perspective"}, scene_b)
    assert current[3] == "perspective"
    late = sync({"scene_id": scene_a, "projection": "orthographic"}, scene_b)
    assert late[3] is no_update
    assert backend.get_state(scene_a)["projection"] == "orthographic"
    assert backend.get_state(scene_b)["projection"] == "perspective"
