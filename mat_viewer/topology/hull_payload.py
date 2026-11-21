"""Render-hull payload for a coordination shell.

MolCrysKit selects the ligand coordinates. This module only decides
whether those points form a 3D polyhedron the renderer can draw.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from molcrys_kit.structures.polyhedra import convex_hull_payload

# Singular values at or below this fraction of the largest one are
# numerical noise, not a geometric axis. On a shell a few angstroms
# across, the absolute floor keeps a 1e-8 Å wobble from counting as
# thickness. A displacement that clears both floors is full rank even
# when Qhull still refuses the simplex.
AFFINE_RANK_RELATIVE_TOLERANCE = 1e-8
AFFINE_RANK_ABSOLUTE_TOLERANCE = 1e-8

REASON_NOT_3D = "not_a_3d_polyhedron"
REASON_QHULL_NUMERICAL = "qhull_numerical_failure"


def affine_rank(shell_coords: Any) -> int:
    """Return how many axes the points span, 0 through 3.

    The rank is the number of singular values of the centered coordinates
    that exceed :data:`AFFINE_RANK_ABSOLUTE_TOLERANCE` and
    :data:`AFFINE_RANK_RELATIVE_TOLERANCE` times the largest singular
    value. Non-finite or non-3D coordinates have rank 0.
    """
    points = np.asarray(shell_coords, dtype=float)
    if points.ndim != 2 or points.shape[0] == 0 or points.shape[1] != 3:
        return 0
    if not np.all(np.isfinite(points)):
        return 0
    centered = points - np.mean(points, axis=0)
    singular = np.linalg.svd(centered, compute_uv=False)
    if singular.size == 0:
        return 0
    scale = float(singular[0])
    cutoff = max(
        AFFINE_RANK_ABSOLUTE_TOLERANCE,
        AFFINE_RANK_RELATIVE_TOLERANCE * scale,
    )
    return int(np.count_nonzero(singular > cutoff))


def render_hull_payload(shell_coords: Any) -> dict[str, Any]:
    """Return a 3D hull, or an explicit empty hull when Qhull rejects it.

    ``convex_hull_payload`` asks Qhull for a volume. A flat set raises
    ``QhullError`` (QH6154, the initial simplex is flat). That error is
    ``not_a_3d_polyhedron`` only when :func:`affine_rank` is below 3.
    A full-rank set that Qhull still rejects, and any non-finite
    coordinate, is ``qhull_numerical_failure``. Neither is reported as
    coplanar. A finite rejection keeps the reported coordinates. A
    non-finite rejection stores no coordinates. Neither emits simplices
    or edges.
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
        finite_shell = (
            coords.ndim == 2
            and coords.shape[1:] == (3,)
            and coords.shape[0] > 0
            and bool(np.all(np.isfinite(coords)))
        )
        if not finite_shell:
            return {
                "vertices": [],
                "simplices": [],
                "edges": [],
                "degenerate": True,
                "reason": REASON_QHULL_NUMERICAL,
                "affine_rank": 0,
            }
        rank = affine_rank(coords)
        reason = REASON_NOT_3D if rank < 3 else REASON_QHULL_NUMERICAL
        return {
            "vertices": coords.tolist(),
            "simplices": [],
            "edges": [],
            "degenerate": True,
            "reason": reason,
            "affine_rank": rank,
        }


def rejected_hull_card(reason: str | None) -> tuple[str, str]:
    """Return ``(hull_reason, error)`` for a hull the renderer did not draw.

    Rank below 3 is the flat-shell card. A missing reason is not evidence
    that the ligands are flat, so it uses the numerical card.
    """
    if reason == REASON_NOT_3D:
        return (
            REASON_NOT_3D,
            "Ligand positions do not span a 3D polyhedron "
            "(coplanar or collinear). No faces were generated.",
        )
    return (
        REASON_QHULL_NUMERICAL,
        "Qhull could not build a hull. This is a numerical failure, "
        "not a flat shell. No faces were generated.",
    )


def note_degenerate_hull(gap_info: dict[str, Any], hull: dict[str, Any]) -> None:
    """Record a render rejection without erasing a chemistry rejection."""
    if hull.get("degenerate") and not gap_info.get("rejection_reason"):
        gap_info["rejection_reason"] = hull.get("reason") or REASON_QHULL_NUMERICAL
