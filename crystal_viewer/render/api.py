"""Unified rendering API.

Single entry point for all rendering backends (Plotly 3D, Matplotlib 2D ORTEP).
Callers use ``render(scene, style).save(path)`` without knowing which backend runs.
"""
from __future__ import annotations

from typing import Any


class FigureResult:
    """Unified wrapper around a Plotly or Matplotlib figure."""

    def __init__(self, *, plotly_fig: Any = None, mpl_fig: Any = None):
        self._plotly = plotly_fig
        self._mpl = mpl_fig

    def save(self, path: str, *, width: int = 800, height: int = 700, dpi: int = 300, scale: int = 2):
        """Save the figure to a file (PNG, PDF, SVG, etc.)."""
        if self._mpl is not None:
            self._mpl.savefig(path, dpi=dpi, bbox_inches="tight")
            import matplotlib.pyplot as plt
            plt.close(self._mpl)
        elif self._plotly is not None:
            self._plotly.write_image(path, width=width, height=height, scale=scale)
        else:
            raise RuntimeError("FigureResult has no figure")

    def to_plotly(self):
        """Return the Plotly figure (for Dash web frontend)."""
        if self._plotly is None:
            raise TypeError("This render result is matplotlib-only (flat+ortep). Use .save() for export.")
        return self._plotly

    @property
    def plotly_figure(self):
        return self._plotly

    @property
    def mpl_figure(self):
        return self._mpl


def render(scene: dict, style: dict, *, force_quality: bool = True, **kwargs) -> FigureResult:
    """Render a scene. Dispatches to the correct backend based on style.

    Parameters
    ----------
    scene : dict
        MatterVis scene dict (from build_bundle_scene or build_scene_from_cif).
    style : dict
        Style dict. Key fields: material, style, disorder, ortep_probability, etc.
    force_quality : bool
        When True (default for scripts/CLI), bypass atom-count fast-rendering
        fallback. Set False for interactive web use.

    Returns
    -------
    FigureResult
        Call .save(path) to write PNG/PDF, or .to_plotly() for Dash.
    """
    from ..scene.style import scene_style as _scene_style
    from .style.core import validate_style_schema

    # Merge user overrides with DEFAULT_STYLE so all keys exist.
    full_style = _scene_style(scene, style)
    full_style = validate_style_schema(full_style)

    if full_style.get("material") == "flat" and full_style.get("style") == "ortep":
        from ..ortep.flat_render import render_ortep_flat
        fig = render_ortep_flat(scene, full_style)
        return FigureResult(mpl_fig=fig)

    from .figures import build_figure
    fig = build_figure(scene, full_style, force_quality=force_quality, **kwargs)
    return FigureResult(plotly_fig=fig)
