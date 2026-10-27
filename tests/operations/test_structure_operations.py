"""Scientific structure operations T04, T05, T10–T15."""

from __future__ import annotations

import numpy as np

from effect_builders import WATER, f1_ordered_nonorthogonal, f4_disorder, f5_host
from mat_viewer.effects import EffectSession
from mat_viewer.effects.errors import AmbiguousSelection, ClashError, UnsupportedOperation, ValidationError
from mat_viewer.effects.operations import IMPLEMENTED_COMMANDS


def _session(crystal, name="struct", properties=None):
    session = EffectSession()
    ref = session.register(crystal, structure_id=name, properties=properties)
    return session, ref


def test_required_commands_are_executable():
    assert set(IMPLEMENTED_COMMANDS) == {
        "build_supercell",
        "build_slab",
        "insert_molecule",
        "delete_sites",
        "replace_species",
        "add_hydrogens",
        "remove_solvent",
        "materialize_selection",
        "commit_disorder_candidate",
    }
    assert "supercell" not in IMPLEMENTED_COMMANDS


def test_t04_build_supercell_nonorthogonal():
    crystal = f1_ordered_nonorthogonal()
    session, ref = _session(crystal, properties={"charges": [0.2, -0.2], "energy": -3.0})
    before = session.fingerprint(ref)
    result = session.operate(
        "build_supercell",
        {"a": 2, "b": 3, "c": 1},
        structure_id=ref.id,
        expected_revision=1,
        property_policy={
            "charges": {"action": "transform"},
            "energy": {"action": "drop", "reason": "supercell energy is not a multiple of the input"},
        },
    )
    original = session.snapshot(ref)
    output = session.snapshot(result.output_ref)
    assert original.n_sites == 2
    assert original.fingerprint == before
    assert output.n_sites == 12
    assert output.species_counts == {"C": 6, "O": 6}
    assert len(output.bonds) == 6
    assert abs(output.volume / original.volume - 6.0) < 1e-8
    assert output.lattice_parameters()["gamma"] != 90.0
    charges = session._frames[(ref.id, "0")][-1].properties["site"]["charges"]
    assert charges == (0.2, -0.2) * 6
    assert "energy" not in session._frames[(ref.id, "0")][-1].properties["structure"]
    assert session._frames[(ref.id, "0")][0].properties["structure"]["energy"] == -3.0
    images = {tuple(item["image"]) for item in result.mappings}
    assert (0, 0, 0) in images and (1, 2, 0) in images


def test_t05_strict_repetition_does_not_clamp():
    session, ref = _session(f1_ordered_nonorthogonal())
    for params in (
        {"a": 0, "b": 1, "c": 1},
        {"a": -2, "b": 1, "c": 1},
        {"a": 1.5, "b": 1, "c": 1},
        {"a": True, "b": 1, "c": 1},
    ):
        try:
            session.operate("build_supercell", params, structure_id=ref.id, expected_revision=1)
        except ValidationError:
            pass
        else:
            raise AssertionError(params)
    try:
        session.operate(
            "build_supercell",
            {"matrix": [[2, 1, 0], [0, 2, 0], [0, 0, 1]]},
            structure_id=ref.id,
            expected_revision=1,
        )
    except UnsupportedOperation:
        pass
    else:
        raise AssertionError("nondiagonal matrix")
    finite = f1_ordered_nonorthogonal()
    finite.pbc = (True, True, False)
    session.register(finite, structure_id="open")
    try:
        session.operate("build_supercell", {"a": 1, "b": 1, "c": 2}, structure_id="open", expected_revision=1)
    except ValidationError:
        pass
    else:
        raise AssertionError("nonperiodic")
    assert session.current_ref(ref.id).revision == 1
    assert session.counters.commits == 0


