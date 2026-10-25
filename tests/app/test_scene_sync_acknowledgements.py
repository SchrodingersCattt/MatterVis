"""Browser acknowledgements must outlive a consumed server snapshot.

Each test invokes the registered callbacks against a real backend. Keeping
the old browser stamp models Dash discarding a response after the server
has already committed its work or consumed the pending broadcast.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from dash import no_update

from mat_viewer.app import ViewerBackend, callbacks_state


class _CallbackRegistry:
    def __init__(self):
        self.callbacks = {}

    def callback(self, *args, **kwargs):
        def register(fn):
            self.callbacks[fn.__name__] = fn
            return fn

        return register


def _trigger(monkeypatch, prop_id):
    monkeypatch.setattr(
        callbacks_state,
        "callback_context",
        SimpleNamespace(triggered=[{"prop_id": prop_id}]),
    )


def _controls(response):
    # Editable values in the Dash callback contract; skip dropdown options.
    return list((*response[1:13], *response[14:17], *response[18:25]))


def _apply(browser, response):
    stamp, label, controls = browser
    return (
        stamp if response[-4] is no_update else response[-4],
        label if response[0] is no_update else response[0],
        [
            old if new is no_update else new
            for old, new in zip(controls, _controls(response))
        ],
    )


def _poll(sync, scene_id, browser, monkeypatch):
    stamp, label, controls = browser
    _trigger(monkeypatch, "agent-state-poll.n_intervals")
    return sync(1, None, None, scene_id, stamp, label, *controls)


def _assert_settles(sync, scene_id, browser, monkeypatch):
    # A snapshot queued by Restore Defaults may need one last drain after
    # the browser acknowledges the replay. It must then stop writing.
    for _ in range(3):
        response = _poll(sync, scene_id, browser, monkeypatch)
        if all(value is no_update for value in response):
            return
        browser = _apply(browser, response)
    pytest.fail("acknowledged controls kept being republished")


@pytest.fixture
def scene_sync(tmp_path, monkeypatch):
    backend = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )
    try:
        scene_id = backend.active_scene_id()
        backend.pop_pending_state()
        backend.patch_state({"axis_scale": 0.3}, broadcast=False)
        registry = _CallbackRegistry()
        callbacks_state.register_state_callbacks(registry, backend)
        sync = registry.callbacks["sync_agent_state"]
        _trigger(monkeypatch, "scene-tabs.value")
        initial = sync(None, None, None, scene_id)
        browser = initial[-4], initial[0], _controls(initial)
        yield backend, scene_id, registry.callbacks, browser
    finally:
        backend.close()


@pytest.mark.parametrize(
    "discarded_trigger",
    [
        "scene-tabs.value",
        "agent-state-poll.n_intervals",
        "display-defaults-btn.n_clicks",
    ],
)
def test_discarded_same_scene_response_replays_until_browser_acknowledges(
    scene_sync, monkeypatch, discarded_trigger
):
    backend, scene_id, callbacks, browser = scene_sync
    sync = callbacks["sync_agent_state"]
    backend.patch_state({"axis_scale": 0.2}, broadcast=True)
    _trigger(monkeypatch, discarded_trigger)
    stamp, label, controls = browser
    discarded = sync(1, None, 1, scene_id, stamp, label, *controls)
    expected_scale = backend.get_state(scene_id)["axis_scale"]
    assert discarded[10] == expected_scale
    assert expected_scale != controls[9]

    # Retrying must keep working even after the first response has drained
    # pending_state. Neither response has reached the browser yet.
    for _ in range(2):
        repaired = _poll(sync, scene_id, browser, monkeypatch)
        assert repaired[10] == expected_scale
        assert repaired[-4] != stamp
        assert repaired[-2] is no_update
    _assert_settles(sync, scene_id, _apply(browser, repaired), monkeypatch)


def test_same_scene_server_rename_recovers_a_discarded_response(
    scene_sync, monkeypatch
):
    backend, scene_id, callbacks, browser = scene_sync
    sync = callbacks["sync_agent_state"]
    backend.update_scene(scene_id, {"label": "Renamed on server"})

    for _ in range(2):
        discarded = _poll(sync, scene_id, browser, monkeypatch)
        assert discarded[0] == "Renamed on server"
        assert discarded[-3]["scene_label"] == "Renamed on server"
        assert discarded[-4] != browser[0]
    _assert_settles(sync, scene_id, _apply(browser, discarded), monkeypatch)


def test_unchanged_server_label_keeps_the_browser_rename_draft(
    scene_sync, monkeypatch
):
    backend, scene_id, callbacks, browser = scene_sync
    sync = callbacks["sync_agent_state"]
    stamp, label, controls = browser
    browser = stamp, label + " draft", controls
    backend.patch_state({"axis_scale": 0.2}, broadcast=True)
    response = _poll(sync, scene_id, browser, monkeypatch)
    assert response[0] is no_update
    assert response[10] == 0.2

    browser = _apply(browser, response)
    controls = list(browser[2])
    controls[9] = 0.25
    browser = browser[0], browser[1], controls
    backend.patch_state({"minor_opacity": 0.61}, broadcast=True)
    response = _poll(sync, scene_id, browser, monkeypatch)
    assert response[0] is no_update
    assert response[10] is no_update
    assert response[5] == 0.61


@pytest.mark.parametrize(
    ("draft_index", "draft", "server_patch", "server_output", "server_value"),
    [
        (0, "cluster", {"axis_scale": 0.2}, 10, 0.2),
        (9, 0.25, {"display_mode": "unit_cell"}, 1, "unit_cell"),
    ],
)
def test_unrelated_server_edit_keeps_an_inflight_control(
    scene_sync, monkeypatch, draft_index, draft, server_patch,
    server_output, server_value,
):
    backend, scene_id, callbacks, browser = scene_sync
    stamp, label, controls = browser
    controls = list(controls)
    controls[draft_index] = draft
    browser = stamp, label, controls
    backend.patch_state(server_patch, broadcast=True)
    response = _poll(callbacks["sync_agent_state"], scene_id, browser, monkeypatch)
    # These two drafts use values before the skipped dropdown-option slots.
    assert response[draft_index + 1] is no_update
    assert response[server_output] == server_value


def test_capture_acknowledges_its_control_values_before_the_next_edit(
    scene_sync, monkeypatch
):
    backend, scene_id, callbacks, browser = scene_sync
    stamp, label, controls = browser
    controls = list(controls)
    controls[9] = 0.2
    _trigger(monkeypatch, "axis-scale-slider.value")
    captured = callbacks["capture_state"](scene_id, *controls, stamp)
    assert captured[0]["axis_scale"] == 0.2
    assert isinstance(captured[2], str)

    # The delivered capture response acknowledges .2. A second .25 edit
    # has not committed when an unrelated server opacity update arrives.
    controls[9] = 0.25
    browser = captured[2], label, controls
    backend.patch_state({"minor_opacity": 0.61}, broadcast=True)
    response = _poll(callbacks["sync_agent_state"], scene_id, browser, monkeypatch)
    assert response[10] is no_update
    assert response[5] == 0.61


def test_capture_does_not_acknowledge_an_undelivered_server_label(
    scene_sync, monkeypatch
):
    backend, scene_id, callbacks, browser = scene_sync
    stamp, label, controls = browser
    backend.update_scene(scene_id, {"label": "Renamed before capture"})
    controls = list(controls)
    controls[9] = 0.2
    _trigger(monkeypatch, "axis-scale-slider.value")
    captured = callbacks["capture_state"](scene_id, *controls, stamp)
    assert captured[0]["scene_label"] == "Renamed before capture"

    # Capture only writes the controls acknowledgement and state store.
    # The rename input still shows its old label until sync delivers it.
    browser = captured[2], label, controls
    response = _poll(callbacks["sync_agent_state"], scene_id, browser, monkeypatch)
    assert response[0] == "Renamed before capture"


def test_newer_camera_command_replays_without_writing_the_camera_store(
    scene_sync, monkeypatch
):
    backend, scene_id, callbacks, browser = scene_sync
    old_revision = backend.get_state(scene_id)["camera_revision"]
    backend.camera_action("reset", scene_id=scene_id, broadcast=False)
    assert backend.get_state(scene_id)["camera_revision"] > old_revision
    for _ in range(2):
        response = _poll(callbacks["sync_agent_state"], scene_id, browser, monkeypatch)
        assert response[-3]["camera_revision"] > old_revision
        assert response[-4] != browser[0]
        assert response[-2] is no_update
    _assert_settles(
        callbacks["sync_agent_state"], scene_id, _apply(browser, response), monkeypatch
    )


def test_capture_does_not_acknowledge_an_undelivered_camera_command(
    scene_sync, monkeypatch
):
    backend, scene_id, callbacks, browser = scene_sync
    stamp, label, controls = browser
    old_revision = backend.get_state(scene_id)["camera_revision"]
    backend.camera_action("reset", scene_id=scene_id, broadcast=False)
    controls = list(controls)
    controls[9] = 0.2
    _trigger(monkeypatch, "axis-scale-slider.value")
    captured = callbacks["capture_state"](scene_id, *controls, stamp)
    assert captured[0]["camera_revision"] > old_revision

    # Persisting a sidebar edit did not deliver the outstanding camera
    # command. Poll must still repair that control transaction.
    browser = captured[2], label, controls
    response = _poll(callbacks["sync_agent_state"], scene_id, browser, monkeypatch)
    assert response[-3]["camera_revision"] > old_revision
    assert response[-4] != captured[2]
    assert response[-2] is no_update


def test_capture_does_not_acknowledge_a_server_edit_after_its_commit(
    scene_sync, monkeypatch
):
    backend, scene_id, callbacks, browser = scene_sync
    stamp, label, controls = browser
    controls = list(controls)
    controls[9] = 0.2
    apply_intent = backend.apply_intent

    def commit_then_server_edit(payload):
        result = apply_intent(payload)
        backend.patch_state({"axis_scale": 0.24}, broadcast=True)
        return result

    monkeypatch.setattr(backend, "apply_intent", commit_then_server_edit)
    _trigger(monkeypatch, "axis-scale-slider.value")
    captured = callbacks["capture_state"](scene_id, *controls, stamp)
    assert backend.get_state(scene_id)["axis_scale"] == 0.24

    # The browser's .2 capture response did not apply the later .24 server
    # edit to its slider. That later value still needs a sync response.
    browser = captured[2], label, controls
    for _ in range(2):
        response = _poll(callbacks["sync_agent_state"], scene_id, browser, monkeypatch)
        assert response[10] == 0.24
