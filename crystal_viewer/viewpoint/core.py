"""Core auto-view-direction logic.

Relocated from ``crystal_viewer.legacy.publication_view`` to provide a
clean, non-legacy import path and configurable weight system.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, List, Optional

import numpy as np

from ..structure.bonds import find_bonds
from ..structure.formula_unit import cluster_atoms, select_formula_unit
from ..structure.geometry import view_rotation
from ..style.disorder import is_major
from ..style.palette import cov_r

# ── Score weight profiles ────────────────────────────────────────────────────

VIEW_SCORE_WEIGHTS: Dict[str, Dict[str, float]] = {
    "default": {
        "organic_plane": 1.05,
        "organic_depth": 0.85,
        "aspect": 0.20,
        "robust_sep": 0.40,
        "close_contact": 1.15,
        "occlusion": 1.70,
        "cluster_crowding": 1.35,
        "elev_pen": 1.25,
    },
    "MPEP": {
        "organic_plane": 0.90,
        "organic_depth": 1.10,
        "close_contact": 1.35,
        "occlusion": 2.10,
        "cluster_crowding": 1.55,
    },
    "HPEP": {
        "organic_plane": 0.90,
        "organic_depth": 1.15,
        "close_contact": 1.25,
        "occlusion": 1.95,
        "cluster_crowding": 1.90,
    },
}

_REGISTERED_WEIGHTS: Dict[str, Dict[str, float]] = {}


def register_view_score_weights(name: str, weights: Dict[str, float]) -> None:
    """Register (or override) a named weight profile.

    Parameters
    ----------
    name
        Profile name. If it collides with a built-in, the registered
        version takes precedence.
    weights
        Partial or full dict of scoring weights. Missing keys will be
        filled from the ``default`` profile at resolve time.
    """
    _REGISTERED_WEIGHTS[name] = dict(weights)


def list_view_score_weights() -> List[str]:
    """Return sorted list of all available profile names (built-in + registered)."""
    return sorted(set(VIEW_SCORE_WEIGHTS) | set(_REGISTERED_WEIGHTS))


def _resolve_view_score_weights(
    name: Optional[str],
    overrides: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """Resolve final scoring weights.

    Priority: *overrides* kwarg > registered profile > built-in profile > default.
    """
    weights = dict(VIEW_SCORE_WEIGHTS["default"])
    # Layer built-in profile
    if name and name in VIEW_SCORE_WEIGHTS:
        weights.update(VIEW_SCORE_WEIGHTS[name])
    # Layer registered profile (takes precedence over built-in)
    if name and name in _REGISTERED_WEIGHTS:
        weights.update(_REGISTERED_WEIGHTS[name])
    # Layer explicit caller overrides (highest priority)
    if overrides:
        weights.update(overrides)
    return weights


# ── Internal helpers ─────────────────────────────────────────────────────────


def _sphere_view_grid(n_elev: int = 25, n_azim: int = 48) -> List[np.ndarray]:
    vecs: List[np.ndarray] = []
    for ie in range(n_elev):
        elev = np.radians(-75.0 + ie * (150.0 / max(n_elev - 1, 1)))
        cos_e = np.cos(elev)
        sin_e = np.sin(elev)
        for ia in range(n_azim):
            azim = np.radians(ia * 360.0 / n_azim)
            vecs.append(
                np.array([cos_e * np.cos(azim), cos_e * np.sin(azim), sin_e])
            )
    return vecs


def _pick_up_vector(view_vec, candidates):
    v = np.array(view_vec, dtype=float)
    v /= np.linalg.norm(v)
    best = None
    best_norm = -1.0
    for cand in candidates:
        c = np.array(cand, dtype=float)
        c_norm = np.linalg.norm(c)
        if c_norm < 1e-8:
            continue
        c /= c_norm
        screen_up = c - np.dot(c, v) * v
        screen_norm = np.linalg.norm(screen_up)
        if screen_norm > best_norm:
            best = screen_up / screen_norm if screen_norm > 1e-8 else None
            best_norm = screen_norm
    if best is not None:
        return best
    fallback = np.array([0.0, 0.0, 1.0])
    if abs(np.dot(fallback, v)) > 0.95:
        fallback = np.array([0.0, 1.0, 0.0])
    return fallback


def _classify_clusters(atoms):
    clusters = cluster_atoms(atoms)
    organic = []
    anion = []
    for idxs in clusters.values():
        elems = {atoms[i]["elem"] for i in idxs if atoms[i]["elem"] != "H"}
        if "Cl" in elems:
            anion.append(sorted(idxs))
        elif "C" in elems or "N" in elems:
            organic.append(sorted(idxs))
    return organic, anion


def _build_pair_exclusions(n_atoms, bond_pairs):
    adjacency = [set() for _ in range(n_atoms)]
    excluded = set()
    for i, j in bond_pairs:
        if i > j:
            i, j = j, i
        excluded.add((i, j))
        adjacency[i].add(j)
        adjacency[j].add(i)
    for i in range(n_atoms):
        for mid in adjacency[i]:
            for j in adjacency[mid]:
                if j == i:
                    continue
                a, b = sorted((i, j))
                excluded.add((a, b))
    return excluded


def _cluster_shape_p80(pts, cluster_radii):
    """Return ``(centroid, radial_p80)`` for a cluster's screen-space extent."""
    centroid = pts.mean(axis=0)
    radial = np.sqrt(((pts - centroid) ** 2).sum(axis=1)) + cluster_radii
    n = radial.size
    if n == 0:
        return centroid, 0.0
    if n == 1:
        return centroid, float(radial[0])
    sorted_r = np.sort(radial)
    rank = 0.8 * (n - 1)
    lo = int(np.floor(rank))
    hi = int(np.ceil(rank))
    frac = rank - lo
    if lo == hi:
        return centroid, float(sorted_r[lo])
    return centroid, float(sorted_r[lo] * (1 - frac) + sorted_r[hi] * frac)


