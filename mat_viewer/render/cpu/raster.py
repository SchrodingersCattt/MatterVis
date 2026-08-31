"""Deterministic CPU rasterizer with Z-buffer and per-pixel A-buffer."""

from __future__ import annotations
import colorsys

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


def _polyhedron_face_rgb(rgb: np.ndarray, lambert: float) -> np.ndarray:
    """Shade a polyhedron face in HLS space while preserving its base hue."""
    red, green, blue = np.clip(np.asarray(rgb, dtype=float), 0.0, 1.0)
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    strength = float(np.clip(lambert, 0.0, 1.0))
    dark = max(0.14, lightness * 0.50)
    bright = min(0.92, lightness + (1.0 - lightness) * 0.42)
    face_saturation = max(0.46, saturation * (0.85 + 0.15 * strength))
    shaded = colorsys.hls_to_rgb(
        hue,
        dark + (bright - dark) * strength,
        min(1.0, face_saturation),
    )
    return np.asarray(shaded, dtype=float)


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
    depth_buffers: dict[str, np.ndarray] = {}
    for viewport in sorted(plan.viewports, key=lambda item: item.semantic_id):
        left, top, right, bottom = _viewport_bounds(viewport, width, height)
        local_width = max(1, right - left)
        local_height = max(1, bottom - top)
        local, z_buffer = _render_viewport(
            viewport,
            local_width,
            local_height,
            background,
            line_scale=scale,
        )
        depth_buffers[viewport.semantic_id] = z_buffer
        canvas[top:bottom, left:right] = local[: bottom - top, : right - left]
    canvas = np.clip(np.rint(canvas * 255.0), 0.0, 255.0).astype(np.uint8)
    _draw_text(canvas, plan, depth_buffers, scale=scale)
    from PIL import Image

    from ..compass_overlay import draw_raster_compass
    from ..property_colorbar import draw_raster_colorbar

    image = Image.fromarray(canvas, mode="RGBA")
    draw_raster_compass(image, plan)
    draw_raster_colorbar(image, plan)
    canvas[:] = np.asarray(image)
    return canvas


