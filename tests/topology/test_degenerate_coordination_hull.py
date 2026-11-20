"""Coplanar coordination shells must not crash Qhull or invent a polyhedron.

A nitrogen centre with six chlorine ligands at 5 Å is a public synthetic
fixture. The octahedral arrangement is a real 3D shell. The coplanar
arrangement has the same coordination number and is not a polyhedron.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mat_viewer.loader import build_loaded_crystal
from mat_viewer.renderer import topology_results_markdown
from mat_viewer.topology.analysis import (
    _degenerate_shape_payload,
    analyze_topology,
    extract_atom_coordination_shells,
)
from mat_viewer.topology.hull_payload import (
    REASON_NOT_3D,
    REASON_QHULL_NUMERICAL,
    affine_rank,
    note_degenerate_hull,
    render_hull_payload,
)


def _write_ncl6(path: Path, ligands: list[tuple[float, float, float]]) -> None:
    rows = [
        "data_synthetic_ncl6",
        "_cell_length_a 40.0",
        "_cell_length_b 40.0",
        "_cell_length_c 40.0",
        "_cell_angle_alpha 90",
        "_cell_angle_beta 90",
        "_cell_angle_gamma 90",
        "_space_group_name_H-M_alt 'P 1'",
        "loop_",
        "_space_group_symop_operation_xyz",
        "'x, y, z'",
        "loop_",
        "_atom_site_label",
        "_atom_site_type_symbol",
        "_atom_site_fract_x",
        "_atom_site_fract_y",
        "_atom_site_fract_z",
        "_atom_site_occupancy",
        "N1 N 0.5 0.5 0.5 1",
    ]
    for index, (x_pos, y_pos, z_pos) in enumerate(ligands, start=1):
        frac = (np.array([x_pos, y_pos, z_pos], dtype=float) + 20.0) / 40.0
        rows.append(
            f"Cl{index} Cl {frac[0]:.6f} {frac[1]:.6f} {frac[2]:.6f} 1"
        )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _bundle(tmp_path: Path, name: str, ligands: list[tuple[float, float, float]]):
    path = tmp_path / f"{name}.cif"
    _write_ncl6(path, ligands)
    # source=upload skips the catalog auto-view, which is not this contract.
    return build_loaded_crystal(
        name=name,
        cif_path=str(path),
        title=name,
        source="upload",
    )


def _octahedron() -> list[tuple[float, float, float]]:
    return [
        (5.0, 0.0, 0.0),
        (-5.0, 0.0, 0.0),
        (0.0, 5.0, 0.0),
        (0.0, -5.0, 0.0),
        (0.0, 0.0, 5.0),
        (0.0, 0.0, -5.0),
    ]


def _coplanar_hexagon() -> list[tuple[float, float, float]]:
    angles = np.linspace(0.0, 2.0 * np.pi, 6, endpoint=False)
    return [
        (float(5.0 * np.cos(angle)), float(5.0 * np.sin(angle)), 0.0)
        for angle in angles
    ]


def _analyze(bundle):
    return analyze_topology(
        bundle,
        0,
        cutoff=8.0,
        ligand_species="Cl",
        level="atom",
        center_species="N",
        center_kind="atom",
    )


def test_octahedral_ncl6_shell_is_cn6_with_zero_cshm(tmp_path: Path):
    bundle = _bundle(tmp_path, "ncl6-octahedron", _octahedron())
    result = _analyze(bundle)
    shape = result["shape"]
    simplices = (result.get("hull") or {}).get("simplices") or []

    assert result["coordination_number"] == 6
    assert len(result["shell_coords"]) == 6
    assert shape["primary_label"] == "octahedron"
    assert shape["cshm_value"] == pytest.approx(0.0, abs=1e-8)
    assert shape.get("degenerate") is not True
    assert len(simplices) == 8
    text = topology_results_markdown(result)
    assert "CN: 6" in text
    assert "octahedron" in text
    assert "CShM = 0.00" in text


def test_coplanar_ncl6_shell_reports_no_polyhedron(tmp_path: Path):
    bundle = _bundle(tmp_path, "ncl6-coplanar", _coplanar_hexagon())
    shells = extract_atom_coordination_shells(
        bundle,
        8.0,
        center_species="N",
        ligand_species="Cl",
    )
    assert len(shells) == 1
    shell = next(iter(shells.values()))
    assert len(shell["source_shell_coords"]) == 6
    assert shell["source_hull"]["simplices"] == []
    assert shell["source_hull"]["edges"] == []
    assert shell["source_hull"]["degenerate"] is True
    assert shell["source_hull"]["reason"] == "not_a_3d_polyhedron"
    assert len(shell["source_hull"]["vertices"]) == 6

    result = _analyze(bundle)
    shape = result["shape"]
    assert result["coordination_number"] == 6
    assert len(result["shell"]) == 6
    assert (result.get("hull") or {}).get("simplices") == []
    assert result["gap_info"]["rejection_reason"] == "not_a_3d_polyhedron"
    assert shape["primary_label"] is None
    assert shape["degenerate"] is True
    assert "3D polyhedron" in shape["error"]
    text = topology_results_markdown(result)
    assert "CN: 6" in text
    assert "No faces were generated" in text
    assert "coplanar or collinear" in text
    assert "Shape:" not in text
    assert "octahedron" not in text.lower()


def _collinear() -> list[tuple[float, float, float]]:
    return [(5.0, 0.0, 0.0), (2.5, 0.0, 0.0), (-2.5, 0.0, 0.0), (-5.0, 0.0, 0.0)]


def _near_flat_hexagon(height: float) -> list[tuple[float, float, float]]:
    """Hexagon in z=0 with one ligand displaced by ``height`` angstroms."""
    angles = np.linspace(0.0, 2.0 * np.pi, 6, endpoint=False)
    return [
        (
            float(5.0 * np.cos(angle)),
            float(5.0 * np.sin(angle)),
            float(height) if index == 0 else 0.0,
        )
        for index, angle in enumerate(angles)
    ]


def _small_octahedron() -> list[tuple[float, float, float]]:
    scale = 1.5
    return [
        (scale, 0.0, 0.0),
        (-scale, 0.0, 0.0),
        (0.0, scale, 0.0),
        (0.0, -scale, 0.0),
        (0.0, 0.0, scale),
        (0.0, 0.0, -scale),
    ]


def test_collinear_shell_is_rank_one_and_not_a_polyhedron(tmp_path: Path):
    points = np.asarray(_collinear(), dtype=float)
    assert affine_rank(points) == 1
    hull = render_hull_payload(points)
    assert hull["reason"] == REASON_NOT_3D
    assert hull["affine_rank"] == 1
    assert hull["simplices"] == []

    bundle = _bundle(tmp_path, "ncl-collinear", _collinear())
    result = _analyze(bundle)
    assert result["coordination_number"] == 4
    assert result["gap_info"]["rejection_reason"] == REASON_NOT_3D
    assert result["shape"]["hull_reason"] == REASON_NOT_3D
    assert "coplanar or collinear" in result["shape"]["error"]
    assert "numerical failure" not in result["shape"]["error"]


def test_near_coplanar_shell_below_rank_tolerance_is_not_3d(tmp_path: Path):
    """A 1e-15 Å bump is below both rank floors and is flat to Qhull.

    A 1e-12 Å bump is still below the rank floor, but current Qhull accepts
    it. A hull Qhull returns is left unchanged: rank does not relabel it.
    """
    rejected = np.asarray(_near_flat_hexagon(1e-15), dtype=float)
    assert affine_rank(rejected) < 3
    hull = render_hull_payload(rejected)
    assert hull["degenerate"] is True
    assert hull["reason"] == REASON_NOT_3D
    assert hull["affine_rank"] < 3
    assert hull["simplices"] == []

    accepted = np.asarray(_near_flat_hexagon(1e-12), dtype=float)
    assert affine_rank(accepted) < 3
    kept = render_hull_payload(accepted)
    assert kept.get("degenerate") is not True
    assert kept["simplices"]
    assert "reason" not in kept

    bundle = _bundle(tmp_path, "ncl6-near-flat", _near_flat_hexagon(1e-15))
    result = _analyze(bundle)
    assert result["coordination_number"] == 6
    assert result["shape"]["primary_label"] is None
    assert result["gap_info"]["rejection_reason"] == REASON_NOT_3D


def test_full_rank_near_plane_is_not_called_coplanar():
    """A 0.001 Å bump on a 5 Å hexagon clears the rank tolerance."""
    points = np.asarray(_near_flat_hexagon(1e-3), dtype=float)
    assert affine_rank(points) == 3
    hull = render_hull_payload(points)
    if hull.get("degenerate"):
        assert hull["reason"] == REASON_QHULL_NUMERICAL
        assert hull["affine_rank"] == 3
        assert hull["reason"] != REASON_NOT_3D
    else:
        assert hull["simplices"]
        assert "reason" not in hull


def test_small_octahedron_remains_a_polyhedron(tmp_path: Path):
    points = np.asarray(_small_octahedron(), dtype=float)
    assert affine_rank(points) == 3
    direct = render_hull_payload(points * 0.02)
    assert direct.get("degenerate") is not True
    assert len(direct["simplices"]) == 8

    bundle = _bundle(tmp_path, "ncl6-small", _small_octahedron())
    result = _analyze(bundle)
    assert result["coordination_number"] == 6
    assert result["shape"].get("degenerate") is not True
    assert result["shape"]["primary_label"] == "octahedron"
    assert len((result.get("hull") or {}).get("simplices") or []) == 8


def test_qhull_error_on_a_full_rank_set_is_numerical(monkeypatch):
    from scipy.spatial import QhullError

    tetrahedron = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.2, 1.0, 0.0],
            [0.2, 0.3, 0.7],
        ],
        dtype=float,
    )
    assert affine_rank(tetrahedron) == 3

    def _fail(_coords):
        raise QhullError("QH6154 initial simplex is flat")

    monkeypatch.setattr(
        "mat_viewer.topology.hull_payload.convex_hull_payload",
        _fail,
    )
    hull = render_hull_payload(tetrahedron)
    assert hull["degenerate"] is True
    assert hull["reason"] == REASON_QHULL_NUMERICAL
    assert hull["affine_rank"] == 3
    assert hull["simplices"] == []
    assert len(hull["vertices"]) == 4

    gap: dict = {}
    note_degenerate_hull(gap, hull)
    assert gap["rejection_reason"] == REASON_QHULL_NUMERICAL
    existing = {"rejection_reason": "chemistry"}
    note_degenerate_hull(existing, hull)
    assert existing["rejection_reason"] == "chemistry"

    shape = _degenerate_shape_payload(4, reason=hull["reason"])
    assert shape["primary_label"] is None
    assert shape["hull_reason"] == REASON_QHULL_NUMERICAL
    assert "numerical failure" in shape["error"]
    assert "coplanar" not in shape["error"]
    assert "collinear" not in shape["error"]
