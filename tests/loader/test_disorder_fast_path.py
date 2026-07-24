from __future__ import annotations

from crystal_viewer.loader import core as loader_core


def _atom(label: str, elem: str, occ: float, da: str, dg: str) -> dict:
    return {
        "label": label,
        "_asym_label": label,
        "elem": elem,
        "occ": occ,
        "da": da,
        "dg": dg,
    }


def test_simple_explicit_assembly_uses_occupancy_fast_path():
    raw_atoms = [
        _atom("Co1", "Co", 0.52, "A", "1"),
        _atom("Ni1", "Ni", 0.48, "A", "2"),
    ]

    # The fast path runs before importing the full solver. If it stops
    # matching, the dummy CIF path will leave these atoms untagged and the
    # assertions below fail.
    out = loader_core._tag_shelx_occupancy_disorder(raw_atoms, "dummy.cif", None)

    assert out[0]["_is_minor"] is False
    assert out[1]["_is_minor"] is True
    assert out[0]["_mv_auto_disorder_assembly"] == "A"
    assert out[1]["_mv_auto_disorder_group"] == "2"


def test_equal_occupancy_explicit_assembly_falls_back():
    raw_atoms = [
        _atom("C1A", "C", 0.5, "A", "1"),
        _atom("C1B", "C", 0.5, "A", "2"),
    ]

    assert loader_core._simple_explicit_assembly_major_groups(raw_atoms) is None


def test_partial_site_disorder_inside_ordered_structure_falls_back():
    raw_atoms = [
        _atom("Si1A", "Si", 0.85, "A", "1"),
        _atom("Si1B", "Si", 0.15, "A", "2"),
        _atom("O1", "O", 1.0, ".", "."),
    ]

    assert loader_core._simple_explicit_assembly_major_groups(raw_atoms) is None


def test_shelx_part_disorder_does_not_use_simple_fast_path():
    raw_atoms = [
        _atom("N1A", "N", 0.6, ".", "-1"),
        _atom("N1B", "N", 0.4, ".", "-2"),
    ]

    assert loader_core._simple_explicit_assembly_major_groups(raw_atoms) is None
