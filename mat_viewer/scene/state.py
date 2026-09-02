"""Per-tab scene state schema helpers."""
from __future__ import annotations

from typing import Any

from ..render.overlay.cells import normalize_cell_overlays
from ..render.overlay.vectors import normalize_vector_overlays

DEFAULT_OVERLAY_OVERRIDES: list[dict[str, Any]] = []


def default_overlay_overrides() -> list[dict[str, Any]]:
    return []


def normalize_overlay_overrides(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        if not kind:
            continue
        anchor = str(item.get("anchor") or "paper").strip()
        if anchor not in {"paper", "world"}:
            continue
        override = dict(item)
        override_id = str(override.get("id") or f"overlay_{index}").strip() or f"overlay_{index}"
        while override_id in seen:
            override_id = f"{override_id}_{len(seen)}"
        seen.add(override_id)
        override["id"] = override_id
        override["kind"] = kind
        override["anchor"] = anchor
        out.append(override)
    return out


__all__ = [
    "DEFAULT_OVERLAY_OVERRIDES",
    "default_overlay_overrides",
    "normalize_cell_overlays",
    "normalize_overlay_overrides",
    "normalize_vector_overlays",
]
