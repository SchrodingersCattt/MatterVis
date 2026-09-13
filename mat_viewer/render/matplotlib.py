"""Projected 2D Matplotlib backend for backend-neutral render plans."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np

from .camera import CameraTransform
from .contracts import (
    LinePrimitive,
    RENDER_RESULT_SCHEMA,
    RenderPlan,
    RenderResult,
    TextPrimitive,
    TriangleMeshPrimitive,
)


def _rgba(value) -> tuple[float, float, float, float]:
    return tuple(float(channel) for channel in value)


def _radius_px(transform: CameraTransform, center_depth: float, radius: float) -> float:
    if transform.spec.projection == "orthographic":
        return radius * transform.height / (2.0 * transform.spec.ortho_scale)
    focal = 1.0 / np.tan(np.radians(transform.spec.fov_y_deg) * 0.5)
    denominator = np.sqrt(max(center_depth * center_depth - radius * radius, 1e-12))
    return 0.5 * transform.height * focal * radius / denominator


def _line_width_points(width_px: float, dpi: float) -> float:
    return max(0.35, float(width_px) * 72.0 / float(dpi))


def _viewport_artists(viewport, *, width: int, height: int, dpi: float):
    from matplotlib.lines import Line2D
    from matplotlib.patches import Circle, Polygon

    transform = CameraTransform(viewport.camera, width, height)
    artists: list[tuple[float, int, str, Any]] = []
    for primitive in viewport.primitives:
        if isinstance(primitive, TriangleMeshPrimitive):
            shape = primitive.metadata.get("_raster_shape")
            if shape == "sphere":
                center = np.asarray(primitive.metadata["_raster_center"], dtype=float)
                camera_center = transform.world_to_camera(center[None, :])[0]
                depth = -float(camera_center[2])
                if depth < transform.spec.near or depth > transform.spec.far:
                    continue
                projected = transform.project_camera(camera_center[None, :])
                if not bool(projected.visible[0]):
                    continue
                radius = float(primitive.metadata["_raster_radius"])
                patch = Circle(
                    tuple(projected.xy[0]),
                    radius=_radius_px(transform, depth, radius),
                    facecolor=_rgba(primitive.rgba),
                    edgecolor=(0.18, 0.18, 0.18, min(1.0, primitive.rgba[3])),
                    linewidth=_line_width_points(0.8, dpi),
                )
                artists.append((depth, 2, primitive.semantic_id, patch))
                continue
            if shape == "cylinder":
                endpoints = np.asarray(
                    [
                        primitive.metadata["_raster_start"],
                        primitive.metadata["_raster_end"],
                    ],
                    dtype=float,
                )
                camera = transform.world_to_camera(endpoints)
                clipped = transform.clip_segment_camera(camera[0], camera[1])
                if clipped is None:
                    continue
                clipped_array = np.asarray(clipped)
                projected = transform.project_camera(clipped_array)
                depth = float(np.mean(projected.depth))
                radius = float(primitive.metadata["_raster_radius"])
                width_px = 2.0 * _radius_px(transform, depth, radius)
                line = Line2D(
                    projected.xy[:, 0],
                    projected.xy[:, 1],
                    color=_rgba(primitive.rgba),
                    linewidth=_line_width_points(width_px, dpi),
                    solid_capstyle="round",
                )
                artists.append((depth, 1, primitive.semantic_id, line))
                continue

            camera_vertices = transform.world_to_camera(primitive.vertices)
            projected = transform.project_camera(camera_vertices)
            for triangle_index, indices in enumerate(primitive.triangles):
                if not bool(np.all(projected.visible[indices])):
                    continue
                depth = float(np.mean(projected.depth[indices]))
                patch = Polygon(
                    projected.xy[indices],
                    closed=True,
                    facecolor=_rgba(primitive.rgba),
                    edgecolor="none",
                )
                artists.append(
                    (depth, 0, f"{primitive.semantic_id}:{triangle_index}", patch)
                )
        elif isinstance(primitive, LinePrimitive):
            for segment_index, segment in enumerate(primitive.segments):
                clipped = transform.clip_segment_world(segment[0], segment[1])
                if clipped is None:
                    continue
                projected = transform.project_camera(np.asarray(clipped))
                depth = float(np.mean(projected.depth))
                line = Line2D(
                    projected.xy[:, 0],
                    projected.xy[:, 1],
                    color=_rgba(primitive.rgba),
                    linewidth=_line_width_points(primitive.width_px, dpi),
                    linestyle="--" if primitive.dash else "-",
                    solid_capstyle="round",
                )
                artists.append(
                    (depth, 0, f"{primitive.semantic_id}:{segment_index}", line)
                )
        elif isinstance(primitive, TextPrimitive):
            projected = transform.project_world(np.asarray([primitive.position]))
            if not bool(projected.visible[0]):
                continue
            artists.append(
                (
                    float(projected.depth[0]),
                    3,
                    primitive.semantic_id,
                    (primitive, projected.xy[0]),
                )
            )
    return sorted(artists, key=lambda item: (-item[0], item[1], item[2]))


def build_figure(plan: RenderPlan, *, dpi: float = 100.0):
    """Project a RenderPlan into a Matplotlib figure without 3D mesh drawing."""

    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    figure = plt.figure(
        figsize=(plan.width / dpi, plan.height / dpi),
        dpi=dpi,
        facecolor=_rgba(plan.background),
    )
    for viewport in plan.viewports:
        x, y, viewport_width, viewport_height = viewport.rect
        axes = figure.add_axes(
            [x, 1.0 - y - viewport_height, viewport_width, viewport_height]
        )
        local_width = max(1, int(round(plan.width * viewport_width)))
        local_height = max(1, int(round(plan.height * viewport_height)))
        axes.set_xlim(0.0, float(local_width))
        axes.set_ylim(float(local_height), 0.0)
        axes.set_aspect("equal")
        axes.set_facecolor(_rgba(plan.background))
        axes.axis("off")
        for zorder, (_, _, _, artist) in enumerate(
            _viewport_artists(
                viewport, width=local_width, height=local_height, dpi=dpi
            ),
            start=1,
        ):
            if isinstance(artist, tuple):
                primitive, xy = artist
                axes.text(
                    xy[0] + primitive.offset_px[0],
                    xy[1] + primitive.offset_px[1],
                    primitive.text,
                    color=_rgba(primitive.rgba),
                    fontsize=primitive.size_pt,
                    zorder=zorder,
                )
            else:
                artist.set_zorder(zorder)
                axes.add_artist(artist)
        from .compass_overlay import draw_matplotlib_compass

        draw_matplotlib_compass(
            axes,
            plan.metadata,
            viewport,
            local_width,
            local_height,
        )
    from .property_colorbar import draw_matplotlib_colorbar

    draw_matplotlib_colorbar(figure, plan)
    return figure


def render(plan: RenderPlan, output: str | Path | None = None) -> RenderResult:
    """Render projected 2D PNG/PDF/SVG; never invoke Plotly or a 3D rasterizer."""

    import matplotlib.pyplot as plt

    destination = Path(output).expanduser().resolve() if output is not None else None
    output_format = destination.suffix.lower().lstrip(".") if destination else "png"
    if output_format not in {"png", "pdf", "svg"}:
        raise ValueError("Matplotlib output must be PNG, PDF, or SVG")
    dpi = 100.0 * max(1, int(plan.metadata.get("scale", 1)))
    figure = build_figure(plan, dpi=dpi)
    buffer = BytesIO()
    figure.savefig(
        buffer,
        format=output_format,
        dpi=dpi,
        facecolor=_rgba(plan.background),
        edgecolor="none",
        metadata={"Creator": "MatterVis"},
    )
    plt.close(figure)
    data = buffer.getvalue()
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    return RenderResult(
        schema=RENDER_RESULT_SCHEMA,
        backend="matplotlib",
        format=output_format,
        width=plan.width,
        height=plan.height,
        plan_sha256=plan.fingerprint(),
        output_sha256=sha256(data).hexdigest(),
        output=destination,
        data=None if destination is not None else data,
        warnings=plan.warnings,
        metadata={
            "fallback": None,
            "projection": "2d",
            "scale": int(plan.metadata.get("scale", 1)),
        },
    )


__all__ = ["build_figure", "render"]
