from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *


def register_ws_routes(server, backend) -> None:
    if Sock is None:
        return

    sock = Sock(server)

    @sock.route("/api/v2/ws")
    def ws_state(socket):
        last_version = -1
        last_figure_seq = 0
        include_figure = False
        while True:
            snapshot = backend.websocket_snapshot(include_figure=include_figure)
            version = snapshot["version"]
            if version != last_version:
                socket.send(json.dumps(snapshot, ensure_ascii=False))
                last_version = version
            if include_figure:
                for payload in backend.figure_broadcasts_since(last_figure_seq):
                    socket.send(json.dumps(payload, ensure_ascii=False))
                    last_figure_seq = max(last_figure_seq, int(payload.get("figure_seq", 0) or 0))
            # Use a shorter poll interval when there are pending figure
            # broadcasts to reduce latency for cache-hit tab switches.
            has_pending = bool(backend.figure_broadcasts_since(last_figure_seq)) if include_figure else False
            poll_timeout = 0.05 if has_pending else 0.5
            try:
                message = socket.receive(timeout=poll_timeout)
            except TypeError:
                message = None
                time.sleep(poll_timeout)
            if message:
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    payload = {"type": "raw", "message": message}
                if isinstance(payload, dict) and payload.get("type") == "subscribe_figure":
                    include_figure = bool(payload.get("enabled", True))
                    last_version = -1
                    continue
                handle_ws_message(backend, payload)

    sock.route("/api/v1/ws")(ws_state)


def handle_ws_message(backend, payload: dict) -> None:
    """Dispatch one WebSocket message envelope.

    Extracted so the dispatch logic is unit-testable without spinning
    a real socket. Currently honours one envelope type:

    - ``{"type": "set_state", "payload": {...}, "scene_id": "..."}``
      patches state on a specific scene; ``scene_id`` may also live
      inside ``payload`` (legacy shape) or be omitted entirely (active
      scene). Returns the new state dict for the targeted scene so
      tests can assert on the result; production callers ignore the
      return value.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("type") == "intent":
        inner = payload.get("payload", {}) or {}
        return backend.apply_intent(inner)
    if payload.get("type") != "set_state":
        return None
    inner = payload.get("payload", {}) or {}
    scene_id = (
        payload.get("scene_id")
        or (inner.get("scene_id") if isinstance(inner, dict) else None)
    )
    return backend.patch_state(inner, scene_id=scene_id)
