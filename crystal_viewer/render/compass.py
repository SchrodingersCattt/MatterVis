from __future__ import annotations

import math

import numpy as np

from .viewport import _axis_cube_scale, _camera_axis_projections


# Plotly preserves free-form ``name`` on annotations/shapes; per-item
# ``meta`` is only a template-string slot.
_COMPASS_ITEM_NAME = "mv_compass"


def axis_key_overlay(scene: dict, style: dict) -> tuple[list[dict], list[dict]]:
    """Build Plotly paper-coord annotations + shapes for a corner compass.

    When ``style["axis_key_via_svg_overlay"]`` is truthy the function
    short-circuits to ``([], [])``. The interactive Dash app sets this
    flag so the compass is rendered live by ``compass_overlay.js``
    into a sibling SVG layer instead of baked into the Plotly layout
    every frame -- baking forces ``Plotly.relayout`` calls that
    interrupt gl3d's render cycle and freeze rotation drags
    (plotly/plotly.js#6359 in v3). Static export pipelines
    (``cube.export_static``, ``scripts/``) leave the flag unset and
    keep the baked compass for kaleido.
    """
    show_axes = bool(style.get("show_axes", False))
    show_axis_key = bool(style.get("show_axis_key", False))
    if not (show_axes or show_axis_key):
        return [], []
    if style.get("axis_key_via_svg_overlay"):
        return [], []
    projections = _camera_axis_projections(scene, style) or scene.get("projected_axes")
    if not projections or len(projections) < 3:
        return [], []

    axes_labels = list(style.get("axes_labels") or scene.get("axis_labels") or ["a", "b", "c"])[:3]
    if len(axes_labels) < 3:
        return [], []
    label_to_proj = {axes_labels[i]: projections[i] for i in range(3)}

    order = list(style.get("axis_key_label_order") or list(axes_labels))
    order = [label for label in order if label in label_to_proj]
    for label in axes_labels:
        if label not in order:
            order.append(label)
    if not order:
        return [], []

    anchor = style.get("axis_key_anchor") or [0.08, 0.12]
    anchor_x = float(anchor[0])
    anchor_y = float(anchor[1])
    fig_w = float(style.get("axis_key_fig_width", 1024.0))
    fig_h = float(style.get("axis_key_fig_height", 720.0))

    if show_axes and not show_axis_key:
        pixel_length = max(20.0, float(style.get("axis_scale", 0.14)) * 360.0)
    else:
        pixel_length = float(style.get("axis_key_pixel_length", 50.0))

    line_width = float(style.get("axis_key_line_width", 2.0))
    arrowhead = int(style.get("axis_key_arrow_head", 3))
    label_pixel_offset = float(style.get("axis_key_label_pixel_offset", 10.0))
    font_size = float(style.get("axis_key_font_size", 14))
    italic = bool(style.get("axis_key_italic", True))
    color_default = style.get("axis_key_color", "#2F2F2F")
    palette = style.get("axis_key_colors")
    if isinstance(palette, (list, tuple)) and len(palette) >= 3:
        colors = {axes_labels[i]: str(palette[i]) for i in range(3)}
    else:
        colors = {label: color_default for label in axes_labels}

    deltas = {label: tuple(map(float, label_to_proj[label])) for label in order}
    norms = {label: math.hypot(*deltas[label]) for label in order}
    max_norm = max(norms.values()) if norms else 0.0
    if max_norm < 1e-8:
        return [], []

    edge_margin_px = 24.0
    avail_left = max(anchor_x * fig_w - edge_margin_px, 1.0)
    avail_right = max((1.0 - anchor_x) * fig_w - edge_margin_px, 1.0)
    avail_down = max(anchor_y * fig_h - edge_margin_px, 1.0)
    avail_up = max((1.0 - anchor_y) * fig_h - edge_margin_px, 1.0)
    cap = pixel_length
    for label in order:
        dx_world, dy_world = deltas[label]
        n = norms[label]
        if n < 1e-12:
            continue
        ux = dx_world / n
        uy = dy_world / n
        rel = n / max_norm
        wanted_px = (pixel_length + label_pixel_offset + edge_margin_px) * rel
        if wanted_px <= 0:
            continue
        if ux > 0:
            allowed_px = avail_right / ux
        elif ux < 0:
            allowed_px = avail_left / -ux
        else:
            allowed_px = float("inf")
        if uy > 0:
            allowed_px = min(allowed_px, avail_up / uy)
        elif uy < 0:
            allowed_px = min(allowed_px, avail_down / -uy)
        if allowed_px < wanted_px:
            cap = min(cap, pixel_length * (allowed_px / wanted_px))
    pixel_length = max(cap, 12.0)
    scale_px = pixel_length / max_norm

    dot_threshold = float(style.get("axis_key_dot_threshold", 0.05))
    dot_radius = float(style.get("axis_key_dot_radius_px", 4.0))

    annotations: list[dict] = []
    shapes: list[dict] = []
    for label in order:
        dx_world, dy_world = deltas[label]
        norm = norms[label]
        color = colors.get(label, color_default)
        text = f"<i>{label}</i>" if italic else label
        rel = norm / max_norm if max_norm > 0 else 0.0

        if rel < dot_threshold:
            r_paper_x = dot_radius / fig_w
            r_paper_y = dot_radius / fig_h
            shapes.append(dict(
                type="circle",
                xref="paper",
                yref="paper",
                x0=anchor_x - r_paper_x,
                x1=anchor_x + r_paper_x,
                y0=anchor_y - r_paper_y,
                y1=anchor_y + r_paper_y,
                fillcolor=color,
                line=dict(color=color, width=0),
                layer="above",
                name=_COMPASS_ITEM_NAME,
            ))
            offset_paper = dot_radius + label_pixel_offset
            annotations.append(dict(
                x=anchor_x + offset_paper / fig_w,
                y=anchor_y + offset_paper / fig_h,
                xref="paper",
                yref="paper",
                text=text,
                showarrow=False,
                xanchor="left",
                yanchor="bottom",
                font=dict(size=font_size, color=color),
                name=_COMPASS_ITEM_NAME,
            ))
            continue

        dx_px = dx_world * scale_px
        dy_px = dy_world * scale_px
        tip_x = anchor_x + dx_px / fig_w
        tip_y = anchor_y + dy_px / fig_h

        annotations.append(dict(
            x=tip_x,
            y=tip_y,
            ax=-dx_px,
            ay=dy_px,
            xref="paper",
            yref="paper",
            axref="pixel",
            ayref="pixel",
            showarrow=True,
            arrowhead=arrowhead,
            arrowsize=1.0,
            arrowwidth=line_width,
            arrowcolor=color,
            text="",
            standoff=0.0,
            startstandoff=0.0,
            name=_COMPASS_ITEM_NAME,
        ))

        length_px = float(math.hypot(dx_px, dy_px))
        ux = dx_px / length_px
        uy = dy_px / length_px
        annotations.append(dict(
            x=tip_x + ux * label_pixel_offset / fig_w,
            y=tip_y + uy * label_pixel_offset / fig_h,
            xref="paper",
            yref="paper",
            text=text,
            showarrow=False,
            xanchor="center",
            yanchor="middle",
            font=dict(size=font_size, color=color),
            name=_COMPASS_ITEM_NAME,
        ))

    return annotations, shapes


