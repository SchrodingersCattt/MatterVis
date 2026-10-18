from __future__ import annotations

import copy

import numpy as np
import pytest

from mat_viewer.loader import build_empty_bundle
from mat_viewer.math import cylinder_vertices_faces
from mat_viewer.presets import DEFAULT_STYLE
from mat_viewer.render.api import render
from mat_viewer.render.morphology import _morphology_traces
from mat_viewer.scene import scene_json
from mat_viewer.renderer import (
    build_figure,
    build_row_figure,
    cylinder_entity,
    mesh_entity,
    through_cylinder_entity,
    uniform_viewport,
)
from mat_viewer.render.figures import _should_use_fast
from mat_viewer.render.viewport import ViewportAccumulator, _scene_ranges


def _scene():
    scene = build_empty_bundle().scene
    scene["display_mode"] = "cluster"
    scene["draw_atoms"] = [
        {
            "label": "C1",
            "elem": "C",
            "cart": [0.0, 0.0, 0.0],
            "atom_radius": 0.2,
            "color": "#555555",
            "color_light": "#888888",
            "is_minor": False,
            "occ": 1.0,
            "disorder_alpha": 1.0,
        }
    ]
    scene["bonds"] = []
    return scene


def test_cylinder_mesh_is_open_by_default_and_has_expected_extent():
    vertices, faces = cylinder_vertices_faces(
        center=[1.0, 2.0, 3.0],
        axis=[1.0, 1.0, 0.0],
        radius=2.0,
        length=10.0,
        segments=12,
        caps=False,
    )
    assert vertices.shape == (24, 3)
    assert faces.shape == (24, 3)
    assert np.allclose(vertices.mean(axis=0), [1.0, 2.0, 3.0], atol=1e-12)
    axis = np.array([1.0, 1.0, 0.0]) / np.sqrt(2.0)
    axial = (vertices - np.array([1.0, 2.0, 3.0])) @ axis
    assert np.isclose(axial.min(), -5.0)
    assert np.isclose(axial.max(), 5.0)


def test_cylinder_side_winding_points_outward_for_oblique_axis():
    vertices, faces = cylinder_vertices_faces(
        center=[0.0, 0.0, 0.0],
        axis=[1.0, 1.0, 0.0],
        radius=2.0,
        length=10.0,
        segments=16,
        caps=False,
    )
    axis = np.array([1.0, 1.0, 0.0], dtype=float)
    axis /= np.linalg.norm(axis)
    for a, b, c in faces:
        p0, p1, p2 = vertices[[a, b, c]]
        normal = np.cross(p1 - p0, p2 - p0)
        centroid = (p0 + p1 + p2) / 3.0
        radial = centroid - np.dot(centroid, axis) * axis
        assert np.dot(normal, radial) > 0.0


def test_closed_cylinder_cap_winding_points_outward():
    vertices, faces = cylinder_vertices_faces(
        center=[0.0, 0.0, 0.0],
        axis=[0.0, 0.0, 1.0],
        radius=2.0,
        length=10.0,
        segments=12,
        caps=True,
    )
    axis = np.array([0.0, 0.0, 1.0])
    # The two cap triangles are interleaved (one bottom and one top per
    # segment) by the primitive builder; classify them by their centre index
    # rather than relying on an incidental append order.
    minus_center = len(vertices) - 2
    plus_center = len(vertices) - 1
    cap_faces = [face for face in faces if minus_center in face or plus_center in face]
    for a, b, c in cap_faces:
        p0, p1, p2 = vertices[[a, b, c]]
        normal = np.cross(p1 - p0, p2 - p0)
        expected = -axis if minus_center in (a, b, c) else axis
        assert np.dot(normal, expected) > 0.0


def test_mesh_entity_triangulates_polygons_without_mutating_input():
    vertices = [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]
    faces = [[0, 1, 2, 3]]
    original = copy.deepcopy(faces)
    entity = mesh_entity(vertices, faces, name="quad", entity_id="q1")
    assert entity["faces"] == [[0, 1, 2], [0, 2, 3]]
    assert faces == original
    assert entity["id"] == "q1"


