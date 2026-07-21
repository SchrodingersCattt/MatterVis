from __future__ import annotations

import numpy as np

from ..style.disorder import _disorder_group_id
from .geometry import _nearest_pbc_cart, bond_vector_mic
from ..style.palette import cov_r

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


# Track whether we already warned about incomplete H bond tables.
_WARNED_H_BOND_TABLE: set = set()


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
    # ── H fallback ──────────────────────────────────────────────────
    # Many CIFs have _geom_bond tables that omit H bonds entirely
    # (riding-model H, constrained solvent H, etc.). When at least one
    # atom is H and it has no bond_partners listed, fall back to the
    # distance criterion rather than silently blocking all H bonds.
    if 'H' in (ai['elem'], aj['elem']):
        h_atom = ai if ai['elem'] == 'H' else aj
        if not h_atom.get('_bond_partners'):
            import warnings
            key = id(h_atom.get('_has_bond_table'))
            if key not in _WARNED_H_BOND_TABLE:
                _WARNED_H_BOND_TABLE.add(key)
                warnings.warn(
                    "CIF _geom_bond table present but H atom "
                    f"{h_atom.get('label', '?')} has no bond partners listed; "
                    "falling back to distance-based H bonding.",
                    stacklevel=2,
                )
            return True
    # ── Disorder fallback ───────────────────────────────────────────
    # Symmetry-expanded disorder atoms (labels like "C6B?") are absent
    # from the CIF _geom_bond table which only lists ASU labels ("C6A").
    # Fall back to distance-based bonding when BOTH atoms carry a
    # synthetic disorder group but neither lists the other as a partner.
    if ai.get('_mv_auto_disorder_group') and aj.get('_mv_auto_disorder_group'):
        if not partners_i and not partners_j:
            return True
    # Allow bonding within the same disorder orientation regardless of
    # bond table — atoms in the same PART must always be able to bond.
    dg_i = ai.get('_mv_auto_disorder_group')
    dg_j = aj.get('_mv_auto_disorder_group')
    if dg_i and dg_j and dg_i == dg_j:
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
_PBC_SELECTIVE_THRESHOLD = 500  # Above this, use face-selective ghost expansion.


def _effective_cutoff(atoms):
    """Compute the tightest cutoff that still covers all possible covalent
    pairs among the element set present in *atoms*.

    Returns at most ``_BOND_MAX_CUTOFF``.  For typical organic crystals
    this is ~3.2 Å (C–I + tolerance), reducing KDTree pair counts 3–5×.
    """
    elems = {a['elem'] for a in atoms}
    if not elems:
        return _BOND_MAX_CUTOFF
    radii = []
    for e in elems:
        try:
            radii.append(cov_r(e))
        except Exception:
            return _BOND_MAX_CUTOFF
    max_sum = radii[-1] + radii[-1]  # placeholder
    radii.sort(reverse=True)
    max_sum = radii[0] + (radii[1] if len(radii) > 1 else radii[0])
    cutoff = max_sum + 0.42  # same tolerance as _bond_cutoff
    return min(cutoff, _BOND_MAX_CUTOFF)


def _pbc_pairs_full(coords, n, M_arr, cutoff):
    """Full 27× ghost expansion — fast for N < _PBC_SELECTIVE_THRESHOLD."""
    from scipy.spatial import cKDTree

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
    pairs = tree.query_pairs(r=cutoff, output_type='ndarray')
    if pairs.size == 0:
        return
    # Vectorized deduplication — avoid Python iteration over millions of pairs.
    mapped = all_orig[pairs]  # shape (K, 2)
    # Drop self-pairs (ghost of same atom)
    mask = mapped[:, 0] != mapped[:, 1]
    mapped = mapped[mask]
    if mapped.size == 0:
        return
    # Canonical ordering (i < j)
    sorted_pairs = np.sort(mapped, axis=1)
    unique_pairs = np.unique(sorted_pairs, axis=0)
    for i, j in unique_pairs:
        yield (int(i), int(j))


