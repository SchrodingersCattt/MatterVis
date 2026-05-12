from __future__ import annotations

import numpy as np

from crystal_viewer import molcrys_bridge
from crystal_viewer.topology import detect_coordination_number


def test_coordination_number_expands_until_centered_inside_hull():
    """Gap-only CN=4 is rejected when X is only in a shallow pocket."""
    coords = np.array(
        [
            [-4.04, 1.06, 2.40],
            [4.08, 1.07, 2.43],
            [0.00, -4.61, 2.52],
            [0.00, 4.80, -2.62],
            [-3.97, 1.06, -7.76],
            [4.07, 1.06, -7.79],
            [0.00, -4.57, -7.71],
            [-4.04, -7.53, -2.60],
            [4.04, -7.53, -2.60],
            [0.00, 4.76, 7.63],
            [-4.10, 7.74, 2.46],
            [4.10, 7.74, 2.46],
        ],
        dtype=float,
    )
    distances = np.linalg.norm(coords, axis=1)

    result = detect_coordination_number(
        distances,
        coords=coords,
        center=[0.0, 0.0, 0.0],
        enforce_enclosure=True,
    )

    assert result["primary_gap_cn"] == 4
    assert result["coordination_number"] == 12
    assert result["enclosed"] is True
    assert result["enclosure_expanded"] is True


def test_coordination_number_keeps_gap_cn_when_shell_is_centered():
    coords = np.array(
        [
            [1.0, 1.0, 1.0],
            [1.0, -1.0, -1.0],
            [-1.0, 1.0, -1.0],
            [-1.0, -1.0, 1.0],
            [5.0, 0.0, 0.0],
            [0.0, 5.0, 0.0],
        ],
        dtype=float,
    )
    distances = np.linalg.norm(coords, axis=1)

    result = detect_coordination_number(
        distances,
        coords=coords,
        center=[0.0, 0.0, 0.0],
        enforce_enclosure=True,
    )

    assert result["primary_gap_cn"] == 4
    assert result["coordination_number"] == 4
    assert result["enclosed"] is True
    assert result["enclosure_expanded"] is False


def test_formula_to_moiety_for_mck_molecule_level_polyhedra():
    assert molcrys_bridge.formula_to_moiety("C6N2") == "C6 N2"
    assert molcrys_bridge.formula_to_moiety("ClO4") == "Cl O4"