def test_cylinder_entity_accepts_custom_edges_without_overwriting_them():
    entity = cylinder_entity(
        center=[0.0, 0.0, 0.0],
        axis=[0.0, 0.0, 1.0],
        radius=1.0,
        length=4.0,
        segments=8,
        edges=[[0, 1]],
        show_edges=True,
    )
    assert entity["edges"] == [[0, 1]]


def test_mesh_entity_normalises_numpy_metadata_for_scene_json():
    entity = mesh_entity(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        [[0, 1, 2]],
        meta={"axis": np.asarray([1.0, 2.0, 3.0]), "flag": np.bool_(True)},
    )
    assert entity["meta"] == {"axis": [1.0, 2.0, 3.0], "flag": True}


def test_through_cylinder_entity_uses_row_lattice_and_reduced_hkl():
    lattice = np.diag([80.0, 70.0, 60.0])
    entity = through_cylinder_entity(
        lattice,
        [2, 0, 0],
        radius=10.0,
        center_frac=[0.5, 0.5, 0.5],
        segments=8,
        caps=False,
        entity_id="void-100",
    )
    vertices = np.asarray(entity["vertices"], dtype=float)
    axis = np.asarray(entity["meta"]["axis_cartesian"], dtype=float)
    center = np.asarray(entity["meta"]["center_frac"]) @ lattice
    axial = (vertices - center) @ (axis / np.linalg.norm(axis))
    assert entity["meta"]["direction_hkl"] == [1, 0, 0]
    assert np.isclose(axial.min(), -40.0)
    assert np.isclose(axial.max(), 40.0)
    assert np.allclose(center, [40.0, 35.0, 30.0])


def test_through_cylinder_entity_preserves_reduced_hkl_sign_in_metadata():
    entity = through_cylinder_entity(
        np.diag([10.0, 10.0, 10.0]),
        [-2, 0, 0],
        radius=1.0,
        segments=8,
    )
    assert entity["meta"]["direction_hkl"] == [-1, 0, 0]
    assert entity["meta"]["axis_cartesian"] == [-10.0, 0.0, 0.0]


def test_geometry_entity_uses_mesh3d_depth_path_and_owns_viewport():
    scene = _scene()
    scene["geometry_entities"] = [
        cylinder_entity(
            center=[0.0, 0.0, 0.0],
            axis=[0, 0, 1],
            radius=2.0,
            length=10.0,
            segments=12,
            caps=False,
            name="through",
            entity_id="void-1",
            show_edges=True,
        )
    ]
    style = {
        **DEFAULT_STYLE,
        "display_mode": "cluster",
        "show_axes": False,
        "show_labels": False,
        "show_unit_cell": False,
    }
    fig = build_figure(scene, style)
    mesh = [
        trace
        for trace in fig.data
        if trace.type == "mesh3d" and trace.name == "through"
    ]
    edges = [
        trace
        for trace in fig.data
        if trace.type == "scatter3d" and trace.name == "through edges"
    ]
    assert len(mesh) == 1
    assert len(edges) == 1
    # Two end rings plus four orientation seams; no triangulation stripes.
    assert len(edges[0].x) == (2 * 12 + 4) * 3
    assert mesh[0].meta["mv_role"] == "geometry_entity"
    assert mesh[0].meta["geometry_id"] == "void-1"
    ranges = _scene_ranges(scene, style)
    assert ranges[2][0] < -5.0 and ranges[2][1] > 5.0


