from __future__ import annotations

import numpy as np
import pytest
from dataclasses import dataclass
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from mat_viewer.render.camera import CameraTransform
from mat_viewer.render.contracts import (
    CameraSpec,
    LinePrimitive,
    RenderPlan,
    RenderSpec,
    TextPrimitive,
    TriangleMeshPrimitive,
    ViewportPlan,
)
from mat_viewer.render.cpu import render
from mat_viewer.render.cpu.bsp import BSPPolygon, build_bsp, traverse_back_to_front
from mat_viewer.render.cpu.raster import render_rgba
from mat_viewer.render.cpu.vector import vector_scene, visible_line_segments
from mat_viewer.render.geometry import (
    aromatic_ring_primitive,
    cylinder_primitive,
    ellipsoid_primitive,
    ellipsoid_principal_axes,
    polyhedron_primitive,
    sphere_primitive,
    unit_cell_primitive,
)
from mat_viewer.render.planning import prepare_render


def _camera(*, projection: str = "orthographic") -> CameraSpec:
    return CameraSpec(
        position=(0.0, 0.0, 5.0),
        target=(0.0, 0.0, 0.0),
        up=(0.0, 1.0, 0.0),
        projection=projection,
        fov_y_deg=50.0,
        near=0.5,
        far=20.0,
        ortho_scale=1.2,
    )


def _plan(*primitives, camera: CameraSpec | None = None) -> RenderPlan:
    return RenderPlan(
        width=96,
        height=96,
        background=(1.0, 1.0, 1.0, 1.0),
        viewports=(
            ViewportPlan(
                semantic_id="main",
                camera=camera or _camera(),
                primitives=tuple(primitives),
            ),
        ),
    )


def _triangle(semantic_id: str, z: float, rgba) -> TriangleMeshPrimitive:
    return TriangleMeshPrimitive(
        semantic_id=semantic_id,
        vertices=np.asarray([[-0.8, -0.8, z], [0.8, -0.8, z], [0.0, 0.8, z]]),
        triangles=np.asarray([[0, 1, 2]]),
        rgba=rgba,
    )


def _text_occlusion_plan(
    *,
    depth_test: bool,
    surface_alpha: float | None = 1.0,
    rect: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0),
    camera: CameraSpec | None = None,
    text_position: tuple[float, float, float] = (0.0, 0.0, -5.0),
    line_alpha: float | None = None,
) -> RenderPlan:
    primitives = []
    if surface_alpha is not None:
        primitives.append(
            TriangleMeshPrimitive(
                semantic_id="opaque-screen",
                vertices=np.asarray(
                    [
                        [-1.0, -1.0, 0.0],
                        [1.0, -1.0, 0.0],
                        [1.0, 1.0, 0.0],
                        [-1.0, 1.0, 0.0],
                    ]
                ),
                triangles=np.asarray([[0, 1, 2], [0, 2, 3]]),
                rgba=(0.0, 0.1, 1.0, surface_alpha),
            )
        )
    if line_alpha is not None:
        primitives.append(
            LinePrimitive(
                semantic_id="foreground-line",
                segments=np.asarray([[[-1.0, 0.0, 0.0], [1.0, 0.0, 0.0]]]),
                rgba=(0.0, 0.1, 1.0, line_alpha),
                width_px=12.0,
            )
        )
    primitives.append(
        TextPrimitive(
            semantic_id="depth-label",
            position=text_position,
            text="DEPTH",
            rgba=(1.0, 0.0, 0.0, 1.0),
            size_pt=12.0,
            offset_px=(3.0, 0.0),
            depth_test=depth_test,
        )
    )
    return RenderPlan(
        width=160,
        height=100,
        background=(1.0, 1.0, 1.0, 1.0),
        viewports=(
            ViewportPlan(
                semantic_id="text-panel",
                camera=camera or _camera(),
                primitives=tuple(primitives),
                rect=rect,
            ),
        ),
    )


def _red_text_pixels(image: np.ndarray) -> np.ndarray:
    rgb = image[..., :3].astype(int)
    return (rgb[..., 0] > rgb[..., 1] + 32) & (
        rgb[..., 0] > rgb[..., 2] + 32
    )


def test_camera_uses_one_depth_convention_and_clips_before_projection():
    transform = CameraTransform(_camera(projection="perspective"), 200, 100)
    target_camera = transform.world_to_camera([[0.0, 0.0, 0.0]])[0]
    assert target_camera == pytest.approx([0.0, 0.0, -5.0])

    clipped = transform.clip_polygon_camera(
        np.asarray([[-0.2, -0.2, -0.2], [0.6, -0.2, -2.0], [0.0, 0.6, -2.0]])
    )
    assert len(clipped) == 4
    assert np.all(-clipped[:, 2] >= _camera().near - 1e-12)
    assert np.all(np.isfinite(transform.project_camera(clipped).xy))


def test_axis_camera_factory_selects_non_degenerate_up_vector():
    camera = CameraSpec.looking_along((0.0, 0.0, 1.0))
    assert np.linalg.norm(np.cross(camera.position, camera.up)) > 0.0


def test_portrait_auto_fit_keeps_all_geometry_inside_orthographic_view():
    scene = {
        "draw_atoms": [
            {"elem": "C", "cart": [-3.0, 0.0, 0.0], "atom_radius": 0.2},
            {"elem": "C", "cart": [3.0, 0.0, 0.0], "atom_radius": 0.2},
        ]
    }
    plan = prepare_render(
        scene,
        render={
            "representation": "ball",
            "width": 80,
            "height": 240,
            "show_cell": False,
            "sphere_detail": (2, 4),
        },
    )
    vertices = np.vstack(
        [
            item.vertices
            for item in plan.primitives
            if isinstance(item, TriangleMeshPrimitive)
        ]
    )
    projected = CameraTransform(plan.camera, plan.width, plan.height).project_world(
        vertices
    )
    assert np.all(projected.visible)


