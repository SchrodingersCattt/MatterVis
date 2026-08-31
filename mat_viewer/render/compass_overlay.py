"""Camera-projected lattice compass shared by static render backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from .camera import CameraTransform
from .contracts import ViewportPlan


@dataclass(frozen=True, slots=True)
class CompassAxis:
    label: str
    color: str
    start: tuple[float, float]
    end: tuple[float, float]
    label_at: tuple[float, float]
    dot: bool = False


def lattice_compass_metadata(lattice: Any) -> dict | None:
    """Return validated metadata for a lattice compass, if available."""
    matrix = np.asarray(lattice if lattice is not None else [], dtype=float)
    if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
        return None
    return {
        "visible": True,
        "matrix": matrix.tolist(),
        "labels": ["a", "b", "c"],
        "colors": ["#C7372F", "#22A660", "#2E86C1"],
    }


def attach_lattice_compass_metadata(
    metadata: dict,
    warnings: list[str],
    lattice: Any,
) -> None:
    """Attach valid lattice-compass metadata or record why it is unavailable."""
    compass = lattice_compass_metadata(lattice)
    if compass is None:
        warnings.append(
            "lattice axes were requested but the source has no finite lattice"
        )
    else:
        metadata["lattice_compass"] = compass


def lattice_compass_clientside_context(
    metadata: Mapping[str, Any],
    width: int,
    height: int,
) -> dict | None:
    """Return the camera-independent payload used by interactive HTML."""
    payload = metadata.get("lattice_compass")
    if not isinstance(payload, Mapping) or not bool(payload.get("visible")):
        return None
    matrix = np.asarray(payload.get("matrix"), dtype=float)
    if (
        matrix.shape != (3, 3)
        or not np.all(np.isfinite(matrix))
        or np.any(np.linalg.norm(matrix, axis=1) < 1.0e-12)
    ):
        return None

    margin = max(54.0, min(width, height) * 0.075)
    arrow_length = max(38.0, min(58.0, min(width, height) * 0.07))
    return {
        "M": matrix.tolist(),
        "labels": list(payload.get("labels") or ("a", "b", "c")),
        "colors": list(payload.get("colors") or ("#C7372F", "#22A660", "#2E86C1")),
        "anchor": [margin / max(width, 1), margin / max(height, 1)],
        "pixel_length": arrow_length,
        "line_width": 2.2,
        "label_pixel_offset": 10.0,
        "font_size": 12,
    }


def lattice_compass_layout(
    metadata: Mapping[str, Any],
    viewport: ViewportPlan,
    width: int,
    height: int,
    *,
    pixel_scale: float = 1.0,
) -> tuple[CompassAxis, ...]:
    """Return deterministic viewport-pixel geometry for an optional compass."""
    payload = metadata.get("lattice_compass")
    if not isinstance(payload, Mapping) or not bool(payload.get("visible")):
        return ()
    matrix = np.asarray(payload.get("matrix"), dtype=float)
    if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
        return ()

    norms = np.linalg.norm(matrix, axis=1)
    if np.any(norms < 1.0e-12):
        return ()
    unit_axes = matrix / norms[:, None]
    transform = CameraTransform(viewport.camera, width, height)
    projected = np.column_stack(
        (unit_axes @ transform.right, -(unit_axes @ transform.up))
    )
    projected_norms = np.linalg.norm(projected, axis=1)
    longest = float(np.max(projected_norms))
    if longest < 1.0e-12:
        return ()

    scale = max(float(pixel_scale), 1.0e-6)
    margin = max(54.0 * scale, min(width, height) * 0.075)
    arrow_length = max(38.0 * scale, min(58.0 * scale, min(width, height) * 0.07))
    anchor = np.asarray([margin, height - margin], dtype=float)
    labels = list(payload.get("labels") or ("a", "b", "c"))
    colors = list(payload.get("colors") or ("#C7372F", "#22A660", "#2E86C1"))

    result: list[CompassAxis] = []
    for index, vector in enumerate(projected):
        length = float(projected_norms[index])
        is_dot = length <= max(0.06 * longest, 1.0e-9)
        if is_dot:
            end = anchor.copy()
            label_at = anchor + np.asarray([10.0, -10.0]) * scale
        else:
            direction = vector / length
            end = anchor + vector / longest * arrow_length
            label_at = end + direction * 10.0 * scale
        result.append(
            CompassAxis(
                label=str(labels[index]),
                color=str(colors[index]),
                start=tuple(anchor),
                end=tuple(end),
                label_at=tuple(label_at),
                dot=is_dot,
            )
        )
    return tuple(result)


def draw_matplotlib_compass(
    axes: Any,
    metadata: Mapping[str, Any],
    viewport: ViewportPlan,
    width: int,
    height: int,
) -> None:
    """Paint the compass on a Matplotlib axes in viewport-pixel coordinates."""
    from matplotlib.patches import Circle

    for item in lattice_compass_layout(metadata, viewport, width, height):
        if item.dot:
            axes.add_patch(
                Circle(
                    item.start,
                    radius=3.5,
                    facecolor=item.color,
                    edgecolor=item.color,
                    zorder=10_000,
                )
            )
        else:
            axes.annotate(
                "",
                xy=item.end,
                xytext=item.start,
                arrowprops={
                    "arrowstyle": "-|>",
                    "color": item.color,
                    "linewidth": 2.2,
                    "shrinkA": 0.0,
                    "shrinkB": 0.0,
                },
                zorder=10_000,
            )
        axes.text(
            item.label_at[0],
            item.label_at[1],
            item.label,
            color=item.color,
            fontsize=10,
            fontweight="bold",
            ha="center",
            va="center",
            zorder=10_001,
        )


def draw_raster_compass(image: Any, plan: Any) -> None:
    """Paint the compass over a finished RGBA Pillow image."""
    from PIL import Image, ImageColor, ImageDraw, ImageFont

    full_width, full_height = image.size
    pixel_scale = full_width / max(float(plan.width), 1.0)
    for viewport in plan.viewports:
        x, y, width_fraction, height_fraction = viewport.rect
        left = int(round(x * full_width))
        top = int(round(y * full_height))
        local_width = max(1, int(round(width_fraction * full_width)))
        local_height = max(1, int(round(height_fraction * full_height)))
        items = lattice_compass_layout(
            plan.metadata,
            viewport,
            local_width,
            local_height,
            pixel_scale=pixel_scale,
        )
        if not items:
            continue
        layer = Image.new("RGBA", (local_width, local_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        line_width = max(2, int(round(2.2 * pixel_scale)))
        head = 8.0 * pixel_scale
        try:
            font = ImageFont.truetype(
                "DejaVuSans-Bold.ttf", max(10, int(round(12 * pixel_scale)))
            )
        except OSError:  # pragma: no cover - platform font fallback
            font = ImageFont.load_default()
        for item in items:
            color = (*ImageColor.getrgb(item.color), 255)
            start = np.asarray(item.start)
            end = np.asarray(item.end)
            if item.dot:
                radius = 3.5 * pixel_scale
                draw.ellipse(
                    (
                        start[0] - radius,
                        start[1] - radius,
                        start[0] + radius,
                        start[1] + radius,
                    ),
                    fill=color,
                )
            else:
                draw.line((tuple(start), tuple(end)), fill=color, width=line_width)
                direction = end - start
                direction /= np.linalg.norm(direction)
                normal = np.asarray([-direction[1], direction[0]])
                base = end - direction * head
                draw.polygon(
                    [
                        tuple(end),
                        tuple(base + normal * head * 0.45),
                        tuple(base - normal * head * 0.45),
                    ],
                    fill=color,
                )
            draw.text(
                item.label_at,
                item.label,
                fill=color,
                font=font,
                anchor="mm",
            )
        image.alpha_composite(layer, dest=(left, top))
