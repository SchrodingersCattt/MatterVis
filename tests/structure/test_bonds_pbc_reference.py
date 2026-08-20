from __future__ import annotations

from itertools import product

import gemmi
import numpy as np
import pytest

import mat_viewer.structure.bonds as bonds_module
from mat_viewer.structure.bonds import find_bonds
from mat_viewer.structure.geometry import ortho_matrix


def _atom(label: str, frac: tuple[float, float, float], M: np.ndarray) -> dict:
    frac_array = np.asarray(frac, dtype=float)
    return {
        "label": label,
        "elem": "C",
        "frac": frac_array,
        "cart": frac_array @ M,
        "occ": 1.0,
        "dg": ".",
        "da": ".",
    }


def _brute_force_distance(left: np.ndarray, right: np.ndarray, M: np.ndarray) -> float:
    best = float("inf")
    for shift in product((-1, 0, 1), repeat=3):
        delta = right + np.asarray(shift, dtype=float) - left
        best = min(best, float(np.linalg.norm(delta @ M)))
    return best


def _brute_force_pairs(atoms: list[dict], M: np.ndarray, cutoff: float = 1.94) -> set[tuple[int, int]]:
    fractions = np.asarray([atom["frac"] for atom in atoms], dtype=float)
    pairs: set[tuple[int, int]] = set()
    for shift in product((-1, 0, 1), repeat=3):
        delta = fractions[:, None, :] - fractions[None, :, :] + np.asarray(shift, dtype=float)
        distances = np.linalg.norm(delta @ M, axis=2)
        indices = np.argwhere(np.triu(distances < cutoff, k=1))
        pairs.update((int(left), int(right)) for left, right in indices)
    return pairs


def _large_atoms(count: int, M: np.ndarray) -> list[dict]:
    atoms = [
        _atom("C1", (0.9992, 0.5, 0.5), M),
        _atom("C2", (0.0008, 0.5, 0.5), M),
        _atom("C3", (0.2, 0.2, 0.2), M),
        _atom("C4", (0.201, 0.2, 0.2), M),
    ]
    for index in range(count - 4):
        x = 0.10 + (index % 20) * 0.04
        y = 0.10 + ((index // 20) % 20) * 0.04
        z = 0.10 + (index // 400) * 0.04
        atoms.append(_atom(f"F{index}", (x, y, z), M))
    return atoms


@pytest.mark.parametrize(
    "cell, left, right",
    [
        (gemmi.UnitCell(10, 10, 10, 90, 90, 90), (0.98, 0.5, 0.5), (0.02, 0.5, 0.5)),
        (gemmi.UnitCell(9, 10, 11, 90, 104, 90), (0.98, 0.2, 0.4), (0.02, 0.2, 0.4)),
        (gemmi.UnitCell(8, 9, 10, 78, 96, 112), (0.97, 0.96, 0.3), (0.03, 0.04, 0.3)),
    ],
)
def test_find_bonds_matches_brute_force_periodic_reference(cell, left, right):
    # ortho_matrix returns the legacy column-vector matrix; MatterVis stores
    # row-vector lattices after the loader boundary transpose.
    legacy_M, _ = ortho_matrix(cell)
    M = legacy_M.T
    atoms = [_atom("C1", left, M), _atom("C2", right, M)]

    reference_distance = _brute_force_distance(atoms[0]["frac"], atoms[1]["frac"], M)
    expected = {(0, 1)} if reference_distance < 1.94 else set()

    assert set(find_bonds(atoms, M=M, cell=cell)) == expected


@pytest.mark.parametrize("count", [64, 500])
def test_find_bonds_kdtree_paths_match_brute_force_periodic_pairs(count):
    cell = gemmi.UnitCell(1000, 1000, 1000, 90, 90, 90)
    legacy_M, _ = ortho_matrix(cell)
    M = legacy_M.T
    atoms = _large_atoms(count, M)
    expected = _brute_force_pairs(atoms, M)

    assert {(0, 1), (2, 3)} <= expected
    assert set(find_bonds(atoms, M=M, cell=cell)) == expected


def test_disabled_bond_telemetry_does_not_start_a_timer(monkeypatch):
    monkeypatch.delenv("MATTERVIS_BOND_PERF_EVENTS", raising=False)
    monkeypatch.setattr(
        bonds_module.time,
        "perf_counter",
        lambda: pytest.fail("disabled bond telemetry must not read perf_counter"),
    )
    atoms = [
        {"label": "C1", "elem": "C", "cart": np.array([0.0, 0.0, 0.0]), "occ": 1.0, "dg": ".", "da": "."},
        {"label": "C2", "elem": "C", "cart": np.array([1.4, 0.0, 0.0]), "occ": 1.0, "dg": ".", "da": "."},
    ]

    assert find_bonds(atoms) == [(0, 1)]