def test_flat_and_smooth_shading_produce_distinct_geometry_and_pixels():
    scene = {"draw_atoms": [{"elem": "Cu", "cart": [0.0, 0.0, 0.0]}]}
    common = {
        "representation": "ball",
        "width": 72,
        "height": 72,
        "show_cell": False,
        "sphere_detail": (4, 8),
    }
    smooth = prepare_render(
        scene, camera=_camera(), render={**common, "shading": "smooth"}
    )
    flat = prepare_render(scene, camera=_camera(), render={**common, "shading": "flat"})
    smooth_mesh = next(
        item for item in smooth.primitives if isinstance(item, TriangleMeshPrimitive)
    )
    flat_mesh = next(
        item for item in flat.primitives if isinstance(item, TriangleMeshPrimitive)
    )
    assert smooth_mesh.vertex_normals is not None
    assert flat_mesh.vertex_normals is None
    assert not np.array_equal(render_rgba(smooth), render_rgba(flat))
    assert render(smooth, format="svg").data != render(flat, format="svg").data
    with pytest.raises(ValueError, match="unknown shading"):
        RenderSpec(shading="silently-invented")
    with pytest.raises(ValueError, match="unknown ORTEP mode"):
        RenderSpec(representation="ortep", ortep_mode="octant")
    with pytest.raises(ValueError, match="missing_adp_policy must be 'error' or 'sphere'"):
        RenderSpec(missing_adp_policy="placeholder")
    with pytest.raises(ValueError, match="requires representation='ortep'"):
        RenderSpec(ortep_mode="hatch")
    with pytest.raises(ValueError, match="unknown RenderSpec fields: glow"):
        prepare_render(scene, render={**common, "glow": True})
    with pytest.raises(ValueError, match="material must be mesh, smooth, or flat"):
        prepare_render(scene, render={**common, "material": "glossy"})
    with pytest.raises(ValueError, match="conflicting surface shading"):
        prepare_render(
            scene,
            render={**common, "material": "flat", "shading": "smooth"},
        )


def test_geometry_builders_emit_backend_neutral_indexed_primitives():
    sphere = sphere_primitive("atom:0", (0, 0, 0), 0.5, "#ff0000")
    ellipsoid = ellipsoid_primitive(
        "atom:1", (1, 0, 0), np.diag([0.04, 0.02, 0.01]), "#0000ff"
    )
    cell = unit_cell_primitive("cell", np.diag([2.0, 3.0, 4.0]))
    ring = aromatic_ring_primitive(
        "ring:0",
        [
            [np.cos(t), np.sin(t), 0.0]
            for t in np.linspace(0, 2 * np.pi, 6, endpoint=False)
        ],
        "#333333",
        mode="disk",
    )
    assert sphere.vertices.shape[1] == 3
    assert sphere.vertex_normals.shape == sphere.vertices.shape
    assert ellipsoid.triangles.shape[1] == 3
    assert cell.segments.shape == (12, 2, 3)
    assert isinstance(ring, TriangleMeshPrimitive)
    assert not sphere.vertices.flags.writeable


@pytest.mark.parametrize(
    ("probability", "expected_radius"),
    [(0.50, 1.5381722545), (0.90, 2.5002777108), (0.95, 2.7954834829)],
)
def test_ortep_probability_radius_matches_exact_chi3_quantiles(
    probability, expected_radius
):
    lengths, _axes = ellipsoid_principal_axes(np.eye(3), probability=probability)
    assert lengths == pytest.approx([expected_radius] * 3, abs=2e-9)


def test_png_z_buffer_is_global_across_primitives_and_order_independent():
    far = _triangle("a-far", 0.0, (0.0, 0.0, 1.0, 1.0))
    near = _triangle("z-near", 1.0, (1.0, 0.0, 0.0, 1.0))
    first = render_rgba(_plan(far, near))[48, 48]
    second = render_rgba(_plan(near, far))[48, 48]
    assert np.array_equal(first, second)
    assert first[0] > first[2]


def test_png_transparency_uses_per_pixel_depth_sorted_fragments():
    far = _triangle("z-far", 0.0, (0.0, 0.0, 1.0, 0.5))
    near = _triangle("a-near", 1.0, (1.0, 0.0, 0.0, 0.5))
    pixel = render_rgba(_plan(near, far))[48, 48]
    assert pixel[0] > pixel[2]
    assert pixel[3] == 255


@pytest.mark.parametrize("projection", ["orthographic", "perspective"])
def test_atom_occludes_far_bond_and_nearer_crossing_bond_wins(projection):
    camera = _camera(projection=projection)
    far_bond = cylinder_primitive(
        "bond:far",
        (-0.9, 0.0, 0.0),
        (0.9, 0.0, 0.0),
        0.07,
        "#0044ff",
        sides=6,
    )
    crossing_bond = cylinder_primitive(
        "bond:near",
        (0.45, -0.7, 0.32),
        (0.45, 0.7, 0.32),
        0.07,
        "#00cc33",
        sides=6,
    )
    atom = sphere_primitive(
        "atom:front",
        (-0.5, 0.0, 0.58),
        0.24,
        "#ee2211",
        lat_steps=4,
        lon_steps=8,
    )
    plan = _plan(far_bond, crossing_bond, atom, camera=camera)
    image = render_rgba(plan)
    transform = CameraTransform(camera, plan.width, plan.height)

    def patch_at(point):
        xy = transform.project_world([point]).xy[0]
        x, y = np.rint(xy).astype(int)
        return image[max(0, y - 1) : y + 2, max(0, x - 1) : x + 2, :3]

    atom_patch = patch_at((-0.5, 0.0, 0.58))
    crossing_patch = patch_at((0.45, 0.0, 0.32))
    assert atom_patch[1, 1, 0] > atom_patch[1, 1, 2]
    assert crossing_patch[1, 1, 1] > crossing_patch[1, 1, 2]


def test_overlapping_aromatic_rings_split_at_crossings_in_png_and_svg():
    def ring(identifier, center_x, z, color):
        points = [
            [center_x + np.cos(angle), np.sin(angle), z]
            for angle in np.linspace(0.0, 2.0 * np.pi, 6, endpoint=False)
        ]
        return aromatic_ring_primitive(identifier, points, color, mode="circle")

    far = ring("ring:far", -0.25, 0.0, "#dd2200")
    near = ring("ring:near", 0.25, 0.35, "#0044ee")
    plan = _plan(far, near)
    _polygons, pieces = vector_scene(plan.viewports[0], plan.width, plan.height)
    assert len(pieces) > len(far.segments) + len(near.segments)
    image = render_rgba(plan)
    intersection_y = float(np.sqrt(0.62**2 - 0.25**2))
    xy = (
        CameraTransform(plan.camera, plan.width, plan.height)
        .project_world([[0.0, intersection_y, 0.35]])
        .xy[0]
    )
    x, y = np.rint(xy).astype(int)
    patch = image[y - 2 : y + 3, x - 2 : x + 3, :3]
    assert patch[2, 2, 2] > patch[2, 2, 0]
    svg = render(plan, format="svg").data
    assert svg is not None and b"<image" not in svg.lower()


