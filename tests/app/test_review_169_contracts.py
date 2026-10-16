"""Contracts from the PR 169 transport review."""

from __future__ import annotations

import copy
import json
import threading

import pytest

from mat_viewer.api import ws
from mat_viewer.app.backend_journal import _JournalBackendMixin
from mat_viewer.app.build_info import asset_sha, build_sha, compute_fingerprint
from mat_viewer.app.callbacks_view import client_http_frame
from mat_viewer.app.render_worker import _TASK_STATUS_LIMIT, AsyncRenderWorker


def test_build_sha_stays_frozen_while_asset_sha_follows_disk(monkeypatch):
    import mat_viewer.app.build_info as info

    monkeypatch.setattr(info, "_FROZEN", None)
    values = iter(["sha-1", "sha-2", "sha-3"])
    monkeypatch.setattr(info, "compute_fingerprint", lambda asset_dir=None: next(values))
    assert build_sha() == "sha-1"
    assert build_sha() == "sha-1"
    assert asset_sha() == "sha-2"


def test_fingerprint_changes_when_an_asset_changes(tmp_path, monkeypatch):
    import mat_viewer.app.build_info as info

    monkeypatch.setattr(info, "_git_head", lambda: "abc")
    asset = tmp_path / "app.js"
    asset.write_text("one", encoding="utf-8")
    first = compute_fingerprint(str(tmp_path))
    asset.write_text("one-two", encoding="utf-8")
    assert compute_fingerprint(str(tmp_path)) != first


class _JournalStub(_JournalBackendMixin):
    def __init__(self):
        self._figure_broadcast_lock = threading.Lock()
        self._figure_broadcasts = []
        self._figure_broadcast_seq = 0
        self._render_worker = _Worker()
        self._lock = threading.Lock()
        self.pending_state = None

    def get_state(self, scene_id=None):
        return {"scene_id": "s1"}


class _Worker:
    def __init__(self):
        self.states = []

    def request_figure_build(self, state):
        self.states.append(state)
        return True


def test_journal_head_ignores_a_frame_appended_during_copy(monkeypatch):
    backend = _JournalStub()
    backend._figure_broadcasts.append({"type": "figure", "figure_seq": 1, "scene_id": "a"})
    backend._figure_broadcast_seq = 1
    real = copy.deepcopy

    def wrapped(payload):
        if backend._figure_broadcast_seq == 1:
            with backend._figure_broadcast_lock:
                backend._figure_broadcasts.append(
                    {"type": "figure", "figure_seq": 2, "scene_id": "a"}
                )
                backend._figure_broadcast_seq = 2
        return real(payload)

    monkeypatch.setattr("mat_viewer.app.backend_journal.copy.deepcopy", wrapped)
    events, head = backend.figure_broadcasts_since_with_head(0)
    assert head == 1
    assert [event["figure_seq"] for event in events] == [1]
    assert backend.figure_journal_seq() == 2
    later, later_head = backend.figure_broadcasts_since_with_head(head)
    assert later_head == 2
    assert [event["figure_seq"] for event in later] == [2]


def test_empty_subscribe_requests_one_figure_until_a_frame_exists():
    backend = _JournalStub()
    assert backend.ensure_active_scene_figure() is True
    assert backend._render_worker.states == [{"scene_id": "s1"}]
    backend._figure_broadcasts.append({"type": "figure", "figure_seq": 1, "scene_id": "s1"})
    backend._figure_broadcast_seq = 1
    assert backend.ensure_active_scene_figure() is False
    assert len(backend._render_worker.states) == 1


def test_missing_scene_frame_queues_scene_scoped_build():
    backend = _JournalStub()
    backend._figure_broadcasts.append({"type": "figure", "figure_seq": 4, "scene_id": "other"})
    backend._figure_broadcast_seq = 4
    assert backend.ensure_scene_figure("s1") is True
    assert backend._render_worker.states == [{"scene_id": "s1"}]


def test_http_cursor_is_per_client_and_per_scene():
    class Backend:
        def __init__(self):
            self.frames = {
                "scene-a": {"type": "figure", "figure_seq": 5, "scene_id": "scene-a"},
                "scene-b": {"type": "figure", "figure_seq": 2, "scene_id": "scene-b"},
            }

        def latest_figure_broadcast(self, scene_id=None):
            return self.frames.get(scene_id)

    backend = Backend()
    first, cursor_a = client_http_frame(backend, {}, "scene-a")
    other, cursor_b = client_http_frame(backend, {}, "scene-b")
    assert first["figure_seq"] == 5
    assert other["figure_seq"] == 2
    again, cursor_a = client_http_frame(backend, cursor_a, "scene-a")
    assert again is None
    crossed, cursor_a = client_http_frame(backend, cursor_a, "scene-b")
    assert crossed["figure_seq"] == 2
    assert cursor_b["scene-b"] == 2
    assert "scene-b" not in cursor_a or cursor_a["scene-b"] == 2


def test_task_status_keeps_only_the_newest_window():
    worker = AsyncRenderWorker(backend=object())
    try:
        total = _TASK_STATUS_LIMIT + 40
        for index in range(total):
            worker._set_task_status(f"k{index}", scene_id="s", status="completed")
        status = worker.task_status()
        assert len(status) == _TASK_STATUS_LIMIT
        assert f"k{total - 1}" in status
        assert "k0" not in status
        assert f"k{total - _TASK_STATUS_LIMIT - 1}" not in status
    finally:
        worker.shutdown(wait=False)


def test_stale_snapshot_advances_without_dropping_a_later_frame(monkeypatch):
    routes = {}

    class Sock:
        def __init__(self, server):
            pass

        def route(self, path):
            def register(fn):
                routes[path] = fn
                return fn
            return register

    class Backend:
        def __init__(self):
            self.calls = 0
            self.ensured = 0

        def websocket_snapshot(self, **kwargs):
            return {"version": 1}

        def figure_broadcasts_since_with_head(self, seq):
            self.calls += 1
            if int(seq) < 4:
                return [], 4
            return [{"type": "figure", "figure_seq": 5}], 5

        def figure_journal_seq(self):
            return 5

        def ensure_active_scene_figure(self):
            self.ensured += 1
            return True

    class Disconnected(Exception):
        pass

    class Socket:
        def __init__(self):
            self.sent = []
            self.receives = 0

        def send(self, message):
            self.sent.append(json.loads(message))

        def receive(self, **kwargs):
            self.receives += 1
            if self.receives == 1:
                return json.dumps({"type": "subscribe_figure"})
            if self.receives >= 3:
                raise Disconnected
            return None

    backend = Backend()
    monkeypatch.setattr(ws, "Sock", Sock)
    ws.register_ws_routes(object(), backend)
    socket = Socket()
    with pytest.raises(Disconnected):
        routes["/api/v2/ws"](socket)
    assert backend.ensured == 1
    assert [payload["figure_seq"] for payload in socket.sent if payload.get("type") == "figure"] == [5]
