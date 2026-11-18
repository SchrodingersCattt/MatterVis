"""Render-hull payload for a coordination shell.

MolCrysKit selects the ligand coordinates. This module only decides
whether those points form a 3D polyhedron the renderer can draw.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from molcrys_kit.structures.polyhedra import convex_hull_payload


def render_hull_payload(shell_coords: Any) -> dict[str, Any]:
    """Return a 3D hull, or an explicit non-polyhedron when Qhull rejects it.

    ``convex_hull_payload`` asks Qhull for a volume. Coplanar and collinear
    sets raise ``QhullError`` (QH6154, the initial simplex is flat). The
    payload keeps the reported coordinates and emits no simplices or edges.
    """
    coords = np.asarray(shell_coords, dtype=float)
    if coords.size == 0:
        coords = np.zeros((0, 3), dtype=float)
    elif coords.ndim == 1:
        coords = coords.reshape(1, -1)
    try:
        from scipy.spatial import QhullError
    except ImportError:
        return convex_hull_payload(coords)
    try:
        return convex_hull_payload(coords)
    except QhullError:
        vertices = []
        if coords.ndim == 2 and coords.shape[1:] == (3,):
            vertices = coords.tolist()
        return {
            "vertices": vertices,
            "simplices": [],
            "edges": [],
            "degenerate": True,
            "reason": "not_a_3d_polyhedron",
        }


def note_degenerate_hull(gap_info: dict[str, Any], hull: dict[str, Any]) -> None:
    """Record a render rejection without erasing a chemistry rejection."""
    if hull.get("degenerate") and not gap_info.get("rejection_reason"):
        gap_info["rejection_reason"] = "not_a_3d_polyhedron"
