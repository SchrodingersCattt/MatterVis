"""Scene serialization helpers.

Moved from ``scene/core.py`` per the layered design in AGENTS.md.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def _to_builtin(value: Any) -> Any:
    """Recursively convert numpy scalars/arrays to Python builtins."""
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, dict):
        return {key: _to_builtin(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_builtin(item) for item in value]
    return value


def scene_metadata(scene: dict[str, Any]) -> dict[str, Any]:
    """Return a small JSON-safe summary of a rendered scene."""
    return {
        "name": scene["name"],
        "title": scene["title"],
        "has_minor": bool(scene.get("has_minor", False)),
        "atom_count": len(scene.get("draw_atoms", [])),
        "bond_count": len(scene.get("bonds", [])),
        "cif_path": scene.get("cif_path"),
    }


def scene_json(scene: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe copy of the scene, stripping internal keys."""
    payload: dict[str, Any] = {}
    for key, value in scene.items():
        if str(key).startswith("_"):
            continue
        if key == "cell":
            payload[key] = {
                "a": float(value.a),
                "b": float(value.b),
                "c": float(value.c),
                "alpha": float(value.alpha),
                "beta": float(value.beta),
                "gamma": float(value.gamma),
                "volume": float(value.volume),
            }
        else:
            payload[key] = _to_builtin(value)
    return payload
