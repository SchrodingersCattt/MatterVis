from __future__ import annotations

import gemmi
import numpy as np

from mat_viewer.loader import build_bundle_scene, build_loaded_crystal
from mat_viewer.scene import build_scene_from_atoms, scene_ops


def _atom(label: str, element: str, frac, M, source_index: int) -> dict:
    frac = np.asarray(frac, dtype=float)
    return {
        "label": label,
        "elem": element,
        "frac": frac,
        "cart": frac @ M,
        "occ": 1.0,
        "dg": ".",
        "da": ".",
        "_source_index": source_index,
        "_source_molecule_index": 0,
        "_wrapped_frac": frac.copy(),
        "_unwrapped": True,
    }


def _ops_without_redetection():
    ops = scene_ops()
    ops.find_bonds = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("canonical record assembly must not re-detect display bonds")
    )
    return ops


def _source_pair_set(scene: dict) -> set[tuple[int, int]]:
    atoms = scene["draw_atoms"]
    return {
        tuple(sorted((int(atoms[bond["i"]]["_source_index"]), int(atoms[bond["j"]]["_source_index"]))))
        for bond in scene["bonds"]
    }


def test_face_fragment_replica_lifts_its_canonical_internal_bond():
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [
        _atom("C1", "C", [0.99, 0.5, 0.5], M, 0),
        _atom("C2", "C", [0.94, 0.5, 0.5], M, 1),
    ]

    scene = build_scene_from_atoms(
        name="face-bond",
        title="face-bond",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=_ops_without_redetection(),
        unwrapped_atoms=atoms,
        canonical_bond_pairs=[(0, 1)],
        canonical_bond_records=[
            {"left": 0, "right": 1, "right_image_shift": [0, 0, 0], "vector": [-0.5, 0.0, 0.0]}
        ],
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    assert len(scene["draw_atoms"]) == 4
    assert len(scene["bonds"]) == 2
    assert _source_pair_set(scene) == {(0, 1)}
    assert sorted(round(float(np.linalg.norm(bond["end"] - bond["start"])), 6) for bond in scene["bonds"]) == [0.5, 0.5]
    replica_indices = {
        index
        for index, atom in enumerate(scene["draw_atoms"])
        if atom.get("_is_fragment_boundary_replica")
    }
    assert any(bond["i"] in replica_indices and bond["j"] in replica_indices for bond in scene["bonds"])


def test_canonical_record_preserves_cross_cell_image_relation():
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    raw_atoms = [
        _atom("C1", "C", [0.98, 0.5, 0.5], M, 0),
        _atom("C2", "C", [0.02, 0.5, 0.5], M, 1),
    ]
    for atom in raw_atoms:
        # Exercise source-image record lifting directly; whole-fragment
        # boundary replication has its own coverage above.
        atom.pop("_source_molecule_index")
    display_atoms = [dict(raw_atoms[0]), dict(raw_atoms[1])]
    display_atoms[1]["frac"] = np.array([1.02, 0.5, 0.5])
    display_atoms[1]["cart"] = display_atoms[1]["frac"] @ M

    scene = build_scene_from_atoms(
        name="cross-cell",
        title="cross-cell",
        atoms=raw_atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=_ops_without_redetection(),
        unwrapped_atoms=display_atoms,
        canonical_bond_pairs=[(0, 1)],
        canonical_bond_records=[
            {"left": 0, "right": 1, "right_image_shift": [1, 0, 0], "vector": [0.4, 0.0, 0.0]}
        ],
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    assert len(scene["draw_atoms"]) == 4
    assert len(scene["bonds"]) == 2
    for bond in scene["bonds"]:
        np.testing.assert_allclose(bond["end"] - bond["start"], [0.4, 0.0, 0.0])


def test_canonical_lift_does_not_cross_connect_duplicate_display_images():
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [
        _atom("C1", "C", [0.0, 0.5, 0.5], M, 0),
        _atom("C2", "C", [0.1, 0.5, 0.5], M, 1),
    ]

    scene = build_scene_from_atoms(
        name="no-cartesian-product",
        title="no-cartesian-product",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=_ops_without_redetection(),
        unwrapped_atoms=atoms,
        canonical_bond_pairs=[(0, 1)],
        canonical_bond_records=[
            {"left": 0, "right": 1, "right_image_shift": [0, 0, 0], "vector": [1.0, 0.0, 0.0]}
        ],
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    assert len(scene["bonds"]) == 2
    assert all(np.linalg.norm(bond["end"] - bond["start"]) < 3.5 for bond in scene["bonds"])


def test_dap4_boundary_fragment_instances_keep_all_internal_canonical_bonds(monkeypatch):
    bundle = build_loaded_crystal(
        name="DAP-4",
        cif_path="scripts/data/DAP-4.cif",
        title="DAP-4",
    )
    monkeypatch.setattr(
        "mat_viewer.scene.core.find_bonds",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unit-cell canonical records must not re-detect bonds"),
        ),
    )

    scene = build_bundle_scene(bundle, display_mode="unit_cell", show_hydrogen=True, preset={})
    atoms = scene["draw_atoms"]
    canonical = {tuple(sorted(pair)) for pair in bundle.molcrys_analysis.bond_pairs}
    replicas: dict[tuple[int, tuple[int, int, int]], list[int]] = {}
    for index, atom in enumerate(atoms):
        if not atom.get("_is_fragment_boundary_replica"):
            continue
        key = (
            int(atom["_source_molecule_index"]),
            tuple(int(value) for value in atom["_image_shift"]),
        )
        replicas.setdefault(key, []).append(index)

    assert replicas
    for indices in replicas.values():
        source_indices = {int(atoms[index]["_source_index"]) for index in indices}
        expected = sum(1 for left, right in canonical if {left, right} <= source_indices)
        actual = sum(1 for bond in scene["bonds"] if bond["i"] in indices and bond["j"] in indices)
        assert actual == expected


def test_strict_cell_completes_a_cross_boundary_tetrahedral_fragment():
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [
        _atom("Cl1", "Cl", [0.02, 0.50, 0.50], M, 0),
        _atom("O1", "O", [0.08, 0.50, 0.50], M, 1),
        _atom("O2", "O", [0.02, 0.56, 0.50], M, 2),
        _atom("O3", "O", [0.02, 0.50, 0.56], M, 3),
        _atom("O4", "O", [0.98, 0.50, 0.50], M, 4),
    ]
    records = [
        {"left": 0, "right": 1, "right_image_shift": [0, 0, 0]},
        {"left": 0, "right": 2, "right_image_shift": [0, 0, 0]},
        {"left": 0, "right": 3, "right_image_shift": [0, 0, 0]},
        {"left": 0, "right": 4, "right_image_shift": [-1, 0, 0]},
    ]
    scene = build_scene_from_atoms(
        name="strict-complete-tetrahedron",
        title="strict-complete-tetrahedron",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        include_boundary_replicas=False,
        ops=_ops_without_redetection(),
        unwrapped_atoms=atoms,
        canonical_bond_pairs=[(0, 1), (0, 2), (0, 3), (0, 4)],
        canonical_bond_records=records,
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    degrees = [0] * len(scene["draw_atoms"])
    for bond in scene["bonds"]:
        degrees[bond["i"]] += 1
        degrees[bond["j"]] += 1
    home_cl = next(index for index, atom in enumerate(scene["draw_atoms"]) if atom["_source_index"] == 0 and not atom.get("_is_bonded_image_replica"))
    image_cl = [index for index, atom in enumerate(scene["draw_atoms"]) if atom["_source_index"] == 0 and atom.get("_is_bonded_image_replica")]
    image_atoms = [index for index, atom in enumerate(scene["draw_atoms"]) if atom.get("_is_bonded_image_replica")]

    assert len(image_cl) == 1
    assert degrees[home_cl] == degrees[image_cl[0]] == 4
    assert len(image_atoms) == 5
    assert all(degrees[index] > 0 for index in image_atoms)
