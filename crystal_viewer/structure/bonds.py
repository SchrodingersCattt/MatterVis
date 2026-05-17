from __future__ import annotations

import numpy as np

from ..disorder import _disorder_group_id
from .geometry import _nearest_pbc_cart, bond_vector_mic
from ..palette import cov_r

def bonds_conflict(ai, aj):
    """
    Return True if ai and aj are in conflicting disorder groups
    (same assembly, different group — like PART 1 vs PART 2 in SHELX).
    """
    gi = _disorder_group_id(ai)
    gj = _disorder_group_id(aj)
    if gi is None or gj is None:
        return False
    da_i, dg_i = gi
    da_j, dg_j = gj
    if da_i in ('.', '?', '') and da_j in ('.', '?', ''):
        return dg_i != dg_j
    return da_i == da_j and dg_i != dg_j

def _bond_cutoff(ai, aj):
    ei, ej = ai['elem'], aj['elem']
    if ei == 'H' and ej == 'H':
        return None
    if set([ei, ej]) == {'Cl', 'O'}:
        return 1.62
    if 'H' in [ei, ej]:
        return 1.15
    return cov_r(ei) + cov_r(ej) + 0.42


def _bond_allowed_by_table(ai, aj):
    partners_i = ai.get('_bond_partners', ())
    partners_j = aj.get('_bond_partners', ())
    has_table = bool(ai.get('_has_bond_table')) or bool(aj.get('_has_bond_table'))
    if not has_table:
        return True
    if partners_i and aj['label'] in partners_i:
        return True
    if partners_j and ai['label'] in partners_j:
        return True
    return False


def _bond_matches_table_distance(ai, aj, distance):
    has_table = bool(ai.get('_has_bond_table')) or bool(aj.get('_has_bond_table'))
    if not has_table:
        return True
    candidates = []
    for ref in ai.get('_bond_lengths', {}).get(aj['label'], ()):
        candidates.append(float(ref))
    for ref in aj.get('_bond_lengths', {}).get(ai['label'], ()):
        candidates.append(float(ref))
    if not candidates:
        return True
    tolerance = 0.18 if 'H' in (ai['elem'], aj['elem']) else 0.22
    return min(abs(distance - ref) for ref in candidates) <= tolerance

def _has_bond_table_atom(atom):
    return bool(atom.get('_has_bond_table'))


def _prune_duplicate_label_bond_candidates(atoms, candidates, tol=0.005):
    """Remove cross-bonds between duplicate disorder/symmetry alternatives.

    CIF bond tables describe site labels, not every symmetry-expanded copy. If
    a scene contains two alternatives with the same label (typical for PART or
    mirror-generated disorder), a naive label-table check allows every C2 copy
    to bond to every F4 copy. Keep only the nearest copy for each
    atom->partner-label relation. This is deliberately in the MatterVis bond
    layer so publication scripts cannot accidentally draw cross-disorder bonds.
    """
    if not candidates:
        return []

    label_counts = {}
    for atom in atoms:
        label = atom.get('label')
        label_counts[label] = label_counts.get(label, 0) + 1

    best = {}
    for i, j, d in candidates:
        ai, aj = atoms[i], atoms[j]
        duplicated = label_counts.get(ai.get('label'), 0) > 1 or label_counts.get(aj.get('label'), 0) > 1
        table_guided = _has_bond_table_atom(ai) or _has_bond_table_atom(aj)
        if not (duplicated and table_guided):
            continue
        for src, dst in ((i, j), (j, i)):
            key = (src, atoms[dst].get('label'))
            best[key] = min(float(d), best.get(key, np.inf))

    if not best:
        return candidates

    pruned = []
    for i, j, d in candidates:
        ai, aj = atoms[i], atoms[j]
        duplicated = label_counts.get(ai.get('label'), 0) > 1 or label_counts.get(aj.get('label'), 0) > 1
        table_guided = _has_bond_table_atom(ai) or _has_bond_table_atom(aj)
        if duplicated and table_guided:
            if float(d) > best.get((i, aj.get('label')), np.inf) + tol:
                continue
            if float(d) > best.get((j, ai.get('label')), np.inf) + tol:
                continue
        pruned.append((i, j, d))
    return pruned


# ── Bond finding ────────────────────────────────────────────────────────────
_BOND_KDTREE_THRESHOLD = 64
_BOND_MAX_CUTOFF = 5.0  # Å — wide enough for any covalent pair the table can return.