def test_intersecting_transparent_polyhedra_use_abuffer_and_vector_bsp():
    faces = ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3))
    first = polyhedron_primitive(
        "polyhedron:a",
        [[0, 0, 0.9], [-0.8, -0.6, -0.6], [0.8, -0.6, -0.6], [0, 0.9, -0.6]],
        faces,
        "#ee2200",
        alpha=0.45,
    )
    second = polyhedron_primitive(
        "polyhedron:b",
        [[0, 0, -0.9], [-0.8, 0.6, 0.6], [0.8, 0.6, 0.6], [0, -0.9, 0.6]],
        faces,
        "#0044ee",
        alpha=0.45,
    )
    plan = _plan(first, second)
    reverse = _plan(second, first)
    assert np.array_equal(render_rgba(plan), render_rgba(reverse))
    polygons, _lines = vector_scene(plan.viewports[0], plan.width, plan.height)
    assert len(polygons) > len(first.triangles) + len(second.triangles)
    svg = render(plan, format="svg").data
    assert svg is not None and b"<image" not in svg.lower()


def test_orthographic_oblique_overlap_paints_back_to_front_in_svg_and_pdf():
    camera = CameraSpec(
        position=(0.0, 0.0, 0.0),
        target=(0.0, 0.0, -1.0),
        up=(0.0, 1.0, 0.0),
        projection="orthographic",
        near=0.1,
        far=30.0,
        ortho_scale=12.0,
    )
    xy = np.asarray([[9.0, -1.0], [11.0, -1.0], [10.0, 1.0]])

    def oblique_triangle(identifier, z, rgba):
        return TriangleMeshPrimitive(
            semantic_id=identifier,
            vertices=np.column_stack((xy, z(xy[:, 0]))),
            triangles=np.asarray([[0, 1, 2]]),
            rgba=rgba,
        )

    front = oblique_triangle(
        "A-front-red",
        lambda x: 1.0 - x,
        (1.0, 0.0, 0.0, 0.5),
    )
    back = oblique_triangle(
        "B-back-blue",
        lambda x: -x,
        (0.0, 0.0, 1.0, 0.5),
    )
    tree = build_bsp(
        [
            BSPPolygon(front.vertices, front.rgba, front.semantic_id, 0),
            BSPPolygon(back.vertices, back.rgba, back.semantic_id, 1),
        ]
    )
    assert [item.semantic_id for item in traverse_back_to_front(tree, eye=np.zeros(3))] == [
        "A-front-red",
        "B-back-blue",
    ]
    assert [
        item.semantic_id
        for item in traverse_back_to_front(
            tree,
            view_direction=np.asarray([0.0, 0.0, -1.0]),
        )
    ] == ["B-back-blue", "A-front-red"]
    plan = _plan(front, back, camera=camera)

    polygons, _lines = vector_scene(plan.viewports[0], plan.width, plan.height)
    assert [item.polygon.semantic_id for item in polygons] == [
        "B-back-blue",
        "A-front-red",
    ]

    svg = render(plan, format="svg").data
    pdf = render(plan, format="pdf").data
    assert svg is not None and b"<image" not in svg.lower()
    assert pdf is not None and pdf.startswith(b"%PDF")
    assert b"/Subtype /Image" not in pdf


def test_png_bytes_and_plan_hash_are_deterministic():
    plan = _plan(_triangle("triangle", 0.0, (0.2, 0.5, 0.8, 1.0)))
    first = render(plan, format="png")
    second = render(plan, format="png")
    assert plan.schema == "mattervis.render-plan/v1"
    assert first.schema == "mattervis.render-result/v1"
    assert first.data == second.data
    assert first.output_sha256 == second.output_sha256
    assert first.plan_sha256 == plan.fingerprint()


def test_backend_neutral_import_does_not_load_optional_frontends():
    script = (
        "import json,sys; import mat_viewer.render; "
        "print(json.dumps([name for name in "
        "('plotly','dash','kaleido','textual','skimage','imageio','matplotlib','PIL') "
        "if name in sys.modules]))"
    )
    process = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(process.stdout) == []


def test_vector_outputs_are_true_vector_without_embedded_page_image():
    plan = _plan(
        _triangle("triangle", 0.0, (0.2, 0.5, 0.8, 0.7)),
        LinePrimitive(
            semantic_id="line",
            segments=np.asarray([[[-0.9, 0.0, 0.5], [0.9, 0.0, 0.5]]]),
            rgba=(0.0, 0.0, 0.0, 1.0),
            width_px=2.0,
        ),
    )
    svg = render(plan, format="svg").data
    pdf = render(plan, format="pdf").data
    assert svg is not None and b"<svg" in svg
    assert b"<image" not in svg.lower()
    assert pdf is not None and pdf.startswith(b"%PDF")
    assert b"/Subtype /Image" not in pdf


@pytest.mark.parametrize("projection", ["orthographic", "perspective"])
def test_text_anchor_depth_test_is_strict_for_opaque_but_not_transparent_surfaces(
    projection,
):
    camera = _camera(projection=projection)
    hidden = render_rgba(_text_occlusion_plan(depth_test=True, camera=camera))
    overlay = render_rgba(_text_occlusion_plan(depth_test=False, camera=camera))
    assert not np.any(_red_text_pixels(hidden))
    assert np.count_nonzero(_red_text_pixels(overlay)) > 20

    transparent_depth_test = render_rgba(
        _text_occlusion_plan(depth_test=True, surface_alpha=0.5, camera=camera)
    )
    transparent_overlay = render_rgba(
        _text_occlusion_plan(depth_test=False, surface_alpha=0.5, camera=camera)
    )
    assert np.array_equal(transparent_depth_test, transparent_overlay)
    assert np.count_nonzero(_red_text_pixels(transparent_depth_test)) > 20

    line_hidden = render_rgba(
        _text_occlusion_plan(
            depth_test=True,
            surface_alpha=None,
            line_alpha=1.0,
            camera=camera,
        )
    )
    line_overlay = render_rgba(
        _text_occlusion_plan(
            depth_test=False,
            surface_alpha=None,
            line_alpha=1.0,
            camera=camera,
        )
    )
    assert not np.any(_red_text_pixels(line_hidden))
    assert np.count_nonzero(_red_text_pixels(line_overlay)) > 20


