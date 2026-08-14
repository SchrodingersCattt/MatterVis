from __future__ import annotations

import copy
import matplotlib
import numpy as np
from PIL import Image

matplotlib.use("Agg")

from crystal_viewer.render.api import FigureResult
from crystal_viewer.render.cli import _parse_publication_options
from crystal_viewer.render.publication import (
    _normalise_sectors,
    _orient_panel,
    build_static_publication_figure,
    filter_polyhedra_to_half_open_cell,
    in_half_open_cell,
)
from crystal_viewer.render.publication_materials import (
    _polyhedron_facecolors,
    _sphere_facecolors,
)
from crystal_viewer.render.publication_style import publication_config


def _overlay(center: list[float]) -> dict:
    center_array = np.asarray(center, dtype=float)
    shell = center_array + 0.5 * np.asarray(
        [
            [1.0, 1.0, 1.0],
            [1.0, -1.0, -1.0],
            [-1.0, 1.0, -1.0],
            [-1.0, -1.0, 1.0],
        ]
    )
    return {
        "center_coords": center_array.tolist(),
        "center_label": "A1",
        "shell_coords": shell.tolist(),
        "distances": [3**0.5 / 2] * 4,
        "hull": {
            "simplices": [
                [0, 1, 2],
                [0, 1, 3],
                [0, 2, 3],
                [1, 2, 3],
            ]
        },
        "is_analysis_anchor": True,
    }


def _scene() -> dict:
    return {
        "name": "mixed-site tetrahedron",
        "title": "mixed-site tetrahedron",
        "display_title": "mixed-site tetrahedron",
        "M": np.eye(3) * 4,
        "cell": np.eye(3) * 4,
        "draw_atoms": [
            {
                "label": "A1",
                "elem": "A",
                "cart": np.array([1.0, 1.0, 1.0]),
                "color": "#86D533",
                "occ": 0.5,
            },
            {
                "label": "B1",
                "elem": "B",
                "cart": np.array([1.0, 1.0, 1.0]),
                "color": "#2F80D9",
                "occ": 0.5,
            },
            {
                "label": "X1",
                "elem": "X",
                "cart": np.array([1.5, 1.5, 1.5]),
                "color": "#FF3F3F",
                "occ": 1.0,
            },
        ],
        "axis_labels": ["a", "b", "c"],
    }


def _topology() -> dict:
    return {
        "spec_results": [
            {
                "spec_id": "tetra",
                "name": "AX4",
                "center_species": "A",
                "ligand_species": "X",
                "color": "#3386C4",
                "opacity": 0.7,
                "overlays": [
                    _overlay([1.0, 1.0, 1.0]),
                    _overlay([5.0, 1.0, 1.0]),
                ],
            }
        ]
    }


def _style() -> dict:
    return {
        "publication": {
            "site_styles": [
                {
                    "elements": ["A", "B"],
                    "colors": ["#86D533", "#2F80D9"],
                    "weights": [1, 1],
                    "label": "A / B",
                    "radius": 0.32,
                }
            ],
            "legend": {
                "entries": [
                    {
                        "colors": ["#86D533", "#2F80D9"],
                        "weights": [1, 1],
                        "label": "A / B",
                    }
                ]
            },
            "specs": {
                "tetra": {
                    "panel_rect": [0.15, 0.02, 0.35, 0.24],
                }
            },
        }
    }


def test_half_open_filter_excludes_boundary_image_without_mutation() -> None:
    scene = _scene()
    topology = _topology()
    original = copy.deepcopy(topology)

    assert in_half_open_cell(scene, [0.0, 0.0, 0.0])
    assert not in_half_open_cell(scene, [4.0, 0.0, 0.0])
    filtered = filter_polyhedra_to_half_open_cell(scene, topology)

    assert len(filtered) == 1
    assert len(filtered[0]["overlays"]) == 1
    assert topology == original


def test_dense_coordination_material_does_not_pin_camera() -> None:
    config = publication_config({})

    assert "camera" not in config["main"]
    assert all(
        "camera" not in profile
        for profile in config["panels"]["by_coordination"].values()
    )
    assert config["lighting"]["polyhedron_ambient"] == 0.45
    assert config["lighting"]["polyhedron_diffuse"] == 0.55
    assert config["lines"]["main_edge_width"] == 0.20
    assert config["lines"]["main_spoke_width"] == 0.0
    assert config["lines"]["main_spoke_alpha"] == 0.0
    assert config["materials"]["8"]["main"]["alpha"] < 0.5
    assert config["materials"]["6"]["main"]["alpha"] > 0.7
    assert config["materials"]["4"]["main"]["alpha"] > 0.7
    assert config["materials"]["8"]["main"]["light_strength"] < 0.25