def _cluster_crowding_penalty(pts_2d, radii, org_clusters, anion_clusters):
    def cluster_shape(idxs):
        if not idxs:
            return None
        idx_arr = np.asarray(idxs, dtype=int)
        return _cluster_shape_p80(pts_2d[idx_arr], radii[idx_arr])

    penalty = 0.0
    org_shapes = [cluster_shape(idxs) for idxs in org_clusters if idxs]
    ani_shapes = [cluster_shape(idxs) for idxs in anion_clusters if idxs]
    org_shapes = [item for item in org_shapes if item is not None]
    ani_shapes = [item for item in ani_shapes if item is not None]

    for oc, orad in org_shapes:
        for ac, arad in ani_shapes:
            dist = np.linalg.norm(oc - ac)
            thresh = 0.90 * (orad + arad)
            if dist < thresh:
                penalty += ((thresh - dist) / max(thresh, 1e-6)) ** 2
    for i in range(len(ani_shapes)):
        for j in range(i + 1, len(ani_shapes)):
            ci, ri = ani_shapes[i]
            cj, rj = ani_shapes[j]
            dist = np.linalg.norm(ci - cj)
            thresh = 0.72 * (ri + rj)
            if dist < thresh:
                penalty += 0.55 * ((thresh - dist) / max(thresh, 1e-6)) ** 2
    return penalty


def _view_plane_basis(view_vec):
    v = np.array(view_vec, dtype=float)
    v /= np.linalg.norm(v)
    anchor = (
        np.array([0.0, 0.0, 1.0]) if abs(v[2]) < 0.9 else np.array([0.0, 1.0, 0.0])
    )
    ex = np.cross(anchor, v)
    ex /= np.linalg.norm(ex)
    ey = np.cross(v, ex)
    ey /= np.linalg.norm(ey)
    return ex, ey


def _perturb_view(view_vec, dx_deg, dy_deg):
    ex, ey = _view_plane_basis(view_vec)
    v = np.array(view_vec, dtype=float)
    v /= np.linalg.norm(v)
    candidate = v + np.tan(np.radians(dx_deg)) * ex + np.tan(np.radians(dy_deg)) * ey
    candidate /= np.linalg.norm(candidate)
    return candidate


def _build_pair_weight_matrix(n, org_pos, anion_pos):
    """Precompute per-pair occlusion-penalty weights as an (N, N) matrix."""
    is_org = np.zeros(n, dtype=bool)
    is_ani = np.zeros(n, dtype=bool)
    if org_pos:
        is_org[np.asarray(list(org_pos), dtype=int)] = True
    if anion_pos:
        is_ani[np.asarray(list(anion_pos), dtype=int)] = True
    i_org = is_org[:, None]
    j_org = is_org[None, :]
    i_ani = is_ani[:, None]
    j_ani = is_ani[None, :]
    both_org = i_org & j_org
    org_ani_cross = (i_org & j_ani) | (j_org & i_ani)
    both_ani = i_ani & j_ani
    return np.where(
        both_org,
        1.25,
        np.where(
            org_ani_cross,
            1.40,
            np.where(both_ani, 0.90, 1.00),
        ),
    )