def _bond_candidate_pairs(atoms, M, cell):
    """Yield ``(i, j)`` index pairs with ``i < j`` whose Cartesian
    distance (or, when ``cell is not None``, **PBC** Cartesian distance)
    is plausibly within bond range.

    For ``len(atoms) >= _BOND_KDTREE_THRESHOLD`` we use
    ``cKDTree.query_pairs`` to prune the O(N^2) python loop down to
    O(N * neighbours). For smaller scenes the python loop is faster
    than constructing a KDTree, so we keep the legacy enumeration.

    PBC handling: when ``cell is not None`` and ``M`` is provided, the
    atom set is expanded with one image-replica per neighbour cell
    (3^3 - 1 = 26 ghosts per atom) before the KDTree query. Pairs that
    fall within the cutoff are then mapped back to the home index. This
    is necessary because a ring that crosses the cell boundary has
    bonds whose **raw cart** length spans the cell (8+ Å) but whose
    PBC-image length is normal covalent. Without ghost replication,
    ``_unwrapped_atoms_from_atoms`` cannot reassemble the ring and the
    user sees fragmented organic cations -- regression observed on the
    MPEP structure (P2_1/c, monoclinic) after the v1 KDTree pre-filter
    landed.
    """
    n = len(atoms)
    if n < _BOND_KDTREE_THRESHOLD:
        for i in range(n):
            for j in range(i + 1, n):
                yield (i, j)
        return
    try:
        from scipy.spatial import cKDTree
    except ImportError:
        for i in range(n):
            for j in range(i + 1, n):
                yield (i, j)
        return
    coords = np.asarray([a['cart'] for a in atoms], dtype=float)

    # No PBC requested -> plain non-periodic KDTree on raw cart coords.
    if cell is None or M is None:
        tree = cKDTree(coords)
        pairs = tree.query_pairs(r=_BOND_MAX_CUTOFF, output_type='ndarray')
        if pairs.size == 0:
            return
        for i, j in pairs.tolist():
            yield (int(i), int(j)) if i < j else (int(j), int(i))
        return

    # PBC path: expand the atom set with 26 image-replicas so that
    # cross-cell bonds become local in cart space.
    M_arr = np.asarray(M, dtype=float)
    a_vec = M_arr[:, 0]
    b_vec = M_arr[:, 1]
    c_vec = M_arr[:, 2]
    coord_chunks = [coords]
    orig_idx_chunks = [np.arange(n, dtype=int)]
    for da in (-1, 0, 1):
        for db in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if da == 0 and db == 0 and dc == 0:
                    continue
                offset = da * a_vec + db * b_vec + dc * c_vec
                coord_chunks.append(coords + offset)
                orig_idx_chunks.append(np.arange(n, dtype=int))
    all_coords = np.vstack(coord_chunks)
    all_orig = np.concatenate(orig_idx_chunks)

    tree = cKDTree(all_coords)
    pairs = tree.query_pairs(r=_BOND_MAX_CUTOFF, output_type='ndarray')
    if pairs.size == 0:
        return
    seen: set[tuple[int, int]] = set()
    for i, j in pairs.tolist():
        oi = int(all_orig[i])
        oj = int(all_orig[j])
        if oi == oj:
            continue  # ghost-of-self at zero distance is not a bond candidate
        a_idx, b_idx = (oi, oj) if oi < oj else (oj, oi)
        if (a_idx, b_idx) in seen:
            continue
        seen.add((a_idx, b_idx))
        yield (a_idx, b_idx)


def find_bonds(atoms, M=None, cell=None):
    """Find bonds, excluding cross-disorder-group and cross-alternative bonds.

    For large atom counts (~64+ atoms, see ``_BOND_KDTREE_THRESHOLD``)
    the candidate set is pre-filtered with a Cartesian KDTree on
    ``cart`` coordinates. The slow per-pair table check then only runs
    on plausible neighbours. This drops the cost from O(N^2) to
    O(N * k) where k ~ 10-20 covalent neighbours per atom -- the
    difference between 1 second and 1 minute on a 1500-atom supercell.
    """
    candidates = []
    for i, j in _bond_candidate_pairs(atoms, M=M, cell=cell):
        ai = atoms[i]
        aj = atoms[j]
        if not _bond_allowed_by_table(ai, aj):
            continue
        if bonds_conflict(ai, aj):
            continue
        cutoff = _bond_cutoff(ai, aj)
        if cutoff is None:
            continue
        if cell is not None:
            near = _nearest_pbc_cart(ai['cart'], aj['cart'], cell)
            d = np.linalg.norm(near - ai['cart'])
        elif M is None:
            d = np.linalg.norm(ai['cart'] - aj['cart'])
        else:
            d = np.linalg.norm(bond_vector_mic(ai, aj, M, search_radius=1)[0])
        if not _bond_matches_table_distance(ai, aj, d):
            continue
        if d < cutoff:
            candidates.append((i, j, float(d)))

    return [(i, j) for i, j, _ in _prune_duplicate_label_bond_candidates(atoms, candidates)]


__all__ = [name for name in globals() if not name.startswith("__")]
