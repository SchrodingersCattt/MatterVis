"""Native colour-bar overlays backed by RenderPlan property metadata."""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np


def colorbar_metadata(plan_or_metadata: Any) -> dict[str, Any] | None:
    metadata = (
        getattr(plan_or_metadata, "metadata", plan_or_metadata)
        if plan_or_metadata is not None
        else {}
    )
    payload = dict((metadata or {}).get("atom_property_color") or {})
    if not payload or not payload.get("show_colorbar"):
        return None
    return payload


def plotly_colorscale(payload: Mapping[str, Any]) -> list[list[Any]]:
    lut = np.asarray(payload["lut"], dtype=np.uint8)
    lower, upper = (float(value) for value in payload["range"])
    center = payload.get("center")
    positions = np.linspace(0.0, 1.0, len(lut))
    if center is not None and upper != lower:
        center = float(center)
        values = np.where(
            positions <= 0.5,
            lower + 2.0 * positions * (center - lower),
            center + 2.0 * (positions - 0.5) * (upper - center),
        )
        positions = (values - lower) / (upper - lower)
    return [
        [float(position), f"rgb({int(red)},{int(green)},{int(blue)})"]
        for position, (red, green, blue) in zip(positions, lut)
    ]


def draw_raster_colorbar(image, plan) -> None:
    payload = colorbar_metadata(plan)
    if payload is None:
        return
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(image)
    width, height = image.size
    x, y, rect_width, rect_height = payload["colorbar_rect"]
    left = int(round(width * (float(x) + float(rect_width) * 0.18)))
    right = int(round(width * (float(x) + float(rect_width) * 0.48)))
    top = int(round(height * (float(y) + float(rect_height) * 0.08)))
    bottom = int(round(height * (float(y) + float(rect_height) * 0.88)))
    lut = np.asarray(payload["lut"], dtype=np.uint8)
    for row in range(max(1, bottom - top)):
        index = int(round((1.0 - row / max(bottom - top - 1, 1)) * 255.0))
        red, green, blue = (int(value) for value in lut[index])
        draw.line((left, top + row, right, top + row), fill=(red, green, blue, 255))
    draw.rectangle((left, top, right, bottom), outline=(40, 40, 40, 255), width=1)
    font = ImageFont.load_default()
    lower, upper = payload["range"]
    text_color = _contrast_text(plan.background)
    draw.text((right + 5, top - 5), _format_value(upper), fill=text_color, font=font)
    draw.text((right + 5, bottom - 6), _format_value(lower), fill=text_color, font=font)
    center = payload.get("center")
    if center is not None:
        draw.text(
            (right + 5, (top + bottom) // 2 - 5),
            _format_value(center),
            fill=text_color,
            font=font,
        )
    title = _title(payload)
    draw.text((left, max(0, top - 18)), title, fill=text_color, font=font)


def draw_matplotlib_colorbar(figure, plan) -> None:
    payload = colorbar_metadata(plan)
    if payload is None:
        return
    from matplotlib.patches import Rectangle

    x, y, width, height = (float(value) for value in payload["colorbar_rect"])
    axes = figure.add_axes([x, 1.0 - y - height, width, height], frameon=False)
    axes.set_xlim(0.0, 1.0)
    axes.set_ylim(0.0, 1.0)
    axes.axis("off")
    lut = np.asarray(payload["lut"], dtype=np.float64) / 255.0
    bar_left, bar_width = 0.16, 0.26
    for index, rgb in enumerate(lut):
        axes.add_patch(
            Rectangle(
                (bar_left, index / 256.0),
                bar_width,
                1.0 / 256.0 + 1.0e-6,
                facecolor=tuple(rgb),
                edgecolor="none",
                linewidth=0.0,
                rasterized=False,
            )
        )
    axes.add_patch(
        Rectangle(
            (bar_left, 0.0),
            bar_width,
            1.0,
            facecolor="none",
            edgecolor="#282828",
            linewidth=0.6,
            rasterized=False,
        )
    )
    lower, upper = payload["range"]
    axes.text(0.49, 0.0, _format_value(lower), va="bottom", ha="left", fontsize=7)
    axes.text(0.49, 1.0, _format_value(upper), va="top", ha="left", fontsize=7)
    center = payload.get("center")
    if center is not None:
        axes.text(0.49, 0.5, _format_value(center), va="center", ha="left", fontsize=7)
    axes.set_title(_title(payload), fontsize=8, loc="left", pad=4)


def add_plotly_colorbar(figure, plan) -> None:
    payload = colorbar_metadata(plan)
    if payload is None:
        return
    x, y, width, height = (float(value) for value in payload["colorbar_rect"])
    figure.add_trace(
        plotly_colorbar_trace(
            payload,
            x=x + width * 0.5,
            y=1.0 - y - height * 0.5,
            length=height,
            thickness=max(12, int(width * plan.width * 0.24)),
        )
    )


def plotly_colorbar_trace(
    payload: Mapping[str, Any],
    *,
    x: float = 0.93,
    y: float = 0.5,
    length: float = 0.84,
    thickness: int = 24,
) -> dict[str, Any]:
    """Return a validator-free dummy trace that owns a shared-LUT colorbar."""

    lower, upper = (float(value) for value in payload["range"])
    return {
        "type": "scatter",
        "x": [None],
        "y": [None],
        "mode": "markers",
        "hoverinfo": "skip",
        "showlegend": False,
        "marker": {
            "color": [lower],
            "cmin": lower,
            "cmax": upper if upper != lower else lower + 1.0,
            "colorscale": plotly_colorscale(payload),
            "showscale": True,
            "colorbar": {
                "title": {"text": _title(payload)},
                "x": float(x),
                "y": float(y),
                "len": float(length),
                "thickness": int(thickness),
                "outlinewidth": 1,
            },
        },
        "meta": {"mv_role": "atom_property_colorbar"},
    }


def _format_value(value: Any) -> str:
    return f"{float(value):.5g}"


def _title(payload: Mapping[str, Any]) -> str:
    label = str(payload.get("label") or "property")
    unit = payload.get("unit")
    return f"{label} ({unit})" if unit else label


def _contrast_text(background: Any) -> tuple[int, int, int, int]:
    red, green, blue = (float(value) for value in background[:3])
    return (20, 20, 20, 255) if red + green + blue > 1.5 else (245, 245, 245, 255)


__all__ = [
    "add_plotly_colorbar",
    "colorbar_metadata",
    "draw_matplotlib_colorbar",
    "draw_raster_colorbar",
    "plotly_colorscale",
    "plotly_colorbar_trace",
]
