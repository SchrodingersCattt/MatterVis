"""Camera-projected paper-coord arrow indicators for Plotly 3D scenes.

Generic primitives for placing 2D arrow annotations on a Plotly figure that
correspond to 3D vectors projected through the active camera. Use cases
include lattice triads, k-paths, dipole moments, force/displacement
vectors, magnetic moments, or any indicator that should not collide with
the 3D scene contents.

The module is split into four layers so each is independently reusable:

- :func:`camera_screen_basis` (Layer 1) — pure camera math; returns the
  screen-right and screen-up unit vectors in data space.
- :func:`project_to_screen` (Layer 2) — project arbitrary ``(N, 3)``
  vectors onto the camera screen plane.
- :func:`paper_arrow_annotations` (Layer 3) — render 2D arrow + label
  annotations at a paper-coords anchor; agnostic of the direction source.
- :func:`lattice_compass_annotations` (Layer 4) — convenience wrapper for
  the most common case (three crystal axes with text labels).

All visual parameters (colors, sizes, fonts, label offsets, arrow widths,
anchors) are exposed as keyword arguments. The library does not bake in
journal- or project-specific styling; supply your own palette/typography
when calling these functions.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


__all__ = [
    "camera_screen_basis",
    "project_to_screen",
    "paper_arrow_annotations",
    "lattice_compass_annotations",
]


# Wong (2011) colorblind-safe palette. Used as the default for Layer 4 only;
# every Layer-4 caller can override via ``colors=``.
_WONG_COLORBLIND = ("#0072B2", "#009E73", "#CC79A7")


def camera_screen_basis(camera: dict) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(right, up)`` unit vectors in data space for a Plotly camera.

    The two vectors form an orthonormal pair on the camera image plane:
    ``right`` increases left-to-right on screen, ``up`` increases
    bottom-to-top. Works for both ``perspective`` and ``orthographic``
    projections (only the view direction matters for the basis).

    Parameters
    ----------
    camera
        A Plotly 3D-scene camera dict with ``eye``, ``center``, ``up``
        sub-dicts (each containing ``x``, ``y``, ``z`` floats).

    Raises
    ------
    ValueError
        If ``camera.eye`` coincides with ``camera.center`` or ``camera.up``
        is parallel to the view direction (degenerate camera).
    """
    eye = np.array([camera["eye"]["x"], camera["eye"]["y"], camera["eye"]["z"]],
                   dtype=float)
    center = np.array([camera["center"]["x"], camera["center"]["y"],
                       camera["center"]["z"]], dtype=float)
    up = np.array([camera["up"]["x"], camera["up"]["y"], camera["up"]["z"]],
                  dtype=float)

    view = center - eye
    n = float(np.linalg.norm(view))
    if n < 1e-12:
        raise ValueError("camera.eye coincides with camera.center")
    view /= n

    right = np.cross(view, up)
    rn = float(np.linalg.norm(right))
    if rn < 1e-12:
        raise ValueError("camera.up is parallel to the view direction")
    right /= rn

    screen_up = np.cross(right, view)
    screen_up /= float(np.linalg.norm(screen_up))
    return right, screen_up


