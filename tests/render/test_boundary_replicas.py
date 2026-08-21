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
import pytest
from types import SimpleNamespace

from mat_viewer.scene import build_scene_from_atoms, scene_ops


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


def _analysis(atoms, records):
    return SimpleNamespace(
        site_records=tuple(
            SimpleNamespace(
                global_index=index,
                molecule_index=0,
                local_index=index,
                fractional_position=np.asarray(atom["frac"], dtype=float),
                image_shift=(0, 0, 0),
                asym_index=index,
                sym_op_index=0,
            )
            for index, atom in enumerate(atoms)
        ),
        bond_records=list(records),
        bond_pairs=[
            tuple(sorted((int(record["left"]), int(record["right"]))))
            for record in records
        ],
        formula_unit_selection=None,
    )


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
        (
            atom["label"],
            round(float(atom["cart"][0]), 5),
            bool(atom.get("_is_fragment_boundary_replica")),
        )
        for atom in scene["draw_atoms"]
    )
    assert labels_by_x == [
        ("C1", 0.0, False),
        ("C1", 10.0, True),
        ("C2", 1.0, False),
        ("C2", 11.0, True),
    ]


def test_unwrapped_atoms_inherit_mck_molecule_provenance():
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [
        _atom("C1", [0.0, 0.5, 0.5], M),
        _atom("C2", [0.1, 0.5, 0.5], M),
    ]
    unwrapped = [dict(atom) for atom in atoms]

    scene = build_scene_from_atoms(
        name="record_provenance",
        title="Record provenance",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=scene_ops(),
        unwrapped_atoms=unwrapped,
        molcrys_analysis=_analysis(
            atoms,
            [{"left": 0, "right": 1, "right_image_shift": [0, 0, 0]}],
        ),
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    replicas = [
        atom
        for atom in scene["draw_atoms"]
        if atom.get("_is_fragment_boundary_replica")
    ]
    assert len(scene["draw_atoms"]) == 4
    assert sorted(atom["label"] for atom in replicas) == ["C1", "C2"]


def test_cell_spanning_component_is_not_replicated_as_a_whole_cell():
    M = np.eye(3) * 10.0
    atoms = [
        {**_atom("C1", [0.02, 0.5, 0.5], M), "_source_molecule_index": 0},
        {**_atom("C2", [1.02, 0.5, 0.5], M), "_source_molecule_index": 0},
    ]

    from mat_viewer.render.boundary_replicas import expand_boundary_replicas

    expanded = expand_boundary_replicas(atoms, M)

    assert len(expanded) == 2
    assert all(atom.get("_cell_spanning_component") for atom in expanded)
    assert not any(atom.get("_is_boundary_replica") for atom in expanded)


def test_cell_spanning_component_uses_signed_record_images():
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [
        {**_atom("C1", [0.02, 0.5, 0.5], M), "_source_molecule_index": 0},
        {**_atom("C2", [1.02, 0.5, 0.5], M), "_source_molecule_index": 0},
    ]
    records = [{"left": 0, "right": 1, "right_image_shift": [1, 0, 0]}]

    scene = build_scene_from_atoms(
        name="spanning_bond",
        title="Spanning bond",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=scene_ops(),
        unwrapped_atoms=atoms,
        molcrys_analysis=_analysis(atoms, records),
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    replicas = [
        (index, atom)
        for index, atom in enumerate(scene["draw_atoms"])
        if atom.get("_is_framework_context_replica")
    ]
    connected = {index for bond in scene["bonds"] for index in (bond["i"], bond["j"])}

    assert len(scene["draw_atoms"]) == 4
    assert len(scene["bonds"]) == 2
    assert {tuple(atom["_image_shift"]) for _, atom in replicas} == {
        (-1, 0, 0),
        (1, 0, 0),
    }
    assert all(index in connected for index, _ in replicas)


def test_cell_spanning_context_completes_boundary_coordination_shells():
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [
        {**_atom("Zn1", [0.05, 0.5, 0.5], M), "_source_molecule_index": 0},
        {**_atom("N1", [1.05, 0.5, 0.5], M), "_source_molecule_index": 0},
        {**_atom("N2", [0.15, 0.5, 0.5], M), "_source_molecule_index": 0},
        {**_atom("C1", [1.15, 0.5, 0.5], M), "_source_molecule_index": 0},
    ]
    records = [
        {"left": 0, "right": 1, "right_image_shift": [1, 0, 0]},
        {"left": 0, "right": 2, "right_image_shift": [0, 0, 0]},
        {"left": 1, "right": 3, "right_image_shift": [0, 0, 0]},
    ]

    scene = build_scene_from_atoms(
        name="spanning_coordination_shell",
        title="Spanning coordination shell",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=scene_ops(),
        unwrapped_atoms=atoms,
        molcrys_analysis=_analysis(atoms, records),
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    replicas = {
        (atom["label"], tuple(atom["_image_shift"]))
        for atom in scene["draw_atoms"]
        if atom.get("_is_framework_context_replica")
    }
    assert replicas == {
        ("Zn1", (-1, 0, 0)),
        ("N2", (-1, 0, 0)),
        ("N1", (1, 0, 0)),
        ("C1", (1, 0, 0)),
    }


def test_fragment_near_face_replicates_by_member_tolerance():
    """A near-face member gives the whole fragment an adjacent image.

    DAP-4 perchlorate has several ClO4 groups whose central Cl is at
    fractional coordinates like 0.012/0.988 rather than exactly 0/1. Treating
    only exact special positions makes the unit cell look asymmetric. Member
    extent, rather than a wrapped centroid, owns periodic-context triggering.
    """
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [
        _atom("Cl1", [0.988, 0.5, 0.5], M),
        _atom("O1", [0.970, 0.55, 0.5], M),
        _atom("O2", [1.010, 0.45, 0.5], M),
    ]
    for atom in atoms:
        atom["_source_molecule_index"] = 12
        atom["_wrapped_frac"] = np.array(atom["frac"], dtype=float)

    scene = build_scene_from_atoms(
        name="fragment_near_face",
        title="Fragment Near Face",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=scene_ops(),
        unwrapped_atoms=atoms,
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    assert len(scene["draw_atoms"]) == 6
    replicas = [
        atom
        for atom in scene["draw_atoms"]
        if atom.get("_is_fragment_boundary_replica")
    ]
    assert sorted(atom["label"] for atom in replicas) == ["Cl1", "O1", "O2"]
    assert {tuple(atom["_image_shift"]) for atom in replicas} == {(-1, 0, 0)}


def test_high_face_member_replicates_whole_fragment_to_negative_image():
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [
        _atom("C1", [0.99, 0.50, 0.50], M),
        _atom("C2", [0.94, 0.50, 0.50], M),
    ]
    for atom in atoms:
        atom["_source_molecule_index"] = 40
        atom["_wrapped_frac"] = np.array(atom["frac"], dtype=float)

    scene = build_scene_from_atoms(
        name="high_face_context",
        title="High face context",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=scene_ops(),
        unwrapped_atoms=atoms,
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    replicas = [
        atom
        for atom in scene["draw_atoms"]
        if atom.get("_is_fragment_boundary_replica")
    ]
    assert len(replicas) == 2
    assert {tuple(atom["_image_shift"]) for atom in replicas} == {(-1, 0, 0)}
    assert sorted(round(float(atom["frac"][0]), 2) for atom in replicas) == [
        -0.06,
        -0.01,
    ]


def test_low_face_member_replicates_whole_fragment_to_positive_image():
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [
        _atom("C1", [0.01, 0.50, 0.50], M),
        _atom("C2", [0.06, 0.50, 0.50], M),
    ]
    for atom in atoms:
        atom["_source_molecule_index"] = 41
        atom["_wrapped_frac"] = np.array(atom["frac"], dtype=float)

    scene = build_scene_from_atoms(
        name="low_face_context",
        title="Low face context",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=scene_ops(),
        unwrapped_atoms=atoms,
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    replicas = [
        atom
        for atom in scene["draw_atoms"]
        if atom.get("_is_fragment_boundary_replica")
    ]
    assert len(replicas) == 2
    assert {tuple(atom["_image_shift"]) for atom in replicas} == {(1, 0, 0)}
    assert sorted(round(float(atom["frac"][0]), 2) for atom in replicas) == [1.01, 1.06]


def test_member_extent_triggers_both_neighbour_images_even_when_centroid_is_middle():
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [
        _atom("C1", [0.99, 0.50, 0.50], M),
        _atom("C2", [0.01, 0.50, 0.50], M),
    ]
    for atom in atoms:
        atom["_source_molecule_index"] = 42
        atom["_wrapped_frac"] = np.array(atom["frac"], dtype=float)

    scene = build_scene_from_atoms(
        name="extent_not_centroid",
        title="Extent not centroid",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=scene_ops(),
        unwrapped_atoms=atoms,
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    shifts = {
        tuple(atom["_image_shift"])
        for atom in scene["draw_atoms"]
        if atom.get("_is_fragment_boundary_replica")
    }
    assert shifts == {(-1, 0, 0), (1, 0, 0)}
    assert len(scene["draw_atoms"]) == 6


def test_near_face_signals_from_different_members_do_not_form_diagonal_image():
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [
        _atom("C1", [0.01, 0.50, 0.50], M),
        _atom("C2", [0.50, 0.01, 0.50], M),
    ]
    for atom in atoms:
        atom["_source_molecule_index"] = 43
        atom["_wrapped_frac"] = np.array(atom["frac"], dtype=float)

    scene = build_scene_from_atoms(
        name="near_face_union",
        title="Near face union",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=scene_ops(),
        unwrapped_atoms=atoms,
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    shifts = {
        tuple(atom["_image_shift"])
        for atom in scene["draw_atoms"]
        if atom.get("_is_fragment_boundary_replica")
    }
    assert shifts == {(0, 1, 0), (1, 0, 0)}


def test_ungrouped_near_face_atoms_get_periodic_context_images():
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [
        _atom("C1", [0.99, 0.50, 0.50], M),
        _atom("C2", [0.01, 0.25, 0.25], M),
    ]

    scene = build_scene_from_atoms(
        name="ungrouped_context",
        title="Ungrouped context",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=scene_ops(),
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    replica_fracs = sorted(
        round(float(atom["frac"][0]), 2)
        for atom in scene["draw_atoms"]
        if atom.get("_is_boundary_replica")
    )
    assert replica_fracs == [-0.01, 1.01]


def test_near_face_replica_uses_triclinic_lattice_vector():
    cell = gemmi.UnitCell(8.0, 9.0, 10.0, 78.0, 96.0, 112.0)
    from mat_viewer.structure.geometry import ortho_matrix

    legacy_M, _ = ortho_matrix(cell)
    M = legacy_M.T
    atom = _atom("C1", [0.99, 0.40, 0.30], M)

    scene = build_scene_from_atoms(
        name="triclinic_context",
        title="Triclinic context",
        atoms=[atom],
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=scene_ops(),
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    original = next(
        item for item in scene["draw_atoms"] if not item.get("_is_boundary_replica")
    )
    replica = next(
        item for item in scene["draw_atoms"] if item.get("_is_boundary_replica")
    )
    np.testing.assert_allclose(replica["cart"] - original["cart"], -M[0])
    np.testing.assert_allclose(replica["frac"], [-0.01, 0.40, 0.30])


@pytest.mark.parametrize(
    "frac, expected_replicas",
    [
        (0.03, 1),
        (0.97, 1),
        (0.0301, 0),
        (0.9699, 0),
    ],
)
def test_periodic_context_tolerance_boundary(frac, expected_replicas):
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0

    scene = build_scene_from_atoms(
        name="tolerance_boundary",
        title="Tolerance boundary",
        atoms=[_atom("C1", [frac, 0.50, 0.50], M)],
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=scene_ops(),
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    assert (
        sum(bool(atom.get("_is_boundary_replica")) for atom in scene["draw_atoms"])
        == expected_replicas
    )


def test_minor_disorder_fragment_replicates_as_whole_fragment():
    """Minor disorder alternatives use the same whole-fragment boundary path.

    MCK now returns minor SHELX PART alternatives in ``mol_indices`` instead of
    leaving them as orphan atoms. Once that provenance is present, the unit-cell
    boundary convention must mirror the full minor fragment, preserving the
    minor styling metadata on the replica atoms.
    """
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [
        _atom("N3", [0.0, 0.25, 0.5], M),
        _atom("C5", [0.1, 0.25, 0.5], M),
    ]
    for atom in atoms:
        atom["_source_molecule_index"] = 17
        atom["_wrapped_frac"] = np.array(atom["frac"], dtype=float)
        atom["_is_minor"] = True
        atom["dg"] = "-1"

    scene = build_scene_from_atoms(
        name="minor_fragment_face",
        title="Minor Fragment Face",
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
    replicas = [
        atom
        for atom in scene["draw_atoms"]
        if atom.get("_is_fragment_boundary_replica")
    ]
    assert sorted(atom["label"] for atom in replicas) == ["C5", "N3"]
    assert {tuple(atom["_image_shift"]) for atom in replicas} == {(1, 0, 0)}
    assert all(atom.get("_is_minor") is True for atom in replicas)
    assert all(atom.get("is_minor") is True for atom in replicas)


def test_disorder_fragment_displayed_near_face_replicates_next_to_partners():
    """PART alternatives unwrapped to a display-cell face get neighbour images.

    SY ethylenediamine alternatives can be chemically paired across the cell
    boundary: one PART image is drawn near x=0.005 and the complementary image
    near x=0.995. Their canonical wrapped positions are not themselves near a
    face, so exact crystallographic boundary logic alone cannot make the two
    PARTs appear adjacent. For disorder fragments only, a display-centroid face
    tolerance adds the neighbouring image while ordinary boundary-crossing
    molecules remain protected by ``test_unit_cell_prefers_unwrapped_atoms...``.
    """
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [
        _atom("N3", [0.005, 0.5, 0.5], M),
        _atom("C5", [0.015, 0.5, 0.5], M),
    ]
    for atom in atoms:
        atom["_source_molecule_index"] = 23
        atom["_wrapped_frac"] = np.array([0.505, 0.5, 0.5])
        atom["_is_minor"] = True
        atom["dg"] = "-1"

    scene = build_scene_from_atoms(
        name="disorder_display_face",
        title="Disorder Display Face",
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
    replicas = [
        atom
        for atom in scene["draw_atoms"]
        if atom.get("_is_fragment_boundary_replica")
    ]
    assert sorted(atom["label"] for atom in replicas) == ["C5", "N3"]
    assert {tuple(atom["_image_shift"]) for atom in replicas} == {(1, 0, 0)}


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
    n_carts = sorted(
        tuple(round(float(c), 3) for c in atom["cart"]) for atom in n_atoms
    )
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
    image_shifts = sorted(
        {
            tuple(atom.get("_image_shift", (0, 0, 0)))
            for atom in scene["draw_atoms"]
            if atom.get("_is_boundary_replica")
        }
    )
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


def test_unwrapped_near_face_fragment_does_not_create_second_neighbour_images():
    """Near-face context may add the adjacent image, never a second neighbour."""
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    # Simulate the post-unwrap output: one atom inside the cell, one
    # drawn just outside the +x face for visual continuity.
    atoms = [
        _atom("C1", [0.98, 0.5, 0.5], M),
        _atom("C2", [1.02, 0.5, 0.5], M),
    ]
    for atom, wrapped in zip(atoms, ([0.98, 0.5, 0.5], [0.02, 0.5, 0.5])):
        atom["_source_molecule_index"] = 44
        atom["_wrapped_frac"] = np.array(wrapped, dtype=float)
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
    fracs = [float(atom["frac"][0]) for atom in scene["draw_atoms"]]
    assert len(scene["draw_atoms"]) == 4
    assert min(fracs) >= -0.02 - 1e-9
    assert max(fracs) <= 1.02 + 1e-9
    assert not any(
        abs(value - 2.02) < 1e-9 or abs(value + 1.02) < 1e-9 for value in fracs
    )


def test_unit_cell_can_omit_outside_boundary_replicas():
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    raw = [_atom("C1", [0.02, 0.5, 0.5], M)]
    unwrapped = [_atom("C1", [1.02, 0.5, 0.5], M)]

    scene = build_scene_from_atoms(
        name="strict_cell",
        title="Strict unit cell",
        atoms=raw,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        include_boundary_replicas=False,
        ops=scene_ops(),
        unwrapped_atoms=unwrapped,
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    assert len(scene["draw_atoms"]) == 1
    assert np.allclose(scene["draw_atoms"][0]["frac"], [0.02, 0.5, 0.5])
    assert scene["unit_cell_boundary_replicas"] is False
    assert not scene["draw_atoms"][0].get("_is_boundary_replica")


def test_strict_unit_cell_manifests_cross_boundary_bonded_images():
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [
        _atom("C1", [-0.02, 0.5, 0.5], M),
        _atom("C2", [0.02, 0.5, 0.5], M),
    ]
    records = [{"left": 0, "right": 1, "right_image_shift": [1, 0, 0]}]

    scene = build_scene_from_atoms(
        name="strict_cross_boundary_bond",
        title="Strict cross-boundary bond",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        include_boundary_replicas=False,
        ops=scene_ops(),
        molcrys_analysis=_analysis(atoms, records),
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    assert len(scene["draw_atoms"]) == 4
    assert len(scene["bonds"]) == 2
    assert scene["bonded_image_replica_count"] == 2
    home_atoms = [
        atom for atom in scene["draw_atoms"] if not atom.get("_is_bonded_image_replica")
    ]
    image_atoms = [
        atom for atom in scene["draw_atoms"] if atom.get("_is_bonded_image_replica")
    ]
    assert len(home_atoms) == 2
    assert len(image_atoms) == 2
    assert all(
        np.all(np.asarray(atom["frac"]) >= 0.0)
        and np.all(np.asarray(atom["frac"]) < 1.0)
        for atom in home_atoms
    )
    np.testing.assert_allclose(
        sorted(tuple(np.asarray(atom["frac"])) for atom in image_atoms),
        [(-0.02, 0.5, 0.5), (1.02, 0.5, 0.5)],
    )
    assert all(
        np.isclose(np.linalg.norm(bond["end"] - bond["start"]), 0.4)
        for bond in scene["bonds"]
    )


def test_strict_unit_cell_keeps_empty_mck_bond_contract_empty():
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [
        _atom("C1", [0.05, 0.5, 0.5], M),
        _atom("C2", [0.15, 0.5, 0.5], M),
        _atom("C3", [0.95, 0.5, 0.5], M),
    ]

    scene = build_scene_from_atoms(
        name="strict_missing_cross_boundary_bond",
        title="Strict missing cross-boundary bond",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        include_boundary_replicas=False,
        ops=scene_ops(),
        molcrys_analysis=_analysis(atoms, []),
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    assert scene["bonded_image_replica_count"] == 0
    assert len(scene["draw_atoms"]) == 3
    assert scene["bonds"] == []


def test_strict_unit_cell_preserves_in_cell_canonical_bonds():
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [
        _atom("C1", [0.40, 0.5, 0.5], M),
        _atom("C2", [0.52, 0.5, 0.5], M),
    ]
    records = [{"left": 0, "right": 1, "right_image_shift": [0, 0, 0]}]

    scene = build_scene_from_atoms(
        name="strict_in_cell_bond",
        title="Strict in-cell bond",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        include_boundary_replicas=False,
        ops=scene_ops(),
        molcrys_analysis=_analysis(atoms, records),
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    assert [atom["_source_index"] for atom in scene["draw_atoms"]] == [0, 1]
    assert len(scene["bonds"]) == 1
    assert np.allclose(scene["bonds"][0]["start"], [4.0, 5.0, 5.0])
    assert np.allclose(scene["bonds"][0]["end"], [5.2, 5.0, 5.0])


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


@pytest.mark.parametrize("display_mode", ["formula_unit", "asymmetric_unit", "cluster"])
def test_near_face_context_is_unit_cell_only(display_mode):
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atom = _atom("C1", [0.99, 0.50, 0.50], M)

    scene = build_scene_from_atoms(
        name="mode_isolation",
        title="Mode isolation",
        atoms=[atom],
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode=display_mode,
        ops=scene_ops(),
        formula_unit_atoms=[atom],
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    assert len(scene["draw_atoms"]) == 1
    assert not scene["draw_atoms"][0].get("_is_boundary_replica")
