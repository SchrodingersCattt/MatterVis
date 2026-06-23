from __future__ import annotations

from pathlib import Path

from crystal_viewer.loader import build_loaded_crystal


ROOT = Path(__file__).resolve().parents[2]
EXTXYZ = ROOT / "_tmp" / "halogen_bond.extxyz"


def test_build_loaded_crystal_from_extxyz() -> None:
    bundle = build_loaded_crystal(
        name="halogen_bond",
        cif_path=str(EXTXYZ),
        title="halogen_bond",
        source="upload",
        source_format="extxyz",
    )

    assert bundle.source_format == "extxyz"
    assert bundle.source_path == str(EXTXYZ)
    assert bundle.cif_path == str(EXTXYZ)
    assert bundle.source_frame_index == 0
    assert bundle.source_frame_count == 20
    assert len(bundle.raw_atoms) == 248
    assert bundle.formula_unit_atoms
    assert bundle.unwrapped_atoms
    assert bundle.fragment_table
    assert bundle.topology_fragment_table
    assert all("source_molecule_index" in frag for frag in bundle.topology_fragment_table)
    assert bundle.scene["source_format"] == "extxyz"