@pytest.mark.parametrize("output_format", ["svg", "pdf"])
def test_vector_text_anchor_depth_test_matches_png(output_format, monkeypatch):
    from matplotlib.axes import Axes

    observed: list[str] = []
    original_text = Axes.text

    def record_text(axes, x, y, text, *args, **kwargs):
        if text == "DEPTH":
            observed.append(text)
        return original_text(axes, x, y, text, *args, **kwargs)

    monkeypatch.setattr(Axes, "text", record_text)
    hidden = render(_text_occlusion_plan(depth_test=True), format=output_format)
    assert observed == []

    observed.clear()
    overlay = render(_text_occlusion_plan(depth_test=False), format=output_format)
    assert observed == ["DEPTH"]

    observed.clear()
    transparent = render(
        _text_occlusion_plan(depth_test=True, surface_alpha=0.5),
        format=output_format,
    )
    assert observed == ["DEPTH"]

    observed.clear()
    line_hidden = render(
        _text_occlusion_plan(
            depth_test=True,
            surface_alpha=None,
            line_alpha=1.0,
        ),
        format=output_format,
    )
    assert observed == []

    observed.clear()
    line_overlay = render(
        _text_occlusion_plan(
            depth_test=False,
            surface_alpha=None,
            line_alpha=1.0,
        ),
        format=output_format,
    )
    assert observed == ["DEPTH"]

    observed.clear()
    near_camera = CameraSpec(
        position=(0.0, 0.0, 0.0),
        target=(0.0, 0.0, -1.0),
        up=(0.0, 1.0, 0.0),
        projection="orthographic",
        near=1.0,
        far=20.0,
        ortho_scale=1.2,
    )
    near_clipped = render(
        _text_occlusion_plan(
            depth_test=False,
            surface_alpha=None,
            camera=near_camera,
            text_position=(0.0, 0.0, -0.5),
        ),
        format=output_format,
    )
    assert observed == []
    for result in (
        hidden,
        overlay,
        transparent,
        line_hidden,
        line_overlay,
        near_clipped,
    ):
        assert result.data is not None
        if output_format == "svg":
            assert b"<image" not in result.data.lower()
        else:
            assert result.data.startswith(b"%PDF")
            assert b"/Subtype /Image" not in result.data


def test_text_anchor_respects_near_clip_and_raster_viewport_offset():
    right_overlay = render_rgba(
        _text_occlusion_plan(
            depth_test=False,
            surface_alpha=None,
            rect=(0.5, 0.0, 0.5, 1.0),
        )
    )
    red = _red_text_pixels(right_overlay)
    assert not np.any(red[:, :80])
    assert np.count_nonzero(red[:, 80:]) > 20

    near_camera = CameraSpec(
        position=(0.0, 0.0, 0.0),
        target=(0.0, 0.0, -1.0),
        up=(0.0, 1.0, 0.0),
        projection="orthographic",
        near=1.0,
        far=20.0,
        ortho_scale=1.2,
    )
    clipped = render_rgba(
        _text_occlusion_plan(
            depth_test=False,
            surface_alpha=None,
            rect=(0.5, 0.0, 0.5, 1.0),
            camera=near_camera,
            text_position=(0.0, 0.0, -0.5),
        )
    )
    assert not np.any(_red_text_pixels(clipped))


def test_vector_line_is_split_at_opaque_triangle_occlusion_boundaries():
    occluder = TriangleMeshPrimitive(
        semantic_id="occluder",
        vertices=np.asarray([[-0.5, -0.5, 1.0], [0.5, -0.5, 1.0], [0.0, 0.5, 1.0]]),
        triangles=np.asarray([[0, 1, 2]]),
        rgba=(0.8, 0.8, 0.8, 1.0),
    )
    behind = LinePrimitive(
        semantic_id="behind",
        segments=np.asarray([[[-1.0, 0.0, 0.0], [1.0, 0.0, 0.0]]]),
        rgba=(0.0, 0.0, 0.0, 1.0),
        width_px=1.0,
    )
    viewport = _plan(occluder, behind).viewports[0]
    pieces = visible_line_segments(viewport, 200, 200)
    assert len(pieces) == 2
    assert max(piece.end[0] - piece.start[0] for piece in pieces) < 85.0


@pytest.mark.parametrize("output_format", ["svg", "pdf"])
def test_vector_non_depth_tested_line_is_drawn_after_transparent_surfaces(
    output_format,
):
    surface = _triangle("transparent-screen", 0.0, (0.0, 0.1, 1.0, 0.5))
    overlay = LinePrimitive(
        semantic_id="overlay-line",
        segments=np.asarray([[[-0.8, 0.0, -1.0], [0.8, 0.0, -1.0]]]),
        rgba=(1.0, 0.0, 0.0, 1.0),
        width_px=3.0,
        depth_test=False,
    )
    plan = _plan(surface, overlay)
    polygons, pieces = vector_scene(plan.viewports[0], plan.width, plan.height)

    assert len(polygons) == 1
    assert pieces
    assert {piece.insertion_index for piece in pieces} == {len(polygons)}

    result = render(plan, format=output_format)
    assert result.data is not None
    if output_format == "svg":
        assert b"<image" not in result.data.lower()
    else:
        assert result.data.startswith(b"%PDF")
        assert b"/Subtype /Image" not in result.data


