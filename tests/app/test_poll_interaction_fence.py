"""A state poll must not outrun a tab switch or an edit it did not see.

Sidebar drafts that are already in the poll request stay put (#229). The
cases here are the ones that request cannot see: the browser has since
selected another scene, or it has bumped its interaction generation. The
poll then has to leave controls, ``agent-state-store``, and the filled-scene
guard alone. A different browser's poll is unaffected.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from dash import no_update

from mat_viewer.app import ViewerBackend, callbacks_state
from mat_viewer.app.scene_controls import (
    _POLL_CLIENT_CONTROL_INDEX,
    _scene_control_values,
)


class _CallbackRegistry:
    def __init__(self):
        self.callbacks = {}

    def callback(self, *args, **kwargs):
        def register(fn):
            self.callbacks[fn.__name__] = fn
            return fn

        return register


def _sidebar(state):
    values = _scene_control_values(state)
    return [values[index] for index in _POLL_CLIENT_CONTROL_INDEX]


def _trigger(monkeypatch, prop_id):
    monkeypatch.setattr(
        callbacks_state,
        "callback_context",
        SimpleNamespace(triggered=[{"prop_id": prop_id}]),
    )


def _sync(tmp_path, monkeypatch):
    backend = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )
    registry = _CallbackRegistry()
    callbacks_state.register_state_callbacks(registry, backend)
    return backend, registry.callbacks["sync_agent_state"]


def test_poll_for_the_scene_just_left_does_not_publish(tmp_path, monkeypatch):
    backend, sync = _sync(tmp_path, monkeypatch)
    try:
        first = backend.active_scene_id()
        second = backend.create_scene(
            structure=backend.get_state()["structure"], label="Second scene"
        )["id"]
        backend.pop_pending_state()
        sidebar = _sidebar(backend.get_state(first))
        _trigger(monkeypatch, "scene-tabs.value")
        switched = sync(
            None, None, None, second, None, None, *sidebar, 1, "browser-a"
        )
        assert switched[-1]["scene_id"] == second
        assert backend.active_scene_id() == second

        # The page has left ``first``, but that scene is still the one an
        # external edit can broadcast. Pending is only queued for the active
        # scene, so point the server back without changing the browser note.
        backend.set_active_scene(first, broadcast=False)
        backend.patch_state({"axis_scale": 0.2}, scene_id=first, broadcast=True)
        assert backend.peek_pending_state()["scene_id"] == first
        _trigger(monkeypatch, "agent-state-poll.n_intervals")
        stale = sync(1, None, None, first, None, None, *sidebar, 1, "browser-a")
        assert all(value is no_update for value in stale)
        assert backend.peek_pending_state()["scene_id"] == first

        # The other page is still on the first scene. Its poll publishes.
        other = _sidebar(backend.get_state(first))
        published = sync(
            2, None, None, first, None, None, *other, 0, "browser-b"
        )
        assert published[10] == 0.2
        assert published[-3]["scene_id"] == first
        assert published[-2] is no_update
    finally:
        backend.close()


def test_older_edit_generation_does_not_rewrite_state(tmp_path, monkeypatch):
    backend, sync = _sync(tmp_path, monkeypatch)
    try:
        scene_id = backend.active_scene_id()
        backend.pop_pending_state()
        backend.patch_state({"axis_scale": 1.4}, broadcast=True)
        state = backend.get_state(scene_id)
        stamp = callbacks_state._scene_controls_stamp(state)
        sidebar = _sidebar(state)
        sidebar[0] = "cluster"
        registry_epoch = callbacks_state.register_state_callbacks
        # ``register_state_callbacks`` already ran; call the recorder directly.
        remember = None
        probe = _CallbackRegistry()
        registry_epoch(probe, backend)
        remember = probe.callbacks["remember_interaction_epoch"]
        assert remember(5, "browser-edit") is no_update

        _trigger(monkeypatch, "agent-state-poll.n_intervals")
        stale = sync(
            1, None, None, scene_id, stamp, None, *sidebar, 4, "browser-edit"
        )
        assert all(value is no_update for value in stale)
        assert backend.get_state(scene_id)["display_mode"] != "cluster"

        current = sync(
            2, None, None, scene_id, stamp, None, *sidebar, 5, "browser-edit"
        )
        assert current[1] is no_update
        assert current[10] == 1.4
        assert current[-3]["axis_scale"] == 1.4
        assert current[-3]["display_mode"] != "cluster"
        assert current[-2] is no_update
    finally:
        backend.close()


def test_capture_records_an_edit_ahead_of_an_older_poll(tmp_path, monkeypatch):
    backend, sync = _sync(tmp_path, monkeypatch)
    try:
        scene_id = backend.active_scene_id()
        backend.pop_pending_state()
        state = backend.get_state(scene_id)
        sidebar = _sidebar(state)
        stamp = callbacks_state._scene_controls_stamp(state)
        probe = _CallbackRegistry()
        callbacks_state.register_state_callbacks(probe, backend)
        _trigger(monkeypatch, "axis-scale-slider.value")
        probe.callbacks["capture_state"](
            scene_id,
            *sidebar,
            stamp,
            interaction_epoch=3,
            client_id="browser-capture",
        )
        _trigger(monkeypatch, "agent-state-poll.n_intervals")
        stale = sync(
            1, None, None, scene_id, stamp, None, *sidebar, 2, "browser-capture"
        )
        assert all(value is no_update for value in stale)
    finally:
        backend.close()


def test_stale_poll_restores_pending_it_already_took(tmp_path, monkeypatch):
    backend, sync = _sync(tmp_path, monkeypatch)
    try:
        scene_id = backend.active_scene_id()
        backend.pop_pending_state()
        backend.patch_state({"axis_scale": 0.7}, broadcast=True)
        sidebar = _sidebar(backend.get_state(scene_id))
        real_claim = callbacks_state.claim_poll_publish

        def claim_after_a_newer_edit(client_id, scene_id, epoch, filled, *, supplied):
            callbacks_state.note_interaction_epoch(client_id, 9)
            return real_claim(
                client_id, scene_id, epoch, filled, supplied=supplied
            )

        monkeypatch.setattr(
            callbacks_state, "claim_poll_publish", claim_after_a_newer_edit
        )
        _trigger(monkeypatch, "agent-state-poll.n_intervals")
        stale = sync(
            1, None, None, scene_id, None, None, *sidebar, 1, "browser-restore"
        )
        assert all(value is no_update for value in stale)
        assert backend.peek_pending_state()["axis_scale"] == 0.7

        newer = {"scene_id": scene_id, "axis_scale": 0.9}
        with backend._lock:
            backend.pending_state = dict(newer)
        callbacks_state._restore_pending_if_absent(
            backend, {"scene_id": scene_id, "axis_scale": 0.1}
        )
        assert backend.peek_pending_state()["axis_scale"] == 0.9
    finally:
        backend.close()


def test_poll_fence_drops_a_response_after_the_browser_moves():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")

    script = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "poll_fence.js"
    program = r"""
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const document = {addEventListener() {}, readyState: "complete"};
const payload = {
  multi: true,
  response: {"agent-state-store": {data: {scene_id: "scene-a"}}},
  sideUpdate: {"scene-tab-rename-input": {value: "old"}},
};
const sandbox = {
  console, JSON, Math, Number, Object, String, Boolean, Array, Promise,
  document,
  setTimeout() {},
  setInterval() { return 0; },
  clearInterval() {},
  Headers: function () { this.delete = function () {}; },
  Response: function (body) { this.body = body; },
  __mvUiEpoch: 2,
  __mvSelectedSceneId: "scene-a",
};
sandbox.window = sandbox;
sandbox.fetch = function () {
  return Promise.resolve({
    status: 200,
    statusText: "OK",
    headers: {},
    clone() { return this; },
    json() {
      sandbox.__mvUiEpoch = 5;
      return Promise.resolve(JSON.parse(JSON.stringify(payload)));
    },
  });
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), sandbox);
const fence = sandbox.MatterVisPollFence;
const pollBody = JSON.stringify({
  output: [
    {id: "scene-tab-rename-input", property: "value"},
    {id: "agent-state-store", property: "data"},
  ],
  changedPropIds: ["agent-state-poll.n_intervals"],
});
const tracked = fence.requestFence(pollBody, {epoch: 2, scene: "scene-a"});
assert.equal(
  JSON.stringify(tracked),
  JSON.stringify({epoch: 2, scene: "scene-a", unacked: 0})
);
assert.equal(fence.requestFence(JSON.stringify({
  output: "agent-state-store scene-tab-rename-input",
  changedPropIds: ["agent-state-poll.n_intervals", "scene-tabs.value"],
}), {epoch: 2, scene: "scene-a"}), null);
assert.equal(fence.responseIsStale(tracked, {epoch: 2, scene: "scene-a"}), false);
assert.equal(fence.responseIsStale(tracked, {epoch: 3, scene: "scene-a"}), true);
assert.equal(fence.responseIsStale(tracked, {epoch: 2, scene: "scene-b"}), true);
const blank = fence.blankPollPayload(payload);
assert.equal(JSON.stringify(blank.response), "{}");
assert.equal(JSON.stringify(blank.sideUpdate), "{}");
assert.equal(payload.response["agent-state-store"].data.scene_id, "scene-a");
sandbox.fetch("/_dash-update-component", {body: pollBody}).then(function (response) {
  const dropped = JSON.parse(response.body);
  assert.equal(JSON.stringify(dropped.response), "{}");
  process.exit(0);
}).catch(function (error) {
  console.error(error);
  process.exit(1);
});
setTimeout(function () {
  console.error("poll fence did not drop the late response");
  process.exit(1);
}, 1000);
"""
    completed = subprocess.run(
        ["node", "-e", program, str(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_poll_fence_drops_a_response_while_an_edit_is_unacked():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")

    script = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "poll_fence.js"
    program = r"""
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const document = {addEventListener() {}, readyState: "complete"};
const payload = {
  multi: true,
  response: {"agent-state-store": {data: {scene_id: "scene-a", axis_scale: 1}}},
};
const sandbox = {
  console, JSON, Math, Number, Object, String, Boolean, Array, Promise,
  document,
  setTimeout() {},
  setInterval() { return 0; },
  clearInterval() {},
  Headers: function () { this.delete = function () {}; },
  Response: function (body) { this.body = body; },
  __mvUiEpoch: 4,
  __mvSelectedSceneId: "scene-a",
  __mvUnackedEpoch: 4,
  __mvUserWrites: 0,
};
sandbox.window = sandbox;
sandbox.fetch = function () {
  return Promise.resolve({
    status: 200,
    statusText: "OK",
    headers: {},
    clone() { return this; },
    json() { return Promise.resolve(JSON.parse(JSON.stringify(payload))); },
    body: JSON.stringify(payload),
  });
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), sandbox);
const pollBody = JSON.stringify({
  output: [
    {id: "scene-tab-rename-input", property: "value"},
    {id: "agent-state-store", property: "data"},
  ],
  changedPropIds: ["agent-state-poll.n_intervals"],
});
const editBody = JSON.stringify({
  output: "agent-state-store.data",
  changedPropIds: ["axis-scale-slider.value"],
});
sandbox.fetch("/_dash-update-component", {body: pollBody}).then(function (response) {
  const dropped = JSON.parse(response.body);
  assert.equal(JSON.stringify(dropped.response), "{}");
  return sandbox.fetch("/_dash-update-component", {body: editBody});
}).then(function () {
  assert.equal(sandbox.__mvUnackedEpoch, 0);
  assert.equal(sandbox.__mvUserWrites, 0);
  return sandbox.fetch("/_dash-update-component", {body: pollBody});
}).then(function (response) {
  const kept = JSON.parse(response.body);
  assert.equal(kept.response["agent-state-store"].data.axis_scale, 1);
  process.exit(0);
}).catch(function (error) {
  console.error(error);
  process.exit(1);
});
setTimeout(function () {
  console.error("poll fence did not settle the unacked edit");
  process.exit(1);
}, 1000);
"""
    completed = subprocess.run(
        ["node", "-e", program, str(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_poll_fence_tab_click_bumps_before_the_response():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    script = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "poll_fence.js"
    program = r"""
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const listeners = [];
const document = {
  addEventListener(name, fn, capture) { listeners.push({name, fn, capture}); },
  readyState: "complete",
};
const sandbox = {
  console, JSON, Math, Number, Object, String, Boolean, Array,
  document, setTimeout() {}, setInterval() { return 0; }, clearInterval() {},
  Promise,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), sandbox);
const pointer = listeners.find((item) => item.name === "pointerdown" && item.capture);
assert.ok(pointer, "pointerdown must be observed before Dash handles the click");
const button = {
  id: JSON.stringify({type: "tab-select", scene_id: "scene-b"}),
  closest(selector) {
    if (selector === ".tab-select") return this;
    if (selector === "#rightclick-target-fallback") return null;
    return null;
  },
};
sandbox.__mvUiEpoch = 1;
sandbox.__mvSelectedSceneId = "scene-a";
pointer.fn({isTrusted: true, target: button});
assert.equal(sandbox.__mvUiEpoch, 2);
assert.equal(sandbox.__mvSelectedSceneId, "scene-b");
"""
    completed = subprocess.run(
        ["node", "-e", program, str(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
