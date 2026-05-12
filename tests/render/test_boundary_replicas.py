"""Phase 4 (perf+symmetry): unit-cell mode replicates boundary atoms.

When a fractional coordinate sits exactly on a face / edge / corner of
the unit cell, the visual cell needs the equivalent images on the
opposite face(s) so the unit cell looks closed. VESTA does this by
default; we mirror the convention.

This test pins the behaviour and the safety bands that prevent the
fix from spuriously replicating *unwrapped* continuation atoms (which
sit at frac like ``1.02`` for visual continuity but are crystallo-
graphically a single site in the home cell).
"""
from __future__ import annotations

import gemmi
import numpy as np

from crystal_viewer.scene import build_scene_from_atoms, scene_ops


def _atom(label: str, frac, M):
    frac_arr = np.asarray(frac, dtype=float)
    return {
        "label": label,
        "elem": "C",
        "frac": frac_arr,
        "cart": M @ frac_arr,
        "occ": 1.0,
        "dg": ".",
        "da": ".",
    }


def test_atom_on_corner_replicates_to_eight_corners():
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [_atom("C1", [0.0, 0.0, 0.0], M)]

    scene = build_scene_from_atoms(
        name="corner",
        title="Corner",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=scene_ops(),
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    assert len(scene["draw_atoms"]) == 8
    coords = sorted(tuple(np.round(a["cart"], 5)) for a in scene["draw_atoms"])
    expected = sorted(
        (10.0 * sx, 10.0 * sy, 10.0 * sz)
        for sx in (0.0, 1.0)
        for sy in (0.0, 1.0)
        for sz in (0.0, 1.0)
    )
    assert coords == expected


def test_atom_on_face_replicates_to_two_faces():
    cell = gemmi.UnitCell(8.0, 8.0, 8.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 8.0
    atoms = [_atom("F1", [0.0, 0.5, 0.5], M)]

    scene = build_scene_from_atoms(
        name="face",
        title="Face",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=scene_ops(),
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    assert len(scene["draw_atoms"]) == 2
    coords = sorted(tuple(np.round(a["cart"], 5)) for a in scene["draw_atoms"])
    assert coords == [(0.0, 4.0, 4.0), (8.0, 4.0, 4.0)]


def test_fragment_on_face_replicates_as_whole_fragment():
    """Boundary images must preserve complete molecular fragments.

    Special-position atoms used to be replicated one-by-one, producing
    orphan dots on the opposite unit-cell face. When MCK source molecule
    metadata is present, the full fragment translates together.
    """
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [
        _atom("C1", [0.0, 0.5, 0.5], M),
        _atom("C2", [0.1, 0.5, 0.5], M),
    ]
    for atom in atoms:
        atom["_source_molecule_index"] = 7
        atom["_wrapped_frac"] = np.array(atom["frac"], dtype=float)

    scene = build_scene_from_atoms(
        name="fragment_face",
        title="Fragment Face",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=scene_ops(),
        unwrapped_atoms=atoms,
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    assert len(scene["draw_atoms"]) == 4
    labels_by_x = sorted(
        (atom["label"], round(float(atom["cart"][0]), 5), bool(atom.get("_is_fragment_boundary_replica")))
        for atom in scene["draw_atoms"]
    )
    assert labels_by_x == [
        ("C1", 0.0, False),
        ("C1", 10.0, True),
        ("C2", 1.0, False),
        ("C2", 11.0, True),
    ]


def test_atom_on_edge_replicates_to_four():
    cell = gemmi.UnitCell(6.0, 6.0, 6.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 6.0
    atoms = [_atom("E1", [0.0, 0.0, 0.5], M)]

    scene = build_scene_from_atoms(
        name="edge",
        title="Edge",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=scene_ops(),
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    assert len(scene["draw_atoms"]) == 4


def test_interior_atom_is_not_replicated():
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [_atom("I1", [0.5, 0.5, 0.5], M)]

    scene = build_scene_from_atoms(
        name="interior",
        title="Interior",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=scene_ops(),
        preset={"style": {"show_labels": False, "show_axes": False}},
    )
    assert len(scene["draw_atoms"]) == 1


def test_unwrapped_continuation_atom_is_not_replicated():
    """Atoms at frac>1 (unwrapped for molecule continuity) must NOT
    trigger a boundary replica. Otherwise a C2 unwrapped to frac=1.02
    spawns a duplicate at frac=0.02, doubling bonds in the picture.
    Regression test for the boundary-replica bug found by
    ``tests/render/test_unwrap_modes.py``.
    """
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    # Simulate the post-unwrap output: one atom inside the cell, one
    # drawn just outside the +x face for visual continuity.
    atoms = [
        _atom("C1", [0.98, 0.5, 0.5], M),
        _atom("C2", [1.02, 0.5, 0.5], M),
    ]
    # These are NOT on a face (their frac is 0.98 / 1.02, not exactly
    # 0 or 1), so neither should be replicated.
    scene = build_scene_from_atoms(
        name="unwrap_continuation",
        title="UC",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=scene_ops(),
        unwrapped_atoms=atoms,
        preset={"style": {"show_labels": False, "show_axes": False}},
    )
    assert len(scene["draw_atoms"]) == 2


def test_formula_unit_mode_does_not_replicate():
    """Boundary replication is a unit_cell-only convention. Other
    display modes must not emit duplicate atoms (their semantics are
    distinct -- formula_unit picks one chemical formula's worth, etc.)."""
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [_atom("C1", [0.0, 0.0, 0.0], M)]

    scene = build_scene_from_atoms(
        name="formula",
        title="Formula",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="formula_unit",
        ops=scene_ops(),
        formula_unit_atoms=atoms,
        preset={"style": {"show_labels": False, "show_axes": False}},
    )
    assert len(scene["draw_atoms"]) == 1
