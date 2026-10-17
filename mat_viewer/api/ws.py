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
                last_figure_seq = _deliver_figures(backend, socket, last_figure_seq)
            # Use a shorter poll interval when there are pending figure
            # broadcasts to reduce latency for cache-hit tab switches.
            has_pending = _figures_pending(backend, last_figure_seq) if include_figure else False
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
                    if include_figure:
                        ensure = getattr(backend, "ensure_active_scene_figure", None)
                        if ensure is not None:
                            ensure()
                    continue
                handle_ws_message(backend, payload)

    sock.route("/api/v1/ws")(ws_state)


def _deliver_figures(backend, socket, last_figure_seq: int) -> int:
    """Send one journal snapshot and return the cursor to keep.

    Delivered events advance the cursor. When that snapshot contained only
    filtered frames, advance to its head so the poll does not spin. A frame
    appended after the snapshot has a higher sequence than that head and is
    still delivered on the next turn.
    """
    reader = getattr(backend, "figure_broadcasts_since_with_head", None)
    if reader is None:
        payloads = list(backend.figure_broadcasts_since(last_figure_seq))
        snapshot_head = last_figure_seq
    else:
        payloads, snapshot_head = reader(last_figure_seq)
        snapshot_head = int(snapshot_head)
    for payload in payloads:
        socket.send(json.dumps(payload, ensure_ascii=False))
        last_figure_seq = max(last_figure_seq, int(payload.get("figure_seq", 0) or 0))
    if not payloads and snapshot_head > last_figure_seq:
        last_figure_seq = snapshot_head
    return last_figure_seq


def _figures_pending(backend, last_figure_seq: int) -> bool:
    seq_fn = getattr(backend, "figure_journal_seq", None)
    if seq_fn is None:
        return bool(backend.figure_broadcasts_since(last_figure_seq))
    return int(seq_fn()) > int(last_figure_seq)


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
