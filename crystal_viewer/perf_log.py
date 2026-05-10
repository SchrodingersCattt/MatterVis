"""Lightweight perf-event log used by the Dash app to surface "what
the server actually did, and how long it took" to the browser.

Why
---
On 2026-05-10 the user reported "every operation takes 3-5 s, and
uploads are slow as shit -- do you even have logs?". The dev server
prints ``POST /_dash-update-component`` lines to stdout but those tell
you nothing about which callback fired or how long it took. This
module is the missing layer:

* :func:`record` appends a structured event with a monotonic-clock
  timestamp, a label (usually the callback name), a duration, and an
  optional info dict (filename, payload bytes, ...).
* :func:`recent` returns the most-recent ``limit`` events for the
  ``/api/v1/perf`` endpoint and the in-app "Server log" pane.
* :func:`time_block` is a contextmanager that records ``label`` with
  the elapsed wall-clock time when the block exits.
* :func:`timed` is the decorator equivalent for whole functions.

Events are also written to an on-disk file (``CV_PERF_LOG`` env var
or ``/tmp/cv-perf.log``) so they survive restarts and can be tailed
from a terminal.

The buffer is bounded (default 1000 events) and lock-protected so the
log can be safely consumed from request handlers while callbacks
write to it from worker threads.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Iterable, Optional, TypeVar

_LOCK = threading.Lock()
_BUFFER: deque[dict[str, Any]] = deque(maxlen=int(os.environ.get("CV_PERF_LOG_BUFFER", "1000")))
_LOG_PATH = os.environ.get("CV_PERF_LOG", "/tmp/cv-perf.log")
_SEQ = 0
_T0 = time.time()  # Wall-clock reference for human-readable timestamps.
_M0 = time.monotonic()  # Monotonic reference for accurate deltas.


def _wall_now() -> float:
    """Monotonic-corrected wall clock, so ordering across events is
    always strictly monotonic even if the system clock jumps."""
    return _T0 + (time.monotonic() - _M0)


def record(
    label: str,
    duration_ms: Optional[float] = None,
    *,
    kind: str = "event",
    info: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Append one event to the in-memory ring + the on-disk log.

    Parameters
    ----------
    label
        Short identifier, typically ``"callback:upload_cif"`` or
        ``"figure_for_state"``.
    duration_ms
        Optional elapsed time in milliseconds. Use :func:`time_block`
        or :func:`timed` to compute it automatically.
    kind
        Tag used by consumers to filter (``event`` for ad-hoc events,
        ``cb`` for Dash callbacks, ``http`` for REST hits, ...).
    info
        Optional JSON-serialisable extras (filename, payload size,
        atom count, ...). Keep it small -- this is also what shows up
        in the UI side panel.
    """
    global _SEQ
    info = info or {}
    timestamp = _wall_now()
    entry: dict[str, Any] = {
        "ts": timestamp,
        "iso": _iso(timestamp),
        "kind": kind,
        "label": label,
        "info": info,
    }
    if duration_ms is not None:
        entry["ms"] = float(duration_ms)
    with _LOCK:
        _SEQ += 1
        entry["seq"] = _SEQ
        _BUFFER.append(entry)
        # Best-effort file write. We never let logging crash a
        # callback, so failures are swallowed silently.
        try:
            with open(_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
        except Exception:
            pass
    return entry


def recent(limit: int = 200, since_seq: Optional[int] = None) -> list[dict[str, Any]]:
    """Return the most-recent events in chronological order.

    If ``since_seq`` is given, only events with a strictly greater
    sequence number are returned -- this lets the UI poll incrementally
    without re-shipping the whole buffer every tick.
    """
    with _LOCK:
        snapshot = list(_BUFFER)
    if since_seq is not None:
        snapshot = [e for e in snapshot if e["seq"] > since_seq]
    if limit and len(snapshot) > limit:
        snapshot = snapshot[-limit:]
    return snapshot


def clear() -> None:
    """Drop the in-memory ring (the on-disk log is left alone)."""
    with _LOCK:
        _BUFFER.clear()


def latest_seq() -> int:
    with _LOCK:
        return _SEQ


@contextmanager
def time_block(label: str, *, kind: str = "event", **info: Any):
    """Context manager that records ``label`` with the elapsed time.

    Usage::

        with time_block("figure_for_state", atoms=len(scene.atoms)):
            fig = build_figure(...)
    """
    start = time.monotonic()
    try:
        yield
    finally:
        record(label, duration_ms=(time.monotonic() - start) * 1000.0, kind=kind, info=info)


F = TypeVar("F", bound=Callable[..., Any])


def timed(label: str, *, kind: str = "cb", info_builder: Optional[Callable[..., dict[str, Any]]] = None) -> Callable[[F], F]:
    """Decorator that records ``label`` with the wall time of every call.

    ``info_builder`` may return extra info given the same ``*args,
    **kwargs`` as the wrapped function -- handy for capturing payload
    sizes without leaking the payload itself.
    """
    def _wrap(fn: F) -> F:
        @wraps(fn)
        def _inner(*args: Any, **kwargs: Any) -> Any:
            start = time.monotonic()
            try:
                return fn(*args, **kwargs)
            finally:
                info: dict[str, Any] = {}
                if info_builder is not None:
                    try:
                        info = info_builder(*args, **kwargs) or {}
                    except Exception:
                        info = {"_info_error": True}
                record(label, duration_ms=(time.monotonic() - start) * 1000.0, kind=kind, info=info)
        return _inner  # type: ignore[return-value]
    return _wrap


def _iso(ts: float) -> str:
    """Return ``ts`` as an ISO-8601 string with millisecond precision."""
    s = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))
    ms = int((ts - int(ts)) * 1000)
    return f"{s}.{ms:03d}"


def log_path() -> str:
    return _LOG_PATH
