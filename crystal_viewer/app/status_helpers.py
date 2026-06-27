from __future__ import annotations

from typing import Any

from .. import perf_log


def status_banner_class(level: str = "info") -> str:
    return f"status-banner status-banner--{level}"


def classify_status_level(message: Any) -> str:
    text = str(message or "")
    lowered = text.lower()
    if "failed" in lowered or "error" in lowered:
        return "error"
    if "must" in lowered or "warning" in lowered:
        return "warning"
    return "success"


def status_banner_payload(message: Any) -> tuple[str, str]:
    text = str(message or "")
    return text, status_banner_class(classify_status_level(text))


def callback_error_message(prefix: str, exc: BaseException, *, max_length: int = 240) -> str:
    text = str(exc) or exc.__class__.__name__
    if len(text) > max_length:
        text = text[: max_length - 3] + "..."
    return f"{prefix} failed: {text}"


def emit_legacy_status(message: str, *, callback_ctx: Any = None) -> None:
    if callback_ctx is None:
        from .shared import callback_context as callback_ctx
    try:
        callback_ctx.set_props("status", {"children": message})
    except Exception:
        # Dash < 2.17 lacks set_props; callers still log to perf-log.
        pass


def surface_callback_error(prefix: str, exc: BaseException, *, callback_ctx: Any = None) -> str:
    message = callback_error_message(prefix, exc)
    emit_legacy_status(message, callback_ctx=callback_ctx)
    perf_log.record(
        "callback:editor_error",
        duration_ms=0.0,
        kind="cb",
        info={"prefix": prefix, "error": str(exc) or exc.__class__.__name__, "type": exc.__class__.__name__},
    )
    return message


__all__ = [
    "callback_error_message",
    "classify_status_level",
    "emit_legacy_status",
    "status_banner_class",
    "status_banner_payload",
    "surface_callback_error",
]