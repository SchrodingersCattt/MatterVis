"""Group and rank server-log events before they are drawn.

The in-app log used to print every callback as its own row. Bursts of fast
polls buried warnings. Levels and consecutive grouping keep the pane readable
without dropping events from the ring buffer.
"""

from __future__ import annotations

from typing import Any

from .normalizers import _PERF_FAST_MS, _PERF_SLOW_MS

_LEVEL_RANK = {"debug": 0, "info": 1, "warn": 2, "error": 3}


def perf_event_level(entry: dict[str, Any] | None) -> str:
    """Map one perf-log record onto ``debug`` / ``info`` / ``warn`` / ``error``."""
    entry = entry or {}
    explicit = str(entry.get("level") or "").strip().lower()
    if explicit == "warning":
        explicit = "warn"
    if explicit in _LEVEL_RANK:
        return explicit
    kind = str(entry.get("kind") or "").strip().lower()
    label = str(entry.get("label") or "").lower()
    info = entry.get("info") if isinstance(entry.get("info"), dict) else {}
    if (
        kind in {"error", "exception"}
        or "error" in label
        or info.get("error")
        or info.get("exc_type")
        or info.get("traceback")
    ):
        return "error"
    ms = entry.get("ms")
    try:
        duration = None if ms is None else float(ms)
    except (TypeError, ValueError):
        duration = None
    if duration is not None and duration >= _PERF_SLOW_MS:
        return "warn"
    if kind == "cache" or (
        duration is not None and duration < _PERF_FAST_MS and kind in {"cb", "event", "http"}
    ):
        return "debug"
    return "info"


def group_perf_log_entries(
    entries: list[dict[str, Any]] | None,
    *,
    minimum_level: str = "info",
) -> list[dict[str, Any]]:
    """Collapse consecutive events that share a label.

    ``entries`` are in display order (newest first). A group's level is the
    most severe event inside it, so one slow call is not hidden inside a
    debug burst. Events below ``minimum_level`` are omitted.
    """
    floor = _LEVEL_RANK.get(str(minimum_level or "info").lower(), _LEVEL_RANK["info"])
    groups: list[dict[str, Any]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        level = perf_event_level(entry)
        label = str(entry.get("label") or "")
        if groups and groups[-1]["label"] == label:
            groups[-1]["entries"].append(entry)
            if _LEVEL_RANK[level] > _LEVEL_RANK[groups[-1]["level"]]:
                groups[-1]["level"] = level
            continue
        groups.append({"label": label, "level": level, "entries": [entry]})
    return [group for group in groups if _LEVEL_RANK[group["level"]] >= floor]
