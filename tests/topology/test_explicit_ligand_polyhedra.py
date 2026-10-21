"""Polyhedron rows need an explicit ligand; ``(auto)`` must not reach MolCrysKit."""
from __future__ import annotations

from pathlib import Path

import pytest

from mat_viewer.app import WORKSPACE_DIR, ViewerBackend
from mat_viewer.app.callbacks_analysis import default_polyhedron_ligand
from mat_viewer.presets import default_preset_path
from mat_viewer.topology.analysis import EXPLICIT_LIGAND_MESSAGE, explicit_ligand_species


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
