from __future__ import annotations

import argparse
from dataclasses import fields

from mat_viewer.cli import _build_render_parser
from mat_viewer.render.contracts import CameraSpec, RenderSpec, ViewSpec


def _render_destinations() -> set[str]:
    parser = argparse.ArgumentParser()
    render = _build_render_parser(parser.add_subparsers())
    return {action.dest for action in render._actions}


def test_backend_neutral_spec_fields_have_cli_destinations() -> None:
    destinations = _render_destinations()
    mappings = {
        ViewSpec: {
            "display": "view",
            "include_boundary_replicas": "include_boundary_replicas",
        },
        CameraSpec: {
            "position": "camera_position",
            "target": "camera_target",
            "up": "camera_up",
            "projection": "projection",
            "fov_y_deg": "field_of_view",
            "near": "camera_clip",
            "far": "camera_clip",
            "ortho_scale": "ortho_scale",
        },
        RenderSpec: {
            "representation": "style",
            "shading": "shading",
            "backend": "backend",
            "width": "width",
            "height": "height",
            "scale": "scale",
            "background": "background",
            "atom_scale": "atom_scale",
            "bond_radius": "bond_radius",
            "show_hydrogen": "show_hydrogen",
            "show_cell": "show_unit_cell",
            "show_axes": "show_axes",
            "show_labels": "show_labels",
            "cell_color": "cell_color",
            "cell_width_px": "cell_width",
            "aromatic_rings": "aromatic_rings",
            "ortep_probability": "ortep_probability",
            "ortep_mode": "ortep_mode",
            "missing_adp_policy": "missing_adp_policy",
            "sphere_detail": "sphere_detail",
            "cylinder_sides": "cylinder_sides",
        },
    }

    for spec, mapping in mappings.items():
        assert set(mapping) == {field.name for field in fields(spec)}
        assert set(mapping.values()) <= destinations


def test_composable_user_surfaces_have_cli_destinations() -> None:
    destinations = _render_destinations()

    assert {
        "vector_overlays",
        "atom_group",
        "bond_group",
        "include_boundary_replicas",
        "polyhedron",
        "frame_range",
        "stride",
        "fps",
        "display_time",
        "time_step",
        "time_step_unit",
        "dump_frequency",
        "first_frame_step",
        "time_position",
    } <= destinations


def test_boundary_replica_cli_is_explicit_and_backwards_compatible() -> None:
    parser = argparse.ArgumentParser()
    render = _build_render_parser(parser.add_subparsers())

    automatic = render.parse_args(["structure.cif", "-o", "figure.png"])
    strict = render.parse_args(
        ["structure.cif", "-o", "figure.png", "--no-boundary-replicas"]
    )
    expanded = render.parse_args(
        ["structure.cif", "-o", "figure.png", "--include-boundary-replicas"]
    )

    assert automatic.include_boundary_replicas is None
    assert strict.include_boundary_replicas is False
    assert expanded.include_boundary_replicas is True