def _pbc_pairs_selective(coords, atoms, n, M_arr, cutoff):
    """Face-selective ghost expansion for large structures (N >= 500).

    Only atoms near a cell face (within ``cutoff`` in Cartesian distance
    of the boundary) are replicated to the adjacent image. Interior
    atoms already have all bonded neighbours in the home cell.

    This reduces the ghost count from 26×N to 26×N_face where N_face
    is typically 5–20% of N for large unit cells, making KDTree
    construction and query_pairs tractable for 5000+ atom structures.
    """
    from scipy.spatial import cKDTree

    a_vec = M_arr[:, 0]
    b_vec = M_arr[:, 1]
    c_vec = M_arr[:, 2]
    # Compute fractional coords for face-proximity test.
    # frac = cart @ inv(M).T  (M columns are lattice vectors)
    M_inv_T = np.linalg.inv(M_arr).T
    fracs = coords @ M_inv_T  # shape (n, 3)

    # For each lattice direction, compute the Cartesian "skin depth"
    # as cutoff / |lattice_vector_component perpendicular to the face|.
    # Approximation: use cutoff / lattice_length along that axis which
    # is a safe upper bound in fractional units.
    lat_lengths = np.array([
        np.linalg.norm(a_vec),
        np.linalg.norm(b_vec),
        np.linalg.norm(c_vec),
    ])
    frac_skin = cutoff / lat_lengths  # fractional depth near each face

    coord_chunks = [coords]
    orig_idx_chunks = [np.arange(n, dtype=int)]
    indices_all = np.arange(n, dtype=int)

    for da in (-1, 0, 1):
        for db in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if da == 0 and db == 0 and dc == 0:
                    continue
                # For offset (da, db, dc), an atom needs replication if
                # it is near the face that this offset would bring a
                # neighbour FROM. E.g. offset da=+1 brings images from
                # the +a side, so atoms near frac_a ≈ 0 need that ghost.
                mask = np.ones(n, dtype=bool)
                for axis, d in enumerate((da, db, dc)):
                    if d == 0:
                        continue
                    f = fracs[:, axis]
                    skin = frac_skin[axis]
                    if d == -1:
                        # Ghost from -a: atoms near frac ≈ 1 need it
                        mask &= (f > 1.0 - skin)
                    else:
                        # Ghost from +a: atoms near frac ≈ 0 need it
                        mask &= (f < skin)
                selected = indices_all[mask]
                if selected.size == 0:
                    continue
                offset = da * a_vec + db * b_vec + dc * c_vec
                coord_chunks.append(coords[selected] + offset)
                orig_idx_chunks.append(selected)

    all_coords = np.vstack(coord_chunks)
    all_orig = np.concatenate(orig_idx_chunks)

    tree = cKDTree(all_coords)
    pairs = tree.query_pairs(r=cutoff, output_type='ndarray')
    if pairs.size == 0:
        return
    # Vectorized deduplication
    mapped = all_orig[pairs]
    mask = mapped[:, 0] != mapped[:, 1]
    mapped = mapped[mask]
    if mapped.size == 0:
        return
    sorted_pairs = np.sort(mapped, axis=1)
    unique_pairs = np.unique(sorted_pairs, axis=0)
    for i, j in unique_pairs:
        yield (int(i), int(j))


def _bond_candidate_pairs(atoms, M, cell):
    """Yield ``(i, j)`` index pairs with ``i < j`` whose Cartesian
    distance (or, when ``cell is not None``, **PBC** Cartesian distance)
    is plausibly within bond range.

    For ``len(atoms) >= _BOND_KDTREE_THRESHOLD`` we use
    ``cKDTree.query_pairs`` to prune the O(N^2) python loop down to
    O(N * neighbours). For smaller scenes the python loop is faster
    than constructing a KDTree, so we keep the legacy enumeration.

    PBC handling: when ``cell is not None`` and ``M`` is provided, the
    atom set is expanded with ghost-image replicas so that cross-cell
    bonds become local in Cartesian space.

    For small structures (N < 500) the full 3^3 - 1 = 26 ghost images
    are used. For large structures (N >= 500) a face-selective strategy
    replicates only atoms near cell boundaries, reducing ghost count
    from 26×N to ~26×N_face where N_face << N.
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
    cutoff = _effective_cutoff(atoms)

    # No PBC requested -> plain non-periodic KDTree on raw cart coords.
    if cell is None or M is None:
        tree = cKDTree(coords)
        pairs = tree.query_pairs(r=cutoff, output_type='ndarray')
        if pairs.size == 0:
            return
        for i, j in pairs.tolist():
            yield (int(i), int(j)) if i < j else (int(j), int(i))
        return

    # PBC path — choose strategy based on atom count.
    M_arr = np.asarray(M, dtype=float)
    if n < _PBC_SELECTIVE_THRESHOLD:
        yield from _pbc_pairs_full(coords, n, M_arr, cutoff)
    else:
        yield from _pbc_pairs_selective(coords, atoms, n, M_arr, cutoff)


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
