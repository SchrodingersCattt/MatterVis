"""Polyhedron rows need an explicit ligand; ``(auto)`` must not reach MolCrysKit."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from mat_viewer.app import WORKSPACE_DIR, ViewerBackend
from mat_viewer.app.callbacks_analysis import default_polyhedron_ligand
from mat_viewer.presets import default_preset_path
from mat_viewer.topology import analysis
from mat_viewer.topology.analysis import (
    EXPLICIT_LIGAND_MESSAGE,
    explicit_ligand_species,
)


@pytest.fixture
def backend(tmp_path: Path) -> ViewerBackend:
    viewer = ViewerBackend(preset_path=default_preset_path(), root_dir=WORKSPACE_DIR)
    try:
        yield viewer
    finally:
        viewer.close()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("(auto)", None),
        ("__auto__", None),
        ("auto", None),
        ("ClO4", "ClO4"),
        (["", "O"], "O"),
    ],
)
def test_explicit_ligand_species_rejects_auto_token(raw, expected):
    assert explicit_ligand_species(raw) == expected


def test_missing_ligand_skips_molcryskit_and_explains_why(backend, monkeypatch):
    def _forbidden(*_args, **_kwargs):
        raise AssertionError("MolCrysKit was called without an explicit ligand")

    monkeypatch.setattr(
        "mat_viewer.app.topology_geometry.analyze_topology", _forbidden
    )
    monkeypatch.setattr(
        "mat_viewer.app.topology_geometry.extract_coordination_shell", _forbidden
    )
    monkeypatch.setattr(
        "mat_viewer.app.topology_geometry.extract_atom_coordination_shells", _forbidden
    )

    state = backend.get_state()
    state["topology_enabled"] = True
    state["polyhedron_specs"] = [
        {
            "id": "auto-row",
            "name": "auto-row",
            "center_species": "N",
            "ligand_species": None,
            "enabled": True,
            "level": "molecule",
        }
    ]

    topology = backend.topology_for_state(state)

    assert topology is not None
    assert topology["spec_results"][0]["overlays"] == []
    assert any(EXPLICIT_LIGAND_MESSAGE in warning for warning in topology.get("warnings") or [])


def test_default_add_path_uses_explicit_ligand_and_renders(backend):
    options = backend.species_options()
    assert len(options) >= 2
    center = str(options[0]["value"])
    ligand = default_polyhedron_ligand(options, center)

    assert ligand
    assert ligand != center
    assert explicit_ligand_species(ligand) == ligand

    state = backend.get_state()
    state["topology_enabled"] = True
    state["polyhedron_specs"] = [
        {
            "id": "default-row",
            "name": f"{center}-{ligand}",
            "center_species": center,
            "ligand_species": ligand,
            "enabled": True,
            "level": "molecule",
        }
    ]
    topology = backend.topology_for_state(state)

    assert topology is not None
    entry = next(item for item in topology["spec_results"] if item["spec_id"] == "default-row")
    assert entry["ligand_species"] == ligand
    assert any(
        (overlay.get("hull") or {}).get("simplices")
        for overlay in entry["overlays"]
    )


def _forbid_molcryskit(monkeypatch) -> list:
    calls: list = []

    def _fake_find_polyhedra(*args, **_kwargs):
        calls.append(args)
        return []

    monkeypatch.setattr(
        "mat_viewer.topology.analysis.find_polyhedra", _fake_find_polyhedra
    )
    monkeypatch.setattr(
        "mat_viewer.structure.molcrys_bridge.molecular_crystal_from_bundle",
        lambda bundle: object(),
    )
    return calls


def _center_bundle() -> SimpleNamespace:
    return SimpleNamespace(
        fragment_table=[
            {
                "index": 0,
                "center": [0.0, 0.0, 0.0],
                "formula": "Ca",
                "label": "Ca1",
                "type": "molecule",
                "source_molecule_index": 0,
            }
        ],
        topology_fragment_table=None,
    )


@pytest.mark.parametrize(
    "ligand",
    ["(auto)", "", "  ", None, ["(auto)"], ("(auto)", ""), ["", "(auto)"]],
)
def test_auto_or_empty_ligand_skips_molcryskit(monkeypatch, ligand):
    calls = _forbid_molcryskit(monkeypatch)
    bundle = _center_bundle()
    fragment = bundle.fragment_table[0]

    with pytest.raises(ValueError, match=re.escape(EXPLICIT_LIGAND_MESSAGE)):
        analysis._mck_polyhedron_record(
            bundle,
            fragment,
            8.0,
            ligand_species=ligand,
            level="atom",
            center_species="Ca",
        )
    with pytest.raises(ValueError, match=re.escape(EXPLICIT_LIGAND_MESSAGE)):
        analysis.extract_atom_coordination_shells(
            bundle,
            4.0,
            center_species="Ca",
            ligand_species=ligand if ligand is not None else "",
        )
    with pytest.raises(ValueError, match=re.escape(EXPLICIT_LIGAND_MESSAGE)):
        analysis.extract_coordination_shell(
            bundle,
            0,
            ligand_species=ligand,
            level="atom",
            center_species="Ca",
        )
    with pytest.raises(ValueError, match=re.escape(EXPLICIT_LIGAND_MESSAGE)):
        analysis.analyze_topology(
            bundle,
            0,
            ligand_species=ligand,
            level="atom",
            center_species="Ca",
        )

    assert calls == []


def test_leading_auto_token_forwards_only_the_real_ligand(monkeypatch):
    calls = _forbid_molcryskit(monkeypatch)
    bundle = _center_bundle()

    analysis._mck_polyhedron_record(
        bundle,
        bundle.fragment_table[0],
        8.0,
        ligand_species=("(auto)", "", "F"),
        level="atom",
        center_species="Ca",
    )
    analysis.extract_atom_coordination_shells(
        bundle,
        4.0,
        center_species="Ca",
        ligand_species=["(auto)", "O"],
    )
    analysis.extract_coordination_shell(
        bundle,
        0,
        ligand_species=["(auto)", "Cl"],
        level="atom",
        center_species="Ca",
    )
    analysis.analyze_topology(
        bundle,
        0,
        ligand_species=["(auto)", "Br"],
        level="atom",
        center_species="Ca",
    )

    forwarded = [call[2] for call in calls]
    assert forwarded == ["F", "O", "Cl", "Br"]
    assert "(auto)" not in forwarded
