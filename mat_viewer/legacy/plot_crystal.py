"""Legacy label-placement utilities relocated from static_publication/plot_crystal.py.

Only the subset used by scene/core.py is retained here.
"""
from __future__ import annotations

from collections import OrderedDict

import numpy as np

from ..style.palette import atom_r

# ── Smart label placement (screen-space, radial + collision avoidance) ───────
_LABEL_POS_CACHE: "OrderedDict[tuple, list]" = OrderedDict()
_LABEL_POS_CACHE_MAX = 64


def _label_pos_cache_key(label_atoms, view_x, view_y, base_offset, all_atoms):
    """Stable hash of inputs to ``_compute_label_positions``."""
    label_bytes = np.round(
        np.array([atom["cart"] for atom in label_atoms], dtype=float), 4
    ).tobytes()
    if all_atoms is None:
        all_bytes = b""
    else:
        all_bytes = np.round(
            np.array([atom["cart"] for atom in all_atoms], dtype=float), 4
        ).tobytes()
    return (
        len(label_atoms),
        label_bytes,
        np.round(np.asarray(view_x, dtype=float), 5).tobytes(),
        np.round(np.asarray(view_y, dtype=float), 5).tobytes(),
        round(float(base_offset), 4),
        len(all_atoms) if all_atoms is not None else -1,
        all_bytes,
    )


def _label_atom_radius(atom, view_x, view_y):
    """Return ``max(a_ax, b_ax)`` for ``atom``'s view-plane ellipse."""
    elem = atom.get('elem', 'C')
    U = atom.get('U')
    if U is not None and elem != 'H':
        try:
            P = np.array([view_x, view_y], dtype=float)
            U2 = P @ np.asarray(U, dtype=float) @ P.T
            U2 = (U2 + U2.T) / 2.0
            eigvals = np.linalg.eigvalsh(U2)
            eigvals = np.abs(eigvals)
            scale = np.sqrt(1.3863)  # 50 % probability
            a_ax = max(0.05, min(scale * np.sqrt(eigvals[0]), 0.40))
            b_ax = max(0.05, min(scale * np.sqrt(eigvals[1]), 0.40))
            return max(a_ax, b_ax)
        except np.linalg.LinAlgError:
            return 0.11
    if elem == 'H':
        return 0.07
    uiso = max(atom.get('uiso', 0.04) or 0.04, 0.02)
    r_atom = atom_r(elem)
    r = max(r_atom * 0.8, min(np.sqrt(1.3863 * uiso) * 0.65, r_atom * 1.3))
    return float(r)