def test_t10_periodic_copy_is_one_transaction():
    session, ref = _session(f1_ordered_nonorthogonal())
    try:
        session.operate(
            "delete_sites",
            {"sites": [{"site_id": "m0:a0", "image": [1, 0, 0]}]},
            structure_id=ref.id,
            expected_revision=1,
        )
    except AmbiguousSelection:
        pass
    else:
        raise AssertionError("display image accepted")
    result = session.operate(
        "delete_periodic_copy",
        {"a": 2, "b": 2, "c": 2, "source_site_id": "m0:a0", "image": [1, 0, 0]},
        structure_id=ref.id,
        expected_revision=1,
    )
    assert session.snapshot(result.output_ref).n_sites == 15
    assert session.snapshot(ref).n_sites == 2
    assert session.counters.commits == 1
    try:
        session.operate(
            "delete_periodic_copy",
            {"a": 2, "b": 2, "c": 2, "source_site_id": "m0:a0", "image": [1, 0, 0]},
            structure_id=ref.id,
            expected_revision=session.current_ref(ref.id).revision,
            inject_failure="commit",
        )
    except Exception:
        pass
    assert session.snapshot(session.current_ref(ref.id)).n_sites == 15


def test_t11_insert_preview_cancel_clash_and_idempotency():
    session, ref = _session(f5_host())
    preview = session.preview(
        "insert_molecule",
        {**WATER, "origin": [8.0, 8.0, 8.0], "origin_system": "cartesian", "clash_policy": "reject"},
        structure_id=ref.id,
        expected_revision=1,
    )
    assert preview.status == "preview"
    assert session.counters.commits == 0
    session.cancel(preview.candidate_id)
    try:
        session.commit_candidate(preview.candidate_id, expected_revision=1)
    except Exception:
        pass
    assert session.current_ref(ref.id).revision == 1
    applied = session.operate(
        "insert_molecule",
        {**WATER, "origin": [8.0, 8.0, 8.0], "origin_system": "cartesian"},
        structure_id=ref.id,
        expected_revision=1,
        request_id="insert-1",
    )
    assert session.snapshot(applied.output_ref).n_sites == 5
    assert session.snapshot(ref).n_sites == 2
    replay = session.operate(
        "insert_molecule",
        {**WATER, "origin": [1.0, 1.0, 1.0]},
        structure_id=ref.id,
        expected_revision=1,
        request_id="insert-1",
    )
    assert replay.idempotent_replay
    assert session.snapshot(session.current_ref(ref.id)).n_sites == 5
    try:
        session.operate(
            "insert_molecule",
            {**WATER, "origin": [2.0, 2.0, 2.0]},
            structure_id=ref.id,
            expected_revision=session.current_ref(ref.id).revision,
        )
    except ClashError:
        pass
    else:
        raise AssertionError("clash")
    assert session.snapshot(session.current_ref(ref.id)).n_sites == 5


def test_t12_substitute_delete_hydrogen_and_solvent():
    session, ref = _session(f5_host(), properties={"charges": [0.4, -0.4]})
    replaced = session.operate(
        "replace_species",
        {"symbol": "N", "sites": [{"site_id": "m0:a0"}]},
        structure_id=ref.id,
        expected_revision=1,
        property_policy={"charges": {"action": "copy"}},
    )
    counts = session.snapshot(replaced.output_ref).species_counts
    assert counts["N"] == 1 and counts["O"] == 1 and "C" not in counts
    carbon = session.register(
        __import__("molcrys_kit.structures.crystal", fromlist=["MolecularCrystal"]).MolecularCrystal(
            np.eye(3) * 12.0,
            [__import__("ase", fromlist=["Atoms"]).Atoms("C", positions=[[3, 3, 3]], cell=np.eye(3) * 12, pbc=True)],
        ),
        structure_id="carbon",
    )
    hydrogen = session.operate("add_hydrogens", {}, structure_id="carbon", expected_revision=1)
    assert session.snapshot(hydrogen.output_ref).species_counts == {"C": 1, "H": 4}
    host = f5_host()
    water = __import__("ase", fromlist=["Atoms"]).Atoms(
        "OH2", positions=[[8, 8, 8], [8.8, 8.2, 8.1], [7.8, 8.9, 8]], cell=np.eye(3) * 12, pbc=True
    )
    solvated = __import__("molcrys_kit.structures.crystal", fromlist=["MolecularCrystal"]).MolecularCrystal(
        np.eye(3) * 12, [host.molecules[0].copy(), water], pbc=(True, True, True)
    )
    session.register(solvated, structure_id="solvent")
    removed = session.operate(
        "remove_solvent",
        {"targets": ["H2O"]},
        structure_id="solvent",
        expected_revision=1,
    )
    assert session.snapshot(removed.output_ref).species_counts == {"C": 1, "O": 1}
    # Hiding an element is not deletion: the source snapshot is unchanged.
    assert session.snapshot(ref).species_counts["C"] == 1


