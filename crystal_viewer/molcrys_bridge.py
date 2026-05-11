"""Adapter between MatterVis raw atom dicts and MolCrysKit's molecule
stoichiometry pipeline.

MatterVis carries each atom as a Python dict (``elem``, ``cart``,
``frac``, ``label``, ``occ``, ``da``, ``dg``, ...) so the rest of the
renderer can read every per-atom field directly.  MolCrysKit operates
on ASE :class:`~ase.Atoms` objects with extra disorder metadata
arrays.  This module translates between the two so the formula-unit
picker and the fragment legend can lean on MolCrysKit's
:class:`StoichiometryAnalyzer` (graph-isomorphism based species ID +
GCD-derived per-FU counts) instead of MatterVis's old hand-rolled
heuristics.
"""

from __future__ import annotations

import copy

import numpy as np


def _require_molcryskit():
    """Import MolCrysKit lazily and surface a clean error if missing."""
    try:
        from ase import Atoms
        from ase.neighborlist import neighbor_list
        import networkx as nx

        from molcrys_kit.structures.crystal import MolecularCrystal
        from molcrys_kit.structures.molecule import CrystalMolecule
        from molcrys_kit.analysis.stoichiometry import StoichiometryAnalyzer
        from molcrys_kit.analysis.interactions import get_bonding_threshold
        from .molcrys_compat import unwrap_positions_along_bonds
        from molcrys_kit.constants import (
            get_atomic_radius,
            has_atomic_radius,
            is_metal_element,
            DEFAULT_NEIGHBOR_CUTOFF,
        )
        from molcrys_kit.constants.config import (
            KEY_OCCUPANCY,
            KEY_DISORDER_GROUP,
            KEY_ASSEMBLY,
            KEY_LABEL,
        )
    except ImportError as exc:
        raise ImportError(
            "molcrys-kit is required for the formula_unit display mode. "
            "Install it with `pip install molcrys-kit` (it is listed in "
            "MatterVis's requirements.txt)."
        ) from exc

    return {
        "Atoms": Atoms,
        "neighbor_list": neighbor_list,
        "nx": nx,
        "MolecularCrystal": MolecularCrystal,
        "CrystalMolecule": CrystalMolecule,
        "StoichiometryAnalyzer": StoichiometryAnalyzer,
        "unwrap_positions_along_bonds": unwrap_positions_along_bonds,
        "get_bonding_threshold": get_bonding_threshold,
        "get_atomic_radius": get_atomic_radius,
        "has_atomic_radius": has_atomic_radius,
        "is_metal_element": is_metal_element,
        "DEFAULT_NEIGHBOR_CUTOFF": DEFAULT_NEIGHBOR_CUTOFF,
        "KEY_OCCUPANCY": KEY_OCCUPANCY,
        "KEY_DISORDER_GROUP": KEY_DISORDER_GROUP,
        "KEY_ASSEMBLY": KEY_ASSEMBLY,
        "KEY_LABEL": KEY_LABEL,
    }


def _ase_atoms_from_raw(raw_atoms, M, mk):
    """Build an index-aligned ASE Atoms with MolCrysKit disorder arrays.

    MatterVis stores the lattice as a 3x3 matrix whose **columns** are
    the a, b, c vectors; ASE expects them as **rows**, so we transpose.
    """
    symbols = [atom["elem"] for atom in raw_atoms]
    positions = np.array([atom["cart"] for atom in raw_atoms], dtype=float)
    cell = np.asarray(M, dtype=float).T

    atoms = mk["Atoms"](
        symbols=symbols,
        positions=positions,
        cell=cell,
        pbc=True,
    )
    n = len(raw_atoms)

    occ = np.empty(n, dtype=float)
    for i, atom in enumerate(raw_atoms):
        try:
            occ[i] = float(atom.get("occ", 1.0))
        except (TypeError, ValueError):
            occ[i] = 1.0

    dg = np.zeros(n, dtype=int)
    for i, atom in enumerate(raw_atoms):
        value = str(atom.get("dg", "") or "").strip()
        if value in ("", ".", "?"):
            continue
        try:
            dg[i] = int(float(value))
        except (TypeError, ValueError):
            dg[i] = 0

    da = np.array([
        (str(atom.get("da", "") or "").strip()) for atom in raw_atoms
    ])
    da = np.array([("" if v in (".", "?") else v) for v in da])

    label = np.array([
        atom.get("label") or atom["elem"] for atom in raw_atoms
    ])

    atoms.set_array(mk["KEY_OCCUPANCY"], occ)
    atoms.set_array(mk["KEY_DISORDER_GROUP"], dg)
    atoms.set_array(mk["KEY_ASSEMBLY"], da)
    atoms.set_array(mk["KEY_LABEL"], label)
    return atoms


