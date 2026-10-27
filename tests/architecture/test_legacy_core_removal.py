"""Section 7 deletion audit. T23."""

from __future__ import annotations

from pathlib import Path

import pytest

from mat_viewer.structure.snapshot import molecular_crystal_from_scene


ROOT = Path(__file__).resolve().parents[2]


def test_obsolete_symbols_are_gone():
    tree = (ROOT / "mat_viewer").read_text if False else None
    text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "mat_viewer").rglob("*.py"))
    assert "_STRUCTURAL_OP_KINDS" not in text
    assert "_DISPLAY_TO_STRUCTURAL_HINT" not in text
    assert "backend_operations" not in text
    assert "NotImplementedError(\"scene -> MolecularCrystal" not in text
    assert "mat_viewer.ops.source" not in text
    assert "mat_viewer.ops.display" not in text
    assert not (ROOT / "mat_viewer" / "app" / "backend_operations.py").exists()
    del tree


def test_rendered_scene_is_not_a_scientific_source():
    with pytest.raises(ValueError, match="not a scientific structure source"):
        molecular_crystal_from_scene({"draw_atoms": [{"elem": "C", "cart": [0, 0, 0]}]})


def test_explicit_snapshot_materializes_without_a_cif():
    crystal = molecular_crystal_from_scene(
        {
            "scientific_snapshot": {
                "lattice": [[4, 0, 0], [0, 4, 0], [0, 0, 4]],
                "pbc": [False, False, False],
                "sites": [
                    {
                        "site_id": "cluster:0",
                        "symbol": "C",
                        "label": "C1",
                        "occupancy": 1,
                        "cartesian": [1, 1, 1],
                        "molecule_index": 0,
                    }
                ],
            }
        }
    )
    assert crystal.pbc == (False, False, False)
    assert sum(len(molecule) for molecule in crystal.molecules) == 1
