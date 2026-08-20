"""Optional Plotly adapter for backend-neutral :class:`RenderPlan` objects."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

import numpy as np

from .contracts import (
    LinePrimitive,
    RENDER_RESULT_SCHEMA,
    RenderPlan,
    RenderResult,
    TextPrimitive,
    TriangleMeshPrimitive,
)


def _plotly():
    try:
        import plotly.graph_objects as go
        import plotly.io as pio
    except ImportError as exc:  # pragma: no cover - minimal CI
        from ..capabilities import resolve_requirements

        install = resolve_requirements("plotly").install_command
        raise ImportError(
            "The Plotly backend requires the 'plotly' capability. "
            f"Install with: {install}"
        ) from exc
    return go, pio


def _rgba(channels) -> str:
    red, green, blue, alpha = channels
    return (
        f"rgba({round(red * 255)},{round(green * 255)},{round(blue * 255)},{alpha:.8g})"
    )


def _scene_name(index: int) -> str:
    return "scene" if index == 0 else f"scene{index + 1}"


def _primitive_trace(primitive, *, scene: str):
    go, _ = _plotly()
    if isinstance(primitive, TriangleMeshPrimitive):
        vertices = primitive.vertices
        triangles = primitive.triangles
        return go.Mesh3d(
            x=vertices[:, 0],
            y=vertices[:, 1],
            z=vertices[:, 2],
            i=triangles[:, 0],
            j=triangles[:, 1],
            k=triangles[:, 2],
            color=_rgba((*primitive.rgba[:3], 1.0)),
            opacity=primitive.rgba[3],
            flatshading=primitive.vertex_normals is None,
            name=primitive.semantic_id,
            hoverinfo="name",
            showscale=False,
            scene=scene,
        )
    if isinstance(primitive, LinePrimitive):
        coordinates = [[], [], []]
        for start, end in primitive.segments:
            for axis in range(3):
                coordinates[axis].extend((start[axis], end[axis], None))
        return go.Scatter3d(
            x=coordinates[0],
            y=coordinates[1],
            z=coordinates[2],
            mode="lines",
            line={
                "color": _rgba(primitive.rgba),
                "width": primitive.width_px,
                "dash": "solid" if not primitive.dash else "dash",
            },
            name=primitive.semantic_id,
            hoverinfo="name",
            showlegend=False,
            scene=scene,
        )
    if isinstance(primitive, TextPrimitive):
        return go.Scatter3d(
            x=[primitive.position[0]],
            y=[primitive.position[1]],
            z=[primitive.position[2]],
            mode="text",
            text=[primitive.text],
            textfont={"color": _rgba(primitive.rgba), "size": primitive.size_pt},
            name=primitive.semantic_id,
            hoverinfo="skip",
            showlegend=False,
            scene=scene,
        )
    raise TypeError(f"unsupported RenderPlan primitive: {type(primitive).__name__}")


def _viewport_points(viewport) -> np.ndarray:
    points: list[np.ndarray] = []
    for primitive in viewport.primitives:
        if isinstance(primitive, TriangleMeshPrimitive):
            points.append(np.asarray(primitive.vertices, dtype=float))
        elif isinstance(primitive, LinePrimitive):
            points.append(np.asarray(primitive.segments, dtype=float).reshape(-1, 3))
        elif isinstance(primitive, TextPrimitive):
            points.append(np.asarray([primitive.position], dtype=float))
    if not points:
        return np.asarray([viewport.camera.target], dtype=float)
    return np.concatenate(points, axis=0)


def _scene_layout(viewport, *, aspect: float) -> dict[str, Any]:
    camera = viewport.camera
    direction = np.asarray(camera.position) - np.asarray(camera.target)
    target = np.asarray(camera.target, dtype=float)
    points = _viewport_points(viewport)
    data_radius = float(
        np.max(np.linalg.norm(points - target[None, :], axis=1), initial=0.0)
    )
    framing_radius = max(
        data_radius,
        float(camera.ortho_scale) * max(1.0, float(aspect)),
        1.0e-6,
    )
    eye = direction / framing_radius
    up = np.asarray(camera.up, dtype=float)
    up /= np.linalg.norm(up)
    x, y, width, height = viewport.rect
    axis_ranges = [
        [float(target[axis] - framing_radius), float(target[axis] + framing_radius)]
        for axis in range(3)
    ]
    return {
        "domain": {"x": [x, x + width], "y": [1.0 - y - height, 1.0 - y]},
        "aspectmode": "cube",
        "camera": {
            "eye": {"x": eye[0], "y": eye[1], "z": eye[2]},
            "up": {"x": up[0], "y": up[1], "z": up[2]},
            "center": {"x": 0.0, "y": 0.0, "z": 0.0},
            "projection": {"type": camera.projection},
        },
        "xaxis": {"visible": False, "range": axis_ranges[0], "autorange": False},
        "yaxis": {"visible": False, "range": axis_ranges[1], "autorange": False},
        "zaxis": {"visible": False, "range": axis_ranges[2], "autorange": False},
        "bgcolor": _rgba((0.0, 0.0, 0.0, 0.0)),
    }


def build_figure(plan: RenderPlan):
    """Convert a RenderPlan to a Plotly figure without changing its geometry."""
    go, _ = _plotly()
    figure = go.Figure()
    scene_layouts: dict[str, Any] = {}
    for index, viewport in enumerate(plan.viewports):
        scene = _scene_name(index)
        viewport_aspect = (plan.width * viewport.rect[2]) / max(
            plan.height * viewport.rect[3], 1.0e-12
        )
        scene_layouts[scene] = _scene_layout(viewport, aspect=viewport_aspect)
        for primitive in viewport.primitives:
            figure.add_trace(_primitive_trace(primitive, scene=scene))
    figure.update_layout(
        **scene_layouts,
        width=plan.width,
        height=plan.height,
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        paper_bgcolor=_rgba(plan.background),
        plot_bgcolor=_rgba(plan.background),
        showlegend=False,
    )
    return figure


def render(
    plan: RenderPlan,
    output: str | Path | None = None,
) -> RenderResult:
    """Render a plan through Plotly; failures are never sent to another backend."""
    _, pio = _plotly()
    figure = build_figure(plan)
    path = Path(output).expanduser().resolve() if output is not None else None
    output_format = path.suffix.lower().lstrip(".") if path is not None else "html"
    if output_format not in {"html", "png", "pdf", "svg"}:
        raise ValueError("Plotly output must be HTML, PNG, PDF, or SVG")
    scale = int(plan.metadata.get("scale", 1))
    try:
        if output_format == "html":
            data = pio.to_html(
                figure,
                include_plotlyjs=True,
                full_html=True,
            ).encode("utf-8")
        else:
            data = pio.to_image(
                figure,
                format=output_format,
                width=plan.width,
                height=plan.height,
                scale=scale,
            )
    except Exception as exc:
        requirement = "plotly" if output_format == "html" else "plotly-export"
        raise RuntimeError(
            f"Plotly {output_format} export failed; no fallback was attempted. "
            f"Check `mat-vis capabilities --require {requirement} --json`. "
            f"Original error: {type(exc).__name__}: {exc}"
        ) from exc
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    camera_warning = (
        "Plotly preserves the RenderPlan target, direction, up vector, projection, "
        "and deterministic axis ranges, but Plotly does not expose exact "
        "CameraSpec near/far planes or field-of-view control."
    )
    return RenderResult(
        schema=RENDER_RESULT_SCHEMA,
        backend="plotly",
        format=output_format,
        width=plan.width * (scale if output_format == "png" else 1),
        height=plan.height * (scale if output_format == "png" else 1),
        plan_sha256=plan.fingerprint(),
        output_sha256=sha256(data).hexdigest(),
        output=path,
        data=None if path is not None else data,
        warnings=tuple(plan.warnings) + (camera_warning,),
        metadata={
            "fallback": None,
            "scale": scale,
            "camera_mapping": "deterministic-range approximation",
        },
    )


__all__ = ["build_figure", "render"]