def _build_excluded_mask(n, excluded_pairs):
    """Precompute symmetric (N, N) bool mask of bond-excluded pairs."""
    mask = np.zeros((n, n), dtype=bool)
    if excluded_pairs:
        idx = np.array(list(excluded_pairs), dtype=int)
        mask[idx[:, 0], idx[:, 1]] = True
        mask[idx[:, 1], idx[:, 0]] = True
    return mask


def _score_auto_view(
    coords,
    radii,
    org_pos,
    anion_pos,
    org_clusters,
    anion_clusters,
    excluded_pairs,
    weights,
    view_vec,
    pair_weight_matrix=None,
    excluded_mask=None,
):
    R = view_rotation(view_vec)
    sx = coords @ R[0]
    sy = coords @ R[1]
    sz = coords @ R[2]
    pts_2d = np.stack([sx, sy], axis=1)

    org_idx = np.array(org_pos, dtype=int)
    org_2d = pts_2d[org_idx]
    org_center = org_2d.mean(axis=0)
    org_cov = (
        np.cov((org_2d - org_center).T) if len(org_2d) > 2 else np.eye(2) * 1e-4
    )
    eigvals = np.clip(np.linalg.eigvalsh(org_cov), 1e-8, None)
    organic_plane = float(np.sqrt(eigvals[0] * eigvals[1]))

    sz_org = np.sort(sz[org_idx])
    n_org = sz_org.size
    if n_org < 2:
        org_depth = 0.0
    else:
        rank_lo = 0.10 * (n_org - 1)
        rank_hi = 0.90 * (n_org - 1)
        lo_lo = int(np.floor(rank_lo))
        lo_hi = int(np.ceil(rank_lo))
        hi_lo = int(np.floor(rank_hi))
        hi_hi = int(np.ceil(rank_hi))
        p10 = sz_org[lo_lo] + (rank_lo - lo_lo) * (sz_org[lo_hi] - sz_org[lo_lo])
        p90 = sz_org[hi_lo] + (rank_hi - hi_lo) * (sz_org[hi_hi] - sz_org[hi_lo])
        org_depth = float(p90 - p10)

    all_w = sx.max() - sx.min()
    all_h = sy.max() - sy.min()
    asp = min(all_w, all_h) / max(all_w, all_h) if max(all_w, all_h) > 1e-6 else 0.0

    diffs = pts_2d[:, None, :] - pts_2d[None, :, :]
    dists = np.sqrt((diffs**2).sum(axis=2) + 1e-12)
    dz = np.abs(sz[:, None] - sz[None, :])
    thresh = 0.78 * (radii[:, None] + radii[None, :])

    n = len(coords)
    if pair_weight_matrix is None:
        pair_weight_matrix = _build_pair_weight_matrix(n, org_pos, anion_pos)
    if excluded_mask is None:
        excluded_mask = _build_excluded_mask(n, excluded_pairs)

    upper = np.triu(np.ones((n, n), dtype=bool), k=1)
    overlap_mat = thresh - dists
    safe_thresh = np.maximum(thresh, 1e-6)
    active = upper & (~excluded_mask) & (overlap_mat > 0)
    if active.any():
        depth_scale = np.clip(1.0 - dz / safe_thresh, 0.0, 1.0)
        contrib = (
            pair_weight_matrix
            * ((overlap_mat / safe_thresh) ** 2)
            * (1.0 + 1.6 * depth_scale)
        )
        occlusion = float(contrib[active].sum())
    else:
        occlusion = 0.0

    robust_sep = 0.0
    close_contact = 0.0
    if anion_pos:
        anion_idx = np.array(anion_pos, dtype=int)
        org_ani_diffs = org_2d[:, None, :] - pts_2d[anion_idx][None, :, :]
        org_ani_dists = np.sqrt((org_ani_diffs**2).sum(axis=2) + 1e-12)
        org_thresh = 0.88 * (radii[org_idx][:, None] + radii[anion_idx][None, :])
        flat_dists = np.sort(org_ani_dists, axis=None)
        robust_sep = float(np.mean(flat_dists[: min(6, len(flat_dists))]))
        overlap_oa = np.clip(org_thresh - org_ani_dists, 0.0, None)
        depth_scale = np.clip(
            1.0
            - np.abs(sz[org_idx][:, None] - sz[anion_idx][None, :])
            / np.maximum(org_thresh, 1e-6),
            0.0,
            1.0,
        )
        close_contact = float(
            np.sum(
                (overlap_oa / np.maximum(org_thresh, 1e-6)) * (1.0 + 1.2 * depth_scale)
            )
        )

    cluster_crowding = _cluster_crowding_penalty(
        pts_2d, radii, org_clusters, anion_clusters
    )

    v = np.array(view_vec, dtype=float)
    v /= np.linalg.norm(v)
    elev_deg = np.degrees(np.arcsin(np.clip(v[2], -1, 1)))
    elev_pen = max(0.0, (abs(elev_deg) - 55.0) / 25.0)

    score = (
        organic_plane * weights["organic_plane"]
        + org_depth * weights["organic_depth"]
        + robust_sep * weights["robust_sep"]
        + asp * weights["aspect"]
        - close_contact * weights["close_contact"]
        - occlusion * weights["occlusion"]
        - cluster_crowding * weights["cluster_crowding"]
        - elev_pen * weights["elev_pen"]
    )
    return score


