"""Periodic distances use a general minimum image, including same-site neighbors."""

from __future__ import annotations

import numpy as np
import pytest
from ase import Atoms
from molcrys_kit.structures.crystal import MolecularCrystal

from mat_viewer.effects import EffectSession
from mat_viewer.effects.analysis import minimum_image_distance
from mat_viewer.effects.errors import ClashError


def test_skew_cell_minimum_image_is_shorter_than_componentwise_rounding():
    cell = np.array([[10.0, 0.0, 0.0], [8.0, 6.0, 0.0], [0.0, 0.0, 10.0]], dtype=float)
    displacement = np.array([0.49, 0.49, 0.0]) @ cell
    distance = minimum_image_distance(np.zeros(3), displacement, cell, (True, True, True))
    assert distance == pytest.approx(3.16796465, abs=1e-6)


def test_skew_insertion_rejects_the_true_periodic_clash():
    cell = np.array([[10.0, 0.0, 0.0], [8.0, 6.0, 0.0], [0.0, 0.0, 10.0]], dtype=float)
    host = MolecularCrystal(cell, [Atoms("C", positions=[[0.0, 0.0, 0.0]], cell=cell, pbc=True)], pbc=(True, True, True))
    session = EffectSession()
    ref = session.register(host, structure_id="skew")
    origin = np.array([0.49, 0.49, 0.0]) @ cell
    try:
        session.operate(
            "insert_molecule",
            {
                "symbols": ["He"],
                "positions": [[0.0, 0.0, 0.0]],
                "origin": origin.tolist(),
                "clash_policy": "reject",
                "clash_threshold": 4.0,
            },
            structure_id=ref.id,
            expected_revision=1,
        )
    except ClashError:
        pass
    else:
        raise AssertionError("skew-cell clash was treated as farther than 4 Å")
    assert session.current_ref("skew").revision == 1


def test_one_site_simple_cubic_coordination_includes_periodic_images():
    lattice = np.eye(3) * 3.0
    crystal = MolecularCrystal(
        lattice,
        [Atoms("C", positions=[[0.0, 0.0, 0.0]], cell=lattice, pbc=True)],
        pbc=(True, True, True),
    )
    session = EffectSession()
    ref = session.register(crystal, structure_id="sc")
    site_id = session.snapshot(ref).sites[0].site_id
    value = session.analyze(
        ref,
        {"kind": "coordination", "center_site_id": site_id, "cutoff": 3.2},
    ).to_dict()["value"]
    assert value["coordination_number"] == 6
    neighbors = value["neighbors"]
    assert len(neighbors) == 6
    assert {tuple(item["image"]) for item in neighbors} == {
        (1, 0, 0),
        (-1, 0, 0),
        (0, 1, 0),
        (0, -1, 0),
        (0, 0, 1),
        (0, 0, -1),
    }
    assert all(item["site_id"] == site_id for item in neighbors)
