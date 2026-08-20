"""True-vector PDF/SVG painter with BSP and projected visibility splitting."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Iterable

import numpy as np

from ..camera import CameraTransform, triangulate_polygon
from ..contracts import (
    LinePrimitive,
    RENDER_RESULT_SCHEMA,
    RenderPlan,
    RenderResult,
    TextPrimitive,
    TriangleMeshPrimitive,
    ViewportPlan,
)
from .bsp import BSPPolygon, build_bsp, traverse_back_to_front


_EPSILON = 1.0e-8


@dataclass(frozen=True, slots=True)
class ProjectedPolygon:
    polygon: BSPPolygon
    xy: np.ndarray
    depths: np.ndarray
    painter_index: int


@dataclass(frozen=True, slots=True)
class VectorLinePiece:
    semantic_id: str
    start: tuple[float, float]
    end: tuple[float, float]
    rgba: tuple[float, float, float, float]
    width_px: float
    dash: tuple[float, ...]
    insertion_index: int
    depth: float
    stable_order: int


def render_vector(
    plan: RenderPlan,
    output: str | Path | None = None,
    *,
    format: str | None = None,
    dpi: int = 96,
) -> RenderResult:
    """Render a plan as genuine SVG/PDF paths, polygons, strokes, and text."""
    destination = Path(output) if output is not None else None
    output_format = (
        format or (destination.suffix.lstrip(".") if destination else "svg")
    ).lower()
    if output_format not in {"svg", "pdf"}:
        raise ValueError("vector format must be svg or pdf")
    if int(dpi) <= 0:
        raise ValueError("dpi must be positive")

    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon

    background = plan.background
    figure_color = background[:3] if background[3] > 0.0 else "none"
    metadata = (
        {"Date": None, "Creator": "MatterVis CPU vector renderer"}
        if output_format == "svg"
        else {
            "CreationDate": None,
            "ModDate": None,
            "Creator": "MatterVis CPU vector renderer",
            "Producer": "MatterVis CPU vector renderer",
        }
    )
    buffer = BytesIO()
    with matplotlib.rc_context(
        {
            "svg.hashsalt": plan.fingerprint(),
            "pdf.compression": 9,
            "path.simplify": False,
        }
    ):
        figure = plt.figure(
            figsize=(plan.width / dpi, plan.height / dpi),
            dpi=dpi,
            facecolor=figure_color,
        )
        for viewport in sorted(plan.viewports, key=lambda item: item.semantic_id):
            x, y, width_fraction, height_fraction = viewport.rect
            axes = figure.add_axes(
                [x, 1.0 - y - height_fraction, width_fraction, height_fraction],
                frameon=False,
            )
            local_width = max(1, int(round(plan.width * width_fraction)))
            local_height = max(1, int(round(plan.height * height_fraction)))
            axes.set_xlim(0.0, float(local_width))
            axes.set_ylim(float(local_height), 0.0)
            axes.set_aspect("equal", adjustable="box")
            axes.axis("off")
            polygons, pieces = vector_scene(viewport, local_width, local_height)
            buckets: dict[int, list[VectorLinePiece]] = {}
            for piece in pieces:
                buckets.setdefault(piece.insertion_index, []).append(piece)

            def draw_lines(index: int) -> None:
                for piece in sorted(
                    buckets.get(index, []),
                    key=lambda item: (-item.depth, item.stable_order, item.semantic_id),
                ):
                    (line,) = axes.plot(
                        [piece.start[0], piece.end[0]],
                        [piece.start[1], piece.end[1]],
                        color=piece.rgba,
                        linewidth=piece.width_px * 72.0 / dpi,
                        solid_capstyle="round",
                        antialiased=True,
                        rasterized=False,
                    )
                    if piece.dash:
                        line.set_dashes(piece.dash)

            for painter_index, projected in enumerate(polygons):
                draw_lines(painter_index)
                patch = Polygon(
                    projected.xy,
                    closed=True,
                    facecolor=projected.polygon.rgba,
                    edgecolor="none",
                    linewidth=0.0,
                    antialiased=True,
                    rasterized=False,
                )
                axes.add_patch(patch)
            draw_lines(len(polygons))
            _draw_vector_text(axes, viewport, local_width, local_height, dpi=dpi)
        figure.savefig(
            buffer,
            format=output_format,
            dpi=dpi,
            facecolor=figure_color,
            transparent=background[3] < 1.0,
            bbox_inches=None,
            pad_inches=0.0,
            metadata=metadata,
        )
        plt.close(figure)

    data = buffer.getvalue()
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    return RenderResult(
        schema=RENDER_RESULT_SCHEMA,
        backend="cpu",
        format=output_format,
        width=plan.width,
        height=plan.height,
        plan_sha256=plan.fingerprint(),
        output_sha256=sha256(data).hexdigest(),
        output=destination,
        data=None if destination is not None else data,
        warnings=plan.warnings,
        metadata={
            "vector": True,
            "visibility": "polygon-bsp+line-boundary-splitting",
        },
    )


def vector_scene(
    viewport: ViewportPlan,
    width: int,
    height: int,
) -> tuple[list[ProjectedPolygon], list[VectorLinePiece]]:
    """Return BSP-ordered polygons and visibility-split line pieces."""
    transform = CameraTransform(viewport.camera, width, height)
    bsp_polygons = _mesh_polygons(viewport, transform)
    tree = build_bsp(bsp_polygons, epsilon=_scene_epsilon(bsp_polygons))
    ordered = traverse_back_to_front(tree)
    projected = _project_polygons(ordered, transform)
    lines = _line_pieces(viewport, transform, projected)
    return projected, lines


def visible_line_segments(
    viewport: ViewportPlan,
    width: int,
    height: int,
) -> list[VectorLinePiece]:
    """Expose exact line visibility splitting for focused regression tests."""
    return vector_scene(viewport, width, height)[1]


def _mesh_polygons(
    viewport: ViewportPlan,
    transform: CameraTransform,
) -> list[BSPPolygon]:
    result: list[BSPPolygon] = []
    rotation = transform.view_matrix[:3, :3]
    light = np.asarray([-0.32, 0.42, 1.0])
    light /= np.linalg.norm(light)
    meshes = sorted(
        (
            item
            for item in viewport.primitives
            if isinstance(item, TriangleMeshPrimitive)
        ),
        key=lambda item: item.semantic_id,
    )
    for primitive_order, primitive in enumerate(meshes):
        camera_vertices = transform.world_to_camera(primitive.vertices)
        for triangle_index, indices in enumerate(primitive.triangles):
            world_triangle = primitive.vertices[indices]
            normal = np.cross(
                world_triangle[1] - world_triangle[0],
                world_triangle[2] - world_triangle[0],
            )
            length = float(np.linalg.norm(normal))
            if length < 1e-14:
                continue
            camera_normal = rotation @ (normal / length)
            if not primitive.double_sided and float(camera_normal[2]) <= 0.0:
                continue
            shading_normal = camera_normal
            if primitive.vertex_normals is not None:
                averaged = np.mean(primitive.vertex_normals[indices], axis=0)
                averaged_length = float(np.linalg.norm(averaged))
                if averaged_length > 1e-14:
                    shading_normal = rotation @ (averaged / averaged_length)
            illumination = 0.68 + 0.32 * abs(float(shading_normal @ light))
            rgba = (
                float(primitive.rgba[0]) * illumination,
                float(primitive.rgba[1]) * illumination,
                float(primitive.rgba[2]) * illumination,
                float(primitive.rgba[3]),
            )
            clipped = transform.clip_polygon_camera(camera_vertices[indices])
            for fragment_index, triangle in enumerate(triangulate_polygon(clipped)):
                result.append(
                    BSPPolygon(
                        vertices=triangle,
                        rgba=rgba,
                        semantic_id=primitive.semantic_id,
                        source_order=primitive_order * 1_000_000 + triangle_index,
                        fragment_order=fragment_index,
                    )
                )
    return result


def _project_polygons(
    polygons: Iterable[BSPPolygon],
    transform: CameraTransform,
) -> list[ProjectedPolygon]:
    result = []
    for painter_index, polygon in enumerate(polygons):
        projection = transform.project_camera(polygon.vertices)
        if not np.all(np.isfinite(projection.xy)):
            continue
        result.append(
            ProjectedPolygon(
                polygon=polygon,
                xy=projection.xy,
                depths=projection.depth,
                painter_index=painter_index,
            )
        )
    # BSP indices may have gaps when a numerically unprojectable polygon was
    # discarded.  Reindex so line insertion buckets remain contiguous.
    return [
        ProjectedPolygon(item.polygon, item.xy, item.depths, index)
        for index, item in enumerate(result)
    ]


def _line_pieces(
    viewport: ViewportPlan,
    transform: CameraTransform,
    polygons: list[ProjectedPolygon],
) -> list[VectorLinePiece]:
    raw: list[dict] = []
    line_primitives = sorted(
        (item for item in viewport.primitives if isinstance(item, LinePrimitive)),
        key=lambda item: item.semantic_id,
    )
    for primitive_order, primitive in enumerate(line_primitives):
        for segment_index, segment in enumerate(primitive.segments):
            clipped = transform.clip_segment_world(segment[0], segment[1])
            if clipped is None:
                continue
            camera = np.asarray(clipped)
            projection = transform.project_camera(camera)
            if not np.all(np.isfinite(projection.xy)):
                continue
            raw.append(
                {
                    "primitive": primitive,
                    "xy": projection.xy,
                    "depths": projection.depth,
                    "breaks": {0.0, 1.0},
                    "order": primitive_order * 1_000_000 + segment_index,
                }
            )

    # Split line-line crossings first, so each surviving span has a stable
    # order relative to every other stroke where they overlap.
    for first_index, first in enumerate(raw):
        for second in raw[first_index + 1 :]:
            parameters = _segment_intersection_parameters(
                first["xy"][0], first["xy"][1], second["xy"][0], second["xy"][1]
            )
            if parameters is not None:
                first_parameter, second_parameter = parameters
                first["breaks"].add(first_parameter)
                second["breaks"].add(second_parameter)

    for segment in raw:
        for polygon in polygons:
            _add_polygon_breakpoints(segment, polygon, transform)

    result: list[VectorLinePiece] = []
    for segment in raw:
        primitive: LinePrimitive = segment["primitive"]
        breakpoints = sorted(_unique_parameters(segment["breaks"]))
        for interval_index, (start_t, end_t) in enumerate(
            zip(breakpoints, breakpoints[1:])
        ):
            if end_t - start_t <= 1e-10:
                continue
            midpoint_t = 0.5 * (start_t + end_t)
            midpoint = _interpolate_xy(segment["xy"], midpoint_t)
            line_depth = _interpolate_depth(
                segment["depths"],
                midpoint_t,
                perspective=transform.spec.projection == "perspective",
            )
            covering: list[tuple[ProjectedPolygon, float]] = []
            for polygon in polygons:
                polygon_depth = _polygon_depth_at(
                    polygon,
                    midpoint,
                    perspective=transform.spec.projection == "perspective",
                )
                if polygon_depth is not None:
                    covering.append((polygon, polygon_depth))
            if primitive.depth_test and any(
                polygon.polygon.rgba[3] >= 1.0 - 1e-12
                and polygon_depth < line_depth - _EPSILON
                for polygon, polygon_depth in covering
            ):
                continue
            closer_transparent = [
                polygon.painter_index
                for polygon, polygon_depth in covering
                if polygon.polygon.rgba[3] < 1.0 - 1e-12
                and polygon_depth < line_depth - _EPSILON
            ]
            insertion = min(closer_transparent) if closer_transparent else len(polygons)
            start = _interpolate_xy(segment["xy"], start_t)
            end = _interpolate_xy(segment["xy"], end_t)
            result.append(
                VectorLinePiece(
                    semantic_id=primitive.semantic_id,
                    start=(float(start[0]), float(start[1])),
                    end=(float(end[0]), float(end[1])),
                    rgba=primitive.rgba,
                    width_px=primitive.width_px,
                    dash=primitive.dash,
                    insertion_index=insertion,
                    depth=float(line_depth),
                    stable_order=int(segment["order"] * 10_000 + interval_index),
                )
            )
    return result


def _add_polygon_breakpoints(
    segment: dict,
    polygon: ProjectedPolygon,
    transform: CameraTransform,
) -> None:
    first, second = segment["xy"]
    for edge_index, edge_start in enumerate(polygon.xy):
        edge_end = polygon.xy[(edge_index + 1) % len(polygon.xy)]
        parameters = _segment_intersection_parameters(
            first, second, edge_start, edge_end
        )
        if parameters is not None:
            segment["breaks"].add(parameters[0])
    ordered = sorted(_unique_parameters(segment["breaks"]))
    for start_t, end_t in zip(ordered, ordered[1:]):
        if end_t - start_t <= 1e-9:
            continue
        middle = 0.5 * (start_t + end_t)
        middle_point = _interpolate_xy(segment["xy"], middle)
        if (
            _polygon_depth_at(
                polygon,
                middle_point,
                perspective=transform.spec.projection == "perspective",
            )
            is None
        ):
            continue
        lower = start_t + (end_t - start_t) * 1e-6
        upper = end_t - (end_t - start_t) * 1e-6
        lower_difference = _depth_difference(segment, polygon, lower, transform)
        upper_difference = _depth_difference(segment, polygon, upper, transform)
        if lower_difference is None or upper_difference is None:
            continue
        if lower_difference * upper_difference < 0.0:
            for _ in range(48):
                candidate = 0.5 * (lower + upper)
                difference = _depth_difference(segment, polygon, candidate, transform)
                if difference is None:
                    break
                if lower_difference * difference <= 0.0:
                    upper = candidate
                else:
                    lower = candidate
                    lower_difference = difference
            segment["breaks"].add(0.5 * (lower + upper))


def _depth_difference(
    segment: dict,
    polygon: ProjectedPolygon,
    parameter: float,
    transform: CameraTransform,
) -> float | None:
    point = _interpolate_xy(segment["xy"], parameter)
    polygon_depth = _polygon_depth_at(
        polygon,
        point,
        perspective=transform.spec.projection == "perspective",
    )
    if polygon_depth is None:
        return None
    line_depth = _interpolate_depth(
        segment["depths"],
        parameter,
        perspective=transform.spec.projection == "perspective",
    )
    return float(line_depth - polygon_depth)


def _polygon_depth_at(
    polygon: ProjectedPolygon,
    point: np.ndarray,
    *,
    perspective: bool,
) -> float | None:
    xy, depths = polygon.xy, polygon.depths
    for index in range(1, len(xy) - 1):
        triangle = np.asarray([xy[0], xy[index], xy[index + 1]])
        weights = _barycentric(point, triangle)
        if weights is None or np.any(weights < -1e-9):
            continue
        triangle_depths = np.asarray([depths[0], depths[index], depths[index + 1]])
        if perspective:
            reciprocal = float(np.sum(weights / triangle_depths))
            return 1.0 / max(reciprocal, 1e-15)
        return float(weights @ triangle_depths)
    return None


def _draw_vector_text(
    axes, viewport: ViewportPlan, width: int, height: int, *, dpi: int
) -> None:
    transform = CameraTransform(viewport.camera, width, height)
    texts = sorted(
        (item for item in viewport.primitives if isinstance(item, TextPrimitive)),
        key=lambda item: item.semantic_id,
    )
    for primitive in texts:
        projection = transform.project_world(np.asarray([primitive.position]))
        if not np.all(np.isfinite(projection.xy)):
            continue
        position = projection.xy[0] + np.asarray(primitive.offset_px)
        axes.text(
            float(position[0]),
            float(position[1]),
            primitive.text,
            color=primitive.rgba,
            fontsize=primitive.size_pt,
            ha="left",
            va="baseline",
            clip_on=True,
            rasterized=False,
        )


def _segment_intersection_parameters(
    first_start: np.ndarray,
    first_end: np.ndarray,
    second_start: np.ndarray,
    second_end: np.ndarray,
) -> tuple[float, float] | None:
    first_vector = first_end - first_start
    second_vector = second_end - second_start
    denominator = _cross2(first_vector, second_vector)
    if abs(denominator) < 1e-12:
        return None
    difference = second_start - first_start
    first_parameter = _cross2(difference, second_vector) / denominator
    second_parameter = _cross2(difference, first_vector) / denominator
    if 1e-10 < first_parameter < 1.0 - 1e-10 and 1e-10 < second_parameter < 1.0 - 1e-10:
        return float(first_parameter), float(second_parameter)
    return None


def _barycentric(point: np.ndarray, triangle: np.ndarray) -> np.ndarray | None:
    first, second, third = triangle
    denominator = _cross2(second - first, third - first)
    if abs(denominator) < 1e-14:
        return None
    weight1 = _cross2(point - first, third - first) / denominator
    weight2 = _cross2(second - first, point - first) / denominator
    weight0 = 1.0 - weight1 - weight2
    return np.asarray([weight0, weight1, weight2])


def _cross2(first: np.ndarray, second: np.ndarray) -> float:
    return float(first[0] * second[1] - first[1] * second[0])


def _interpolate_xy(segment: np.ndarray, parameter: float) -> np.ndarray:
    return segment[0] + float(parameter) * (segment[1] - segment[0])


def _interpolate_depth(
    depths: np.ndarray, parameter: float, *, perspective: bool
) -> float:
    parameter = float(parameter)
    if perspective:
        return 1.0 / (
            (1.0 - parameter) / float(depths[0]) + parameter / float(depths[1])
        )
    return float((1.0 - parameter) * depths[0] + parameter * depths[1])


def _unique_parameters(values: Iterable[float]) -> list[float]:
    result: list[float] = []
    for value in sorted(float(np.clip(item, 0.0, 1.0)) for item in values):
        if not result or abs(value - result[-1]) > 1e-9:
            result.append(value)
    return result


def _scene_epsilon(polygons: list[BSPPolygon]) -> float:
    if not polygons:
        return _EPSILON
    scale = max(
        1.0,
        max(
            float(np.linalg.norm(vertex))
            for polygon in polygons
            for vertex in polygon.vertices
        ),
    )
    return max(_EPSILON, np.finfo(float).eps * scale * 128.0)


__all__ = [
    "ProjectedPolygon",
    "VectorLinePiece",
    "render_vector",
    "vector_scene",
    "visible_line_segments",
]
