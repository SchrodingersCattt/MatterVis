"""ORTEP facade with lazy Plotly and Matplotlib adapters."""

from __future__ import annotations

from importlib import import_module

_FLAT_EXPORTS = {"render_ortep_flat"}


def __getattr__(name: str):
    module_name = ".flat_render" if name in _FLAT_EXPORTS else ".core"
    module = import_module(module_name, __name__)
    try:
        value = getattr(module, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc
    globals()[name] = value
    return value


__all__ = [
    "build_ortep_panel_figure",
    "ellipsoid_principal_axes",
    "ortep_atom_billboard_traces",
    "ortep_atom_fill_traces",
    "ortep_atom_mesh_traces",
    "ortep_axis_dash_traces",
    "ortep_billboard_polygon",
    "ortep_mesh3d",
    "ortep_octant_hatch_traces",
    "ortep_octant_shade_traces",
    "ortep_octant_shading",
    "ortep_principal_axis_segments",
    "ortep_silhouette_outline_traces",
    "render_ortep_flat",
]
