"""Static publication renderer package."""

from __future__ import annotations

from .figure import _orient_panel as _orient_panel
from .figure import build_static_publication_figure
from .geometry import filter_polyhedra_to_half_open_cell, in_half_open_cell
from .primitives import _normalise_sectors as _normalise_sectors
from .primitives import _split_hull_edges_by_facing as _split_hull_edges_by_facing
from .style import DENSE_COORDINATION_PRESET, publication_config

__all__ = [
    "DENSE_COORDINATION_PRESET",
    "build_static_publication_figure",
    "filter_polyhedra_to_half_open_cell",
    "in_half_open_cell",
    "publication_config",
]
