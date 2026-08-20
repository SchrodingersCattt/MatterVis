from __future__ import annotations

import numpy as np

# Re-export canonical domain-neutral math primitives from mat_viewer.math
from ..math.pbc import bond_vector_mic, nearest_lattice_shift_frac  # noqa: F401
from ..math.rotation import view_rotation, view_vec_to_elev_azim  # noqa: F401


# ── Orthogonalisation matrix ────────────────────────────────────────────────
def ortho_matrix(cell):
    a, b, c = cell.a, cell.b, cell.c
    al = np.radians(cell.alpha)
    be = np.radians(cell.beta)
    ga = np.radians(cell.gamma)
    cos_al, cos_be, cos_ga = np.cos(al), np.cos(be), np.cos(ga)
    sin_ga = np.sin(ga)
    vol = cell.volume
    M = np.array(
        [
            [a, b * cos_ga, c * cos_be],
            [0, b * sin_ga, c * (cos_al - cos_be * cos_ga) / sin_ga],
            [0, 0, vol / (a * b * sin_ga)],
        ]
    )
    N = M / np.array([a, b, c])
    return M, N


def _wrap_frac01(frac):
    frac = np.array(frac, dtype=float)
    return frac - np.floor(frac)


def _nearest_pbc_cart(ref_cart, pos_cart, cell):
    from ase.geometry import find_mic

    if not all(
        hasattr(cell, name) for name in ("a", "b", "c", "alpha", "beta", "gamma")
    ):
        values = tuple(float(value) for value in cell)
        if len(values) != 6:
            raise ValueError("cell must provide a, b, c, alpha, beta, gamma")
        from .cif_parse import CellParameters

        a, b, c, alpha, beta, gamma = values
        cos_alpha, cos_beta, cos_gamma = np.cos(np.radians([alpha, beta, gamma]))
        volume_factor = np.sqrt(
            max(
                0.0,
                1.0
                + 2.0 * cos_alpha * cos_beta * cos_gamma
                - cos_alpha**2
                - cos_beta**2
                - cos_gamma**2,
            )
        )
        cell = CellParameters(
            a,
            b,
            c,
            alpha,
            beta,
            gamma,
            volume=float(a * b * c * volume_factor),
        )

    legacy_matrix, _ = ortho_matrix(cell)
    row_lattice = legacy_matrix.T
    reference = np.asarray(ref_cart, dtype=float)
    delta = np.asarray(pos_cart, dtype=float) - reference
    mic, _ = find_mic(delta, cell=row_lattice, pbc=True)
    return reference + np.asarray(mic, dtype=float)


__all__ = [name for name in globals() if not name.startswith("__")]
