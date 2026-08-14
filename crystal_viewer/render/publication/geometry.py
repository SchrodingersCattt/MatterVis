"""Canonical-cell geometry helpers for static publication figures."""

from __future__ import annotations

from typing import Any

import numpy as np
from molcrys_kit.utils.geometry import cart_to_frac


def in_half_open_cell(scene: dict[str, Any], cart: Any, *, tol: float = 1e-8) -> bool:
    """Return whether a Cartesian point belongs to the canonical [0, 1) cell."""
    matrix_value = scene.get("M")
    if matrix_value is None:
        matrix_value = scene.get("cell")
    matrix = np.asarray(matrix_value, dtype=float)
    point = np.asarray(cart, dtype=float)
    if matrix.shape != (3, 3) or point.shape != (3,):
        return False
    frac = np.asarray(cart_to_frac(point, matrix), dtype=float)
    return bool(np.all(frac >= -tol) and np.all(frac < 1.0 - tol))


def filter_polyhedra_to_half_open_cell(
    scene: dict[str, Any],
    topology_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Copy spec results while retaining one canonical image of each center."""
    filtered = []
    for result in topology_data.get("spec_results") or []:
        overlays = [
            overlay
            for overlay in result.get("overlays") or []
            if in_half_open_cell(scene, overlay.get("center_coords"))
        ]
        if overlays:
            filtered.append({**result, "overlays": overlays})
    return filtered
