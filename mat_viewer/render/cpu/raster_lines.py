"""CPU rasterization of line primitives, including per-endpoint colors."""

from __future__ import annotations

import numpy as np

from ..camera import CameraTransform
from ..contracts import LinePrimitive
from .raster_math import _dash_visible


def endpoint_screen_weight(
    screen_t: float,
    depth0: float,
    depth1: float,
    *,
    perspective: bool,
) -> float:
    """Return the weight of endpoint 1 at a projected-segment parameter.

    ``depth0`` and ``depth1`` are the positive camera depths from
    ``project_camera`` (``-camera_z``). Orthographic color follows the
    screen parameter. Perspective color uses
    ``(t / depth1) / ((1 - t) / depth0 + t / depth1)``. Depths 1 and 9
    therefore give the screen midpoint weights 0.9 and 0.1, matching the
    perspective-correct depth already used for the Z buffer.
    """
    parameter = float(np.clip(screen_t, 0.0, 1.0))
    if not perspective:
        return parameter
    near_depth = float(depth0)
    far_depth = float(depth1)
    if (
        not np.isfinite(near_depth)
        or not np.isfinite(far_depth)
        or near_depth <= 0.0
        or far_depth <= 0.0
    ):
        return parameter
    near_weight = (1.0 - parameter) / near_depth
    far_weight = parameter / far_depth
    denominator = near_weight + far_weight
    if denominator <= 0.0:
        return parameter
    return far_weight / denominator


def _clipped_endpoint_rgb(
    transform: CameraTransform,
    segment: np.ndarray,
    clipped: tuple[np.ndarray, np.ndarray],
    endpoint_colors: np.ndarray,
) -> np.ndarray:
    """Return RGB at the clipped endpoints, in camera-space parameter order."""
    camera = transform.world_to_camera(np.asarray(segment, dtype=float))
    direction = camera[1] - camera[0]
    denominator = float(direction @ direction)
    if denominator < 1.0e-16:
        return np.vstack((endpoint_colors[0], endpoint_colors[0]))
    clipped_camera = np.vstack(clipped)
    parameters = np.clip((clipped_camera - camera[0]) @ direction / denominator, 0.0, 1.0)
    return (1.0 - parameters)[:, None] * endpoint_colors[0] + parameters[:, None] * endpoint_colors[1]


def rasterize_lines(
    primitive: LinePrimitive,
    transform: CameraTransform,
    color: np.ndarray,
    z_buffer: np.ndarray,
    order_buffer: np.ndarray,
    fragments: dict[int, list],
    primitive_order: int,
    *,
    width_scale: int,
) -> None:
    """Draw one line primitive. Endpoint colors replace the uniform RGB."""
    from .raster import _DEPTH_EPSILON, _append_fragment, _Fragment

    height, width = z_buffer.shape
    half_width = max(0.5, primitive.width_px * width_scale * 0.5)
    for segment_index, segment in enumerate(primitive.segments):
        clipped = transform.clip_segment_world(segment[0], segment[1])
        if clipped is None:
            continue
        endpoint_rgb = None
        if primitive.endpoint_colors is not None:
            endpoint_rgb = _clipped_endpoint_rgb(
                transform,
                segment,
                clipped,
                primitive.endpoint_colors[segment_index],
            )
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
                if endpoint_rgb is None:
                    sample_rgba = primitive.rgba
                else:
                    color_t = endpoint_screen_weight(
                        parameter,
                        float(projected.depth[0]),
                        float(projected.depth[1]),
                        perspective=transform.spec.projection == "perspective",
                    )
                    rgb = (1.0 - color_t) * endpoint_rgb[0] + color_t * endpoint_rgb[1]
                    sample_rgba = (
                        float(rgb[0]),
                        float(rgb[1]),
                        float(rgb[2]),
                        float(primitive.rgba[3]),
                    )
                if primitive.rgba[3] >= 1.0 - 1e-12:
                    existing = z_buffer[y_value, x_value]
                    if depth < existing - _DEPTH_EPSILON or (
                        (depth == existing or abs(depth - existing) <= _DEPTH_EPSILON)
                        and stable_order < order_buffer[y_value, x_value]
                    ):
                        z_buffer[y_value, x_value] = depth
                        order_buffer[y_value, x_value] = stable_order
                        color[y_value, x_value] = sample_rgba
                else:
                    _append_fragment(
                        fragments,
                        y_value * width + x_value,
                        _Fragment(
                            depth, stable_order, primitive.semantic_id, sample_rgba
                        ),
                    )