def test_static_geometry_export_omits_face_target_metadata():
    scene = _scene()
    entity = mesh_entity(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        [[0, 1, 2]],
        name="picked-face",
        entity_id="mesh-face",
        meta={
            "face_id": "caller-face",
            "face_ids": ["caller-face"],
            "subentity_id": "caller-face",
            "face_ranges": [{"id": "caller-face", "start": 0, "end": 1}],
        },
    )
    entity["face_ids"] = ["caller-face"]
    scene["geometry_entities"] = [entity]
    style = {
        **DEFAULT_STYLE,
        "display_mode": "cluster",
        "show_axes": False,
        "show_labels": False,
        "show_unit_cell": False,
    }
    interactive = build_figure(scene, style, include_interaction_traces=True)
    static = build_figure(scene, style, include_interaction_traces=False)
    interactive_mesh = next(trace for trace in interactive.data if trace.name == "picked-face")
    static_mesh = next(trace for trace in static.data if trace.name == "picked-face")
    assert list(interactive_mesh.meta["face_ids"]) == ["caller-face"]
    assert list(interactive_mesh.meta["face_ranges"]) == [{"id": "caller-face", "start": 0, "end": 1}]
    assert interactive_mesh.meta["face_id"] == "caller-face"
    assert interactive_mesh.meta["subentity_id"] == "caller-face"
    assert "face_ids" not in (static_mesh.meta or {})
    assert "face_id" not in (static_mesh.meta or {})
    assert "subentity_id" not in (static_mesh.meta or {})
    assert "face_ranges" not in (static_mesh.meta or {})


def test_geometry_entity_requires_real_mesh_material_for_depth():
    scene = _scene()
    scene["geometry_entities"] = [
        mesh_entity(
            [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            [[0, 1, 2]],
            name="triangle",
        )
    ]
    with pytest.raises(ValueError, match="require material='mesh'"):
        build_figure(
            scene,
            {
                **DEFAULT_STYLE,
                "material": "flat",
                "show_axes": False,
                "show_labels": False,
            },
        )


def test_render_api_rejects_geometry_in_flat_ortep_mode():
    scene = _scene()
    scene["geometry_entities"] = [
        mesh_entity(
            [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            [[0, 1, 2]],
            name="triangle",
        )
    ]
    with pytest.raises(ValueError, match="require material='mesh'"):
        render(scene, {"material": "flat", "style": "ortep"})


def test_geometry_entities_disable_automatic_large_scene_scatter_fallback():
    scene = _scene()
    scene["draw_atoms"] = [
        {
            **scene["draw_atoms"][0],
            "label": f"C{i}",
            "cart": [float(i) * 0.2, 0.0, 0.0],
        }
        for i in range(2001)
    ]
    scene["geometry_entities"] = [
        mesh_entity(
            [[0, 0, -1], [1, 0, -1], [0, 1, -1]],
            [[0, 1, 2]],
            name="occluder",
        )
    ]
    style = {
        **DEFAULT_STYLE,
        "material": "mesh",
        "style": "ball",
        "show_axes": False,
        "show_labels": False,
        "show_unit_cell": False,
    }
    assert _should_use_fast(scene, style) is False
    scene.pop("geometry_entities")
    assert _should_use_fast(scene, style) is True


def test_build_figure_uses_geometry_aware_fast_path(monkeypatch):
    scene = _scene()
    scene["draw_atoms"] = [
        {**scene["draw_atoms"][0], "label": f"C{i}", "cart": [float(i), 0.0, 0.0]}
        for i in range(2001)
    ]
    scene["geometry_entities"] = [
        mesh_entity([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]])
    ]
    style = {
        **DEFAULT_STYLE,
        "material": "mesh",
        "style": "ball",
        "show_axes": False,
        "show_labels": False,
        "show_unit_cell": False,
    }
    import mat_viewer.render.figures as figures_module

    observed: dict[str, bool] = {}
    original = figures_module._cached_atom_bond_meshes

    def capture(*args, **kwargs):
        observed["use_fast"] = bool(kwargs["use_fast"])
        return original(*args, **kwargs)

    monkeypatch.setattr(figures_module, "_cached_atom_bond_meshes", capture)
    build_figure(scene, style)
    assert observed["use_fast"] is False


def test_raw_entity_edges_are_validated_with_context():
    scene = _scene()
    scene["geometry_entities"] = [
        {
            "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            "faces": [[0, 1, 2]],
            "show_edges": True,
            "edges": [[0, 99]],
        }
    ]
    with pytest.raises(ValueError, match=r"geometry_entities\[0\].*outside vertices"):
        build_figure(scene, {**DEFAULT_STYLE, "show_axes": False, "show_labels": False})


def test_geometry_entity_survives_scene_json_serialisation():
    scene = _scene()
    scene["geometry_entities"] = [
        mesh_entity(
            np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float),
            np.asarray([[0, 1, 2]], dtype=int),
            name="serialisable",
        )
    ]
    payload = scene_json(scene)
    assert payload["geometry_entities"][0]["vertices"][1] == [1.0, 0.0, 0.0]
    assert payload["geometry_entities"][0]["faces"] == [[0, 1, 2]]


