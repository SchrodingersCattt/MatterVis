from __future__ import annotations

from argparse import Namespace

import numpy as np
import pytest

from crystal_viewer.cli import (
    _apply_camera_overrides,
    _build_render_parser,
    _parse_polyhedron_specs,
    _plotly_static_export_available,
)
import argparse


def _args(**overrides) -> Namespace:
    values = {
        "camera_axis": None,
        "view_direction": None,
        "camera_position": None,
        "camera_up": None,
        "camera_distance": 1.8,
        "projection": "orthographic",
    }
    values.update(overrides)
    return Namespace(**values)


def test_camera_defaults_to_orthographic_positive_c_axis():
    scene = {"M": np.diag([3.0, 4.0, 5.0])}
    style = {}

    _apply_camera_overrides(scene, style, _args())

    assert np.allclose(scene["view_direction"], [0.0, 0.0, 1.0])
    assert np.allclose(scene["up"], [0.0, 1.0, 0.0])
    assert style["camera"]["eye"] == {"x": 0.0, "y": 0.0, "z": 1.8}
    assert style["camera"]["projection"] == {"type": "orthographic"}


def test_explicit_position_controls_eye_and_flat_scene_basis():
    scene = {"M": np.eye(3)}
    style = {}

    _apply_camera_overrides(
        scene,
        style,
        _args(camera_position=[2.0, 0.0, 0.0], camera_up=[0.0, 0.0, 1.0]),
    )

    assert style["camera"]["eye"] == {"x": 2.0, "y": 0.0, "z": 0.0}
    assert np.allclose(scene["view_direction"], [1.0, 0.0, 0.0])
    assert np.allclose(scene["up"], [0.0, 0.0, 1.0])


def test_zero_view_direction_is_rejected():
    with pytest.raises(ValueError, match="view direction must be non-zero"):
        _apply_camera_overrides(
            {"M": np.eye(3)},
            {},
            _args(view_direction=[0.0, 0.0, 0.0]),
        )


def test_static_export_preflight_returns_reason_when_unavailable():
    available, reason = _plotly_static_export_available()
    assert isinstance(available, bool)
    assert reason is None if available else isinstance(reason, str) and bool(reason)


def test_polyhedron_json_supports_atom_and_molecule_centres():
    specs = _parse_polyhedron_specs(
        [
            '{"center":"Pb","ligand":"I","level":"atom","fallback_max":6}',
            '{"center":"C6N2","ligand":"ClO4","level":"molecule",'
            '"center_kind":"heavy_centroid","hard_cutoff":8.0}',
        ]
    )

    assert specs[0]["center_species"] == "Pb"
    assert specs[0]["ligand_species"] == "I"
    assert specs[0]["level"] == "atom"
    assert specs[0]["fallback_max"] == 6
    assert specs[1]["level"] == "molecule"
    assert specs[1]["center_kind"] == "heavy_centroid"
    assert specs[1]["hard_cutoff"] == 8.0


def test_polyhedron_json_requires_ligand():
    with pytest.raises(ValueError, match="ligand is required"):
        _parse_polyhedron_specs(['{"center":"Pb","level":"atom"}'])


def test_render_parser_exposes_repeatable_polyhedra():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    _build_render_parser(subparsers)

    args = parser.parse_args(
        [
            "render", "input.cif", "-o", "out.png",
            "--polyhedron", '{"center":"Pb","ligand":"I","level":"atom"}',
            "--polyhedron", '{"center":"C6N2","ligand":"ClO4"}',
            "--polyhedron-cutoff", "6.5",
        ]
    )

    assert len(args.polyhedron) == 2
    assert args.polyhedron_cutoff == 6.5


def test_cli_topology_stamps_distinct_spec_colors(monkeypatch):
    import crystal_viewer.app.backend_topology as backend_topology
    from crystal_viewer.cli import _build_cli_topology_data

    monkeypatch.setattr(
        backend_topology,
        "compute_topology_geometry",
        lambda **kwargs: {
            "spec_results": [
                {"spec_id": "a", "overlays": [{"hull": {"simplices": [[0, 1, 2]]}}]},
                {"spec_id": "b", "overlays": [{"hull": {"simplices": [[0, 1, 2]]}}]},
            ]
        },
    )
    args = Namespace(
        polyhedron=[
            '{"id":"a","center":"C6N2","ligand":"ClO4","color":"#0072B2"}',
            '{"id":"b","center":"N","ligand":"ClO4","color":"#D55E00"}',
        ],
        polyhedron_site=0,
        polyhedron_cutoff=10.0,
    )
    scene = {"fragment_table": [{"index": 0, "formula": "C6N2", "elem_set": ["C", "N"]}]}

    topology = _build_cli_topology_data(object(), scene, args)

    assert [entry["color"] for entry in topology["spec_results"]] == ["#0072b2", "#d55e00"]