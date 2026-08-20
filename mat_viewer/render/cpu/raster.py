"""Deterministic CPU rasterizer with Z-buffer and per-pixel A-buffer."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
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


_DEPTH_EPSILON = 1.0e-9


@dataclass(frozen=True, slots=True)
class _Fragment:
    depth: float
    order: int
    source: str
    rgba: tuple[float, float, float, float]


def render_png(
    plan: RenderPlan,
    output: str | Path | None = None,
) -> RenderResult:
    """Render one plan to deterministic PNG bytes or a path."""
    from PIL import Image

    scale = max(1, int(plan.metadata.get("scale", 1)))
    rgba = render_rgba(plan, scale=scale)
    image = Image.fromarray(rgba, mode="RGBA")
    if scale != 1:
        image = image.resize(
            (plan.width, plan.height),
            resample=Image.Resampling.LANCZOS,
        )
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=9)
    data = buffer.getvalue()
    destination = Path(output) if output is not None else None
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    return RenderResult(
        schema=RENDER_RESULT_SCHEMA,
        backend="cpu",
        format="png",
        width=plan.width,
        height=plan.height,
        plan_sha256=plan.fingerprint(),
        output_sha256=sha256(data).hexdigest(),
        output=destination,
        data=None if destination is not None else data,
        warnings=plan.warnings,
        metadata={"transparent_compositor": "per-pixel-fragment-list", "scale": scale},
    )


def render_rgba(plan: RenderPlan, *, scale: int = 1) -> np.ndarray:
    """Render a plan to an RGBA uint8 array, primarily for testing."""
    if int(scale) <= 0:
        raise ValueError("scale must be positive")
    scale = int(scale)
    width, height = int(plan.width) * scale, int(plan.height) * scale
    background = np.asarray(plan.background, dtype=float)
    canvas = np.empty((height, width, 4), dtype=float)
    canvas[...] = background
    for viewport in sorted(plan.viewports, key=lambda item: item.semantic_id):
        x, y, viewport_width, viewport_height = viewport.rect
        left = int(round(x * width))
        top = int(round(y * height))
        right = int(round((x + viewport_width) * width))
        bottom = int(round((y + viewport_height) * height))
        local_width = max(1, right - left)
        local_height = max(1, bottom - top)
        local = _render_viewport(
            viewport,
            local_width,
            local_height,
            background,
            line_scale=scale,
        )
        canvas[top:bottom, left:right] = local[: bottom - top, : right - left]
    canvas = np.clip(np.rint(canvas * 255.0), 0.0, 255.0).astype(np.uint8)
    _draw_text(canvas, plan, scale=scale)
    return canvas


def _render_viewport(
    viewport: ViewportPlan,
    width: int,
    height: int,
    background: np.ndarray,
    *,
    line_scale: int,
) -> np.ndarray:
    transform = CameraTransform(viewport.camera, width, height)
    color = np.empty((height, width, 4), dtype=float)
    color[...] = background
    z_buffer = np.full((height, width), np.inf, dtype=float)
    order_buffer = np.full((height, width), np.iinfo(np.int64).max, dtype=np.int64)
    fragments: dict[int, list[_Fragment]] = {}
    primitive_order = {
        primitive.semantic_id: index
        for index, primitive in enumerate(
            sorted(viewport.primitives, key=lambda item: item.semantic_id)
        )
    }
    for primitive in sorted(viewport.primitives, key=lambda item: item.semantic_id):
        order = primitive_order[primitive.semantic_id]
        if isinstance(primitive, TriangleMeshPrimitive):
            _rasterize_mesh(
                primitive,
                transform,
                color,
                z_buffer,
                order_buffer,
                fragments,
                order,
            )
        elif isinstance(primitive, LinePrimitive):
            _rasterize_lines(
                primitive,
                transform,
                color,
                z_buffer,
                order_buffer,
                fragments,
                order,
                width_scale=line_scale,
            )
    _composite_fragments(color, z_buffer, fragments)
    return color


def _rasterize_mesh(
    primitive: TriangleMeshPrimitive,
    transform: CameraTransform,
    color: np.ndarray,
    z_buffer: np.ndarray,
    order_buffer: np.ndarray,
    fragments: dict[int, list[_Fragment]],
    primitive_order: int,
) -> None:
    camera_vertices = transform.world_to_camera(primitive.vertices)
    rotation = transform.view_matrix[:3, :3]
    light = np.asarray([-0.32, 0.42, 1.0], dtype=float)
    light /= np.linalg.norm(light)
    for triangle_index, indices in enumerate(primitive.triangles):
        camera_triangle = camera_vertices[indices]
        world_triangle = primitive.vertices[indices]
        world_normal = np.cross(
            world_triangle[1] - world_triangle[0],
            world_triangle[2] - world_triangle[0],
        )
        normal_length = float(np.linalg.norm(world_normal))
        if normal_length < 1e-14:
            continue
        camera_normal = rotation @ (world_normal / normal_length)
        if not primitive.double_sided and float(camera_normal[2]) <= 0.0:
            continue
        if primitive.vertex_normals is None:
            illumination = 0.68 + 0.32 * abs(float(camera_normal @ light))
            triangle_rgb = np.tile(
                np.asarray(primitive.rgba[:3]) * illumination,
                (3, 1),
            )
        else:
            camera_normals = primitive.vertex_normals[indices] @ rotation.T
            camera_normals /= np.maximum(
                np.linalg.norm(camera_normals, axis=1, keepdims=True), 1e-12
            )
            illumination = 0.68 + 0.32 * np.abs(camera_normals @ light)
            triangle_rgb = (
                np.asarray(primitive.rgba[:3])[None, :] * illumination[:, None]
            )
        clipped, clipped_rgb = _clip_polygon_attributes(
            camera_triangle,
            triangle_rgb,
            near=transform.spec.near,
            far=transform.spec.far,
        )
        if len(clipped) < 3:
            continue
        rgba = (*primitive.rgba[:3], float(primitive.rgba[3]))
        triangles = triangulate_polygon(clipped)
        color_triangles = [
            np.asarray([clipped_rgb[0], clipped_rgb[index], clipped_rgb[index + 1]])
            for index in range(1, len(clipped_rgb) - 1)
        ]
        for clipped_index, (triangle, vertex_rgb) in enumerate(
            zip(triangles, color_triangles)
        ):
            stable_order = (
                primitive_order * 1_000_000 + triangle_index * 16 + clipped_index
            )
            projected = transform.project_camera(triangle)
            _rasterize_triangle(
                projected.xy,
                projected.depth,
                rgba,
                vertex_rgb,
                primitive.semantic_id,
                stable_order,
                perspective=transform.spec.projection == "perspective",
                color=color,
                z_buffer=z_buffer,
                order_buffer=order_buffer,
                fragments=fragments,
            )


def _rasterize_triangle(
    xy: np.ndarray,
    depths: np.ndarray,
    rgba: tuple[float, float, float, float],
    vertex_rgb: np.ndarray | None,
    source: str,
    order: int,
    *,
    perspective: bool,
    color: np.ndarray,
    z_buffer: np.ndarray,
    order_buffer: np.ndarray,
    fragments: dict[int, list[_Fragment]],
) -> None:
    height, width = z_buffer.shape
    area = _edge(xy[0], xy[1], xy[2])
    if abs(area) < 1e-12:
        return
    minimum_x = max(0, int(np.floor(float(xy[:, 0].min()) - 0.5)))
    maximum_x = min(width - 1, int(np.ceil(float(xy[:, 0].max()) - 0.5)))
    minimum_y = max(0, int(np.floor(float(xy[:, 1].min()) - 0.5)))
    maximum_y = min(height - 1, int(np.ceil(float(xy[:, 1].max()) - 0.5)))
    if minimum_x > maximum_x or minimum_y > maximum_y:
        return
    xs = np.arange(minimum_x, maximum_x + 1, dtype=float) + 0.5
    ys = np.arange(minimum_y, maximum_y + 1, dtype=float) + 0.5
    grid_x, grid_y = np.meshgrid(xs, ys)
    points = np.stack((grid_x, grid_y), axis=-1)
    weight0 = _edge_array(xy[1], xy[2], points) / area
    weight1 = _edge_array(xy[2], xy[0], points) / area
    weight2 = 1.0 - weight0 - weight1
    tolerance = -1e-10
    inside = (weight0 >= tolerance) & (weight1 >= tolerance) & (weight2 >= tolerance)
    if not np.any(inside):
        return
    if perspective:
        reciprocal = weight0 / depths[0] + weight1 / depths[1] + weight2 / depths[2]
        depth_values = 1.0 / np.maximum(reciprocal, 1e-15)
        if vertex_rgb is not None:
            rgb_values = (
                weight0[..., None] * vertex_rgb[0] / depths[0]
                + weight1[..., None] * vertex_rgb[1] / depths[1]
                + weight2[..., None] * vertex_rgb[2] / depths[2]
            ) / np.maximum(reciprocal[..., None], 1e-15)
        else:
            rgb_values = np.empty((*weight0.shape, 3), dtype=float)
            rgb_values[...] = rgba[:3]
    else:
        depth_values = weight0 * depths[0] + weight1 * depths[1] + weight2 * depths[2]
        if vertex_rgb is not None:
            rgb_values = (
                weight0[..., None] * vertex_rgb[0]
                + weight1[..., None] * vertex_rgb[1]
                + weight2[..., None] * vertex_rgb[2]
            )
        else:
            rgb_values = np.empty((*weight0.shape, 3), dtype=float)
            rgb_values[...] = rgba[:3]
    local_y, local_x = np.nonzero(inside)
    pixel_x = local_x + minimum_x
    pixel_y = local_y + minimum_y
    pixel_depths = depth_values[local_y, local_x]
    pixel_rgb = np.clip(rgb_values[local_y, local_x], 0.0, 1.0)
    if rgba[3] >= 1.0 - 1e-12:
        existing = z_buffer[pixel_y, pixel_x]
        existing_order = order_buffer[pixel_y, pixel_x]
        wins = (pixel_depths < existing - _DEPTH_EPSILON) | (
            (np.abs(pixel_depths - existing) <= _DEPTH_EPSILON)
            & (order < existing_order)
        )
        if np.any(wins):
            winner_y, winner_x = pixel_y[wins], pixel_x[wins]
            z_buffer[winner_y, winner_x] = pixel_depths[wins]
            order_buffer[winner_y, winner_x] = order
            color[winner_y, winner_x, :3] = pixel_rgb[wins]
            color[winner_y, winner_x, 3] = rgba[3]
    else:
        for x_value, y_value, depth, rgb in zip(
            pixel_x, pixel_y, pixel_depths, pixel_rgb
        ):
            _append_fragment(
                fragments,
                int(y_value) * width + int(x_value),
                _Fragment(
                    float(depth),
                    int(order),
                    source,
                    (float(rgb[0]), float(rgb[1]), float(rgb[2]), rgba[3]),
                ),
            )


def _rasterize_lines(
    primitive: LinePrimitive,
    transform: CameraTransform,
    color: np.ndarray,
    z_buffer: np.ndarray,
    order_buffer: np.ndarray,
    fragments: dict[int, list[_Fragment]],
    primitive_order: int,
    *,
    width_scale: int,
) -> None:
    height, width = z_buffer.shape
    half_width = max(0.5, primitive.width_px * width_scale * 0.5)
    for segment_index, segment in enumerate(primitive.segments):
        clipped = transform.clip_segment_world(segment[0], segment[1])
        if clipped is None:
            continue
        camera_segment = np.asarray(clipped)
        projected = transform.project_camera(camera_segment)
        first, second = projected.xy
        vector = second - first
        length_squared = float(vector @ vector)
        screen_length = float(np.sqrt(length_squared))
        if length_squared < 1e-16:
            continue
        minimum_x = max(0, int(np.floor(min(first[0], second[0]) - half_width)))
        maximum_x = min(width - 1, int(np.ceil(max(first[0], second[0]) + half_width)))
        minimum_y = max(0, int(np.floor(min(first[1], second[1]) - half_width)))
        maximum_y = min(height - 1, int(np.ceil(max(first[1], second[1]) + half_width)))
        stable_order = primitive_order * 1_000_000 + segment_index
        for y_value in range(minimum_y, maximum_y + 1):
            for x_value in range(minimum_x, maximum_x + 1):
                point = np.asarray([x_value + 0.5, y_value + 0.5])
                parameter = float(
                    np.clip((point - first) @ vector / length_squared, 0.0, 1.0)
                )
                if primitive.dash and not _dash_visible(
                    parameter * screen_length, primitive.dash
                ):
                    continue
                closest = first + parameter * vector
                if float(np.linalg.norm(point - closest)) > half_width:
                    continue
                if transform.spec.projection == "perspective":
                    depth = 1.0 / (
                        (1.0 - parameter) / projected.depth[0]
                        + parameter / projected.depth[1]
                    )
                else:
                    depth = float(
                        (1.0 - parameter) * projected.depth[0]
                        + parameter * projected.depth[1]
                    )
                if not primitive.depth_test:
                    depth = -np.inf
                if primitive.rgba[3] >= 1.0 - 1e-12:
                    existing = z_buffer[y_value, x_value]
                    if depth < existing - _DEPTH_EPSILON or (
                        abs(depth - existing) <= _DEPTH_EPSILON
                        and stable_order < order_buffer[y_value, x_value]
                    ):
                        z_buffer[y_value, x_value] = depth
                        order_buffer[y_value, x_value] = stable_order
                        color[y_value, x_value] = primitive.rgba
                else:
                    _append_fragment(
                        fragments,
                        y_value * width + x_value,
                        _Fragment(
                            depth, stable_order, primitive.semantic_id, primitive.rgba
                        ),
                    )


def _append_fragment(
    fragments: dict[int, list[_Fragment]],
    pixel: int,
    fragment: _Fragment,
) -> None:
    bucket = fragments.setdefault(pixel, [])
    # Adjacent triangles of one continuous surface may both cover a shared
    # sample.  Retain one surface hit while preserving distinct front/back
    # intersections of a transparent closed mesh.
    for existing in bucket:
        if (
            existing.source == fragment.source
            and abs(existing.depth - fragment.depth) <= _DEPTH_EPSILON
        ):
            return
    bucket.append(fragment)


def _composite_fragments(
    color: np.ndarray,
    z_buffer: np.ndarray,
    fragments: dict[int, list[_Fragment]],
) -> None:
    height, width = z_buffer.shape
    for pixel in sorted(fragments):
        y_value, x_value = divmod(pixel, width)
        opaque_depth = z_buffer[y_value, x_value]
        visible = [
            fragment
            for fragment in fragments[pixel]
            if fragment.depth < opaque_depth - _DEPTH_EPSILON
            or not np.isfinite(opaque_depth)
        ]
        visible.sort(key=lambda item: (-item.depth, item.order, item.source))
        destination = color[y_value, x_value].copy()
        for fragment in visible:
            source = np.asarray(fragment.rgba, dtype=float)
            source_alpha = source[3]
            output_alpha = source_alpha + destination[3] * (1.0 - source_alpha)
            if output_alpha <= 1e-15:
                destination[:] = 0.0
            else:
                destination[:3] = (
                    source[:3] * source_alpha
                    + destination[:3] * destination[3] * (1.0 - source_alpha)
                ) / output_alpha
                destination[3] = output_alpha
        color[y_value, x_value] = destination


def _draw_text(canvas: np.ndarray, plan: RenderPlan, *, scale: int) -> None:
    texts: list[tuple[ViewportPlan, TextPrimitive]] = []
    for viewport in plan.viewports:
        texts.extend(
            (viewport, primitive)
            for primitive in viewport.primitives
            if isinstance(primitive, TextPrimitive)
        )
    if not texts:
        return
    from PIL import Image, ImageDraw, ImageFont

    image = Image.fromarray(canvas, mode="RGBA")
    draw = ImageDraw.Draw(image)
    full_width, full_height = image.size
    for viewport, primitive in sorted(texts, key=lambda item: item[1].semantic_id):
        x, y, viewport_width, viewport_height = viewport.rect
        local_width = max(1, int(round(viewport_width * full_width)))
        local_height = max(1, int(round(viewport_height * full_height)))
        transform = CameraTransform(viewport.camera, local_width, local_height)
        projected = transform.project_world(np.asarray([primitive.position]))
        if not np.isfinite(projected.xy).all():
            continue
        screen = projected.xy[0] + np.asarray(
            [x * full_width, y * full_height], dtype=float
        )
        screen += np.asarray(primitive.offset_px) * scale
        size_px = max(1, int(round(primitive.size_pt * scale * 96.0 / 72.0)))
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", size_px)
        except OSError:  # pragma: no cover - platform font fallback
            font = ImageFont.load_default()
        rgba = tuple(int(round(channel * 255.0)) for channel in primitive.rgba)
        draw.text(tuple(screen), primitive.text, fill=rgba, font=font, anchor="ls")
    canvas[:] = np.asarray(image)


def _clip_polygon_attributes(
    points: np.ndarray,
    attributes: np.ndarray,
    *,
    near: float,
    far: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Clip camera points and linearly coupled vertex attributes together."""
    clipped_points = np.asarray(points, dtype=float)
    clipped_attributes = np.asarray(attributes, dtype=float)
    for boundary, keep_nearer in ((float(near), False), (float(far), True)):
        if len(clipped_points) == 0:
            break

        def inside(point: np.ndarray) -> bool:
            depth = -float(point[2])
            return (
                depth <= boundary + 1e-12 if keep_nearer else depth >= boundary - 1e-12
            )

        next_points: list[np.ndarray] = []
        next_attributes: list[np.ndarray] = []
        previous_point = clipped_points[-1]
        previous_attribute = clipped_attributes[-1]
        previous_inside = inside(previous_point)
        for current_point, current_attribute in zip(clipped_points, clipped_attributes):
            current_inside = inside(current_point)
            if current_inside != previous_inside:
                previous_depth = -float(previous_point[2])
                current_depth = -float(current_point[2])
                denominator = current_depth - previous_depth
                fraction = (
                    0.0
                    if abs(denominator) < 1e-15
                    else (boundary - previous_depth) / denominator
                )
                fraction = float(np.clip(fraction, 0.0, 1.0))
                next_points.append(
                    previous_point + fraction * (current_point - previous_point)
                )
                next_attributes.append(
                    previous_attribute
                    + fraction * (current_attribute - previous_attribute)
                )
            if current_inside:
                next_points.append(current_point)
                next_attributes.append(current_attribute)
            previous_point = current_point
            previous_attribute = current_attribute
            previous_inside = current_inside
        clipped_points = np.asarray(next_points, dtype=float).reshape(-1, 3)
        clipped_attributes = np.asarray(next_attributes, dtype=float).reshape(
            -1, attributes.shape[1]
        )
    return clipped_points, clipped_attributes


def _edge(first: np.ndarray, second: np.ndarray, point: np.ndarray) -> float:
    return float(
        (point[0] - first[0]) * (second[1] - first[1])
        - (point[1] - first[1]) * (second[0] - first[0])
    )


def _dash_visible(distance: float, pattern: tuple[float, ...]) -> bool:
    values = pattern if len(pattern) % 2 == 0 else pattern * 2
    period = float(sum(values))
    if period <= 0.0:
        return True
    position = float(distance) % period
    for index, length in enumerate(values):
        if position <= length:
            return index % 2 == 0
        position -= length
    return True


def _edge_array(
    first: np.ndarray, second: np.ndarray, points: np.ndarray
) -> np.ndarray:
    return (points[..., 0] - first[0]) * (second[1] - first[1]) - (
        points[..., 1] - first[1]
    ) * (second[0] - first[0])


__all__ = ["render_png", "render_rgba"]
