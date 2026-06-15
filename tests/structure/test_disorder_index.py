"""Coordinate bridge between MCK disorder indices and MatterVis raw atoms.

Regression guard for the DAN-2 bug: MolCrysKit's ``scan_cif_disorder`` and
MatterVis' ``parse_asu`` expand symmetry independently, so ``kept_indices``
must be mapped to ``raw_atoms`` by coordinate, never by positional index.
"""

from __future__ import annotations

import crystal_viewer.structure.disorder_index as di


def _raw(label, elem, frac):
    return {"label": label, "elem": elem, "frac": list(frac)}


def test_maps_by_coordinate_when_index_spaces_diverge(monkeypatch):
    # MCK DisorderInfo: (symbol, fx, fy, fz). Note index 2 (the kept O at
    # 0.90) has NO positional twin in raw_atoms below -- only a coordinate
    # twin at raw index 1.
    mck_rows = (
        ("K", 0.0, 0.0, 0.0),       # 0 -> raw 0
        ("O", 0.10, 0.20, 0.30),    # 1 -> raw 2 (minor alternate, not kept)
        ("O", 0.90, 0.80, 0.70),    # 2 -> raw 1 (major, kept)
    )
    monkeypatch.setattr(di, "_scan_disorder_info", lambda _p: mck_rows)

    raw_atoms = [
        _raw("K1", "K", (0.0, 0.0, 0.0)),       # 0
        _raw("O1b", "O", (0.90, 0.80, 0.70)),   # 1  <- coordinate twin of MCK 2
        _raw("O1a", "O", (0.10, 0.20, 0.30)),   # 2  <- coordinate twin of MCK 1
    ]

    mapping = di.map_mck_indices_to_raw("dummy.cif", raw_atoms, [0, 2])
    # Positional would wrongly give {0:0, 2:2}; coordinate gives {0:0, 2:1}.
    assert mapping == {0: 0, 2: 1}


def test_handles_periodic_wrap_and_skips_unmatched(monkeypatch):
    mck_rows = (
        ("N", 0.999, 0.0, 0.0),   # wraps to ~0.0 -> matches raw at 0.001
        ("C", 0.5, 0.5, 0.5),     # no carbon in raw -> dropped
    )
    monkeypatch.setattr(di, "_scan_disorder_info", lambda _p: mck_rows)
    raw_atoms = [_raw("N1", "N", (0.001, 0.0, 0.0))]

    mapping = di.map_mck_indices_to_raw("dummy.cif", raw_atoms, [0, 1])
    assert mapping == {0: 0}


def test_returns_empty_when_scan_unavailable(monkeypatch):
    monkeypatch.setattr(di, "_scan_disorder_info", lambda _p: None)
    assert di.map_mck_indices_to_raw("x.cif", [_raw("O1", "O", (0, 0, 0))], [0]) == {}