def test_bsp_splits_intersecting_polygons_instead_of_centroid_sorting():
    first = BSPPolygon(
        vertices=np.asarray([[0.0, -1.0, -3.0], [0.0, 1.0, -3.0], [0.0, 0.0, -5.0]]),
        rgba=(1.0, 0.0, 0.0, 1.0),
        semantic_id="first",
        source_order=0,
    )
    second = BSPPolygon(
        vertices=np.asarray([[-1.0, -0.2, -4.0], [1.0, -0.2, -4.0], [0.0, 1.0, -4.0]]),
        rgba=(0.0, 0.0, 1.0, 0.5),
        semantic_id="second",
        source_order=1,
    )
    ordered = traverse_back_to_front(build_bsp([first, second]))
    assert len(ordered) > 2
    assert {item.semantic_id for item in ordered} == {"first", "second"}


def test_prepare_render_builds_atoms_bonds_ring_cell_and_polyhedron():
    ring_points = [
        [float(np.cos(t)), float(np.sin(t)), 0.0]
        for t in np.linspace(0, 2 * np.pi, 6, endpoint=False)
    ]
    scene = {
        "draw_atoms": [
            {"elem": "C", "label": f"C{index + 1}", "cart": point, "atom_radius": 0.7}
            for index, point in enumerate(ring_points)
        ],
        "bonds": [
            {
                "i": index,
                "j": (index + 1) % 6,
                "start": ring_points[index],
                "end": ring_points[(index + 1) % 6],
            }
            for index in range(6)
        ],
        "rings": [
            {
                "cycle_atom_indices": list(range(6)),
                "aromatic": True,
                "normal": [0, 0, 1],
            }
        ],
        "M": np.eye(3) * 3.0,
        "polyhedra": [
            {
                "vertices": [[0, 0, 0.5], [1, 0, 0.5], [0, 1, 0.5], [0, 0, 1.5]],
                "faces": [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]],
                "color": "#55aa88",
            }
        ],
    }
    plan = prepare_render(
        scene,
        render=RenderSpec(
            width=120,
            height=100,
            aromatic_rings="circle",
            show_cell=True,
        ),
    )
    ids = {primitive.semantic_id for primitive in plan.primitives}
    assert any(identifier.startswith("atom:") for identifier in ids)
    assert any(identifier.startswith("bond:") for identifier in ids)
    assert "ring:0" in ids
    assert "unit-cell" in ids
    assert any(identifier.startswith("polyhedron:") for identifier in ids)


def test_shuffled_source_ring_cycle_is_lifted_to_each_formula_unit_copy():
    source_cycle = (41, 7, 83, 2, 59, 13)
    display_order = (83, 13, 41, 59, 7, 2)
    source_positions = {
        source_index: np.asarray([np.cos(angle), np.sin(angle), 0.0])
        for source_index, angle in zip(
            source_cycle,
            np.linspace(0.0, 2.0 * np.pi, 6, endpoint=False),
        )
    }
    atoms = []
    expected: dict[tuple[int, int, int], list[int]] = {}
    for shift in ((1, 0, 0), (0, 0, 0)):
        expected[shift] = []
        translation = np.asarray(shift, dtype=float) * np.asarray([4.0, 4.0, 4.0])
        indices_for_source = {}
        for source_index in display_order:
            indices_for_source[source_index] = len(atoms)
            atoms.append(
                {
                    "elem": "C",
                    "label": f"C{source_index}",
                    "cart": source_positions[source_index] + translation,
                    "atom_radius": 0.2,
                    "_source_index": source_index,
                    "_molecule_index": 12,
                    "_formula_image_shift": shift,
                    # Deliberately different per atom: this must not split a
                    # formula-unit ring into crystallographic image fragments.
                    "_image_shift": (source_index % 2, 0, 0),
                }
            )
        expected[shift] = [indices_for_source[index] for index in source_cycle]

    plan = prepare_render(
        {
            "draw_atoms": atoms,
            "rings": [
                {
                    "molecule_index": 12,
                    "cycle_atom_indices": source_cycle,
                    "is_aromatic": True,
                    "normal": (0.0, 0.0, 1.0),
                }
            ],
        },
        render={
            "representation": "ball",
            "aromatic_rings": "circle",
            "show_cell": False,
        },
    )
    rings = [
        item for item in plan.primitives if item.metadata.get("kind") == "aromatic_ring"
    ]
    assert len(rings) == 2
    for ring in rings:
        shift = tuple(ring.metadata["display_copy"][2])
        assert ring.metadata["source_atom_indices"] == list(source_cycle)
        assert ring.metadata["atom_indices"] == expected[shift]
        copy_positions = np.asarray(
            [atoms[index]["cart"] for index in ring.metadata["atom_indices"]]
        )
        assert np.ptp(copy_positions[:, 0]) < 2.1


def test_aromatic_ring_arc_is_split_and_hidden_by_a_foreground_atom():
    source_cycle = (19, 3, 47, 8, 31, 12)
    ring_atoms = [
        {
            "elem": "C",
            "label": f"C{source_index}",
            "cart": [np.cos(angle), np.sin(angle), 0.0],
            "atom_radius": 0.2,
            "_source_index": source_index,
            "_source_molecule_index": 4,
            "_image_shift": (0, 0, 0),
        }
        for source_index, angle in zip(
            source_cycle,
            np.linspace(0.0, 2.0 * np.pi, 6, endpoint=False),
        )
    ]
    ring_record = {
        "molecule_index": 4,
        "cycle_atom_indices": source_cycle,
        "is_aromatic": True,
        "normal": (0.0, 0.0, 1.0),
    }
    render_spec = {
        "representation": "ball",
        "aromatic_rings": "circle",
        "show_cell": False,
        "width": 160,
        "height": 160,
        "sphere_detail": (2, 4),
    }
    clear = prepare_render(
        {"draw_atoms": ring_atoms, "rings": [ring_record]},
        camera=_camera(),
        render=render_spec,
    )
    occluding_atom = {
        "elem": "O",
        "label": "O-front",
        "cart": [0.62, 0.0, 0.55],
        "atom_radius": 0.38,
        "_source_index": 999,
        "_source_molecule_index": 99,
        "_image_shift": (0, 0, 0),
    }
    occluded = prepare_render(
        {"draw_atoms": [*ring_atoms, occluding_atom], "rings": [ring_record]},
        camera=_camera(),
        render=render_spec,
    )
    clear_pieces = [
        piece
        for piece in visible_line_segments(clear.viewports[0], 160, 160)
        if piece.semantic_id.startswith("ring:")
    ]
    occluded_pieces = [
        piece
        for piece in visible_line_segments(occluded.viewports[0], 160, 160)
        if piece.semantic_id.startswith("ring:")
    ]
    clear_length = sum(
        np.linalg.norm(np.subtract(piece.end, piece.start)) for piece in clear_pieces
    )
    occluded_length = sum(
        np.linalg.norm(np.subtract(piece.end, piece.start)) for piece in occluded_pieces
    )
    assert occluded_length < clear_length * 0.9
    assert render_rgba(occluded).shape == (160, 160, 4)


