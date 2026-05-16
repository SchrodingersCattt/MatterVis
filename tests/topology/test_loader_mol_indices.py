"""Regression tests for the ``feat/loader-mol-indices`` refactor.

The fragment-table builder now consumes :attr:`molcrys_bridge.\
CrystalAnalysis.mol_indices` directly instead of re-deriving bonds via
the legacy ``ops.find_bonds`` path. These tests pin down the
behaviour change for the two structures the refactor was driven by:

* DAP-4 (8 NH4+ + 8 DABCO + 24 ClO4 in the unit cell). The legacy path
  produced 8 NH4 fragments with **inconsistent** ``cluster_size``
  values (2 / 3 / 5) and 18 orphan-H ``"?"`` fragments. After the fix
  every NH4 has ``cluster_size == 5`` and there are zero orphans.
* SY (negative-PART disordered cation + ClO4 cell). The legacy path
  produced 12 isolated H1 fragments; after the fix none.

Plus unit tests for the pieces:

* ``CrystalAnalysis.bond_pairs`` is the flattened molecule-graph edge
  list with sorted ``(i, j)`` ordering.
* ``is_minor`` only trusts explicit disorder provenance or PART markers;
  blank partial occupancy alone may be an ordered special-position site.
* ``_has_shelx_occupancy_disorder`` distinguishes the SHELX-occupancy
  pattern from PART-style or fully-resolved CIFs.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from crystal_viewer.static_publication import plot_crystal as pc
from crystal_viewer.loader import (
    _has_shelx_occupancy_disorder,
    build_loaded_crystal,
)


# --------------------------------------------------------------------- #
# 1) CrystalAnalysis.bond_pairs                                         #
# --------------------------------------------------------------------- #
def test_crystal_analysis_exposes_bond_pairs_in_raw_indices():
    bundle = build_loaded_crystal(
        name="DAP-4", cif_path="scripts/data/DAP-4.cif", title="DAP-4"
    )
    analysis = bundle.molcrys_analysis
    pairs = analysis.bond_pairs

    assert pairs, "DAP-4 has 336 atoms in 40 molecules; bond_pairs must be non-empty"
    # Sorted, unique, raw-index ordered (i < j)
    for i, j in pairs:
        assert isinstance(i, int) and isinstance(j, int)
        assert 0 <= i < j < len(bundle.raw_atoms)
    assert pairs == sorted(set(pairs))


# --------------------------------------------------------------------- #
# 2) DAP-4 fragment table is clean                                      #
# --------------------------------------------------------------------- #
def test_dap4_topology_fragment_table_is_clean():
    bundle = build_loaded_crystal(
        name="DAP-4", cif_path="scripts/data/DAP-4.cif", title="DAP-4"
    )
    table = bundle.topology_fragment_table

    formulas = Counter(f["formula"] for f in table)
    types = Counter(f["type"] for f in table)
    cluster_sizes = Counter(
        (f["formula"], f["cluster_size"]) for f in table
    )

    assert formulas == Counter({"ClO4": 24, "N": 8, "C6N2": 8})
    assert types == Counter({"X": 24, "B": 8, "A": 8})
    # Bug 2 regression: all 8 NH4+ used to split into cluster_size = 2/3/5.
    assert cluster_sizes[("N", 5)] == 8
    # Bug 3 regression: 18 orphan-H "?" fragments are gone.
    assert all(f["formula"] != "?" for f in table)


def test_dap4_formula_unit_table_matches_per_fu_stoichiometry():
    bundle = build_loaded_crystal(
        name="DAP-4", cif_path="scripts/data/DAP-4.cif", title="DAP-4"
    )

    # MolCrysKit's per-FU is C6H14N2 + (NH4) + 3 ClO4. fragment_table
    # therefore has 5 rows: 1 A, 1 B, 3 X.
    types = Counter(f["type"] for f in bundle.fragment_table)
    assert types == Counter({"A": 1, "B": 1, "X": 3})


# --------------------------------------------------------------------- #
# 3) SY structure has zero orphan-H fragments                           #
# --------------------------------------------------------------------- #
def test_sy_topology_table_has_no_orphan_hydrogens():
    bundle = build_loaded_crystal(
        name="SY", cif_path="scripts/data/SY.cif", title="SY"
    )
    table = bundle.topology_fragment_table

    # The legacy fragment-table builder produced 12 isolated "H1"
    # fragments (orphan disorder hydrogens). After the refactor the
    # cell only has the chemically-real fragments.
    formulas = Counter(f["formula"] for f in table)
    assert "H1" not in formulas
    assert "?" not in formulas
    # All ClO4 anions show CN = 5 (1 Cl + 4 O).
    assert all(
        f["cluster_size"] == 5 for f in table if f["formula"] == "ClO4"
    )


# --------------------------------------------------------------------- #
# 4) loader-authored minor disorder flag                                #
# --------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "atom, expected",
    [
        # Fully-occupied, no disorder tags -> major.
        ({"label": "C1", "elem": "C", "occ": 1.0, "dg": ".", "da": "."}, False),
        # Raw PART strings alone are not render-fade provenance.
        ({"label": "C1A", "elem": "C", "occ": 0.6, "dg": "1", "da": "A"}, False),
        ({"label": "C1B", "elem": "C", "occ": 0.4, "dg": "2", "da": "A"}, False),
        ({"label": "C1B", "elem": "C", "occ": 0.4, "dg": "-1", "da": "."}, False),
        # Loader-resolved disorder is mirrored onto _is_minor.
        ({"label": "C1B", "elem": "C", "occ": 0.4, "dg": "2", "da": "A", "_is_minor": True}, True),
        ({"label": "C1B", "elem": "C", "occ": 0.4, "dg": "-1", "da": ".", "_is_minor": True}, True),
        # Partial occupancy alone can be an ordered special-position atom.
        # The loader must tag occupancy-only disorder explicitly via
        # _is_minor before it is rendered or analysed as minor.
        ({"label": "H3X", "elem": "H", "occ": 0.3, "dg": ".", "da": "."}, False),
        ({"label": "H3X", "elem": "H", "occ": 0.3, "dg": ".", "da": ".", "_is_minor": True}, True),
        # Edge case: occ exactly 0.5 with dg='.' -> ambiguous, currently
        # treated as major (one of a 0.5/0.5 pair). The auto-disorder
        # resolver in ``build_loaded_crystal`` is what tags one of the
        # pair via _is_minor=True for those CIFs.
        ({"label": "H3A", "elem": "H", "occ": 0.5, "dg": ".", "da": "."}, False),
    ],
)
def test_is_minor_reads_loader_flag_only(atom, expected):
    assert pc.is_minor(atom) is expected


def test_is_minor_explicit_flag_is_the_single_source_of_truth():
    minor = {"label": "X", "elem": "C", "occ": 1.0, "_is_minor": True}
    major = {"label": "X", "elem": "C", "occ": 0.2, "dg": "2", "da": "A", "_is_minor": False}
    assert pc.is_minor(minor) is True
    assert pc.is_minor(major) is False


# --------------------------------------------------------------------- #
# 5) _has_shelx_occupancy_disorder detection                            #
# --------------------------------------------------------------------- #
def test_has_shelx_occupancy_disorder_distinguishes_patterns():
    # SHELX occupancy-only pattern: sibling labels, occ<1, blank disorder tags.
    shelx = [
        {"label": "H3A", "elem": "H", "occ": 0.5, "dg": ".", "da": "."},
        {"label": "H3B", "elem": "H", "occ": 0.5, "dg": ".", "da": "."},
    ]
    # Ordered special-position pattern: one partial site, blank disorder tags.
    ordered_special_position = [
        {"label": "Cu1", "elem": "Cu", "occ": 0.25, "dg": ".", "da": "."},
        {"label": "N1", "elem": "N", "occ": 1.0, "dg": ".", "da": "."},
    ]
    # PART-style: occ<1 but dg encodes the alternative.
    part_style = [
        {"elem": "C", "occ": 0.6, "dg": "1", "da": "A"},
        {"elem": "C", "occ": 0.4, "dg": "2", "da": "A"},
    ]
    # Fully ordered.
    ordered = [
        {"elem": "C", "occ": 1.0, "dg": ".", "da": "."},
        {"elem": "N", "occ": 1.0, "dg": ".", "da": "."},
    ]

    assert _has_shelx_occupancy_disorder(shelx) is True
    assert _has_shelx_occupancy_disorder(ordered_special_position) is False
    assert _has_shelx_occupancy_disorder(part_style) is False
    assert _has_shelx_occupancy_disorder(ordered) is False


# --------------------------------------------------------------------- #
# 6) bridge analysis matches the bond-pair list                         #
# --------------------------------------------------------------------- #
def test_analysis_bond_pairs_match_mol_indices_membership():
    """Every edge in ``bond_pairs`` must connect two atoms inside the
    same ``mol_indices`` group; we never want a bond crossing molecule
    boundaries (that's the whole point of using MolCrysKit's
    connected-component split)."""
    bundle = build_loaded_crystal(
        name="DAP-4", cif_path="scripts/data/DAP-4.cif", title="DAP-4"
    )
    analysis = bundle.molcrys_analysis
    membership = {}
    for k, indices in enumerate(analysis.mol_indices):
        for raw in indices:
            membership[int(raw)] = k

    cross = [
        (i, j) for i, j in analysis.bond_pairs
        if membership.get(i) != membership.get(j)
    ]
    assert cross == [], f"bond_pairs crossed molecule boundaries: {cross[:5]}"


# --------------------------------------------------------------------- #
# 7) SY ethylenediamine should be split, not fused                      #
# --------------------------------------------------------------------- #
def test_sy_ethylenediamine_not_fused():
    """Regression for SY ethylenediamine fusion. SHELX -PART disorder +
    Pa-3 symmetry expansion creates 8 N3 + 8 N2 atoms that overlap at
    0.15 A pairs (alternative orientations of the same nucleus).
    MolCrysKit's neighbour-list bonded the alternates together,
    producing one C4N4H20 fused species instead of two C2N2H10.

    After the loader-side fix ``_tag_shelx_occupancy_disorder`` runs
    ``generate_ordered_replicas`` on a sanitized copy of the CIF and
    tags one orientation as minor; the resulting fragment table must
    contain two distinct cation species (en + DABCO) plus 4 ClO4.
    """
    bundle = build_loaded_crystal(
        name="SY", cif_path="scripts/data/SY.cif", title="SY"
    )
    table = bundle.topology_fragment_table
    formulas = Counter(f["formula"] for f in table)

    # Unit-cell-level counts: 16 ClO4 + 4 en (C2N2 heavy) + 4 DABCO
    # (C6N2 heavy). Per-FU stoichiometry would be 4 / 1 / 1.
    assert formulas.get("ClO4") == 16
    assert formulas.get("C2N2") == 4, (
        f"expected 4 ethylenediamine cations, got {formulas}"
    )
    assert formulas.get("C6N2") == 4, (
        f"expected 4 DABCO cations, got {formulas}"
    )
    # Crucially: no fused C4N4 species.
    assert "C4N4" not in formulas, (
        f"two ethylenediamines fused into C4N4: {formulas}"
    )


def test_sy_minor_ethylenediamine_fragments_are_grouped_whole():
    """Minor SHELX -PART atoms must remain first-class fragments.

    The renderer draws minor alternatives faded, but they still need MCK
    molecule provenance so boundary handling and fragment diagnostics treat the
    entire ethylenediamine as one object instead of orphaning individual atoms.
    """
    bundle = build_loaded_crystal(
        name="SY", cif_path="scripts/data/SY.cif", title="SY"
    )
    raw_to_mol: dict[int, int] = {}
    for mol_idx, raw_indices in enumerate(bundle.molcrys_analysis.mol_indices):
        for raw_idx in raw_indices:
            raw_to_mol[int(raw_idx)] = int(mol_idx)

    minor_indices = [
        idx for idx, atom in enumerate(bundle.raw_atoms)
        if atom.get("_is_minor")
    ]
    assert len(minor_indices) == 56
    assert all(idx in raw_to_mol for idx in minor_indices)

    minor_mol_sizes = Counter(
        len(bundle.molcrys_analysis.mol_indices[raw_to_mol[idx]])
        for idx in minor_indices
    )
    assert minor_mol_sizes == Counter({14: 56})


# --------------------------------------------------------------------- #
# 8) DAP-4 NH4+ rotamer paired-group fix                                #
# --------------------------------------------------------------------- #
def test_dap4_nh4_count_correct():
    """Regression for the H3A/H3B paired-alternative bug. Even with no
    ``_atom_site_disorder_group`` tags (Pa-3 CIF where SHELX writes
    NH4+ rotamers as occ=0.5 + disorder_group='.') the loader must
    recover one major NH4 per crystallographic site, not split the
    8 sym-images into 8 independent occ=0.5 alternatives.
    """
    bundle = build_loaded_crystal(
        name="DAP-4", cif_path="scripts/data/DAP-4.cif", title="DAP-4"
    )
    table = bundle.topology_fragment_table
    formulas = Counter(f["formula"] for f in table)
    # Unit-cell counts: 8 NH4+ + 8 DABCO + 24 ClO4. Per-FU
    # stoichiometry would be 1 NH4 + 1 DABCO + 3 ClO4.
    assert formulas.get("ClO4") == 24
    assert formulas.get("N") == 8, (
        f"expected 8 NH4+ cations (formula 'N' since heavy-atom), got {formulas}"
    )
    assert formulas.get("C6N2") == 8, (
        f"expected 8 DABCO cations, got {formulas}"
    )


# --------------------------------------------------------------------- #
# 9) "Black cage" -- cross-orientation ghost bonds are filtered          #
# --------------------------------------------------------------------- #
def test_no_cross_orientation_ghost_bonds_in_unit_cell_scene():
    """Regression for cross-orientation ghost bonds: ``find_bonds`` doesn't know
    about ``_is_minor`` and would happily bond a major N3 (kept
    orientation) to a minor C4 (discarded orientation) at 0.83 A
    apart. The renderer drew those as full opaque lines, making
    every disordered cation look like it's wrapped in a dark cage of
    phantom bonds. ``build_scene_from_atoms`` now skips bonds whose
    endpoints have incompatible disorder groups, while preserving valid
    major/minor bonds to ordered hubs.
    """
    from crystal_viewer.loader import build_bundle_scene

    bundle = build_loaded_crystal(
        name="SY", cif_path="scripts/data/SY.cif", title="SY"
    )
    scene = build_bundle_scene(bundle, display_mode="unit_cell", show_hydrogen=True)
    bonds = scene.get("bonds") or []
    draw_atoms = scene.get("draw_atoms") or []
    # Each bond stores its endpoint indices into ``draw_atoms``;
    # confirm no bond bridges mutually exclusive disorder groups.
    for b in bonds:
        ai = draw_atoms[b["i"]]
        aj = draw_atoms[b["j"]]
        assert not pc.bonds_conflict(ai, aj), (
            f"cross-orientation bond between atoms "
            f"{ai.get('label')} (group={ai.get('_mv_auto_disorder_group') or ai.get('dg')}) and "
            f"{aj.get('label')} (group={aj.get('_mv_auto_disorder_group') or aj.get('dg')})"
        )


def test_hpep_minor_branches_keep_ordered_hub_bonds():
    """HPEP PART-2 branches bond through ordered Cl/N hub atoms.

    Those are valid major/minor bonds and must not be removed by the
    cross-orientation ghost-bond filter.
    """
    if not Path("scripts/data/HPEP.cif").exists():
        pytest.skip("local HPEP CIF fixture is not present")

    bundle = build_loaded_crystal(
        name="HPEP", cif_path="scripts/data/HPEP.cif", title="HPEP"
    )
    scene = bundle.scene
    draw_atoms = scene.get("draw_atoms") or []
    label_pairs = {
        frozenset((draw_atoms[b["i"]]["label"], draw_atoms[b["j"]]["label"]))
        for b in scene.get("bonds") or []
    }

    for pair in (("Cl3", "O10A"), ("Cl3", "O11A"), ("Cl3", "O12A"), ("N1", "C1A"), ("N2", "C2A")):
        assert frozenset(pair) in label_pairs
