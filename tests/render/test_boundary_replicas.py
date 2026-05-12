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


def test_fragment_with_mck_drift_replicates_at_canonical_cell_corners():
    """When MCK has translated a fragment by an integer cell vector to keep
    it continuous, the boundary replicas must still land at the proper
    cell mirror positions -- NOT at ``(MCK_home + canonical_shift)`` which
    would put them at ``2x`` cell positions outside the unit cell box.

    Reproduces the DAP-4 NH4-at-corner symptom: parse_asu wraps N to
    ``(0, 0, 0)`` but MCK draws it at the equivalent corner ``(1, 1, 0)``
    so the H atoms stay in one continuous blob. Earlier code applied the
    canonical mirror shift ``(1, 0, 0)`` directly to the MCK cart, sending
    the replica to ``(2a, a, 0)`` -- visibly outside the cell. The drift
    correction pulls it back to ``(a, 0, 0)``.
    """
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0

    # Atoms as they would appear AFTER MCK has unwrapped a corner-NH4 to
    # the (1, 1, 0) image: ``frac`` is MCK's continuous coordinate (with
    # values >= 1) while ``_wrapped_frac`` is the original ``parse_asu``
    # wrapped position (in [0, 1)) used for boundary detection.
    n_atom = _atom("N1", [1.0, 1.0, 0.0], M)
    n_atom["elem"] = "N"
    n_atom["_wrapped_frac"] = np.array([0.0, 0.0, 0.0])
    n_atom["_source_molecule_index"] = 31

    h_atom = _atom("H1", [1.04, 1.04, 0.04], M)
    h_atom["elem"] = "H"
    h_atom["_wrapped_frac"] = np.array([0.04, 0.04, 0.04])
    h_atom["_source_molecule_index"] = 31

    atoms = [n_atom, h_atom]
    scene = build_scene_from_atoms(
        name="mck_drift_corner",
        title="MCK drift corner",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=scene_ops(),
        unwrapped_atoms=atoms,
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    # Expected: 1 home (at MCK (1,1,0) corner) + 7 mirror corners. None
    # should land outside ``[-eps, a + eps]`` along any axis.
    n_atoms = [a for a in scene["draw_atoms"] if a["elem"] == "N"]
    assert len(n_atoms) == 8
    n_carts = sorted(tuple(round(float(c), 3) for c in atom["cart"]) for atom in n_atoms)
    expected = sorted(
        (10.0 * sx, 10.0 * sy, 10.0 * sz)
        for sx in (0.0, 1.0)
        for sy in (0.0, 1.0)
        for sz in (0.0, 1.0)
    )
    assert n_carts == expected
    # No atom should sit beyond one cell width.
    for atom in scene["draw_atoms"]:
        cart = atom["cart"]
        for c in cart:
            assert -0.5 <= float(c) <= 10.5, (
                f"atom {atom.get('label')} placed at {cart} -- outside "
                "the unit-cell box (MCK drift not subtracted from shift)"
            )


def test_fragment_face_membership_does_not_cartesian_explode():
    """Two atoms in the same molecule, each touching a *different* face,
    must NOT spawn a phantom (+x, +y, 0) replica of the whole fragment.

    Regression for a bug where ``boundary_shifts_for_fragment`` collected
    per-axis shift signals from independent atoms (atom A on x=0 ->
    shift +x; atom B on y=0 -> shift +y) and then took the Cartesian
    product, yielding shifts {(+x,0,0), (0,+y,0), (+x,+y,0)}. The
    (+x,+y,0) replica has no crystallographic basis -- it would only be
    legitimate if some single atom in the molecule lay on the (x=0, y=0)
    edge. The fragment translates as a whole, so the correct shift set
    is the *union* of per-atom shifts, not the product.
    """
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [
        _atom("H1", [0.0, 0.5, 0.5], M),
        _atom("H2", [0.5, 0.0, 0.5], M),
    ]
    for atom in atoms:
        atom["_source_molecule_index"] = 11
        atom["_wrapped_frac"] = np.array(atom["frac"], dtype=float)

    scene = build_scene_from_atoms(
        name="fragment_two_faces",
        title="Fragment Two Faces",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=scene_ops(),
        unwrapped_atoms=atoms,
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    # Originals (2) + replicas for {(+x,0,0), (0,+y,0)} applied to the
    # whole 2-atom fragment = 2 + 2 * 2 = 6. The cartesian-product bug
    # would have produced 2 + 3 * 2 = 8 (extra (+x,+y,0) copy).
    assert len(scene["draw_atoms"]) == 6
    image_shifts = sorted({
        tuple(atom.get("_image_shift", (0, 0, 0)))
        for atom in scene["draw_atoms"]
        if atom.get("_is_boundary_replica")
    })
    assert image_shifts == [(0, 1, 0), (1, 0, 0)]


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
