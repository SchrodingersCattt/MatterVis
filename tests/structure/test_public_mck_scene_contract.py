from __future__ import annotations

from types import SimpleNamespace

import gemmi
import numpy as np
import pytest

import mat_viewer.scene as scene_api
from mat_viewer.scene import build_scene_from_atoms, build_scene_from_cif
from mat_viewer.structure import molcrys_bridge
from mat_viewer.viewpoint import auto_view_dir


def _atom() -> dict:
    return {
        "label": "C1",
        "elem": "C",
        "frac": np.array([0.25, 0.25, 0.25]),
        "cart": np.array([2.5, 2.5, 2.5]),
        "occ": 1.0,
        "dg": ".",
        "da": ".",
    }


def _site() -> SimpleNamespace:
    return SimpleNamespace(
        global_index=0,
        molecule_index=0,
        local_index=0,
        fractional_position=np.array([0.25, 0.25, 0.25]),
        image_shift=(0, 0, 0),
        asym_index=0,
        sym_op_index=0,
    )


def test_scene_facade_does_not_publish_local_chemistry_helpers() -> None:
    assert not hasattr(scene_api, "find_bonds")
    assert not hasattr(scene_api, "cluster_atoms")
    assert not hasattr(scene_api, "select_formula_unit")
    assert not hasattr(scene_api, "legacy_scene")


def test_scene_missing_bond_records_is_fatal() -> None:
    analysis = SimpleNamespace(site_records=(_site(),), formula_unit_selection=None)
    with pytest.raises(
        molcrys_bridge.StructureContractError,
        match="BondRecord",
    ):
        build_scene_from_atoms(
            name="missing-records",
            title="missing-records",
            atoms=[_atom()],
            cell=gemmi.UnitCell(10, 10, 10, 90, 90, 90),
            M=np.eye(3) * 10.0,
            R=np.eye(3),
            display_mode="unit_cell",
            molcrys_analysis=analysis,
        )


def test_auto_view_does_not_swallow_missing_formula_selection() -> None:
    analysis = SimpleNamespace(site_records=(_site(),), bond_records=[])
    with pytest.raises(
        molcrys_bridge.StructureContractError,
        match="FormulaUnitSelection",
    ):
        auto_view_dir(
            [_atom()],
            np.eye(3) * 10.0,
            gemmi.UnitCell(10, 10, 10, 90, 90, 90),
            molcrys_analysis=analysis,
        )


def test_build_scene_from_cif_never_calls_local_find_bonds(monkeypatch) -> None:
    monkeypatch.setattr(
        "mat_viewer.structure.bonds.find_bonds",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("public CIF scene path called local find_bonds")
        ),
    )
    scene = build_scene_from_cif(
        name="DAP-4",
        title="DAP-4",
        cif_path="scripts/data/DAP-4.cif",
        display_mode="unit_cell",
    )
    assert scene["_canonical_site_records"]
    assert scene["_canonical_bond_records"]
