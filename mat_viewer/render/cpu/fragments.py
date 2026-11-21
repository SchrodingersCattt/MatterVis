"""Transparent fragment list and triangle coverage for the CPU rasterizer.

Opaque coverage stays on the Z buffer in ``raster.py``. This module only
changes pixels whose specular reflection must not be multiplied by opacity.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .raster_math import _edge, _edge_array
from .surface_shade import evaluate_surface

_DEPTH_EPSILON = 1.0e-9


@dataclass(frozen=True, slots=True)
class _Fragment:
    depth: float
    order: int
    source: str
    rgba: tuple[float, float, float, float]
    specular: tuple[float, float, float] = (0.0, 0.0, 0.0)


def _write_samples(
    *,
    pixel_x: np.ndarray,
    pixel_y: np.ndarray,
    pixel_depths: np.ndarray,
    pixel_rgb: np.ndarray,
    rgba: tuple[float, float, float, float],
    pixel_alpha: np.ndarray | None = None,
    pixel_specular: np.ndarray | None = None,
    source: str,
    order: int,
    color: np.ndarray,
    z_buffer: np.ndarray,
    order_buffer: np.ndarray,
    fragments: dict[int, list[_Fragment]],
) -> None:
    # Keep the fast opaque path for ordinary primitives. Analytic overlays
    # may supply a per-pixel alpha field for a native front/back depth cue.
    if pixel_alpha is None and rgba[3] >= 1.0 - 1e-12:
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
    alpha_values = (
        np.asarray(pixel_alpha, dtype=float).reshape(-1)
        if pixel_alpha is not None
        else np.full(len(pixel_x), float(rgba[3]), dtype=float)
    )
    specular_rows = (
        None if pixel_specular is None else np.asarray(pixel_specular, dtype=float)
    )
    for index, (x_value, y_value, depth, rgb, alpha_value) in enumerate(
        zip(pixel_x, pixel_y, pixel_depths, pixel_rgb, alpha_values)
    ):
        alpha_value = float(np.clip(alpha_value, 0.0, 1.0))
        if alpha_value <= 1.0e-8:
            continue
        if specular_rows is None:
            specular = (0.0, 0.0, 0.0)
        else:
            specular = tuple(float(channel) for channel in specular_rows[index])
        _append_fragment(
            fragments,
            int(y_value) * width + int(x_value),
            _Fragment(
                float(depth),
                int(order),
                source,
                (float(rgb[0]), float(rgb[1]), float(rgb[2]), alpha_value),
                specular,  # type: ignore[arg-type]
            ),
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
    phong=None,
    orthographic: bool = True,
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
    if phong is not None and vertex_rgb is not None and np.shape(vertex_rgb)[-1] == 9:
        samples = rgb_values[local_y, local_x]
        diffuse, specular, alpha = evaluate_surface(
            samples[:, 3:6],
            samples[:, :3],
            samples[:, 6:9],
            phong,
            orthographic=orthographic,
            base_alpha=float(rgba[3]),
        )
        if float(rgba[3]) >= 1.0 - 1e-12:
            pixel_rgb = np.clip(diffuse + specular, 0.0, 1.0)
        else:
            _write_shaded_fragments(
                pixel_x,
                pixel_y,
                pixel_depths,
                np.clip(diffuse, 0.0, 1.0),
                np.maximum(specular, 0.0),
                np.clip(alpha, 0.0, 1.0),
                source,
                order,
                width,
                fragments,
            )
            return
    else:
        pixel_rgb = None
    wide = vertex_rgb is not None and np.shape(vertex_rgb)[-1] == 7
    if pixel_rgb is not None:
        wide = False
    if wide:
        samples = rgb_values[local_y, local_x]
        diffuse = np.clip(samples[:, :3], 0.0, 1.0)
        specular = np.maximum(samples[:, 3:6], 0.0)
        alpha = np.clip(samples[:, 6], 0.0, 1.0)
        for x_value, y_value, depth, rgb, highlight, alpha_value in zip(
            pixel_x, pixel_y, pixel_depths, diffuse, specular, alpha
        ):
            alpha_value = float(alpha_value)
            if alpha_value <= 1.0e-8:
                continue
            _append_fragment(
                fragments,
                int(y_value) * width + int(x_value),
                _Fragment(
                    float(depth),
                    int(order),
                    source,
                    (float(rgb[0]), float(rgb[1]), float(rgb[2]), alpha_value),
                    (float(highlight[0]), float(highlight[1]), float(highlight[2])),
                ),
            )
        return
    if pixel_rgb is None:
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


def _write_shaded_fragments(
    pixel_x: np.ndarray,
    pixel_y: np.ndarray,
    pixel_depths: np.ndarray,
    diffuse: np.ndarray,
    specular: np.ndarray,
    alpha: np.ndarray,
    source: str,
    order: int,
    width: int,
    fragments: dict[int, list[_Fragment]],
) -> None:
    for x_value, y_value, depth, rgb, highlight, alpha_value in zip(
        pixel_x, pixel_y, pixel_depths, diffuse, specular, alpha
    ):
        alpha_value = float(alpha_value)
        if alpha_value <= 1.0e-8:
            continue
        _append_fragment(
            fragments,
            int(y_value) * width + int(x_value),
            _Fragment(
                float(depth),
                int(order),
                source,
                (float(rgb[0]), float(rgb[1]), float(rgb[2]), alpha_value),
                (float(highlight[0]), float(highlight[1]), float(highlight[2])),
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
            specular = np.asarray(fragment.specular, dtype=float)
            output_alpha = source_alpha + destination[3] * (1.0 - source_alpha)
            if output_alpha <= 1e-15:
                destination[:] = 0.0
                continue
            # Reflected specular is not multiplied by the transmitted alpha,
            # including when a Fresnel rim clamps that alpha to exactly 1.
            # Matte fragments carry a zero specular and keep the straight
            # alpha blend below, which is the historical compositor.
            if source_alpha > 1.0e-8 and float(np.max(specular)) > 1.0e-8:
                associated = np.clip(source[:3] * source_alpha + specular, 0.0, 1.0)
                destination[:3] = (
                    associated
                    + destination[:3] * destination[3] * (1.0 - source_alpha)
                ) / output_alpha
            else:
                destination[:3] = (
                    source[:3] * source_alpha
                    + destination[:3] * destination[3] * (1.0 - source_alpha)
                ) / output_alpha
            destination[3] = output_alpha
        color[y_value, x_value] = destination


__all__ = [
    "_DEPTH_EPSILON",
    "_Fragment",
    "_append_fragment",
    "_composite_fragments",
    "_rasterize_triangle",
    "_write_samples",
]
