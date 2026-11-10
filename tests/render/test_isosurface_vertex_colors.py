"""Backend contract for isosurface vertex and endpoint colors."""

from __future__ import annotations

import numpy as np
import pytest

from mat_viewer.render.camera import CameraTransform
from mat_viewer.render.contracts import (
    CameraSpec,
    LinePrimitive,
    RenderPlan,
    TriangleMeshPrimitive,
    ViewportPlan,
)
from mat_viewer.render.cpu import render_rgba
from mat_viewer.render.cpu.vector import _mesh_polygons


def _camera() -> CameraSpec:
    return CameraSpec(
        position=(0.0, 0.0, 5.0),
        target=(0.0, 0.0, 0.0),
        up=(0.0, 1.0, 0.0),
        projection="orthographic",
        fov_y_deg=50.0,
        near=0.5,
        far=20.0,
        ortho_scale=1.2,
    )


def _plan(*primitives) -> RenderPlan:
    return RenderPlan(
        width=96,
        height=96,
        background=(1.0, 1.0, 1.0, 1.0),
        viewports=(
            ViewportPlan(
                semantic_id="main",
                camera=_camera(),
                primitives=tuple(primitives),
            ),
        ),
    )


def _colored_triangle(*, normals: bool) -> TriangleMeshPrimitive:
    vertices = np.asarray([[-0.8, -0.6, 0.0], [0.8, -0.6, 0.0], [0.0, 0.75, 0.0]])
    return TriangleMeshPrimitive(
        semantic_id="colored-triangle",
        vertices=vertices,
        triangles=np.asarray([[0, 1, 2]]),
        rgba=(0.5, 0.5, 0.5, 1.0),
        vertex_normals=vertices / np.linalg.norm(vertices, axis=1, keepdims=True)
        if normals
        else None,
        vertex_colors=np.asarray(
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        ),
        metadata={"material": {"ambient": 1.0, "diffuse": 0.0}},
    )


def test_flat_shading_keeps_vertex_colors_in_the_cpu_image() -> None:
    image = render_rgba(_plan(_colored_triangle(normals=False)))
    assert int(image[:, :, 0].max()) > 200
    assert int(image[:, :, 1].max()) > 200
    assert int(image[:, :, 2].max()) > 200


