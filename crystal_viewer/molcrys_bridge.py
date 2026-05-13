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
import re

import numpy as np
from molcrys_kit.utils.geometry import cart_to_frac, frac_to_cart


def _require_molcryskit():
    """Import MolCrysKit lazily and surface a clean error if missing."""
    try:
        from ase import Atoms

        from molcrys_kit.structures.crystal import MolecularCrystal
        from molcrys_kit.io.cif import identify_molecules
        from molcrys_kit.analysis.stoichiometry import StoichiometryAnalyzer
        from molcrys_kit.constants.config import (
            KEY_OCCUPANCY,
            KEY_DISORDER_GROUP,
            KEY_ASSEMBLY,
            KEY_LABEL,
            KEY_SYM_OP_INDEX,
        )
    except ImportError as exc:
        raise ImportError(
            "molcrys-kit is required for the formula_unit display mode. "
            "Install it with `pip install molcrys-kit` (it is listed in "
            "MatterVis's requirements.txt)."
        ) from exc

    return {
        "Atoms": Atoms,
        "MolecularCrystal": MolecularCrystal,
        "identify_molecules": identify_molecules,
        "StoichiometryAnalyzer": StoichiometryAnalyzer,
        "KEY_OCCUPANCY": KEY_OCCUPANCY,
        "KEY_DISORDER_GROUP": KEY_DISORDER_GROUP,
        "KEY_ASSEMBLY": KEY_ASSEMBLY,
        "KEY_LABEL": KEY_LABEL,
        "KEY_SYM_OP_INDEX": KEY_SYM_OP_INDEX,
    }


def _ase_atoms_from_raw(raw_atoms, M, mk):
    """Build an index-aligned ASE Atoms with MolCrysKit disorder arrays.

    MatterVis stores the lattice as a 3x3 matrix whose **rows** are
    the a, b, c vectors, matching ASE and MolCrysKit.
    """
    symbols = [atom["elem"] for atom in raw_atoms]
    positions = np.array([atom["cart"] for atom in raw_atoms], dtype=float)
    cell = np.asarray(M, dtype=float)

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
    for i, atom in enumerate(raw_atoms):
        if dg[i] != 0:
            continue
        # Occupancy-only rotamer disorder (e.g. DAP-4 NH4+) has no CIF PART
        # label, so MCK would see every alternative as group 0 and allow
        # cross-orientation close contacts to fuse. Reuse the optimal-replica
        # picker result as a synthetic PART label only at the MCK adapter
        # boundary; raw atom dictionaries keep their original dg/da fields.
        if atom.get("_is_minor") is True:
            dg[i] = -1
        elif atom.get("_is_major") is True or atom.get("_is_minor") is False:
            dg[i] = 1

    da = np.array([
        (str(atom.get("da", "") or "").strip()) for atom in raw_atoms
    ])
    da = np.array([("" if v in (".", "?") else v) for v in da])

    label = np.array([
        atom.get("label") or atom["elem"] for atom in raw_atoms
    ])
    sym_op_index = np.array([
        int(atom.get("_symop_index", 0) or 0) for atom in raw_atoms
    ], dtype=int)

    atoms.set_array(mk["KEY_OCCUPANCY"], occ)
    atoms.set_array(mk["KEY_DISORDER_GROUP"], dg)
    atoms.set_array(mk["KEY_ASSEMBLY"], da)
    atoms.set_array(mk["KEY_LABEL"], label)
    atoms.set_array(mk["KEY_SYM_OP_INDEX"], sym_op_index)
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


_FORMULA_TOKEN_RE = re.compile(r"([A-Z][a-z]?)(\d*)")


