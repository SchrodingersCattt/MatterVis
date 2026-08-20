from __future__ import annotations

import numpy as np

from .bonds import find_bonds
from .geometry import nearest_lattice_shift_frac

# ── Cluster atoms ────────────────────────────────────────────────────────────
def cluster_atoms(atoms, M=None, cell=None, bonds=None):
    n = len(atoms)
    parent = list(range(n))
    def find(x):
        while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(x, y):
        px, py = find(x), find(y)
        if px != py: parent[px] = py
    if bonds is None:
        bonds = find_bonds(atoms, M=M, cell=cell)
    for i, j in bonds:
        union(i, j)
    clusters = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)
    return clusters

# ── PBC nearest image helper ─────────────────────────────────────────────────
def _pbc_nearest(centroid, ref_point, a_vec, b_vec, c_vec):
    best_dist = np.inf
    best_offset = np.zeros(3)
    for na in range(-2, 3):
        for nb in range(-2, 3):
            for nc in range(-2, 3):
                offset = na*a_vec + nb*b_vec + nc*c_vec
                d = np.linalg.norm(centroid + offset - ref_point)
                if d < best_dist:
                    best_dist = d
                    best_offset = offset
    return best_dist, best_offset

def _translate_cluster(atoms, idxs, offset):
    if np.linalg.norm(offset) < 1e-6:
        return
    for i in idxs:
        atoms[i] = dict(atoms[i])
        atoms[i]['cart'] = atoms[i]['cart'] + offset

def _translate_cluster_frac(atoms, idxs, shift_frac, M):
    shift_frac = np.array(shift_frac, dtype=float)
    if np.linalg.norm(shift_frac) < 1e-9:
        return
    shift_cart = M @ shift_frac
    for i in idxs:
        atoms[i] = dict(atoms[i])
        atoms[i]['frac'] = np.array(atoms[i]['frac'], dtype=float) + shift_frac
        atoms[i]['cart'] = atoms[i]['cart'] + shift_cart

def assemble_component_p1(atoms, idxs, bond_pairs, M):
    idxs = list(idxs)
    idx_set = set(idxs)
    adjacency = {i: [] for i in idxs}
    for i, j in bond_pairs:
        if i in idx_set and j in idx_set:
            adjacency[i].append(j)
            adjacency[j].append(i)
    shifts = {idxs[0]: np.zeros(3)}
    queue = [idxs[0]]
    while queue:
        i = queue.pop(0)
        for j in adjacency.get(i, []):
            delta_frac = np.array(atoms[j]['frac'], dtype=float) - np.array(atoms[i]['frac'], dtype=float)
            nearest_shift = nearest_lattice_shift_frac(delta_frac, M, search_radius=1)
            proposed = shifts[i] - nearest_shift
            if j not in shifts:
                shifts[j] = proposed
                queue.append(j)
    atoms_out = [dict(at) for at in atoms]
    for i in idxs:
        shift_frac = shifts.get(i, np.zeros(3))
        atoms_out[i]['frac'] = np.array(atoms[i]['frac'], dtype=float) + shift_frac
        atoms_out[i]['cart'] = M @ atoms_out[i]['frac']
    return atoms_out

def _cluster_attachment_cost(cluster_idxs, selected_idxs, atoms, M, shift_frac):
    shift_cart = M @ np.array(shift_frac, dtype=float)
    cluster_cart = np.array([atoms[i]['cart'] for i in cluster_idxs]) + shift_cart
    selected_cart = np.array([atoms[i]['cart'] for i in selected_idxs])
    if len(selected_cart) == 0:
        return 0.0
    dists = np.sqrt(((cluster_cart[:, None, :] - selected_cart[None, :, :]) ** 2).sum(axis=2)).ravel()
    nearest = np.sort(dists)
    k = nearest[:min(8, len(nearest))]
    overlap_pen = np.sum(np.clip(1.35 - nearest[:min(12, len(nearest))], 0.0, None) ** 2)
    return float(np.mean(k) + overlap_pen * 8.0)