def project_to_screen(camera: dict, vectors: np.ndarray) -> np.ndarray:
    """Project ``(N, 3)`` data-space vectors onto the camera screen plane.

    Returns an ``(N, 2)`` array of ``(right, up)`` screen components in the
    same units as the input (typically Å). The result preserves relative
    lengths so callers can scale it to whatever pixel/paper magnitude they
    want without losing direction information.
    """
    arr = np.asarray(vectors, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 3:
        raise ValueError(f"vectors must have shape (N, 3); got {arr.shape}")
    right, screen_up = camera_screen_basis(camera)
    return np.stack([arr @ right, arr @ screen_up], axis=1)


def paper_arrow_annotations(
    anchor_xy: tuple[float, float],
    deltas_2d: np.ndarray,
    *,
    fig_size: tuple[float, float],
    labels: Sequence[str] | None = None,
    colors: Sequence[str] | None = None,
    pixel_length: float = 50.0,
    arrow_width: float = 3.0,
    arrow_head: int = 3,
    label_pixel_offset: float = 8.0,
    font_size: int = 14,
    font_family: str = "Arial, Helvetica, sans-serif",
    bold_labels: bool = True,
) -> list[dict]:
    """Render arrow + label annotations from a paper-coords anchor.

    The function is agnostic of where ``deltas_2d`` came from — supply
    screen-projected lattice vectors, BZ axes, dipole moments, force
    vectors, or any other 2D directions. All arrows share the same
    ``anchor_xy`` tail; the longest delta is normalised to
    ``pixel_length`` and the rest are scaled proportionally so relative
    magnitudes are preserved.

    Parameters
    ----------
    anchor_xy
        Tail position in Plotly paper coordinates (``[0, 1]`` on each axis).
    deltas_2d
        ``(N, 2)`` screen-plane direction vectors in any unit.
    fig_size
        ``(width_px, height_px)`` of the final figure. Required because
        Plotly arrow tail offsets are specified in pixels (``axref="pixel"``)
        while the head sits in paper coords; matching the two requires
        knowing the figure pixel dimensions.
    labels
        Optional length-N text labels (one per arrow). ``None`` skips
        labels entirely.
    colors
        Optional length-N hex/CSS colors. Falls back to a single grey
        if not provided.
    pixel_length
        Pixel length of the LONGEST arrow; others are scaled proportionally.
    arrow_width
        Stroke width of arrow shafts.
    arrow_head
        Plotly arrowhead shape index (0..8). 3 is a small filled triangle.
    label_pixel_offset
        Pixel distance from arrow tip to its label centre, along the arrow
        direction.
    font_size, font_family, bold_labels
        Label typography overrides.
    """
    deltas = np.asarray(deltas_2d, dtype=float)
    if deltas.ndim != 2 or deltas.shape[1] != 2:
        raise ValueError(f"deltas_2d must have shape (N, 2); got {deltas.shape}")
    n = deltas.shape[0]
    if labels is not None and len(labels) != n:
        raise ValueError(f"labels length {len(labels)} != N={n}")
    if colors is not None and len(colors) != n:
        raise ValueError(f"colors length {len(colors)} != N={n}")

    fig_w, fig_h = float(fig_size[0]), float(fig_size[1])
    if fig_w <= 0 or fig_h <= 0:
        raise ValueError(f"fig_size must be positive; got {fig_size}")

    lengths = np.linalg.norm(deltas, axis=1)
    max_len = float(lengths.max()) if lengths.size else 0.0
    if max_len < 1e-12:
        return []
    scale_px = float(pixel_length) / max_len
    cx, cy = float(anchor_xy[0]), float(anchor_xy[1])

    annotations: list[dict] = []
    for i in range(n):
        dx_px = float(deltas[i, 0] * scale_px)
        dy_px = float(deltas[i, 1] * scale_px)
        tip_x = cx + dx_px / fig_w
        tip_y = cy + dy_px / fig_h
        color = colors[i] if colors is not None else "#444444"

        # Arrow: head in paper coords, tail offset in pixels.
        # Plotly's pixel y points DOWN, so flip sign on the y offset.
        annotations.append(dict(
            x=tip_x, y=tip_y,
            ax=-dx_px, ay=dy_px,
            xref="paper", yref="paper",
            axref="pixel", ayref="pixel",
            showarrow=True,
            arrowhead=int(arrow_head),
            arrowsize=1.0,
            arrowwidth=float(arrow_width),
            arrowcolor=color,
            text="",
            standoff=0.0,
            startstandoff=0.0,
        ))

        if labels is not None:
            length_px = float(np.hypot(dx_px, dy_px))
            if length_px > 1e-9:
                ux = dx_px / length_px
                uy = dy_px / length_px
            else:
                ux, uy = 0.0, 1.0
            lx = tip_x + ux * label_pixel_offset / fig_w
            ly = tip_y + uy * label_pixel_offset / fig_h
            text = f"<b>{labels[i]}</b>" if bold_labels else str(labels[i])
            annotations.append(dict(
                x=lx, y=ly,
                xref="paper", yref="paper",
                text=text,
                showarrow=False,
                font=dict(family=font_family, size=int(font_size), color=color),
                xanchor="center", yanchor="middle",
            ))
    return annotations


def lattice_compass_annotations(
    camera: dict,
    lattice: np.ndarray,
    *,
    panel_x_domains: Sequence[tuple[float, float]],
    fig_size: tuple[float, float],
    anchor_in_panel: tuple[float, float] = (0.06, 0.10),
    labels: Sequence[str] = ("a", "b", "c"),
    colors: Sequence[str] = _WONG_COLORBLIND,
    pixel_length: float = 50.0,
    arrow_width: float = 3.0,
    label_pixel_offset: float = 8.0,
    font_size: int = 14,
    font_family: str = "Arial, Helvetica, sans-serif",
) -> list[dict]:
    """Convenience wrapper: paper-coord lattice compass on each panel.

    Combines :func:`project_to_screen` and :func:`paper_arrow_annotations`
    for the common case of crystal/cell basis vectors. The defaults
    (``a/b/c`` labels, Wong colorblind-safe palette) are conventional for
    crystallography but **every** styling parameter is overridable; the
    same wrapper trivially renders ``x/y/z`` axes, primitive lattice axes
    of a sub-cell, or any other named triplet by passing different
    ``labels`` and ``colors``.

    Parameters
    ----------
    camera
        Plotly camera dict, shared with the 3D scene.
    lattice
        ``(3, 3)`` matrix whose ROWS are the basis vectors in data space
        (typically Å). Generalises trivially: pass ``(N, 3)`` for N
        non-triplet vectors and supply matching ``labels``/``colors``.
    panel_x_domains
        Per-panel ``(x0, x1)`` paper-coord domains. The compass is repeated
        on each panel anchored relative to that panel's domain.
    fig_size
        Figure size in pixels.
    anchor_in_panel
        Paper anchor inside each panel: ``x`` is fractional within
        ``(x0, x1)``, ``y`` is absolute paper-y. Default is lower-left.
    labels, colors, pixel_length, arrow_width, label_pixel_offset,
    font_size, font_family
        Forwarded to :func:`paper_arrow_annotations`.
    """
    arr = np.asarray(lattice, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 3:
        raise ValueError(f"lattice must have shape (N, 3); got {arr.shape}")
    n_vec = arr.shape[0]
    if len(labels) != n_vec:
        raise ValueError(f"labels length {len(labels)} != N={n_vec}")
    if len(colors) != n_vec:
        raise ValueError(f"colors length {len(colors)} != N={n_vec}")

    deltas = project_to_screen(camera, arr)

    annotations: list[dict] = []
    for x0, x1 in panel_x_domains:
        anchor = (x0 + (x1 - x0) * float(anchor_in_panel[0]),
                  float(anchor_in_panel[1]))
        annotations.extend(paper_arrow_annotations(
            anchor_xy=anchor,
            deltas_2d=deltas,
            fig_size=fig_size,
            labels=list(labels),
            colors=list(colors),
            pixel_length=pixel_length,
            arrow_width=arrow_width,
            label_pixel_offset=label_pixel_offset,
            font_size=font_size,
            font_family=font_family,
        ))
    return annotations