# ── Cache ────────────────────────────────────────────────────────────────────

_AUTO_VIEW_CACHE: "OrderedDict[tuple, tuple[np.ndarray, np.ndarray]]" = OrderedDict()
_AUTO_VIEW_CACHE_MAX = 64


def _auto_view_cache_key(atoms, M, cell, compound_name, weights) -> tuple:
    """Stable hash that includes explicit weight overrides."""
    M_arr = np.asarray(M, dtype=float)
    pos_bytes = np.round(
        np.array([atom["cart"] for atom in atoms], dtype=float), 4
    ).tobytes()
    labels = tuple(str(atom.get("label") or atom.get("elem")) for atom in atoms)
    elems = tuple(str(atom.get("elem")) for atom in atoms)
    cell_key = None
    if cell is not None:
        try:
            cell_key = (
                round(float(cell.a), 5),
                round(float(cell.b), 5),
                round(float(cell.c), 5),
                round(float(cell.alpha), 4),
                round(float(cell.beta), 4),
                round(float(cell.gamma), 4),
            )
        except AttributeError:
            cell_key = tuple(
                np.round(np.asarray(cell, dtype=float), 5).flatten().tolist()
            )
    # Include weights in the key so different overrides produce different
    # cached entries.
    weights_key = tuple(sorted(weights.items())) if weights else ()
    return (
        len(atoms),
        pos_bytes,
        labels,
        elems,
        np.round(M_arr, 5).tobytes(),
        cell_key,
        str(compound_name or ""),
        weights_key,
    )


# ── Public entry point ───────────────────────────────────────────────────────