def _is_minor_atom(atom) -> bool:
    """SHELX-aware "is this a minor disorder image?" check.

    Mirrors :func:`crystal_viewer.legacy.plot_crystal.is_minor` without
    importing the legacy module (we don't want molcrys_bridge to
    depend on the legacy renderer).
    """
    if "_is_minor" in atom:
        return bool(atom["_is_minor"])
    dg = str(atom.get("dg") or "").strip()
    if dg == "2":
        return True
    if dg.startswith("-") and dg not in ("-",):
        return True
    if atom.get("_is_major"):
        return False
    da = str(atom.get("da") or "").strip()
    if dg in (".", "?", "") and da in (".", "?", ""):
        try:
            occ = float(atom.get("occ", 1.0))
        except (TypeError, ValueError):
            occ = 1.0
        if occ < 0.5 - 1e-6:
            return True
    return False


def _minor_index_set(raw_atoms) -> set[int]:
    """Indices into ``raw_atoms`` that should be excluded from
    bond perception. After ``_tag_shelx_occupancy_disorder`` has run
    on a disordered CIF, ``_is_minor`` reflects the actual chosen
    optimal orientation -- atoms that didn't make the cut are tagged
    minor and won't bond into any molecule, restoring the correct
    one-orientation-per-disorder-site molecule grouping.
    """
    return {i for i, atom in enumerate(raw_atoms) if _is_minor_atom(atom)}


def _components_with_indices(ase_atoms, mk, *, exclude_indices=None):
    """Build the bond graph and return connected components keeping
    their original atom indices, plus the graph itself (for unwrap).

    Mirrors :func:`molcrys_kit.io.cif.identify_molecules` but without
    discarding the index map.

    ``exclude_indices`` (optional) is a set of atom indices that must
    not participate in any bond. They are still present in the graph
    as isolated nodes so downstream code can address them, but they
    will never appear in a returned multi-atom connected component.

    For SHELX-disordered CIFs the loader runs
    :func:`_tag_shelx_occupancy_disorder` *before* calling
    :func:`analyze`; that pass marks every alternative-orientation atom
    that didn't make MolCrysKit's optimal-replica choice with
    ``_is_minor=True``, and :func:`_minor_index_set` collects them so
    bond perception only sees one consistent set of atoms per disorder
    site. Without that guard, MolCrysKit's neighbour list would bond a
    major-orientation N to a minor-orientation N at 0.15 A apart and
    fuse two chemically distinct cations into one species (the SY
    ``C4H20N4`` "fused ethylenediamine" bug).
    """
    nx = mk["nx"]
    neighbor_list = mk["neighbor_list"]

    n = len(ase_atoms)
    graph = nx.Graph()
    graph.add_nodes_from(range(n))
    if n == 0:
        return [], graph

    excluded = set(int(x) for x in (exclude_indices or ()))

    symbols = ase_atoms.get_chemical_symbols()
    i_list, j_list, d_list, D_vectors = neighbor_list(
        "ijdD", ase_atoms, cutoff=mk["DEFAULT_NEIGHBOR_CUTOFF"]
    )

    for i, j, dist, D_vec in zip(i_list, j_list, d_list, D_vectors):
        if i >= j:
            continue
        ii, jj = int(i), int(j)
        if ii in excluded or jj in excluded:
            continue
        sym_i = symbols[i]
        sym_j = symbols[j]
        rad_i = mk["get_atomic_radius"](sym_i) if mk["has_atomic_radius"](sym_i) else 0.5
        rad_j = mk["get_atomic_radius"](sym_j) if mk["has_atomic_radius"](sym_j) else 0.5
        threshold = mk["get_bonding_threshold"](
            rad_i,
            rad_j,
            mk["is_metal_element"](sym_i),
            mk["is_metal_element"](sym_j),
        )
        if dist < threshold:
            graph.add_edge(ii, jj, vector=np.asarray(D_vec, dtype=float))

    components = []
    for comp in nx.connected_components(graph):
        comp_list = sorted(int(c) for c in comp)
        # Drop singleton excluded nodes; they're tracked as orphans
        # downstream rather than appearing in the molecule list.
        if len(comp_list) == 1 and comp_list[0] in excluded:
            continue
        components.append(comp_list)
    components.sort(key=lambda comp: comp[0])
    return components, graph