def _compute_label_positions(label_atoms, view_x, view_y, base_offset=0.38,
                             all_atoms=None):
    """Compute 3D label positions using a vectorised force-directed layout."""
    if not label_atoms:
        return []

    cache_key = _label_pos_cache_key(label_atoms, view_x, view_y, base_offset, all_atoms)
    cached = _LABEL_POS_CACHE.get(cache_key)
    if cached is not None:
        _LABEL_POS_CACHE.move_to_end(cache_key)
        return [pos.copy() for pos in cached]

    non_h = [a for a in label_atoms if a['elem'] != 'H']
    if not non_h:
        non_h = label_atoms
    if all_atoms is None:
        all_non_h_atoms = non_h
    else:
        all_non_h_atoms = [a for a in all_atoms if a.get('elem') != 'H']
        if not all_non_h_atoms:
            all_non_h_atoms = non_h

    view_x_arr = np.asarray(view_x, dtype=float)
    view_y_arr = np.asarray(view_y, dtype=float)

    label_carts = np.array([a['cart'] for a in label_atoms], dtype=float)
    label_xy = np.column_stack(
        (label_carts @ view_x_arr, label_carts @ view_y_arr)
    )
    ellipse_rs = np.array(
        [_label_atom_radius(at, view_x_arr, view_y_arr) for at in label_atoms],
        dtype=float,
    )

    non_h_carts = np.array([a['cart'] for a in non_h], dtype=float)
    cx = float((non_h_carts @ view_x_arr).mean())
    cy = float((non_h_carts @ view_y_arr).mean())

    # Step 1: initial radial placement
    delta = label_xy - np.array([cx, cy])
    norms = np.linalg.norm(delta, axis=1)
    safe_mask = norms < 0.05
    direction = np.where(
        safe_mask[:, None],
        np.array([0.0, 1.0]),
        np.divide(delta, np.where(norms[:, None] == 0, 1.0, norms[:, None])),
    )
    scale = (ellipse_rs + base_offset)[:, None]
    positions = label_xy + direction * scale

    # Step 2: iterative repulsion
    label_r = 0.55
    min_sep = label_r * 2.0

    atom_carts = np.array([a['cart'] for a in all_non_h_atoms], dtype=float)
    atom_xy = np.column_stack(
        (atom_carts @ view_x_arr, atom_carts @ view_y_arr)
    )
    atom_er = np.array(
        [_label_atom_radius(at, view_x_arr, view_y_arr) for at in all_non_h_atoms],
        dtype=float,
    )

    eps = 1e-6
    move_eps = 1e-3
    n_lab = len(positions)
    n_atom = len(atom_xy)

    LABEL_OPT_MAX_ATOMS = 600
    if n_atom > LABEL_OPT_MAX_ATOMS:
        result = [
            positions[k, 0] * view_x_arr + positions[k, 1] * view_y_arr
            for k in range(n_lab)
        ]
        _LABEL_POS_CACHE[cache_key] = result
        if len(_LABEL_POS_CACHE) > _LABEL_POS_CACHE_MAX:
            _LABEL_POS_CACHE.popitem(last=False)
        return [pos.copy() for pos in result]

    owner_mask = (
        (np.abs(label_xy[:, 0:1] - atom_xy[:, 0]) < 1e-6)
        & (np.abs(label_xy[:, 1:2] - atom_xy[:, 1]) < 1e-6)
    )

    use_kdtree = n_atom > 64
    nearby_idx: list[np.ndarray] | None = None
    if use_kdtree:
        try:
            from scipy.spatial import cKDTree
            tree = cKDTree(atom_xy)
            cutoff = float(atom_er.max()) + label_r * 0.85 + 0.5

            def query_nearby(pos_xy: np.ndarray) -> list[np.ndarray]:
                neigh = tree.query_ball_point(pos_xy, r=cutoff)
                return [np.asarray(idx, dtype=int) for idx in neigh]

            nearby_idx = query_nearby(positions)
        except ImportError:
            use_kdtree = False

    for _ in range(80):
        # label <-> label forces
        diff_ll = positions[:, None, :] - positions[None, :, :]
        dist_ll = np.sqrt((diff_ll ** 2).sum(axis=2))
        np.fill_diagonal(dist_ll, np.inf)
        active_ll = (dist_ll < min_sep) & (dist_ll > eps)
        if active_ll.any():
            push_ll = np.where(active_ll, (min_sep - dist_ll) / 2.0 + 0.02, 0.0)
            inv_ll = np.where(active_ll, 1.0 / np.where(dist_ll == 0, 1.0, dist_ll), 0.0)
            unit_ll = diff_ll * inv_ll[:, :, None]
            force_ll = (unit_ll * push_ll[:, :, None]).sum(axis=1)
        else:
            force_ll = np.zeros_like(positions)

        # label <-> atom forces
        if use_kdtree and nearby_idx is not None:
            force_la = np.zeros_like(positions)
            for i, idx_arr in enumerate(nearby_idx):
                if idx_arr.size == 0:
                    continue
                diff = positions[i, None, :] - atom_xy[idx_arr, :]
                dist = np.sqrt((diff ** 2).sum(axis=1))
                req = atom_er[idx_arr] + label_r * 0.85
                mask = (dist < req) & (dist > eps) & (~owner_mask[i, idx_arr])
                if not mask.any():
                    continue
                push = np.where(mask, (req - dist) + 0.02, 0.0)
                inv = np.where(mask, 1.0 / np.where(dist == 0, 1.0, dist), 0.0)
                unit = diff * inv[:, None]
                force_la[i] += (unit * push[:, None]).sum(axis=0)
        else:
            diff_la = positions[:, None, :] - atom_xy[None, :, :]
            dist_la = np.sqrt((diff_la ** 2).sum(axis=2))
            req = atom_er[None, :] + label_r * 0.85
            active_la = (dist_la < req) & (dist_la > eps) & (~owner_mask)
            if active_la.any():
                push_la = np.where(active_la, (req - dist_la) + 0.02, 0.0)
                inv_la = np.where(active_la, 1.0 / np.where(dist_la == 0, 1.0, dist_la), 0.0)
                unit_la = diff_la * inv_la[:, :, None]
                force_la = (unit_la * push_la[:, :, None]).sum(axis=1)
            else:
                force_la = np.zeros_like(positions)

        delta_step = force_ll + force_la
        max_move = float(np.linalg.norm(delta_step, axis=1).max() if n_lab else 0.0)
        if max_move < move_eps:
            break
        positions = positions + delta_step
        if use_kdtree and nearby_idx is not None and max_move > 0.5:
            nearby_idx = query_nearby(positions)

    result = [
        positions[k, 0] * view_x_arr + positions[k, 1] * view_y_arr
        for k in range(n_lab)
    ]
    _LABEL_POS_CACHE[cache_key] = result
    if len(_LABEL_POS_CACHE) > _LABEL_POS_CACHE_MAX:
        _LABEL_POS_CACHE.popitem(last=False)
    return [pos.copy() for pos in result]