def formula_to_moiety(formula: str) -> str:
    """Convert MatterVis compact formula keys to MolCrysKit moiety strings.

    MatterVis species selectors are compact formula keys such as ``C6N2`` or
    ``ClO4``. MolCrysKit's molecule-level packing-shell API accepts single
    moiety strings such as ``C6 N2`` and ``Cl O4``. This adapter is purely
    syntactic; invalid or multi-moiety values fail instead of falling back to
    MatterVis-local chemistry.
    """
    text = str(formula or "").strip()
    if not text or text == "?":
        raise ValueError(f"Cannot convert empty fragment formula to moiety: {formula!r}")
    parts: list[str] = []
    pos = 0
    for match in _FORMULA_TOKEN_RE.finditer(text):
        if match.start() != pos:
            raise ValueError(f"Invalid compact formula for MolCrysKit moiety: {formula!r}")
        elem, count = match.groups()
        parts.append(f"{elem}{count}" if count else elem)
        pos = match.end()
    if pos != len(text) or not parts:
        raise ValueError(f"Invalid compact formula for MolCrysKit moiety: {formula!r}")
    return " ".join(parts)


def molecular_crystal_from_bundle(bundle):
    """Return the MolCrysKit ``MolecularCrystal`` already built for a bundle."""
    analysis = getattr(bundle, "molcrys_analysis", None)
    crystal = getattr(analysis, "crystal", None) or getattr(bundle, "crystal", None)
    if crystal is None:
        raise ValueError("Bundle has no MolCrysKit MolecularCrystal analysis.")
    if getattr(crystal, "molecules", None) is None or getattr(crystal, "lattice", None) is None:
        raise TypeError("MolCrysKit molecule-level polyhedra require .molecules and .lattice.")
    return crystal


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


def analyze(raw_atoms, M, *, max_atoms=None):
    """Run MolCrysKit on ``raw_atoms`` (full unit cell) and return a
    :class:`CrystalAnalysis` summarising species + per-FU counts.

    MolCrysKit's ``identify_molecules`` is disorder-aware: atoms in
    incompatible non-zero PART groups are not bonded even when their
    Cartesian positions overlap. MatterVis passes CIF disorder groups
    through directly, and synthesises a private +1/-1 group at the ASE
    adapter boundary for occupancy-only rotamers already classified by
    ``_tag_shelx_occupancy_disorder``. Both major and minor alternatives
    therefore remain full molecular fragments in ``mol_indices``; the
    renderer still distinguishes them via the original ``_is_minor`` flag.
    """
    mk = _require_molcryskit()
    if not raw_atoms:
        crystal = mk["MolecularCrystal"](np.eye(3), [], pbc=(True, True, True))
        return CrystalAnalysis(crystal, [], [], {}, {}, bond_pairs=[])

    ase_atoms = _ase_atoms_from_raw(raw_atoms, M, mk)
    identified = mk["identify_molecules"](ase_atoms, max_atoms=max_atoms)

    molecules = []
    mol_indices = []
    mol_cart_positions = []
    bond_pairs: set[tuple[int, int]] = set()
    for molecule in identified:
        indices = [int(i) for i in (molecule.info.get("atom_indices") or [])]
        if not indices:
            continue
        positions = np.asarray(molecule.get_positions(), dtype=float)
        if positions.ndim != 2 or positions.shape[0] != len(indices):
            continue
        molecules.append(molecule)
        mol_indices.append(indices)
        mol_cart_positions.append(positions)
        for i, j in molecule.info.get("bond_pairs") or []:
            a, b = int(i), int(j)
            if a > b:
                a, b = b, a
            bond_pairs.add((a, b))

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
        bond_pairs=sorted(bond_pairs),
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
                shift_cart = frac_to_cart(shift_frac, M)
                d = float(np.linalg.norm(base + shift_cart - anchor))
                if d < best_d:
                    best_d = d
                    best_shift = shift_frac
    return best_shift, best_d


def _translate_cluster(raw_atoms, indices, shift_frac, M, cart_positions=None):
    M = np.asarray(M, dtype=float)
    shift_cart = frac_to_cart(shift_frac, M)
    out = []
    for local_idx, i in enumerate(indices):
        atom = copy.deepcopy(raw_atoms[i])
        base_cart = (
            np.asarray(cart_positions[local_idx], dtype=float)
            if cart_positions is not None
            else np.asarray(atom["cart"], dtype=float)
        )
        atom["cart"] = base_cart + shift_cart
        atom["frac"] = cart_to_frac(atom["cart"], M)
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