def _unwrapped_positions(ase_atoms, indices, graph, mk, *, max_atoms=None):
    """Walk the bond graph from the smallest index outward to obtain
    PBC-continuous positions for ``indices``.
    """
    unwrapped, _completed = mk["unwrap_positions_along_bonds"](
        graph,
        indices,
        ase_atoms.get_positions(),
        max_atoms=max_atoms,
    )
    return unwrapped


def _build_crystal_molecule(ase_atoms, indices, unwrapped_positions, mk):
    Atoms = mk["Atoms"]
    CrystalMolecule = mk["CrystalMolecule"]
    symbols = [ase_atoms.get_chemical_symbols()[i] for i in indices]
    sub = Atoms(symbols=symbols, positions=unwrapped_positions)
    for key in (
        mk["KEY_OCCUPANCY"],
        mk["KEY_DISORDER_GROUP"],
        mk["KEY_ASSEMBLY"],
        mk["KEY_LABEL"],
    ):
        if key in ase_atoms.arrays:
            sub.set_array(key, ase_atoms.arrays[key][indices])
    return CrystalMolecule(sub, check_pbc=False)


class CrystalAnalysis:
    """MolCrysKit-derived chemistry on the unit cell.

    Attributes
    ----------
    crystal:
        :class:`MolecularCrystal` instance MolCrysKit returns.
    mol_indices:
        ``mol_indices[k]`` lists the original raw_atom indices spanned
        by the k-th molecule in ``crystal.molecules``.
    mol_cart_positions:
        ``mol_cart_positions[k]`` stores MolCrysKit's PBC-unwrapped
        Cartesian coordinates for the same molecule.  These are what
        formula-unit rendering must draw; using the wrapped raw atom
        coordinates reintroduces long MIC-crossing bonds.
    species_map:
        ``species_id -> [mol_idx, ...]``.  Species IDs come from
        :class:`StoichiometryAnalyzer` and look like ``C6H14N2_1``.
    per_fu:
        ``species_id -> count`` after dividing every species' cell
        count by the GCD across all species.  Canonical Z=1 stoich.
    """

    def __init__(
        self,
        crystal,
        mol_indices,
        mol_cart_positions,
        species_map,
        per_fu,
        bond_pairs=None,
    ):
        self.crystal = crystal
        self.mol_indices = mol_indices
        self.mol_cart_positions = mol_cart_positions
        self.species_map = species_map
        self.per_fu = per_fu
        # ``bond_pairs`` is the canonical molecule-graph edge list in raw_atom
        # indices, sorted (i < j). It is the single source of truth for bond
        # connectivity in the unit cell. Downstream code that needs a bond
        # list (renderer, fragment-table builder) MUST consume this rather
        # than calling the legacy ``ops.find_bonds`` again, otherwise it
        # reintroduces the disorder/PBC mishandling that produced the
        # "?-orphan-H" / variable-cluster_size NH4 bugs (DAP-4, SY).
        self.bond_pairs: list[tuple[int, int]] = list(bond_pairs or [])


