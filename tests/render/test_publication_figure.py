from __future__ import annotations

import copy

import numpy as np

from crystal_viewer.render.figures import build_publication_figure
from crystal_viewer.render.topology import (
    representative_polyhedron_overlay,
    representative_polyhedron_traces,
    topology_background_traces,
)
from crystal_viewer.scene import scene_style


def _overlay(*, anchor: bool = False):
    shell = [[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]]
    return {
        "center_coords": [0, 0, 0],
        "center_label": "M1",
        "shell_coords": shell,
        "distances": [3 ** 0.5] * 4,
        "hull": {"simplices": [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]]},
        "is_analysis_anchor": anchor,
    }


def _scene():
    return {
        "name": "sample",
        "title": "sample",
        "display_title": "sample",
        "M": np.eye(3) * 4,
        "cell": np.eye(3) * 4,
        "draw_atoms": [{
            "label": "M1", "elem": "M", "cart": [0, 0, 0],
            "color": "#808080", "color_light": "#B0B0B0", "atom_radius": 0.7,
            "is_minor": False,
        }],
        "bonds": [],
        "view_direction": np.array([0.0, 0.0, 1.0]),
        "up": np.array([0.0, 1.0, 0.0]),
        "axis_labels": ["a", "b", "c"],
    }


def test_representative_prefers_analysis_anchor_and_does_not_mutate():
    first = _overlay(anchor=False)
    anchor = _overlay(anchor=True)
    anchor["center_coords"] = [3, 2, 1]
    original = copy.deepcopy(anchor)

    selected = representative_polyhedron_overlay({"overlays": [first, anchor]})
    traces, radius = representative_polyhedron_traces(selected, color="#336699")

    assert selected is anchor
    assert anchor == original
    assert radius > 1
    assert any(trace.get("type") == "mesh3d" and trace.get("color") == "#336699" for trace in traces)


def test_representative_uses_explicit_polyhedron_paint():
    traces, _ = representative_polyhedron_traces(
        _overlay(anchor=True), color="#336699", opacity=0.72,
        edge_opacity=0.65, edge_width=2.5, flatshading=False,
    )

    mesh = next(trace for trace in traces if trace.get("type") == "mesh3d")
    edges = next(trace for trace in traces if trace.get("name") == "coordination-edges")
    assert mesh["opacity"] == 0.72
    assert mesh["flatshading"] is False
    assert edges["opacity"] == 0.65
    assert edges["line"]["width"] == 2.5


def test_dense_equivalent_sites_share_one_spec_material():
    topology = {
        "spec_results": [{
            "spec_id": "tetra",
            "color": "#336699",
            "opacity": 0.72,
            "edge_opacity": 0.65,
            "edge_width": 2.5,
            "flatshading": False,
            "overlays": [_overlay(anchor=True), _overlay(anchor=False)],
        }],
    }

    traces = topology_background_traces(topology, style={})
    meshes = [trace for trace in traces if trace.get("type") == "mesh3d"]
    edges = next(trace for trace in traces if trace.get("name") == "coordination-edges")
    assert len(meshes) == 1
    assert meshes[0]["opacity"] == 0.72
    assert meshes[0]["flatshading"] is False
    assert edges["opacity"] == 0.65
    assert edges["line"]["width"] == 2.5


def test_publication_figure_builds_main_and_representative_panels():
    scene = _scene()
    style = scene_style(scene, {
        "show_axes": True,
        "show_axis_key": True,
        "show_unit_cell": False,
        "show_labels": False,
        "material": "mesh",
        "style": "ball_stick",
        "projection": "orthographic",
        "topology_enabled": True,
    })
    topology = {
        "center_coords": [0, 0, 0],
        "shell_coords": _overlay()["shell_coords"],
        "distances": [3 ** 0.5] * 4,
        "analysis_spec_id": "tetra",
        "spec_results": [
            {"spec_id": "tetra", "name": "MO4", "color": "#336699", "overlays": [_overlay(anchor=True)]},
            {"spec_id": "octa", "name": "MO6", "color": "#CC5500", "overlays": [_overlay()]},
        ],
    }
    original = copy.deepcopy(topology)

    figure = build_publication_figure(
        scene, style, topology,
        title="Crystal structure", subtitle="Cubic phase", width=1200, height=900,
    )
    payload = figure.to_dict()

    assert payload["layout"]["scene"]["domain"]["y"] == [0.29, 0.94]
    assert payload["layout"]["scene2"]["domain"]["y"] == [0.015, 0.245]
    assert payload["layout"]["scene3"]["domain"]["y"] == [0.015, 0.245]
    texts = [annotation.get("text", "") for annotation in payload["layout"]["annotations"]]
    assert any("Crystal structure" in text for text in texts)
    assert any("Cubic phase" in text for text in texts)
    assert any("MO4" in text and "MO6" in text for text in texts)
    assert any(trace.get("scene") == "scene2" for trace in payload["data"])
    assert any(trace.get("scene") == "scene3" for trace in payload["data"])
    assert topology["spec_results"] == original["spec_results"]