from __future__ import annotations

import gemmi
import numpy as np
import pytest

from mat_viewer.render.figures import build_figure, build_publication_figure, build_row_figure
from mat_viewer.render.overlay.vectors import (
    normalize_vector_overlays,
    paper_vector_label_annotations,
    resolve_vector_overlays,
    vector_mesh_traces,
    vector_overlay_bounds,
)
from mat_viewer.scene import build_scene_from_atoms, scene_ops, scene_style


def _groups(mode="normalized"):
    group = {
        "id": "polarization",
        "name": "Polarization",
        "magnitude_mode": mode,
        "viewport_policy": "include",
        "style": {"shaft_radius": 0.08, "sides": 10},
        "arrows": [{"id": "p0", "origin": [0, 0, 0], "vector": [2, 0, 0], "color": "#D55E00", "label": "P"}],
    }
    if mode == "normalized":
        group["length"] = 3.0
    elif mode == "scaled":
        group["scale"] = 0.5
    return [group]


def _scene():
    cell = gemmi.UnitCell(20, 20, 20, 90, 90, 90)
    matrix = np.eye(3) * 20
    atoms = [{"label": "C1", "elem": "C", "frac": np.array([0.5, 0.5, 0.5]), "cart": np.array([0.0, 0.0, 0.0]), "occ": 1.0, "dg": ".", "da": "."}]
    return build_scene_from_atoms(name="one", title="One", atoms=atoms, cell=cell, M=matrix, R=np.eye(3), display_mode="cluster", ops=scene_ops())


def test_modes_are_explicit_and_resolve_lengths() -> None:
    with pytest.raises(ValueError):
        normalize_vector_overlays([{"id": "bad", "arrows": []}])
    normalized = resolve_vector_overlays(_groups("normalized"))[0]
    scaled = resolve_vector_overlays(_groups("scaled"))[0]
    absolute = resolve_vector_overlays(_groups("absolute"))[0]
    assert normalized["display_magnitude"] == pytest.approx(3.0)
    assert scaled["display_magnitude"] == pytest.approx(1.0)
    assert absolute["display_magnitude"] == pytest.approx(2.0)


def test_center_anchor_places_atom_at_arrow_midpoint() -> None:
    groups = _groups("absolute")
    groups[0]["anchor"] = "center"
    groups[0]["arrows"][0]["origin"] = [3.0, 4.0, 5.0]
    arrow = resolve_vector_overlays(groups)[0]

    assert np.allclose(
        0.5 * (arrow["origin"] + arrow["end"]),
        [3.0, 4.0, 5.0],
    )


def test_fractional_origin_and_direction_use_row_vector_lattice() -> None:
    groups = _groups("absolute")
    groups[0]["arrows"][0].update({"origin": [0.5, 0, 0], "origin_space": "fractional", "vector": [0, 0.25, 0], "direction_space": "fractional"})
    lattice = np.diag([10.0, 20.0, 30.0])
    arrow = resolve_vector_overlays(groups, lattice=lattice)[0]
    assert np.allclose(arrow["origin"], [5, 0, 0])
    assert np.allclose(arrow["end"], [5, 5, 0])


def test_trace_is_opaque_mesh_with_vector_metadata() -> None:
    trace = vector_mesh_traces(_groups())[0]
    assert trace["type"] == "mesh3d"
    assert trace["opacity"] == 1.0
    assert trace["meta"]["mv_role"] == "vector"
    assert trace["customdata"][0][0] == "p0"


def test_viewport_policy_controls_bounds() -> None:
    included = _groups()
    clipped = _groups()
    clipped[0]["viewport_policy"] = "clip"
    assert vector_overlay_bounds(included)[0] is not None
    assert vector_overlay_bounds(clipped) == (None, None)


def test_single_and_row_figures_attach_world_vectors() -> None:
    scene = _scene()
    style = scene_style(scene, {"projection": "orthographic"})
    single = build_figure(scene, style, vector_overlays=_groups(), include_interaction_traces=False)
    assert any(isinstance(trace.meta, dict) and trace.meta.get("mv_role") == "vector" for trace in single.data)
    row = build_row_figure([(scene, style), (scene, style)], vector_overlays_by_scene=[_groups(), None], include_interaction_traces=False)
    vector_traces = [trace for trace in row.data if isinstance(trace.meta, dict) and trace.meta.get("mv_role") == "vector"]
    assert len(vector_traces) == 1
    assert vector_traces[0].scene == "scene"


def test_static_label_is_paper_overlay_and_perspective_rejected() -> None:
    camera = {"eye": {"x": 0, "y": 0, "z": 2}, "center": {"x": 0, "y": 0, "z": 0}, "up": {"x": 0, "y": 1, "z": 0}, "projection": {"type": "orthographic"}}
    annotation = paper_vector_label_annotations(
        _groups(),
        camera=camera,
        ranges=([-4, 4], [-4, 4], [-4, 4]),
        cube_scale=[4, 4, 4],
    )[0]
    assert annotation["xref"] == "paper" and annotation["yref"] == "paper"
    camera["projection"]["type"] = "perspective"
    with pytest.raises(ValueError):
        paper_vector_label_annotations(
            _groups(),
            camera=camera,
            ranges=([-4, 4], [-4, 4], [-4, 4]),
            cube_scale=[4, 4, 4],
        )


def test_publication_figure_attaches_vector_to_main_scene() -> None:
    scene = _scene()
    style = scene_style(scene, {"projection": "orthographic"})
    figure = build_publication_figure(
        scene,
        style,
        {"spec_results": []},
        vector_overlays=_groups(),
    )
    vectors = [
        trace
        for trace in figure.data
        if isinstance(trace.meta, dict) and trace.meta.get("mv_role") == "vector"
    ]
    assert len(vectors) == 1
    assert vectors[0].scene == "scene"
