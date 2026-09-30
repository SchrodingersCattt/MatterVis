"""Stable, CSS-safe identities for Plotly traces."""

from __future__ import annotations

import re


_UNSAFE_TRACE_UID = re.compile(r"[^A-Za-z0-9_-]+")


def plotly_trace_uid(value: object) -> str:
    """Return a deterministic trace UID that is safe in a CSS selector."""

    text = _UNSAFE_TRACE_UID.sub("-", str(value)).strip("-") or "trace"
    if text[0].isdigit():
        text = f"mv-{text}"
    return text


__all__ = ["plotly_trace_uid"]