def test_perspective_line_uses_camera_depth_not_the_screen_midpoint() -> None:
    from mat_viewer.render.cpu.raster_lines import endpoint_screen_weight, rasterize_lines

    camera = CameraSpec(
        position=(0.0, 0.0, 10.0),
        target=(0.0, 0.0, 0.0),
        up=(0.0, 1.0, 0.0),
        projection="perspective",
        fov_y_deg=90.0,
        near=0.05,
        far=100.0,
    )
    transform = CameraTransform(camera, 241, 241)
    near = transform.camera_to_world(np.asarray([[-0.35, 0.0, -1.0]]))[0]
    far = transform.camera_to_world(np.asarray([[0.35, 0.0, -9.0]]))[0]
    projected = transform.project_world(np.vstack((near, far)))
    assert projected.depth[0] == pytest.approx(1.0)
    assert projected.depth[1] == pytest.approx(9.0)
    primitive = LinePrimitive(
        semantic_id="depth-edge",
        segments=np.asarray([[near, far]], dtype=float),
        rgba=(0.2, 0.2, 0.2, 1.0),
        width_px=14.0,
        endpoint_colors=np.asarray([[[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]]),
    )
    color = np.zeros((241, 241, 4), dtype=float)
    z_buffer = np.full((241, 241), np.inf)
    order_buffer = np.full((241, 241), np.iinfo(np.int64).max)
    rasterize_lines(
        primitive,
        transform,
        color,
        z_buffer,
        order_buffer,
        {},
        0,
        width_scale=1,
    )
    midpoint = 0.5 * (projected.xy[0] + projected.xy[1])
    written = np.argwhere(color[:, :, 3] > 0.0)
    assert len(written)
    centers = written[:, ::-1] + 0.5
    chosen = written[int(np.argmin(np.linalg.norm(centers - midpoint, axis=1)))]
    point = np.asarray([chosen[1] + 0.5, chosen[0] + 0.5])
    direction = projected.xy[1] - projected.xy[0]
    screen_t = float(
        np.clip((point - projected.xy[0]) @ direction / (direction @ direction), 0.0, 1.0)
    )
    far_weight = endpoint_screen_weight(
        screen_t, float(projected.depth[0]), float(projected.depth[1]), perspective=True
    )
    assert far_weight == pytest.approx(0.1, abs=0.04)
    assert color[chosen[0], chosen[1], 0] == pytest.approx(1.0 - far_weight, abs=0.04)
    assert color[chosen[0], chosen[1], 2] == pytest.approx(far_weight, abs=0.04)
    linear = 0.5
    assert abs(color[chosen[0], chosen[1], 0] - (1.0 - far_weight)) < abs(
        color[chosen[0], chosen[1], 0] - (1.0 - linear)
    )


def test_vector_edge_gradient_keeps_both_endpoint_colors() -> None:
    from mat_viewer.render.cpu.vector import render_vector

    result = render_vector(
        _plan(
            LinePrimitive(
                semantic_id="edge",
                segments=np.asarray([[[-0.9, 0.0, 0.0], [0.9, 0.0, 0.0]]]),
                rgba=(0.2, 0.2, 0.2, 1.0),
                width_px=4.0,
                endpoint_colors=np.asarray([[[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]]),
            )
        ),
        format="svg",
    )
    strokes = _svg_stroke_rgbs(result.data.decode())
    assert len(strokes) > 2
    assert strokes[0][0] > 0.85
    assert strokes[0][2] < 0.15
    assert strokes[-1][2] > 0.85
    assert strokes[-1][0] < 0.15


def _svg_stroke_rgbs(svg: str) -> list[tuple[float, float, float]]:
    import re

    colors: list[tuple[float, float, float]] = []
    for match in re.finditer(r"stroke:\s*([^;\"']+)", svg):
        colors.append(_css_color(match.group(1).strip()))
    return colors


def _css_color(text: str) -> tuple[float, float, float]:
    if text.startswith("#") and len(text) == 7:
        return tuple(int(text[index : index + 2], 16) / 255.0 for index in (1, 3, 5))
    channels = text.removeprefix("rgb(").removesuffix(")").split(",")
    values = []
    for channel in channels[:3]:
        number = channel.strip()
        if number.endswith("%"):
            values.append(float(number[:-1]) / 100.0)
        else:
            values.append(float(number) / 255.0)
    return (values[0], values[1], values[2])


def test_endpoint_colors_reach_the_cpu_line_image() -> None:
    image = render_rgba(
        _plan(
            LinePrimitive(
                semantic_id="edge",
                segments=np.asarray([[[-0.9, 0.0, 0.0], [0.9, 0.0, 0.0]]]),
                rgba=(0.2, 0.2, 0.2, 1.0),
                width_px=8.0,
                endpoint_colors=np.asarray(
                    [[[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]]
                ),
            )
        )
    )
    assert int(image[:, :, 0].max()) > 200
    assert int(image[:, :, 2].max()) > 200


def test_vector_surface_uses_the_mean_vertex_color() -> None:
    primitive = _colored_triangle(normals=False)
    viewport = _plan(primitive).viewports[0]
    polygons = _mesh_polygons(viewport, CameraTransform(_camera(), 32, 32))
    assert polygons
    assert polygons[0].rgba[0] == pytest.approx(1.0 / 3.0)
    assert polygons[0].rgba[1] == pytest.approx(1.0 / 3.0)
    assert polygons[0].rgba[2] == pytest.approx(1.0 / 3.0)


def test_vertex_colors_change_the_plan_fingerprint() -> None:
    colored = _colored_triangle(normals=False)
    plain = TriangleMeshPrimitive(
        semantic_id=colored.semantic_id,
        vertices=colored.vertices,
        triangles=colored.triangles,
        rgba=colored.rgba,
        metadata=dict(colored.metadata),
    )
    assert _plan(colored).fingerprint() != _plan(plain).fingerprint()


def test_plotly_mesh_receives_vertex_colors() -> None:
    pytest.importorskip("plotly")
    from mat_viewer.render.plotly import _primitive_trace

    trace = _primitive_trace(_colored_triangle(normals=False), scene="scene")
    assert len(trace.vertexcolor) == 3
    assert "255" in trace.vertexcolor[0]


def test_plotly_rejects_wireframe_endpoint_colors() -> None:
    pytest.importorskip("plotly")
    from mat_viewer.render.plotly import _primitive_trace

    primitive = LinePrimitive(
        semantic_id="wire",
        segments=np.asarray([[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]]),
        rgba=(0.2, 0.2, 0.2, 1.0),
        endpoint_colors=np.asarray([[[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]]),
    )
    with pytest.raises(ValueError, match="wireframe"):
        _primitive_trace(primitive, scene="scene")


def test_matplotlib_rejects_vertex_colors() -> None:
    pytest.importorskip("matplotlib")
    from mat_viewer.render.matplotlib import build_figure

    with pytest.raises(ValueError, match="per-vertex"):
        build_figure(_plan(_colored_triangle(normals=False)))