def _render_viewport(
    viewport: ViewportPlan,
    width: int,
    height: int,
    background: np.ndarray,
    *,
    line_scale: int,
    initial_color: np.ndarray | None = None,
    initial_depth: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    transform = CameraTransform(viewport.camera, width, height)
    if initial_color is None:
        color = np.empty((height, width, 4), dtype=float)
        color[...] = background
    else:
        color = np.array(initial_color, dtype=float, copy=True)
        if color.shape != (height, width, 4):
            raise ValueError("initial_color must have shape (height, width, 4)")
    if initial_depth is None:
        z_buffer = np.full((height, width), np.inf, dtype=float)
    else:
        z_buffer = np.array(initial_depth, dtype=float, copy=True)
        if z_buffer.shape != (height, width):
            raise ValueError("initial_depth must have shape (height, width)")
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
            raster_shape = primitive.metadata.get("_raster_shape")
            if primitive.vertex_normals is not None and raster_shape == "sphere":
                _rasterize_sphere(
                    primitive,
                    transform,
                    color,
                    z_buffer,
                    order_buffer,
                    fragments,
                    order,
                )
            elif primitive.vertex_normals is not None and raster_shape == "cylinder":
                _rasterize_cylinder(
                    primitive,
                    transform,
                    color,
                    z_buffer,
                    order_buffer,
                    fragments,
                    order,
                )
            else:
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
    return color, z_buffer


def composite_primitives(
    rgba: np.ndarray,
    depth: np.ndarray,
    camera,
    primitives,
    *,
    metadata: dict | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Depth-compose general overlay primitives over an analytic batch frame."""

    values = np.ascontiguousarray(rgba, dtype=np.uint8)
    if values.ndim != 3 or values.shape[2] != 4:
        raise ValueError("rgba must have shape (height, width, 4)")
    height, width = values.shape[:2]
    viewport = ViewportPlan(
        semantic_id="main",
        camera=camera,
        primitives=tuple(primitives),
    )
    color, composed_depth = _render_viewport(
        viewport,
        width,
        height,
        np.zeros(4, dtype=float),
        line_scale=1,
        initial_color=values.astype(float) / 255.0,
        initial_depth=depth,
    )
    output = np.clip(np.rint(color * 255.0), 0.0, 255.0).astype(np.uint8)
    plan = RenderPlan(
        width=width,
        height=height,
        background=(0.0, 0.0, 0.0, 0.0),
        viewports=(viewport,),
        metadata=dict(metadata or {}),
    )
    _draw_text(output, plan, {"main": composed_depth}, scale=1)
    if metadata:
        from PIL import Image

        from ..compass_overlay import draw_raster_compass

        image = Image.fromarray(output, mode="RGBA")
        draw_raster_compass(image, plan)
        output[:] = np.asarray(image)
    return output, composed_depth


def _write_samples(
    *,
    pixel_x: np.ndarray,
    pixel_y: np.ndarray,
    pixel_depths: np.ndarray,
    pixel_rgb: np.ndarray,
    rgba: tuple[float, float, float, float],
    source: str,
    order: int,
    color: np.ndarray,
    z_buffer: np.ndarray,
    order_buffer: np.ndarray,
    fragments: dict[int, list[_Fragment]],
) -> None:
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
        return
    width = z_buffer.shape[1]
    for x_value, y_value, depth, rgb in zip(pixel_x, pixel_y, pixel_depths, pixel_rgb):
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


def _rasterize_sphere(
    primitive: TriangleMeshPrimitive,
    transform: CameraTransform,
    color: np.ndarray,
    z_buffer: np.ndarray,
    order_buffer: np.ndarray,
    fragments: dict[int, list[_Fragment]],
    primitive_order: int,
) -> None:
    center = np.asarray(primitive.metadata["_raster_center"], dtype=float)
    radius = float(primitive.metadata["_raster_radius"])
    camera_center = transform.world_to_camera(center[None, :])[0]
    center_depth = -float(camera_center[2])
    if (
        center_depth + radius < transform.spec.near
        or center_depth - radius > transform.spec.far
    ):
        return
    projected_center = transform.project_camera(camera_center[None, :]).xy[0]
    if transform.spec.projection == "orthographic":
        radius_px = radius * transform.height / (2.0 * transform.spec.ortho_scale)
    else:
        if center_depth <= radius + 1e-12:
            _rasterize_mesh(
                primitive,
                transform,
                color,
                z_buffer,
                order_buffer,
                fragments,
                primitive_order,
            )
            return
        focal = 1.0 / np.tan(np.radians(transform.spec.fov_y_deg) * 0.5)
        radius_px = (
            0.5
            * transform.height
            * focal
            * radius
            / np.sqrt(center_depth * center_depth - radius * radius)
        )
    minimum_x = max(0, int(np.floor(projected_center[0] - radius_px - 1.0)))
    maximum_x = min(
        transform.width - 1, int(np.ceil(projected_center[0] + radius_px + 1.0))
    )
    minimum_y = max(0, int(np.floor(projected_center[1] - radius_px - 1.0)))
    maximum_y = min(
        transform.height - 1, int(np.ceil(projected_center[1] + radius_px + 1.0))
    )
    if minimum_x > maximum_x or minimum_y > maximum_y:
        return
    xs = np.arange(minimum_x, maximum_x + 1, dtype=float) + 0.5
    ys = np.arange(minimum_y, maximum_y + 1, dtype=float) + 0.5
    grid_x, grid_y = np.meshgrid(xs, ys)
    if transform.spec.projection == "orthographic":
        world_per_pixel = 2.0 * transform.spec.ortho_scale / transform.height
        delta_x = (grid_x - projected_center[0]) * world_per_pixel
        delta_y = -(grid_y - projected_center[1]) * world_per_pixel
        radial_squared = delta_x * delta_x + delta_y * delta_y
        inside = radial_squared <= radius * radius
        front = np.sqrt(np.maximum(radius * radius - radial_squared, 0.0))
        depth_values = center_depth - front
        normals = np.stack((delta_x, delta_y, front), axis=-1) / radius
    else:
        focal = 1.0 / np.tan(np.radians(transform.spec.fov_y_deg) * 0.5)
        ndc_x = 2.0 * grid_x / transform.width - 1.0
        ndc_y = 1.0 - 2.0 * grid_y / transform.height
        rays = np.stack(
            (
                ndc_x * transform.aspect / focal,
                ndc_y / focal,
                -np.ones_like(ndc_x),
            ),
            axis=-1,
        )
        rays /= np.linalg.norm(rays, axis=-1, keepdims=True)
        along = rays @ camera_center
        discriminant = along * along - (
            float(camera_center @ camera_center) - radius * radius
        )
        inside = discriminant >= 0.0
        distance = along - np.sqrt(np.maximum(discriminant, 0.0))
        hits = rays * distance[..., None]
        depth_values = -hits[..., 2]
        normals = (hits - camera_center) / radius
    inside &= (depth_values >= transform.spec.near - 1e-12) & (
        depth_values <= transform.spec.far + 1e-12
    )
    if not np.any(inside):
        return
    light = np.asarray([-0.32, 0.42, 1.0], dtype=float)
    light /= np.linalg.norm(light)
    illumination = 0.68 + 0.32 * np.abs(normals @ light)
    rgb_values = np.asarray(primitive.rgba[:3])[None, None, :] * illumination[..., None]
    local_y, local_x = np.nonzero(inside)
    _write_samples(
        pixel_x=local_x + minimum_x,
        pixel_y=local_y + minimum_y,
        pixel_depths=depth_values[local_y, local_x],
        pixel_rgb=np.clip(rgb_values[local_y, local_x], 0.0, 1.0),
        rgba=primitive.rgba,
        source=primitive.semantic_id,
        order=primitive_order * 1_000_000,
        color=color,
        z_buffer=z_buffer,
        order_buffer=order_buffer,
        fragments=fragments,
    )


def _rasterize_cylinder(
    primitive: TriangleMeshPrimitive,
    transform: CameraTransform,
    color: np.ndarray,
    z_buffer: np.ndarray,
    order_buffer: np.ndarray,
    fragments: dict[int, list[_Fragment]],
    primitive_order: int,
) -> None:
    endpoints = np.asarray(
        [primitive.metadata["_raster_start"], primitive.metadata["_raster_end"]],
        dtype=float,
    )
    camera = transform.world_to_camera(endpoints)
    clipped = transform.clip_segment_camera(camera[0], camera[1])
    if clipped is None:
        return
    projected = transform.project_camera(np.asarray(clipped))
    first, second = projected.xy
    vector = second - first
    length_squared = float(vector @ vector)
    if length_squared < 1e-16:
        return
    radius = float(primitive.metadata["_raster_radius"])
    if transform.spec.projection == "orthographic":
        half_width = radius * transform.height / (2.0 * transform.spec.ortho_scale)
    else:
        focal = 1.0 / np.tan(np.radians(transform.spec.fov_y_deg) * 0.5)
        half_width = (
            0.5
            * transform.height
            * focal
            * radius
            / max(float(np.min(projected.depth)), 1e-12)
        )
    half_width = max(0.5, half_width)
    minimum_x = max(0, int(np.floor(min(first[0], second[0]) - half_width - 1.0)))
    maximum_x = min(
        transform.width - 1,
        int(np.ceil(max(first[0], second[0]) + half_width + 1.0)),
    )
    minimum_y = max(0, int(np.floor(min(first[1], second[1]) - half_width - 1.0)))
    maximum_y = min(
        transform.height - 1,
        int(np.ceil(max(first[1], second[1]) + half_width + 1.0)),
    )
    if minimum_x > maximum_x or minimum_y > maximum_y:
        return
    xs = np.arange(minimum_x, maximum_x + 1, dtype=float) + 0.5
    ys = np.arange(minimum_y, maximum_y + 1, dtype=float) + 0.5
    grid_x, grid_y = np.meshgrid(xs, ys)
    parameter = np.clip(
        ((grid_x - first[0]) * vector[0] + (grid_y - first[1]) * vector[1])
        / length_squared,
        0.0,
        1.0,
    )
    closest_x = first[0] + parameter * vector[0]
    closest_y = first[1] + parameter * vector[1]
    distance_squared = (grid_x - closest_x) ** 2 + (grid_y - closest_y) ** 2
    inside = distance_squared <= half_width * half_width
    if transform.spec.projection == "perspective":
        reciprocal = (1.0 - parameter) / projected.depth[
            0
        ] + parameter / projected.depth[1]
        depth_values = 1.0 / np.maximum(reciprocal, 1e-15)
    else:
        depth_values = (1.0 - parameter) * projected.depth[
            0
        ] + parameter * projected.depth[1]
    radial = np.sqrt(
        np.maximum(1.0 - distance_squared / (half_width * half_width), 0.0)
    )
    depth_values = depth_values - radius * radial
    inside &= (depth_values >= transform.spec.near - 1e-12) & (
        depth_values <= transform.spec.far + 1e-12
    )
    if not np.any(inside):
        return
    illumination = 0.72 + 0.28 * radial
    rgb_values = np.asarray(primitive.rgba[:3])[None, None, :] * illumination[..., None]
    local_y, local_x = np.nonzero(inside)
    _write_samples(
        pixel_x=local_x + minimum_x,
        pixel_y=local_y + minimum_y,
        pixel_depths=depth_values[local_y, local_x],
        pixel_rgb=np.clip(rgb_values[local_y, local_x], 0.0, 1.0),
        rgba=primitive.rgba,
        source=primitive.semantic_id,
        order=primitive_order * 1_000_000,
        color=color,
        z_buffer=z_buffer,
        order_buffer=order_buffer,
        fragments=fragments,
    )


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
    triangle_indices = np.asarray(primitive.triangles, dtype=int)
    if len(triangle_indices) == 0:
        return
    world_triangles = primitive.vertices[triangle_indices]
    world_normals = np.cross(
        world_triangles[:, 1] - world_triangles[:, 0],
        world_triangles[:, 2] - world_triangles[:, 0],
    )
    normal_lengths = np.linalg.norm(world_normals, axis=1)
    camera_normals = (
        world_normals / np.maximum(normal_lengths[:, None], 1e-14)
    ) @ rotation.T
    projected_vertices = transform.project_camera(camera_vertices)
    vertex_depths = -camera_vertices[:, 2]
    vertex_inside = (vertex_depths >= float(transform.spec.near) - 1e-12) & (
        vertex_depths <= float(transform.spec.far) + 1e-12
    )
    vertex_rgb_all = None
    if primitive.vertex_normals is not None:
        camera_vertex_normals = primitive.vertex_normals @ rotation.T
        camera_vertex_normals /= np.maximum(
            np.linalg.norm(camera_vertex_normals, axis=1, keepdims=True), 1e-12
        )
        vertex_illumination = 0.68 + 0.32 * np.abs(camera_vertex_normals @ light)
        vertex_rgb_all = (
            np.asarray(primitive.rgba[:3])[None, :] * vertex_illumination[:, None]
        )
    rgba = (*primitive.rgba[:3], float(primitive.rgba[3]))
    for triangle_index, indices in enumerate(primitive.triangles):
        if normal_lengths[triangle_index] < 1e-14:
            continue
        if (
            not primitive.double_sided
            and float(camera_normals[triangle_index, 2]) <= 0.0
        ):
            continue
        if vertex_rgb_all is None:
            lambert = abs(float(camera_normals[triangle_index] @ light))
            if primitive.metadata.get("kind") == "polyhedron":
                face_rgb = _polyhedron_face_rgb(primitive.rgba[:3], lambert)
            else:
                face_rgb = np.asarray(primitive.rgba[:3]) * (0.68 + 0.32 * lambert)
            triangle_rgb = np.tile(
                face_rgb,
                (3, 1),
            )
        else:
            triangle_rgb = vertex_rgb_all[indices]
        stable_order = primitive_order * 1_000_000 + triangle_index * 16
        if bool(np.all(vertex_inside[indices])):
            _rasterize_triangle(
                projected_vertices.xy[indices],
                projected_vertices.depth[indices],
                rgba,
                triangle_rgb,
                primitive.semantic_id,
                stable_order,
                perspective=transform.spec.projection == "perspective",
                color=color,
                z_buffer=z_buffer,
                order_buffer=order_buffer,
                fragments=fragments,
            )
            continue
        camera_triangle = camera_vertices[indices]
        clipped, clipped_rgb = _clip_polygon_attributes(
            camera_triangle,
            triangle_rgb,
            near=transform.spec.near,
            far=transform.spec.far,
        )
        if len(clipped) < 3:
            continue
        triangles = triangulate_polygon(clipped)
        color_triangles = [
            np.asarray([clipped_rgb[0], clipped_rgb[index], clipped_rgb[index + 1]])
            for index in range(1, len(clipped_rgb) - 1)
        ]
        for clipped_index, (triangle, vertex_rgb) in enumerate(
            zip(triangles, color_triangles)
        ):
            clipped_order = (
                primitive_order * 1_000_000 + triangle_index * 16 + clipped_index
            )
            projected = transform.project_camera(triangle)
            _rasterize_triangle(
                projected.xy,
                projected.depth,
                rgba,
                vertex_rgb,
                primitive.semantic_id,
                clipped_order,
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
                        (depth == existing or abs(depth - existing) <= _DEPTH_EPSILON)
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


def _draw_text(
    canvas: np.ndarray,
    plan: RenderPlan,
    depth_buffers: dict[str, np.ndarray],
    *,
    scale: int,
) -> None:
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
    full_width, full_height = image.size
    for viewport, primitive in sorted(texts, key=lambda item: item[1].semantic_id):
        left, top, right, bottom = _viewport_bounds(viewport, full_width, full_height)
        local_width = max(1, right - left)
        local_height = max(1, bottom - top)
        transform = CameraTransform(viewport.camera, local_width, local_height)
        projected = transform.project_world(np.asarray([primitive.position]))
        if not bool(projected.visible[0]):
            continue
        anchor = projected.xy[0]
        if not (0.0 <= anchor[0] < local_width and 0.0 <= anchor[1] < local_height):
            continue
        if primitive.depth_test:
            pixel_x, pixel_y = np.floor(anchor).astype(int)
            opaque_depth = depth_buffers[viewport.semantic_id][pixel_y, pixel_x]
            if opaque_depth < float(projected.depth[0]) - _DEPTH_EPSILON:
                continue
        screen = anchor + np.asarray(primitive.offset_px) * scale
        size_px = max(1, int(round(primitive.size_pt * scale * 96.0 / 72.0)))
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", size_px)
        except OSError:  # pragma: no cover - platform font fallback
            font = ImageFont.load_default()
        rgba = tuple(int(round(channel * 255.0)) for channel in primitive.rgba)
        label_layer = Image.new("RGBA", (local_width, local_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(label_layer)
        draw.text(tuple(screen), primitive.text, fill=rgba, font=font, anchor="ls")
        image.alpha_composite(label_layer, dest=(left, top))
    canvas[:] = np.asarray(image)


def _viewport_bounds(
    viewport: ViewportPlan,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    x, y, viewport_width, viewport_height = viewport.rect
    return (
        int(round(x * width)),
        int(round(y * height)),
        int(round((x + viewport_width) * width)),
        int(round((y + viewport_height) * height)),
    )


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