def test_uniform_viewport_includes_entity_when_atoms_are_empty():
    scene = _scene()
    scene["draw_atoms"] = []
    scene["geometry_entities"] = [
        mesh_entity(
            [[20, 0, 0], [22, 0, 0], [20, 2, 0]],
            [[0, 1, 2]],
            name="triangle",
        )
    ]
    viewports = uniform_viewport([scene])
    assert viewports[0]["x"][0] <= 20.0
    assert viewports[0]["x"][1] >= 22.0
    assert viewports[0]["center"][0] == pytest.approx(21.0)


def test_row_figure_routes_entities_to_their_own_plotly_scene():
    left = _scene()
    right = _scene()
    left["geometry_entities"] = [
        mesh_entity([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]], name="left")
    ]
    right["geometry_entities"] = [
        mesh_entity([[10, 0, 0], [11, 0, 0], [10, 1, 0]], [[0, 1, 2]], name="right")
    ]
    style = {
        **DEFAULT_STYLE,
        "display_mode": "cluster",
        "show_axes": False,
        "show_labels": False,
        "show_unit_cell": False,
    }
    fig = build_row_figure([(left, style), (right, style)])
    entities = {
        trace.name: trace.scene for trace in fig.data if trace.name in {"left", "right"}
    }
    assert entities == {"left": "scene", "right": "scene2"}


def test_malformed_entity_has_contextual_error():
    scene = _scene()
    scene["geometry_entities"] = [
        {"name": "bad", "vertices": [[0, 0, 0]], "faces": [[0, 0, 0]]}
    ]
    with pytest.raises(ValueError, match=r"geometry_entities\[0\]"):
        build_figure(scene, {**DEFAULT_STYLE, "show_axes": False, "show_labels": False})


def test_bfdh_morphology_reuses_geometry_entity_depth_path():
    scene = {
        "M": np.diag([10.0, 10.0, 10.0]),
        "bfdh_morphology": {
            "enabled": True,
            "scale": 1.0,
            "opacity": 1.0,
            "facets": [
                {
                    "triangles": [[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]],
                    "centroid": [0.33, 0.33, 0.0],
                    "miller": [1, 0, 0],
                }
            ],
        },
    }
    traces = _morphology_traces(scene, {"bfdh_morphology_color": "#123456"})
    morphology_mesh = [trace for trace in traces if trace.name == "Morphology"]
    assert len(morphology_mesh) == 1
    assert morphology_mesh[0].type == "mesh3d"
    assert morphology_mesh[0].meta["mv_role"] == "geometry_entity"