def _flatten_bond_pairs(graph) -> list[tuple[int, int]]:
    """Flatten a :class:`networkx.Graph` into a sorted list of ``(i, j)``
    edges with ``i < j`` and integer node ids.

    The molecule graph built by :func:`_components_with_indices` already
    uses raw_atom indices as node labels, so the flattened edge list maps
    1:1 onto the global atom indexing used by the rest of the loader.
    """
    pairs: list[tuple[int, int]] = []
    for u, v in graph.edges():
        a, b = int(u), int(v)
        if a > b:
            a, b = b, a
        pairs.append((a, b))
    pairs.sort()
    return pairs


def analyze(raw_atoms, M, *, max_atoms=None):
    """Run MolCrysKit on ``raw_atoms`` (full unit cell) and return a
    :class:`CrystalAnalysis` summarising species + per-FU counts.

    Atoms tagged as minor disorder images (``_is_minor=True`` set by
    :func:`crystal_viewer.loader._tag_shelx_occupancy_disorder`, or
    matched by the SHELX heuristic in :func:`_is_minor_atom`) are
    excluded from bond perception. Without this guard MolCrysKit's
    neighbour-list bonded a major-orientation N to a minor-orientation
    N at 0.15 A apart -- well below the 1.5 A covalent N-N threshold
    -- and fused two chemically distinct ethylenediamines into one
    C4H20N4 component. Excluding minor images restores the correct
    one-orientation-per-disorder-site molecule grouping.

    Minor atoms remain in ``raw_atoms`` (the renderer still draws them
    faded); they just don't appear in any ``mol_indices`` entry.
    """
    mk = _require_molcryskit()
    if not raw_atoms:
        crystal = mk["MolecularCrystal"](np.eye(3), [], pbc=(True, True, True))
        return CrystalAnalysis(crystal, [], [], {}, {}, bond_pairs=[])

    ase_atoms = _ase_atoms_from_raw(raw_atoms, M, mk)
    minor_indices = _minor_index_set(raw_atoms)
    components, graph = _components_with_indices(
        ase_atoms, mk, exclude_indices=minor_indices
    )

    molecules = []
    mol_indices = []
    mol_cart_positions = []
    for comp in components:
        unwrapped = _unwrapped_positions(ase_atoms, comp, graph, mk, max_atoms=max_atoms)
        molecules.append(_build_crystal_molecule(ase_atoms, comp, unwrapped, mk))
        mol_indices.append(comp)
        mol_cart_positions.append(unwrapped)

    crystal = mk["MolecularCrystal"](
        ase_atoms.get_cell(), molecules, pbc=tuple(ase_atoms.get_pbc())
    )
    analyzer = mk["StoichiometryAnalyzer"](crystal)
    return CrystalAnalysis(
        crystal=crystal,
        mol_indices=mol_indices,
        mol_cart_positions=mol_cart_positions,
        species_map=copy.deepcopy(analyzer.species_map),
        per_fu=copy.deepcopy(analyzer.get_simplest_unit()),
        bond_pairs=_flatten_bond_pairs(graph),
    )


def _centroid(raw_atoms, indices, cart_positions=None):
    if cart_positions is not None:
        return np.mean(np.asarray(cart_positions, dtype=float), axis=0)
    return np.mean([np.asarray(raw_atoms[i]["cart"], dtype=float) for i in indices], axis=0)


def _best_pbc_translation(raw_atoms, indices, anchor, M, cart_positions=None, search_radius=2):
    base = _centroid(raw_atoms, indices, cart_positions=cart_positions)
    best_shift = np.zeros(3)
    best_d = float("inf")
    for na in range(-search_radius, search_radius + 1):
        for nb in range(-search_radius, search_radius + 1):
            for nc in range(-search_radius, search_radius + 1):
                shift_frac = np.array([na, nb, nc], dtype=float)
                shift_cart = M @ shift_frac
                d = float(np.linalg.norm(base + shift_cart - anchor))
                if d < best_d:
                    best_d = d
                    best_shift = shift_frac
    return best_shift, best_d