def test_ortep_missing_adp_is_never_a_silent_default():
    scene = {"draw_atoms": [{"elem": "C", "label": "C1", "cart": [0, 0, 0]}]}
    with pytest.raises(ValueError, match="requires Ucart"):
        prepare_render(scene, render={"representation": "ortep"})
    plan = prepare_render(
        scene,
        render={"representation": "ortep", "missing_adp_policy": "sphere"},
    )
    assert any("explicit isotropic placeholder" in warning for warning in plan.warnings)


def test_disorder_opacity_and_cross_cell_bond_vector_survive_planning():
    scene = {
        "draw_atoms": [
            {
                "elem": "C",
                "label": "C1A",
                "cart": [0.8, 0.0, 0.0],
                "occupancy": 0.5,
                "disorder_group": 1,
                "disorder_assembly": "A",
                "_source_index": 0,
            },
            {
                "elem": "N",
                "label": "N1",
                "cart": [0.1, 0.0, 0.0],
                "occupancy": 1.0,
                "_source_index": 1,
            },
        ],
        "bonds": [
            {
                "left_global_index": 0,
                "right_global_index": 1,
                "vector_A": [0.3, 0.0, 0.0],
                "right_image_shift": [1, 0, 0],
            }
        ],
    }
    plan = prepare_render(
        scene,
        camera=_camera(),
        render={"show_cell": False, "sphere_detail": (2, 4), "cylinder_sides": 6},
    )
    disordered = next(
        item for item in plan.primitives if item.semantic_id.startswith("atom:0")
    )
    assert disordered.rgba[3] == pytest.approx(0.5)
    assert disordered.metadata["disorder_group"] == 1
    bond_meshes = [
        item for item in plan.primitives if item.metadata.get("kind") == "bond"
    ]
    assert bond_meshes
    bond_vertices = np.vstack([item.vertices for item in bond_meshes])
    assert bond_vertices[:, 0].min() == pytest.approx(0.8)
    assert bond_vertices[:, 0].max() == pytest.approx(1.1)
    assert all(item.metadata["right_image_shift"] == (1, 0, 0) for item in bond_meshes)
    assert render(plan, format="png").data.startswith(b"\x89PNG")


def test_ortep_hatch_is_depth_tested_in_raster_and_vector_pipelines():
    scene = {
        "draw_atoms": [
            {
                "elem": "C",
                "label": "C1",
                "cart": [0.0, 0.0, 0.0],
                "U": np.diag([0.04, 0.025, 0.015]),
            }
        ]
    }
    plan = prepare_render(
        scene,
        camera=_camera(),
        render={
            "representation": "ortep",
            "shading": "flat",
            "ortep_mode": "hatch",
            "show_cell": False,
            "width": 80,
            "height": 80,
            "sphere_detail": (3, 6),
        },
    )
    assert any(item.semantic_id.endswith(":hatch") for item in plan.primitives)
    surface = next(
        item for item in plan.primitives if item.semantic_id.startswith("atom:")
    )
    assert surface.vertex_normals is None
    clear_hatch = [
        piece
        for piece in visible_line_segments(plan.viewports[0], 80, 80)
        if piece.semantic_id.endswith(":hatch")
    ]
    occluder = TriangleMeshPrimitive(
        semantic_id="foreground-mask",
        vertices=np.asarray(
            [[-1.0, -1.0, 0.8], [0.0, -1.0, 0.8], [0.0, 1.0, 0.8], [-1.0, 1.0, 0.8]]
        ),
        triangles=np.asarray([[0, 1, 2], [0, 2, 3]]),
        rgba=(1.0, 1.0, 1.0, 1.0),
    )
    masked_viewport = ViewportPlan(
        semantic_id="masked",
        camera=plan.camera,
        primitives=(*plan.primitives, occluder),
    )
    masked_hatch = [
        piece
        for piece in visible_line_segments(masked_viewport, 80, 80)
        if piece.semantic_id.endswith(":hatch")
    ]
    clear_length = sum(
        np.linalg.norm(np.subtract(item.end, item.start)) for item in clear_hatch
    )
    masked_length = sum(
        np.linalg.norm(np.subtract(item.end, item.start)) for item in masked_hatch
    )
    assert masked_length < clear_length * 0.7
    assert render(plan, format="png").data.startswith(b"\x89PNG")
    svg = render(plan, format="svg").data
    assert svg is not None and b"<image" not in svg.lower()


def test_prepare_render_consumes_exact_molcryskit_public_records():
    @dataclass(frozen=True)
    class Site:
        global_index: int
        molecule_index: int
        local_index: int
        symbol: str
        label: str
        cartesian_position_A: tuple[float, float, float]
        fractional_position: tuple[float, float, float]
        occupancy: float
        disorder_group: int | None = None
        disorder_assembly: str | None = None
        asym_index: int | None = None
        sym_op_index: int = 0
        site_symmetry_order: int = 1
        image_shift: tuple[int, int, int] = (0, 0, 0)
        uiso_A2: float | None = None
        u_cart_A2: np.ndarray | None = None

    @dataclass(frozen=True)
    class Bond:
        molecule_index: int
        left_local_index: int
        right_local_index: int
        left_global_index: int
        right_global_index: int
        left_asym_index: int | None
        right_asym_index: int | None
        right_image_shift: tuple[int, int, int]
        vector_A: tuple[float, float, float]
        distance_A: float

    class Structure:
        cell = np.eye(3) * 4.0

        def get_site_records(self):
            return [
                Site(0, 0, 0, "C", "C1", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 1.0),
                Site(1, 0, 1, "O", "O1", (1.2, 0.0, 0.0), (0.3, 0.0, 0.0), 1.0),
            ]

        def get_bond_records(self):
            return [Bond(0, 0, 1, 0, 1, 0, 1, (0, 0, 0), (1.2, 0.0, 0.0), 1.2)]

    plan = prepare_render(
        Structure(),
        view={"display": "unit_cell"},
        render={"show_cell": True},
    )
    assert sum(item.semantic_id.startswith("atom:") for item in plan.primitives) == 2
    assert sum(item.semantic_id.startswith("bond:") for item in plan.primitives) == 2
    assert "unit-cell" in {item.semantic_id for item in plan.primitives}