def test_t13_slab_preview_does_not_edit_bulk_and_keeps_angles():
    from ase import Atoms
    from molcrys_kit.structures.crystal import MolecularCrystal

    lattice = np.array([[7.0, 0.0, 0.0], [1.1, 8.0, 0.0], [0.4, 0.7, 9.0]])
    water = Atoms("OH2", positions=[[2, 2, 2], [2.8, 2.2, 2.1], [1.8, 2.9, 2]], cell=lattice, pbc=True)
    session, ref = _session(MolecularCrystal(lattice, [water]), name="slab")
    preview = session.preview(
        "build_slab",
        {"miller": [1, 0, 0], "layers": 1, "vacuum": 8.0},
        structure_id=ref.id,
        expected_revision=1,
    )
    assert session.current_ref(ref.id).revision == 1
    assert preview.provenance["pbc"][2] is False
    committed = session.commit_candidate(preview.candidate_id, expected_revision=1)
    output = session.snapshot(committed.output_ref)
    assert output.pbc == (True, True, False)
    angles = output.lattice_parameters()
    assert abs(angles["gamma"] - 90.0) > 1.0
    assert session.snapshot(ref).pbc == (True, True, True)
    assert session.snapshot(ref).n_sites == 3


def test_t14_disorder_candidate_is_the_scientific_structure():
    session, ref = _session(f4_disorder(), name="disorder")
    partial = [site for site in session.snapshot(ref).sites if site.symbol == "O"]
    assert all(site.occupancy == 0.5 for site in partial)
    carbon = next(site.site_id for site in session.snapshot(ref).sites if site.symbol == "C")
    deleted = session.operate(
        "delete_sites",
        {"sites": [{"site_id": carbon}]},
        structure_id=ref.id,
        expected_revision=1,
    )
    remaining = session.snapshot(deleted.output_ref).sites
    assert {site.symbol for site in remaining} == {"O"}
    assert all(site.occupancy == 0.5 for site in remaining)
    session.undo(ref.id)
    committed = session.operate(
        "commit_disorder_candidate",
        {"method": "enumerate", "count": 4, "candidate_index": 1, "seed": 1},
        structure_id=ref.id,
        expected_revision=1,
    )
    ordered = session.snapshot(committed.output_ref)
    assert ordered.species_counts == {"C": 1, "O": 1}
    assert session.snapshot(ref).species_counts == {"C": 1, "O": 2}


def test_t15_materialize_finite_cluster_rejects_ghosts():
    session, ref = _session(f1_ordered_nonorthogonal())
    result = session.operate(
        "materialize_selection",
        {"instances": [{"site_id": "m0:a0", "image": [0, 0, 0]}, {"site_id": "m0:a1", "image": [1, 0, 0]}], "pbc": False},
        structure_id=ref.id,
        expected_revision=1,
    )
    cluster = session.snapshot(result.output_ref)
    assert cluster.n_sites == 2
    assert cluster.pbc == (False, False, False)
    assert session.snapshot(ref).n_sites == 2
    shift = np.asarray([1, 0, 0], dtype=float) @ np.asarray(session.snapshot(ref).lattice, dtype=float)
    source = session.snapshot(ref)
    expected = np.asarray(source.site("m0:a1").cartesian) + shift
    # The cluster is recentered into its own cell; the two images keep their separation.
    carts = np.asarray([site.cartesian for site in cluster.sites])
    assert abs(np.linalg.norm(carts[1] - carts[0]) - np.linalg.norm(expected - np.asarray(source.site("m0:a0").cartesian))) < 1e-6
    try:
        session.operate(
            "materialize_selection",
            {"instances": [{"kind": "ghost"}]},
            structure_id=ref.id,
            expected_revision=session.current_ref(ref.id).revision,
        )
    except AmbiguousSelection:
        pass
    else:
        raise AssertionError("ghost")
