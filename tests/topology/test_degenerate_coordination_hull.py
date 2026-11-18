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
    analyze_topology,
    extract_atom_coordination_shells,
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
    assert "Shape:" not in text
    assert "octahedron" not in text.lower()
