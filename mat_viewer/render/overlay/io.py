"""Shared file loading helpers for overlay JSON inputs."""

from __future__ import annotations

import json
from pathlib import Path


def load_overlay_file(path: str | Path | None, option: str):
    """Load a CLI overlay-list JSON file without backend-specific parsing."""
    if path is None:
        return None
    with Path(path).expanduser().open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise TypeError(f"{option} JSON root must be a list")
    return payload


__all__ = ["load_overlay_file"]
