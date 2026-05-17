"""Manual overlay override helpers.

Overrides are per-scene state entries. Paper-anchored components store
`paper_xy`; world-anchored components store a target plus `pixel_offset`.
"""
from __future__ import annotations

from typing import Any


def normalize_overlay_override(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("overlay override must be a dict")
    kind = str(raw.get("kind") or "").strip()
    if not kind:
        raise ValueError("overlay override kind is required")
    anchor = str(raw.get("anchor") or "paper").strip()
    if anchor not in {"paper", "world"}:
        raise ValueError("overlay override anchor must be 'paper' or 'world'")
    out = dict(raw)
    out["kind"] = kind
    out["anchor"] = anchor
    return out


__all__ = ["normalize_overlay_override"]