def test_publication_cli_options_cover_nested_material_values() -> None:
    style = _parse_publication_options(
        "dense_coordination",
        [
            "materials.8.main.fill=#4CB17A",
            "materials.8.main.alpha=0.34",
            "atoms.sphere_ambient=0.72",
        ],
        site_styles=[["M8a,M8b", "#86D533,#2F80D9", "1,1", "site A", "0.28"]],
        legend_entries=[["#86D533,#2F80D9", "site A"]],
        panel_labels=[["cn8", "[M8]X8"]],
        legend_footer="coordination colors",
    )
    config = publication_config(style)

    assert config["materials"]["8"]["main"]["fill"] == "#4CB17A"
    assert config["materials"]["8"]["main"]["alpha"] == 0.34
    assert config["atoms"]["sphere_ambient"] == 0.72
    assert config["site_styles"][0]["elements"] == ["M8a", "M8b"]
    assert config["legend"]["entries"][0]["label"] == "site A"
    assert config["legend"]["footer"] == "coordination colors"
    assert config["specs"]["cn8"]["panel_label"] == "[M8]X8"
    assert "camera" not in config["main"]


def test_polyhedron_material_uses_face_normals_and_preserves_alpha() -> None:
    faces = [
        np.asarray([[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]),
        np.asarray([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
    ]
    shaded = _polyhedron_facecolors(
        faces,
        ["#4CB17A80", "#4CB17A80"],
        basis=(np.eye(3)[0], np.eye(3)[1], np.eye(3)[2]),
        ambient=0.45,
        diffuse=0.55,
    )

    assert shaded.shape == (2, 4)
    assert not np.allclose(shaded[0, :3], shaded[1, :3])
    assert np.allclose(shaded[:, 3], 128 / 255)


def test_sphere_material_has_bright_floor_and_directional_gradient() -> None:
    xyz = np.asarray(
        [
            [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
            [[0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]],
        ]
    )

    shaded = _sphere_facecolors(
        xyz,
        np.zeros(3),
        "#FFFFFF",
        basis=(np.eye(3)[0], np.eye(3)[1], np.eye(3)[2]),
        ambient=0.72,
        diffuse=0.28,
    )

    assert shaded.shape == (2, 2, 4)
    assert shaded[..., :3].min() >= 0.72
    assert np.ptp(shaded[..., 0]) > 0.15
    assert np.allclose(shaded[..., 3], 1.0)


def test_raw_panel_orientation_returns_centered_shell() -> None:
    overlay = {
        "center_coords": [1.0, 2.0, 3.0],
        "shell_coords": [[2.0, 2.0, 3.0], [1.0, 4.0, 3.0]],
    }

    shell = _orient_panel(overlay, {"orientation": "raw"})

    assert np.allclose(shell, [[1.0, 0.0, 0.0], [0.0, 2.0, 0.0]])


def test_mixed_site_sector_weights_are_normalized() -> None:
    colors, fractions = _normalise_sectors(
        ["#86D533", "#2F80D9"],
        [1.0, 3.0],
    )

    assert colors == ["#86D533", "#2F80D9"]
    assert np.allclose(fractions, [0.25, 0.75])


def test_static_publication_uses_face_stack_and_depth_layers(tmp_path) -> None:
    figure = build_static_publication_figure(
        _scene(),
        _style(),
        _topology(),
        title="ABX4",
        width=640,
        height=480,
        dpi=100,
    )
    metadata = figure._mattervis_publication

    assert metadata["cell_polyhedron_counts"] == {"tetra": 1}
    assert metadata["ligand_vertex_count"] == 4
    assert sum(metadata["panel_layers"]["tetra"].values()) == 4
    assert metadata["panel_layers"]["tetra"]["back_ligands"] >= 1
    face_stacks = [
        collection
        for axis in figure.axes
        for collection in axis.collections
        if getattr(collection, "_mattervis_role", None) == "polyhedron_face_stack"
    ]
    assert len(face_stacks) == 1
    main_edges = [
        collection
        for axis in figure.axes
        for collection in axis.collections
        if getattr(collection, "_mattervis_role", None) == "main_polyhedron_edges"
    ]
    main_spokes = [
        collection
        for axis in figure.axes
        for collection in axis.collections
        if getattr(collection, "_mattervis_role", None) == "main_polyhedron_spokes"
    ]
    assert main_edges == []
    assert main_spokes == []
    assert face_stacks[0]._mattervis_front_edge_faces > 0
    assert face_stacks[0]._mattervis_back_edge_faces > 0

    output = tmp_path / "publication.png"
    FigureResult(
        mpl_fig=figure,
        mpl_save_kwargs={"bbox_inches": None, "facecolor": "#FFFFFF"},
    ).save(str(output), width=640, height=480, dpi=100)

    assert Image.open(output).size == (640, 480)