def test_bfdh_face_ordinals_stay_aligned_with_semantic_facet_ids():
    scene = _scene()
    scene["bfdh_morphology"] = {
        "enabled": True,
        "scale": 1.0,
        "opacity": 0.3,
        "facets": [
            {
                "miller": [1, 0, 0],
                "triangles": [
                    [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                    [[0, 0, 1], [1, 0, 1], [0, 1, 1]],
                ],
                "centroid": [0.3, 0.3, 0.0],
            },
            {
                "miller": [0, 1, 0],
                "triangles": [[[0, 0, 0], [0, 1, 0], [0, 0, 1]]],
                "centroid": [0.0, 0.3, 0.3],
            },
        ],
    }
    traces = _morphology_traces(scene, {"bfdh_morphology_color": "#123456"})
    mesh = next(trace for trace in traces if trace.name == "Morphology")
    assert list(mesh.meta["face_ordinals"]) == [0, 0, 1]
    assert mesh.meta["face_ids"][0] == mesh.meta["face_ids"][1]
    assert mesh.meta["face_ids"][1] != mesh.meta["face_ids"][2]
    labels = next(trace for trace in traces if trace.name == "Morphology Labels")
    assert labels.customdata[0][1] == mesh.meta["face_ids"][0]
    assert labels.customdata[1][1] == mesh.meta["face_ids"][2]


def test_bfdh_hidden_facets_are_omitted_and_opacity_is_trace_scoped():
    from mat_viewer.render.picking import facet_instance_id

    scene = _scene()
    scene["bfdh_morphology"] = {
        "enabled": True,
        "opacity": 0.8,
        "facets": [
            {
                "miller": [1, 0, 0],
                "triangles": [[[0, 0, 0], [1, 0, 0], [0, 1, 0]]],
                "centroid": [0.3, 0.3, 0.0],
            },
            {
                "miller": [0, 1, 0],
                "triangles": [[[0, 0, 1], [1, 0, 1], [0, 1, 1]]],
                "centroid": [0.3, 0.3, 1.0],
            },
        ],
    }
    hidden_id = facet_instance_id([1, 0, 0], 0)
    faded_id = facet_instance_id([0, 1, 0], 1)
    traces = _morphology_traces(
        scene,
        {
            "bfdh_morphology_color": "#123456",
            "style_overrides": {
                hidden_id: {"kind": "facet", "visible": False},
                faded_id: {"kind": "facet", "opacity": 0.25, "color": "#ff0000"},
            },
        },
    )
    meshes = [trace for trace in traces if trace.name == "Morphology"]
    assert len(meshes) == 1
    assert float(meshes[0].opacity) == 0.25
    assert list(meshes[0].meta["face_ids"]) == [faded_id]
    labels = next(trace for trace in traces if trace.name == "Morphology Labels")
    assert list(labels.customdata[0][1]) == list(faded_id)


def test_static_morphology_export_omits_facet_target_metadata():
    scene = _scene()
    scene["bfdh_morphology"] = {
        "enabled": True,
        "facets": [
            {
                "miller": [1, 0, 0],
                "triangles": [[[0, 0, 0], [1, 0, 0], [0, 1, 0]]],
                "centroid": [0.3, 0.3, 0.0],
            }
        ],
    }
    style = {
        **DEFAULT_STYLE,
        "display_mode": "cluster",
        "show_axes": False,
        "show_labels": False,
        "show_unit_cell": False,
    }
    interactive = build_figure(scene, style, include_interaction_traces=True)
    static = build_figure(scene, style, include_interaction_traces=False)
    interactive_mesh = next(trace for trace in interactive.data if trace.name == "Morphology")
    static_mesh = next(trace for trace in static.data if trace.name == "Morphology")
    assert interactive_mesh.meta.get("face_ids")
    assert "face_ids" not in (static_mesh.meta or {})
    static_labels = next(trace for trace in static.data if trace.name == "Morphology Labels")
    assert static_labels.customdata is None


def test_bfdh_morphology_remains_renderable_in_flat_material():
    scene = {
        "M": np.diag([10.0, 10.0, 10.0]),
        "draw_atoms": [],
        "bonds": [],
        "bfdh_morphology": {
            "enabled": True,
            "facets": [
                {
                    "triangles": [
                        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
                    ],
                    "centroid": [0.33, 0.33, 0.0],
                    "miller": [1, 0, 0],
                }
            ],
        },
    }
    style = {
        **DEFAULT_STYLE,
        "material": "flat",
        "style": "ball",
        "show_axes": False,
        "show_labels": False,
        "show_unit_cell": False,
    }
    figure = build_figure(scene, style)
    assert any(trace.name == "Morphology" for trace in figure.data)


def test_json_safe_rejects_cyclic_entity_metadata():
    metadata: dict[str, object] = {}
    metadata["self"] = metadata
    with pytest.raises(ValueError, match="cyclic"):
        mesh_entity(
            [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            [[0, 1, 2]],
            meta=metadata,
        )


def test_viewport_accumulator_includes_geometry_only_scene():
    scene = _scene()
    scene["draw_atoms"] = []
    scene["geometry_entities"] = [
        mesh_entity(
            [[20, 0, 0], [22, 0, 0], [20, 2, 0]],
            [[0, 1, 2]],
            name="triangle",
        )
    ]
    accumulator = ViewportAccumulator()
    accumulator.update(scene)
    viewport = accumulator.viewport()
    assert viewport["x"][0] <= 20.0
    assert viewport["x"][1] >= 22.0