def compass_clientside_context(scene: dict, style: dict) -> dict | None:
    """Serialise the inputs the clientside callback needs for reprojection."""
    show_axes = bool(style.get("show_axes", False))
    show_axis_key = bool(style.get("show_axis_key", False))
    if not (show_axes or show_axis_key):
        return None
    M = np.asarray(scene.get("M"), dtype=float) if scene.get("M") is not None else None
    if M is None or M.ndim != 2 or M.shape != (3, 3):
        return None
    axes_labels = list(style.get("axes_labels") or scene.get("axis_labels") or ["a", "b", "c"])[:3]
    if len(axes_labels) < 3:
        return None
    palette = style.get("axis_key_colors")
    color_default = style.get("axis_key_color", "#2F2F2F")
    if isinstance(palette, (list, tuple)) and len(palette) >= 3:
        colors = [str(palette[i]) for i in range(3)]
    else:
        colors = [color_default, color_default, color_default]
    anchor = style.get("axis_key_anchor") or [0.08, 0.12]
    if show_axes and not show_axis_key:
        pixel_length = max(20.0, float(style.get("axis_scale", 0.14)) * 360.0)
    else:
        pixel_length = float(style.get("axis_key_pixel_length", 50.0))
    cube_scale = _axis_cube_scale(scene, style)
    cube_scale_payload = (
        [float(cube_scale[0]), float(cube_scale[1]), float(cube_scale[2])]
        if cube_scale is not None
        else None
    )
    return {
        "M": [[float(M[i, j]) for j in range(3)] for i in range(3)],
        "cube_scale": cube_scale_payload,
        "labels": list(axes_labels),
        "colors": colors,
        "anchor": [float(anchor[0]), float(anchor[1])],
        "pixel_length": float(pixel_length),
        "line_width": float(style.get("axis_key_line_width", 2.0)),
        "arrowhead": int(style.get("axis_key_arrow_head", 3)),
        "label_pixel_offset": float(style.get("axis_key_label_pixel_offset", 10.0)),
        "font_size": float(style.get("axis_key_font_size", 14)),
        "italic": bool(style.get("axis_key_italic", True)),
        "dot_threshold": float(style.get("axis_key_dot_threshold", 0.05)),
        "dot_radius_px": float(style.get("axis_key_dot_radius_px", 4.0)),
    }


def compose_axis_key_layout(scene: dict, style: dict) -> tuple[list[dict], list[dict]]:
    """Produce paper-coord compass layout plus optional ORTEP caption."""
    annotations, shapes = axis_key_overlay(scene, style)
    if (
        style.get("style") == "ortep"
        and scene.get("has_minor")
        and style.get("disorder") in {"outline_rings", "dashed_bonds"}
    ):
        annotations = list(annotations)
        annotations.append(dict(
            x=0.5,
            y=0.02,
            xref="paper",
            yref="paper",
            text="filled = major / outline = minor disorder",
            showarrow=False,
            xanchor="center",
            yanchor="bottom",
            font=dict(size=11, color="#666666"),
        ))
    return annotations, shapes