def test_direct_molecular_crystal_uses_local_geometry_cycle_contract(monkeypatch):
    @dataclass(frozen=True)
    class Site:
        global_index: int
        molecule_index: int
        local_index: int
        symbol: str
        label: str
        cartesian_position_A: tuple[float, float, float]
        occupancy: float = 1.0

    class Ring:
        atom_indices = (0, 1, 2)
        cycle_atom_indices = (2, 0, 1)
        is_aromatic = True
        normal = (0.0, 0.0, 1.0)

    class Geometry:
        def rings(self):
            return [Ring()]

    class Cache:
        def __init__(self, source):
            assert len(source.molecules) == 1

        def __getitem__(self, molecule_index):
            assert molecule_index == 0
            return Geometry()

    class Structure:
        molecules = (object(),)

        def get_site_records(self):
            return [
                Site(0, 0, 0, "C", "C0", (1.0, 0.0, 0.0)),
                Site(1, 0, 1, "C", "C1", (-0.5, 0.866, 0.0)),
                Site(2, 0, 2, "C", "C2", (-0.5, -0.866, 0.0)),
            ]

        def get_bond_records(self):
            return []

    import molcrys_kit.analysis

    monkeypatch.setattr(molcrys_kit.analysis, "LocalGeometryCache", Cache)
    plan = prepare_render(
        Structure(),
        view={"display": "unit_cell"},
        render={
            "representation": "ball",
            "aromatic_rings": "circle",
            "show_cell": False,
        },
    )
    ring = next(item for item in plan.primitives if item.semantic_id == "ring:0")
    assert ring.metadata["source_atom_indices"] == [2, 0, 1]
    assert ring.metadata["atom_indices"] == [2, 0, 1]


def test_direct_molecular_crystal_display_modes_use_public_selection_contracts(
    monkeypatch,
):
    @dataclass(frozen=True)
    class Site:
        global_index: int
        molecule_index: int
        local_index: int
        symbol: str
        label: str
        cartesian_position_A: tuple[float, float, float]
        asym_index: int
        sym_op_index: int
        image_shift: tuple[int, int, int] = (0, 0, 0)
        occupancy: float = 1.0

    @dataclass(frozen=True)
    class Bond:
        molecule_index: int
        left_global_index: int
        right_global_index: int
        vector_A: tuple[float, float, float]
        right_image_shift: tuple[int, int, int] = (0, 0, 0)

    class Geometry:
        def rings(self):
            return []

    class Cache:
        def __init__(self, source):
            self.source = source

        def __getitem__(self, molecule_index):
            return Geometry()

    class Analyzer:
        def __init__(self, source):
            self.source = source

        def select_formula_unit(self):
            member = SimpleNamespace(
                species_id="CN_1",
                molecule_index=1,
                image_shift=(1, 0, 0),
            )
            return SimpleNamespace(members=(member,))

    class Structure:
        lattice = np.diag([10.0, 10.0, 10.0])
        molecules = (object(), object())

        def get_site_records(self):
            return [
                Site(0, 0, 0, "O", "O1", (1.0, 0.0, 0.0), 0, 0),
                Site(1, 1, 0, "C", "C1", (2.0, 0.0, 0.0), 0, 1),
                Site(2, 1, 1, "N", "N1", (3.0, 0.0, 0.0), 1, 0),
            ]

        def get_bond_records(self):
            return [Bond(1, 1, 2, (1.0, 0.0, 0.0))]

    import molcrys_kit.analysis

    monkeypatch.setattr(molcrys_kit.analysis, "LocalGeometryCache", Cache)
    monkeypatch.setattr(molcrys_kit.analysis, "StoichiometryAnalyzer", Analyzer)
    common = {"representation": "ball", "show_cell": False, "sphere_detail": (2, 4)}
    formula = prepare_render(Structure(), render=common)
    unit_cell = prepare_render(
        Structure(), view={"display": "unit_cell"}, render=common
    )
    asymmetric = prepare_render(
        Structure(), view={"display": "asymmetric_unit"}, render=common
    )

    formula_atoms = [
        item for item in formula.primitives if item.metadata.get("kind") == "atom"
    ]
    unit_atoms = [
        item for item in unit_cell.primitives if item.metadata.get("kind") == "atom"
    ]
    asym_atoms = [
        item for item in asymmetric.primitives if item.metadata.get("kind") == "atom"
    ]
    assert [item.metadata["source_index"] for item in formula_atoms] == [1, 2]
    assert len(unit_atoms) == 3
    assert {item.metadata["source_index"] for item in asym_atoms} == {0, 2}
    assert min(float(item.vertices[:, 0].mean()) for item in formula_atoms) > 11.5
    assert formula.metadata["display_mode"] == "formula_unit"
    assert unit_cell.metadata["display_mode"] == "unit_cell"
    assert asymmetric.metadata["display_mode"] == "asymmetric_unit"
    with pytest.raises(ValueError, match="undefined for direct MolecularCrystal"):
        prepare_render(Structure(), view={"display": "cluster"}, render=common)


def test_real_molecular_crystal_lattice_emits_unit_cell_primitive():
    from ase import Atoms
    from molcrys_kit.structures import MolecularCrystal

    if not hasattr(MolecularCrystal, "get_site_records"):
        pytest.skip("requires the pinned MolCrysKit structure-contract commit")
    lattice = np.asarray([[4.0, 0.0, 0.0], [0.8, 5.0, 0.0], [0.3, 0.4, 6.0]])
    crystal = MolecularCrystal(
        lattice,
        [Atoms("C", positions=[[0.5, 0.6, 0.7]])],
    )
    plan = prepare_render(
        crystal,
        render={"representation": "ball", "show_cell": True},
    )
    cell = next(item for item in plan.primitives if item.semantic_id == "unit-cell")
    assert np.max(cell.segments[:, :, 0]) == pytest.approx(5.1)
    assert np.max(cell.segments[:, :, 2]) == pytest.approx(6.0)


