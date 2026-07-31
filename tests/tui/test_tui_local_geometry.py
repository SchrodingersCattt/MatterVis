from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from crystal_viewer.tui.controller import TerminalViewController
from crystal_viewer.tui.crystal_ir import AtomIR, BondIR, CrystalIR, Lattice


ROOT = Path(__file__).resolve().parents[2]
DIRTY_VASP = Path(__file__).parent / "fixtures" / "dirty_geometry.vasp"


def _crystal() -> CrystalIR:
    matrix = np.diag([10.0, 10.0, 10.0])
    atoms = [
        AtomIR("N", np.array([0.0, 0.0, 0.0]), np.zeros(3), label="N1", index=0, source_index=0, display_copy_id="N1/source:0/image:0,0,0"),
        AtomIR("C", np.array([1.0, 0.0, 0.0]), np.array([0.1, 0.0, 0.0]), label="C2", index=1, source_index=1, display_copy_id="C2/source:1/image:0,0,0"),
        AtomIR("C", np.array([0.5, np.sqrt(3.0) / 2.0, 0.0]), np.array([0.05, np.sqrt(3.0) / 20.0, 0.0]), label="C3", index=2, source_index=2, display_copy_id="C3/source:2/image:0,0,0"),
        AtomIR("Cl", np.array([9.0, 0.0, 0.0]), np.array([0.9, 0.0, 0.0]), label="Cl4", index=3, source_index=3, display_copy_id="Cl4/source:3/image:0,0,0"),
    ]
    return CrystalIR(
        atoms=atoms,
        bonds=[
            BondIR(0, 1, distance=1.0),
            BondIR(0, 2, distance=1.0),
            BondIR(0, 3, distance=9.0),
        ],
        lattice=Lattice(10.0, 10.0, 10.0, 90.0, 90.0, 90.0, matrix),
        metadata={"bond_source": "distance_heuristic", "explicit_bond_table": False},
    )


def test_local_geometry_reports_manifested_neighbors_angles_and_mic() -> None:
    controller = TerminalViewController(_crystal())
    before = controller.state

    result = controller.inspect_local_geometry({"label": "N1"})

    assert controller.state == before
    assert result["center"]["label"] == "N1"
    assert result["coordination_number"] == 3
    assert [bond["neighbor_label"] for bond in result["bonds"]] == ["C2", "C3", "Cl4"]
    periodic = next(bond for bond in result["bonds"] if bond["neighbor_label"] == "Cl4")
    assert periodic["rendered_distance"] == pytest.approx(9.0)
    assert periodic["direct_distance"] == pytest.approx(9.0)
    assert periodic["mic_distance"] == pytest.approx(1.0)
    assert periodic["nearest_image_shift"] == [-1, 0, 0]
    angle = next(item for item in result["angles"] if item["atoms"] == ["C2", "N1", "C3"])
    assert angle["angle_deg"] == pytest.approx(60.0)
    assert result["topology_provenance"] == {
        "source": "distance_heuristic",
        "explicit_bond_table": False,
        "neighbor_scope": "manifested_display_bonds",
        "angle_vectors": "minimum_image",
    }


def test_local_geometry_requires_one_exact_display_atom() -> None:
    crystal = _crystal()
    crystal.atoms.append(
        AtomIR("N", np.array([2.0, 0.0, 0.0]), np.array([0.2, 0.0, 0.0]), label="N1", index=4, source_index=0, display_copy_id="N1/source:0/image:1,0,0")
    )
    controller = TerminalViewController(crystal)

    with pytest.raises(ValueError, match="exactly one"):
        controller.inspect_local_geometry({"label": "N1"})

    result = controller.inspect_local_geometry({"display_copy_id": "N1/source:0/image:0,0,0"}, include_angles=False)
    assert result["center"]["display_index"] == 0
    assert result["angles"] == []


def test_batch_local_geometry_can_read_selected_or_all_atoms_without_mutation() -> None:
    controller = TerminalViewController(_crystal())
    before = controller.state

    selected = controller.inspect_local_geometries(
        [{"label": "N1"}, {"label": "Cl4"}],
        include_angles=False,
    )
    all_atoms = controller.inspect_local_geometries(include_angles=False)

    assert controller.state == before
    assert selected["count"] == 2
    assert [item["center"]["label"] for item in selected["geometries"]] == ["N1", "Cl4"]
    assert all_atoms["count"] == 4
    assert all(item["angles"] == [] for item in all_atoms["geometries"])


def test_dirty_vasp_exposes_cn_small_angles_and_periodic_long_bond() -> None:
    controller = TerminalViewController.from_file(str(DIRTY_VASP))

    nitrogen = controller.inspect_local_geometry({"label": "N9"})
    carbon = controller.inspect_local_geometry({"label": "C14"})
    cadmium = controller.inspect_local_geometry({"label": "Cd2"})

    assert nitrogen["coordination_number"] == 4
    assert next(
        angle["angle_deg"]
        for angle in nitrogen["angles"]
        if angle["atoms"] == ["C12", "N9", "C13"]
    ) == pytest.approx(46.02, abs=0.01)
    assert next(
        angle["angle_deg"]
        for angle in carbon["angles"]
        if angle["atoms"] == ["C12", "C14", "C13"]
    ) == pytest.approx(45.68, abs=0.01)
    periodic = next(bond for bond in cadmium["bonds"] if bond["neighbor_label"] == "Cl3")
    assert periodic["rendered_distance"] == pytest.approx(5.4905, abs=0.0001)
    assert periodic["mic_distance"] == pytest.approx(2.6451, abs=0.0001)
    assert periodic["nearest_image_shift"] == [0, 0, 1]
    assert cadmium["topology_provenance"]["source"] == "distance_heuristic"