def _best_cluster_shift_frac(cluster_idxs, selected_idxs, atoms, M, search_radius=2):
    best_cost = np.inf
    best_shift = np.zeros(3)
    for na in range(-search_radius, search_radius + 1):
        for nb in range(-search_radius, search_radius + 1):
            for nc in range(-search_radius, search_radius + 1):
                shift = np.array([na, nb, nc], dtype=float)
                cost = _cluster_attachment_cost(cluster_idxs, selected_idxs, atoms, M, shift)
                if cost < best_cost:
                    best_cost = cost
                    best_shift = shift
    return best_shift, best_cost

def _grow_local_environment(atoms, anchor_idxs, candidate_clusters, M, max_count):
    selected = list(anchor_idxs)
    remaining = list(candidate_clusters)
    chosen = []
    while remaining and len(chosen) < max_count:
        scored = []
        for root, idxs in remaining:
            shift_frac, cost = _best_cluster_shift_frac(idxs, selected, atoms, M, search_radius=2)
            scored.append((cost, root, idxs, shift_frac))
        scored.sort(key=lambda item: item[0])
        _, root, idxs, shift_frac = scored[0]
        _translate_cluster_frac(atoms, idxs, shift_frac, M)
        selected.extend(idxs)
        chosen.append((root, idxs))
        remaining = [(r, c) for r, c in remaining if r != root]
    return selected, chosen

# ── Select one formula unit ──────────────────────────────────────────────────

# Transition metals and common coordination-complex metals
_METAL_ELEMENTS = frozenset([
    "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd",
    "La", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
    "Ce", "Pr", "Nd", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er",
    "Tm", "Yb", "Lu", "Ac", "Th", "U", "Np", "Pu",
    "Al", "Ga", "In", "Tl", "Sn", "Pb", "Bi",
])


def select_formula_unit(atoms, M, cell):
    atoms = [dict(a) for a in atoms]
    bond_pairs = find_bonds(atoms, cell=cell)
    clusters = cluster_atoms(atoms, bonds=bond_pairs)
    for idxs in clusters.values():
        atoms = assemble_component_p1(atoms, idxs, bond_pairs, M)

    organic_clusters = {}
    anion_clusters = {}
    metal_clusters = {}
    for root, idxs in clusters.items():
        elems = set(atoms[i]['elem'] for i in idxs if atoms[i]['elem'] != 'H')
        has_metal = bool(elems & _METAL_ELEMENTS)
        if has_metal:
            metal_clusters[root] = idxs
        elif 'Cl' in elems or 'Br' in elems or 'I' in elems:
            anion_clusters[root] = idxs
        elif 'C' in elems or 'N' in elems:
            organic_clusters[root] = idxs

    if not organic_clusters and not metal_clusters:
        return atoms, list(range(len(atoms)))

    # Pick the anchor from the largest cluster (organic or metal)
    all_molecular = list(organic_clusters.items()) + list(metal_clusters.items())
    all_molecular.sort(key=lambda kv: len(kv[1]), reverse=True)
    anchor_root, anchor_idxs = all_molecular[0]
    anchor_size = len(anchor_idxs)
    anchor_labels = frozenset(atoms[i]['label'] for i in anchor_idxs)

    selected_org_idxs = list(anchor_idxs)

    # Grow additional molecular clusters (both organic and metal)
    if len(all_molecular) >= 2:
        preferred = []
        fallback = []
        for root, idxs in all_molecular[1:]:
            if len(idxs) < anchor_size * 0.25:
                continue
            clabels = frozenset(atoms[i]['label'] for i in idxs)
            item = (root, idxs)
            if clabels & anchor_labels:
                fallback.append(item)
            else:
                preferred.append(item)
        candidates = preferred if preferred else fallback
        if candidates:
            # Allow up to 3 additional molecular partners (covers structures
            # with separate cation + anion complex + counterion clusters)
            selected_org_idxs, chosen_org = _grow_local_environment(
                atoms, selected_org_idxs, candidates, M, max_count=3)

    selected_idxs = list(selected_org_idxs)
    anion_candidates = [(root, idxs) for root, idxs in anion_clusters.items() if len(idxs) >= 4]
    if len(anion_candidates) < 4:
        anion_candidates = list(anion_clusters.items())
    selected_idxs, _ = _grow_local_environment(
        atoms, selected_idxs, anion_candidates, M, max_count=min(4, len(anion_candidates)))

    return atoms, selected_idxs


__all__ = [name for name in globals() if not name.startswith("__")]