def auto_view_dir(
    atoms,
    M,
    cell,
    compound_name: Optional[str] = None,
    weights: Optional[Dict[str, float]] = None,
):
    """Pick the best viewing direction for a crystal structure.

    Parameters
    ----------
    atoms
        Sequence of atom dicts with at least ``cart``, ``elem``, and ``label``.
    M
        Fractional-to-Cartesian matrix (3×3, any orientation convention).
    cell
        Unit cell object (attributes a, b, c, alpha, beta, gamma) or 3×3
        array.
    compound_name
        Optional name for looking up a pre-registered weight profile.
    weights
        Optional dict of scoring-weight overrides. Keys are any subset of:
        ``organic_plane``, ``organic_depth``, ``aspect``, ``robust_sep``,
        ``close_contact``, ``occlusion``, ``cluster_crowding``, ``elev_pen``.
        Supplied values override both the default and the compound profile.

    Returns
    -------
    tuple[ndarray, ndarray]
        ``(view_direction, up_vector)`` — unit vectors.
    """
    resolved_weights = _resolve_view_score_weights(compound_name, overrides=weights)
    cache_key = _auto_view_cache_key(atoms, M, cell, compound_name, weights)
    cached = _AUTO_VIEW_CACHE.get(cache_key)
    if cached is not None:
        _AUTO_VIEW_CACHE.move_to_end(cache_key)
        view_dir, up = cached
        return view_dir.copy(), up.copy()

    atoms_copy = [dict(a) for a in atoms]
    try:
        atoms_sel, sel_idxs = select_formula_unit(atoms_copy, M, cell)
        sel_atoms = [atoms_sel[i] for i in sel_idxs]
    except Exception:
        view_dir = np.array([0.174, 0.985, 0.000])
        up = np.array([0.0, 0.0, 1.0])
        _AUTO_VIEW_CACHE[cache_key] = (view_dir, up)
        if len(_AUTO_VIEW_CACHE) > _AUTO_VIEW_CACHE_MAX:
            _AUTO_VIEW_CACHE.popitem(last=False)
        return view_dir.copy(), up.copy()

    valid_atoms = [at for at in sel_atoms if at["elem"] != "H" and is_major(at)]
    if len(valid_atoms) < 3:
        view_dir = np.array([0.174, 0.985, 0.000])
        up = np.array([0.0, 0.0, 1.0])
        _AUTO_VIEW_CACHE[cache_key] = (view_dir, up)
        if len(_AUTO_VIEW_CACHE) > _AUTO_VIEW_CACHE_MAX:
            _AUTO_VIEW_CACHE.popitem(last=False)
        return view_dir.copy(), up.copy()

    org_clusters, anion_clusters = _classify_clusters(valid_atoms)
    if not org_clusters:
        org_clusters = [list(range(len(valid_atoms)))]
    org_pos = sorted({idx for group in org_clusters for idx in group})
    anion_pos = sorted({idx for group in anion_clusters for idx in group})

    coords = np.array([at["cart"] for at in valid_atoms], dtype=float)
    radii = np.array([cov_r(at["elem"]) for at in valid_atoms], dtype=float)
    org_coords = coords[np.array(org_pos)]
    centered = org_coords - org_coords.mean(axis=0)
    excluded_pairs = _build_pair_exclusions(len(valid_atoms), find_bonds(valid_atoms))

    try:
        _, _, vt = np.linalg.svd(centered, full_matrices=False)
        pca_axes = [vt[0], vt[1], vt[2]]
    except np.linalg.LinAlgError:
        pca_axes = [
            np.array([1.0, 0.0, 0.0]),
            np.array([0.0, 1.0, 0.0]),
            np.array([0.0, 0.0, 1.0]),
        ]

    candidates: List[np.ndarray] = []
    seen: set = set()

    def add_candidate(vec):
        v = np.array(vec, dtype=float)
        n = np.linalg.norm(v)
        if n < 1e-8:
            return
        v /= n
        key = tuple(np.round(v, 4))
        if key not in seen:
            seen.add(key)
            candidates.append(v)

    for axis in pca_axes:
        add_candidate(axis)
        add_candidate(-axis)
    for vec in _sphere_view_grid(n_elev=19, n_azim=36):
        add_candidate(vec)

    n_atoms_view = len(coords)
    pair_weight_matrix = _build_pair_weight_matrix(n_atoms_view, org_pos, anion_pos)
    excluded_mask = _build_excluded_mask(n_atoms_view, excluded_pairs)

    ranked = []
    for view_vec in candidates:
        score = _score_auto_view(
            coords,
            radii,
            org_pos,
            anion_pos,
            org_clusters,
            anion_clusters,
            excluded_pairs,
            resolved_weights,
            view_vec,
            pair_weight_matrix=pair_weight_matrix,
            excluded_mask=excluded_mask,
        )
        ranked.append((score, view_vec))
    ranked.sort(key=lambda item: item[0], reverse=True)

    fine_candidates = []
    fine_seen: set = set()
    for _, base_vec in ranked[:8]:
        for dx_deg in (-14, -8, -4, 0, 4, 8, 14):
            for dy_deg in (-14, -8, -4, 0, 4, 8, 14):
                cand = _perturb_view(base_vec, dx_deg, dy_deg)
                key = tuple(np.round(cand, 5))
                if key in fine_seen:
                    continue
                fine_seen.add(key)
                fine_candidates.append(cand)

    best_score = ranked[0][0]
    best_view = ranked[0][1]
    for view_vec in fine_candidates:
        score = _score_auto_view(
            coords,
            radii,
            org_pos,
            anion_pos,
            org_clusters,
            anion_clusters,
            excluded_pairs,
            resolved_weights,
            view_vec,
            pair_weight_matrix=pair_weight_matrix,
            excluded_mask=excluded_mask,
        )
        if score > best_score:
            best_score = score
            best_view = view_vec

    up_vec = _pick_up_vector(
        best_view,
        pca_axes
        + [
            np.array([0.0, 0.0, 1.0]),
            np.array([0.0, 1.0, 0.0]),
            np.array([1.0, 0.0, 0.0]),
        ],
    )
    _AUTO_VIEW_CACHE[cache_key] = (best_view, up_vec)
    if len(_AUTO_VIEW_CACHE) > _AUTO_VIEW_CACHE_MAX:
        _AUTO_VIEW_CACHE.popitem(last=False)
    return best_view.copy(), up_vec.copy()
