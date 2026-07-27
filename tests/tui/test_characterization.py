from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from crystal_viewer.cli import _apply_display_filter
from crystal_viewer.loader import build_loaded_crystal
from crystal_viewer.math.camera import Camera, project_points
from crystal_viewer.tui.compositor import _compute_viewport, compose_frame
from crystal_viewer.tui.loader_adapter import load_for_tui


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "scripts" / "tui_benchmark" / "fixtures"
ORACLES = json.loads(
    (ROOT / "scripts" / "tui_benchmark" / "manifests" / "synthetic_oracles.v1.json").read_text(
        encoding="utf-8"
    )
)


def _nonempty_bbox(frame: str) -> tuple[int, int]:
    lines = frame.splitlines()
    return max((len(line) for line in lines), default=0), len(lines)


def _case(case_id: str):
    return next(case for case in ORACLES["cases"] if case["case_id"] == case_id)


def test_triclinic_oracle_is_analytically_consistent() -> None:
    answer = _case("triclinic_lattice")["tasks"][0]["answer"]
    expected = np.asarray(answer["row_matrix"], dtype=float)
    expected_frac = np.asarray(answer["frac"], dtype=float)
    expected_cart = np.asarray(answer["cart"], dtype=float)
    sqrt = np.sqrt
    analytic = np.array(
        [
            [10.0, 0.0, 0.0],
            [5.0, 5.0 * sqrt(3.0), 0.0],
            [5.0, 5.0 / sqrt(3.0), 10.0 * sqrt(2.0 / 3.0)],
        ]
    )
    assert expected == pytest.approx(analytic, abs=1e-12)
    assert expected_frac @ analytic == pytest.approx(expected_cart, abs=1e-8)


def test_triclinic_lattice_uses_row_vector_matrix() -> None:
    structure = load_for_tui(str(FIXTURES / "triclinic_lattice.cif"))
    answer = _case("triclinic_lattice")["tasks"][0]["answer"]
    expected = np.asarray(answer["row_matrix"], dtype=float)
    expected_frac = np.asarray(answer["frac"], dtype=float)
    expected_cart = np.asarray(answer["cart"], dtype=float)

    assert structure.lattice is not None
    assert structure.atoms[0].frac == pytest.approx(expected_frac, abs=1e-12)
    assert structure.atoms[0].cart == pytest.approx(expected_cart, abs=1e-8)
    assert structure.lattice.matrix == pytest.approx(expected, abs=1e-12)
    for atom in structure.atoms:
        assert atom.frac @ structure.lattice.matrix == pytest.approx(
            atom.cart, abs=1e-8
        )


def test_static_cli_honors_requested_projection() -> None:
    command = [
        sys.executable,
        "-m",
        "crystal_viewer",
        "tui",
        str(FIXTURES / "projection_stack_6.cif"),
        "--no-interaction",
        "--format",
        "structured",
        "--projection",
        "perspective",
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "  projection: perspective\n" in result.stdout


def test_zoom_out_reduces_projected_extent() -> None:
    structure = load_for_tui(str(FIXTURES / "projection_stack_6.cif"))
    camera = Camera.from_view_name("diagonal", structure)
    points, _ = project_points(camera, structure.cart_coords)

    normal = _compute_viewport(points, [], 80, 24, zoom=1.0)
    zoomed_out = _compute_viewport(points, [], 80, 24, zoom=0.5)

    normal_x_span = normal.x_max - normal.x_min
    normal_y_span = normal.y_max - normal.y_min
    assert zoomed_out.x_max - zoomed_out.x_min > normal_x_span
    assert zoomed_out.y_max - zoomed_out.y_min > normal_y_span


def test_small_terminal_frame_never_exceeds_requested_size() -> None:
    structure = load_for_tui(str(FIXTURES / "projection_stack_6.cif"))
    camera = Camera.from_view_name("diagonal", structure)
    points, depth = project_points(camera, structure.cart_coords)

    frame = compose_frame(
        structure,
        camera,
        points,
        depth,
        width=12,
        height=6,
        mono=True,
        label_mode="dot",
    )
    frame_width, frame_height = _nonempty_bbox(frame)

    assert frame_width <= 12
    assert frame_height <= 6


def test_tui_preserves_canonical_major_minor_selection() -> None:
    path = FIXTURES / "disorder_70_30.cif"
    structure = load_for_tui(str(path))
    bundle = build_loaded_crystal(
        name="disorder_70_30",
        cif_path=str(path),
        title="synthetic",
    )

    tui_minor = {atom.label for atom in structure.atoms if atom.is_minor}
    canonical_minor = {
        atom["label"] for atom in bundle.raw_atoms if atom.get("_is_minor")
    }
    assert tui_minor == canonical_minor


def test_canonical_disorder_matches_synthetic_oracle() -> None:
    path = FIXTURES / "disorder_70_30.cif"
    bundle = build_loaded_crystal(
        name="disorder_70_30",
        cif_path=str(path),
        title="synthetic",
    )
    canonical_minor = {
        atom["label"] for atom in bundle.raw_atoms if atom.get("_is_minor")
    }
    assert canonical_minor == {"C1B"}


def test_formula_unit_uses_all_per_fu_species_counts() -> None:
    path = FIXTURES / "fu_1_1_3.cif"
    structure = load_for_tui(str(path))
    filtered = _apply_display_filter(structure, "formula_unit")

    assert filtered.n_atoms == 10
    assert filtered.element_counts() == {"O": 1, "H": 5, "N": 1, "Ne": 3}


def test_canonical_formula_unit_matches_synthetic_oracle() -> None:
    path = FIXTURES / "fu_1_1_3.cif"
    bundle = build_loaded_crystal(
        name="fu_1_1_3",
        cif_path=str(path),
        title="synthetic",
    )
    assert bundle.molcrys_analysis.per_fu == {
        "H2O_1": 1,
        "H3N_1": 1,
        "Ne_1": 3,
    }
    assert len(bundle.formula_unit_atoms) == 10


def test_pbc_bond_distance_uses_unwrapped_endpoints() -> None:
    structure = load_for_tui(str(FIXTURES / "pbc_pair.cif"))
    bond = next(
        bond
        for bond in structure.bonds
        if {structure.atoms[bond.i].label, structure.atoms[bond.j].label}
        == {"O1", "H1"}
    )

    assert bond.distance == pytest.approx(1.0, abs=1e-8)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Observation metadata still calls symmetry-expanded atoms source atoms.",
)
def test_observation_scopes_distinguish_source_and_expanded_atoms() -> None:
    structure = load_for_tui(str(FIXTURES / "disorder_70_30.cif"))
    assert "source_site_atom_count" in structure.metadata
    assert "expanded_atom_count" in structure.metadata
    assert "source_atom_count" not in structure.metadata


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Viewport still refits every rotated 2D projection and changes scale.",
)
def test_viewport_scale_is_rotation_invariant() -> None:
    structure = load_for_tui(str(FIXTURES / "projection_stack_6.cif"))
    scales = []
    for azimuth in range(0, 360, 30):
        camera = Camera.from_view_name("diagonal", structure)
        camera.azimuth = float(azimuth)
        points, _ = project_points(camera, structure.cart_coords)
        scales.append(_compute_viewport(points, [], 80, 24).scale)
    assert max(scales) == pytest.approx(min(scales), rel=1e-12, abs=1e-12)