def test_structure_input_view_rebuilds_loaded_bundle_with_canonical_display_mode(
    monkeypatch,
):
    from mat_viewer.loader import core as loader_core

    calls = []

    def fake_build_bundle_scene(bundle, *, display_mode, show_hydrogen):
        calls.append((bundle, display_mode, show_hydrogen))
        count = 1 if display_mode == "formula_unit" else 3
        return {
            "draw_atoms": [
                {
                    "elem": "C",
                    "label": f"C{index}",
                    "cart": [float(index), 0.0, 0.0],
                }
                for index in range(count)
            ],
            "bonds": [],
            "display_mode": display_mode,
        }

    monkeypatch.setattr(loader_core, "build_bundle_scene", fake_build_bundle_scene)
    bundle = SimpleNamespace(
        scene={"draw_atoms": [], "display_mode": "formula_unit"},
        raw_atoms=[],
        scene_cache={},
        M=np.eye(3),
        cell=SimpleNamespace(),
        formula_unit_atoms=[],
        cube_data=None,
        cif_path="view-test.cif",
        molcrys_analysis={"source": "MolCrysKit"},
    )
    frame = SimpleNamespace(index=2, bundle=bundle, info={}, atom_arrays={})
    structure_input = SimpleNamespace(
        path=Path("view-test.cif"),
        input_format="cif",
        frames=(frame,),
        total_frames=1,
    )
    formula = prepare_render(
        structure_input,
        view={"display": "formula_unit"},
        render={"representation": "ball", "show_cell": False},
    )
    unit_cell = prepare_render(
        structure_input,
        view={"display": "unit_cell"},
        render={"representation": "ball", "show_cell": False},
    )
    formula_atoms = [
        item for item in formula.primitives if item.metadata.get("kind") == "atom"
    ]
    cell_atoms = [
        item for item in unit_cell.primitives if item.metadata.get("kind") == "atom"
    ]
    assert len(formula_atoms) == 1
    assert len(cell_atoms) == 3
    assert formula.metadata["display_mode"] == "formula_unit"
    assert unit_cell.metadata["display_mode"] == "unit_cell"
    assert [item[1:] for item in calls] == [
        ("formula_unit", False),
        ("unit_cell", False),
    ]


def test_view_spec_rejects_unknown_display_mode():
    with pytest.raises(ValueError, match="unsupported display mode"):
        prepare_render(
            {"draw_atoms": []},
            view={"display": "mystery"},
            render={"show_cell": False},
        )


def test_precomputed_cube_mesh_renders_on_cpu_without_plotly_or_skimage():
    mesh = {
        "vertices": [
            [-0.6, -0.6, 0.0],
            [0.6, -0.6, 0.0],
            [0.0, 0.6, 0.0],
            [0.0, 0.0, 0.8],
        ],
        "faces": [[0, 1, 2], [0, 1, 3], [1, 2, 3], [2, 0, 3]],
        "color": "#D55E00",
        "opacity": 0.55,
        "phase": "positive",
        "name": "+orbital",
    }
    scene = {"draw_atoms": [], "bonds": [], "isosurfaces": [mesh]}
    plan = prepare_render(
        scene,
        render={"width": 80, "height": 80, "show_cell": False},
    )
    surface = next(
        item for item in plan.primitives if item.semantic_id.startswith("isosurface:")
    )
    assert surface.metadata["phase"] == "positive"
    assert render(plan, format="png").data.startswith(b"\x89PNG")
    assert b"<image" not in render(plan, format="svg").data.lower()

    script = (
        "import sys; from mat_viewer.render.planning import prepare_render; "
        "s={'draw_atoms':[],'isosurfaces':[{'vertices':[[0,0,0],[1,0,0],[0,1,0]],"
        "'faces':[[0,1,2]]}]}; prepare_render(s,render={'show_cell':False}); "
        "print(int('plotly' in sys.modules),int('skimage' in sys.modules))"
    )
    process = subprocess.run(
        [sys.executable, "-c", script], check=True, capture_output=True, text=True
    )
    assert process.stdout.strip() == "0 0"


def test_loaded_crystal_reads_adapter_precomputed_cube_meshes():
    class Cube:
        surface_meshes = [
            (
                np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float),
                np.asarray([[0, 1, 2]], dtype=int),
            )
        ]

    class Bundle:
        scene = {"draw_atoms": [], "bonds": []}
        cube_data = Cube()

    plan = prepare_render(Bundle(), render={"show_cell": False})
    assert any(item.semantic_id.startswith("isosurface:") for item in plan.primitives)


def test_raw_cube_data_without_adapter_mesh_fails_loudly():
    with pytest.raises(RuntimeError, match="lazily run marching cubes"):
        prepare_render(
            {"draw_atoms": [], "cube_data": object()},
            render={"show_cell": False},
        )


def test_prepare_render_selects_first_structure_input_frame_and_keeps_provenance():
    bundle = SimpleNamespace(
        scene={
            "draw_atoms": [{"elem": "C", "label": "C1", "cart": [0.0, 0.0, 0.0]}],
            "bonds": [],
        },
        cube_data=None,
        cif_path="fallback.cif",
        molcrys_analysis={"formula": "C", "source": "MolCrysKit"},
    )
    frame = SimpleNamespace(
        index=7,
        bundle=bundle,
        info={"step": 40},
        atom_arrays={"forces": np.zeros((1, 3))},
    )
    structure_input = SimpleNamespace(
        path=Path("trajectory.extxyz"),
        input_format="extxyz",
        frames=(frame,),
        total_frames=12,
    )
    plan = prepare_render(structure_input, render={"show_cell": False})
    assert plan.metadata["source"] == "trajectory.extxyz"
    assert plan.metadata["input_format"] == "extxyz"
    assert plan.metadata["frame_index"] == 7
    assert plan.metadata["frame_info"] == {"step": 40}
    assert plan.metadata["molcrys_provenance"]["source"] == "MolCrysKit"