def _translate_cluster(raw_atoms, indices, shift_frac, M, cart_positions=None):
    M = np.asarray(M, dtype=float)
    shift_cart = M @ shift_frac
    inv_m = np.linalg.inv(M)
    out = []
    for local_idx, i in enumerate(indices):
        atom = copy.deepcopy(raw_atoms[i])
        base_cart = (
            np.asarray(cart_positions[local_idx], dtype=float)
            if cart_positions is not None
            else np.asarray(atom["cart"], dtype=float)
        )
        atom["cart"] = base_cart + shift_cart
        atom["frac"] = inv_m @ atom["cart"]
        # Preserve the raw_atoms index on every translated copy so the
        # fragment-table builder (which consumes mol_indices into raw_atoms)
        # can still figure out which molecule each formula-unit atom
        # belongs to. Without this, formula_unit-mode draw_atoms lose their
        # provenance and we'd have to re-derive the grouping by Cartesian
        # proximity -- which is exactly the legacy mistake we're eliminating.
        atom["_source_index"] = int(i)
        out.append(atom)
    return out


def _species_priority(species_id, mol_indices_list, raw_atoms):
    """Sort key for picking the anchor species: heaviest molecule first
    (so we don't anchor on a small counterion / solvent), then the
    species with fewer cell copies (further breaks ties)."""
    if not mol_indices_list:
        return (0, 0)
    sample = mol_indices_list[0]
    heavy = sum(1 for i in sample if raw_atoms[i].get("elem") != "H")
    return (-heavy, len(mol_indices_list))


def select_formula_unit(raw_atoms, M, *, analysis=None):
    """Pick one set of molecules realising the simplest stoichiometric
    unit and translate them so the rendered FU stays spatially compact.

    Counts come from MolCrysKit's GCD analysis (no hard-coded
    ``max_count=4``).  Selection is greedy: anchor on one molecule of
    the heaviest species; for every other species pick its
    ``per_fu`` molecules in proximity-first order, choosing the PBC
    translation that minimises distance to the running centroid.
    """
    if analysis is None:
        analysis = analyze(raw_atoms, M)
    if not analysis.per_fu:
        return [copy.deepcopy(atom) for atom in raw_atoms]

    M = np.asarray(M, dtype=float)
    species_order = sorted(
        analysis.per_fu.keys(),
        key=lambda sid: _species_priority(
            sid,
            [analysis.mol_indices[mi] for mi in analysis.species_map[sid]],
            raw_atoms,
        ),
    )

    chosen_atoms = []
    anchor_centroid = None

    for species_id in species_order:
        n_keep = analysis.per_fu.get(species_id, 0)
        if n_keep <= 0:
            continue
        mol_idx_list = list(analysis.species_map[species_id])
        if not mol_idx_list:
            continue

        if anchor_centroid is None:
            first_mi = mol_idx_list[0]
            translated = _translate_cluster(
                raw_atoms,
                analysis.mol_indices[first_mi],
                np.zeros(3),
                M,
                cart_positions=analysis.mol_cart_positions[first_mi],
            )
            chosen_atoms.extend(translated)
            anchor_centroid = np.mean(
                [np.asarray(a["cart"], dtype=float) for a in translated], axis=0
            )
            mol_idx_list.remove(first_mi)
            n_keep -= 1
            if n_keep <= 0:
                continue

        scored = []
        for mi in mol_idx_list:
            shift, dist = _best_pbc_translation(
                raw_atoms,
                analysis.mol_indices[mi],
                anchor_centroid,
                M,
                cart_positions=analysis.mol_cart_positions[mi],
            )
            scored.append((dist, mi, shift))
        scored.sort(key=lambda item: item[0])
        for _, mi, shift in scored[:n_keep]:
            translated = _translate_cluster(
                raw_atoms,
                analysis.mol_indices[mi],
                shift,
                M,
                cart_positions=analysis.mol_cart_positions[mi],
            )
            mol_centroid = np.mean(
                [np.asarray(a["cart"], dtype=float) for a in translated], axis=0
            )
            prev_n = len(chosen_atoms)
            chosen_atoms.extend(translated)
            anchor_centroid = (
                anchor_centroid * prev_n + mol_centroid * len(translated)
            ) / max(len(chosen_atoms), 1)

    return chosen_atoms
