"""Cube IO and canonical rendering bridges with lazy optional frontends.

Use :mod:`mat_viewer.agent` for CPU rendering or :func:`build_cube_figure`
for the optional Plotly frontend.  The removed standalone orbital builders
inferred bonds from Euclidean distances and therefore were not a canonical
chemical-structure API.
"""

from __future__ import annotations

from .io import (
    CubeAtom,
    CubeData,
    cube_grid,
    default_isovalue,
    read_cube,
    tile_cube,
    tile_cube_data,
)

_CORE_EXPORTS = {
    "atom_sphere_traces",
    "axis_indicator_traces",
    "cell_box_trace",
    "cube_atom_trace",
    "export_static",
    "mask_to_atoms",
    "orbital_isosurface_traces",
    "orbital_mesh_traces",
}
_BRIDGE_EXPORTS = {
    "build_cube_figure",
    "cube_lattice_matrix",
    "cube_to_cell",
    "cube_to_raw_atoms",
}


def __getattr__(name: str):
    if name in _CORE_EXPORTS:
        from . import core

        return getattr(core, name)
    if name in _BRIDGE_EXPORTS:
        from . import bridge

        return getattr(bridge, name)
    raise AttributeError(name)


__all__ = [
    "CubeAtom",
    "CubeData",
    "atom_sphere_traces",
    "axis_indicator_traces",
    "build_cube_figure",
    "cell_box_trace",
    "cube_atom_trace",
    "cube_grid",
    "cube_lattice_matrix",
    "cube_to_cell",
    "cube_to_raw_atoms",
    "default_isovalue",
    "export_static",
    "mask_to_atoms",
    "orbital_isosurface_traces",
    "orbital_mesh_traces",
    "read_cube",
    "tile_cube",
    "tile_cube_data",
]
