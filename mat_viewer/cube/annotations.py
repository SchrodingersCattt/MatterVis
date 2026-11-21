"""Plotly annotation traces for a cube cell.

Cell edges, lattice-direction arrows, and the phase legend are display
overlays. They are separate from the shared isosurface shading record.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import plotly.graph_objects as go


def _plotly_go():
    try:
        import plotly.graph_objects as go
    except ImportError as exc:
        raise ImportError(
            "Plotly cube traces require `pip install matter-vis[plotly]`. "
            "CPU cube rendering requires only `matter-vis[cube]`."
        ) from exc
    return go


def cell_box_trace(
    lattice: np.ndarray,
    origin: np.ndarray | None = None,
    *,
    color: str = "#444444",
    width: float = 3.0,
) -> go.Scatter3d:
    """Return a single Scatter3d trace tracing the 12 edges of a parallelepiped."""
    if origin is None:
        origin = np.zeros(3)
    a, b, c = lattice[0], lattice[1], lattice[2]
    o = np.asarray(origin, dtype=float)
    corners = np.array(
        [o, o + a, o + a + b, o + b, o + c, o + a + c, o + a + b + c, o + b + c]
    )
    edges = [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 0),
        (4, 5),
        (5, 6),
        (6, 7),
        (7, 4),
        (0, 4),
        (1, 5),
        (2, 6),
        (3, 7),
    ]
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for i, j in edges:
        xs.extend([corners[i, 0], corners[j, 0], None])
        ys.extend([corners[i, 1], corners[j, 1], None])
        zs.extend([corners[i, 2], corners[j, 2], None])
    return _plotly_go().Scatter3d(
        x=xs,
        y=ys,
        z=zs,
        mode="lines",
        line=dict(color=color, width=width),
        hoverinfo="skip",
        showlegend=False,
        name="cell",
    )


def sign_legend_annotations(
    *,
    positive_color: str = "#D55E00",
    negative_color: str = "#0072B2",
    x: float = 0.99,
    y: float = 0.99,
    fontsize: int = 14,
    spacing: float = 0.035,
) -> list[dict]:
    """Two-line +/- sign legend for orbital phase (paper-coord annotations)."""
    return [
        dict(
            xref="paper",
            yref="paper",
            x=x,
            y=y,
            xanchor="right",
            yanchor="top",
            text=f"<b><span style='color:{positive_color}'>\u25a0</span> +</b>",
            showarrow=False,
            font=dict(
                family="Arial, Helvetica, sans-serif", size=fontsize, color="#000"
            ),
        ),
        dict(
            xref="paper",
            yref="paper",
            x=x,
            y=y - spacing,
            xanchor="right",
            yanchor="top",
            text=f"<b><span style='color:{negative_color}'>\u25a0</span> \u2212</b>",
            showarrow=False,
            font=dict(
                family="Arial, Helvetica, sans-serif", size=fontsize, color="#000"
            ),
        ),
    ]


def axis_indicator_traces(
    lattice: np.ndarray,
    origin: np.ndarray | None = None,
    *,
    length: float = 2.5,
    width: float = 6.0,
    label_offset: float = 0.6,
    colors: tuple[str, str, str] = ("#D62728", "#2CA02C", "#1F77B4"),
    labels: tuple[str, str, str] = ("a", "b", "c"),
) -> list[go.Scatter3d]:
    """Three short ``a/b/c`` arrows + labels anchored at ``origin``.

    Lengths are normalised so the indicator is the same physical size in
    Angstrom along each axis, regardless of the lattice anisotropy.
    """
    if origin is None:
        origin = np.zeros(3)
    o = np.asarray(origin, dtype=float)
    traces: list[go.Scatter3d] = []
    for vec, color, label in zip(lattice, colors, labels):
        n = float(np.linalg.norm(vec))
        if n < 1e-9:
            continue
        end = o + (vec / n) * length
        traces.append(
            _plotly_go().Scatter3d(
                x=[o[0], end[0]],
                y=[o[1], end[1]],
                z=[o[2], end[2]],
                mode="lines",
                line=dict(color=color, width=width),
                hoverinfo="skip",
                showlegend=False,
                name=f"axis-{label}",
            )
        )
        tip = o + (vec / n) * (length + label_offset)
        traces.append(
            _plotly_go().Scatter3d(
                x=[tip[0]],
                y=[tip[1]],
                z=[tip[2]],
                mode="text",
                text=[f"<b>{label}</b>"],
                textfont=dict(
                    color=color, size=18, family="Arial, Helvetica, sans-serif"
                ),
                hoverinfo="skip",
                showlegend=False,
                name=f"label-{label}",
            )
        )
    return traces


__all__ = [
    "axis_indicator_traces",
    "cell_box_trace",
    "sign_legend_annotations",
]
