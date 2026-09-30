"""A frame injected after the history snapshot must survive the next poll."""
import json

import pytest

from mat_viewer.api import ws


def test_completion_between_history_and_latest_is_delivered(monkeypatch):
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
            self.events = []
            self.injected = False

        def websocket_snapshot(self, **kwargs):
            return {"version": 0}

        def figure_broadcasts_since(self, seq):
            history = [event for event in self.events if event["figure_seq"] > seq]
            if not self.injected:
                self.injected = True
                self.events.append({"type": "figure", "figure_seq": 1})
            return history

        def latest_figure_seq(self):
            return 1 if self.injected else 0

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

    monkeypatch.setattr(ws, "Sock", Sock)
    ws.register_ws_routes(object(), Backend())
    socket = Socket()
    with pytest.raises(Disconnected):
        routes["/api/v2/ws"](socket)
    assert [p["figure_seq"] for p in socket.sent if p.get("type") == "figure"] == [1]