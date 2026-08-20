# ruff: noqa: F401,F403 - legacy compatibility facade
from __future__ import annotations

from ..render.api import FigureResult, render
from ..render.compass import (
    _COMPASS_ITEM_NAME,
    axis_key_overlay,
    compose_axis_key_layout,
)
from ..render.figures import build_figure, build_publication_figure, build_row_figure
from ..render.publication import build_static_publication_figure
from ..render.publication import DENSE_COORDINATION_PRESET

# Re-export everything that render/scene_traces exported, so
# ``from mat_viewer.renderer import *`` still works.
from ..render.scene_traces import *
from ..render.style import style_from_controls, validate_style_schema
from ..render.topology import topology_histogram_figure, topology_results_markdown
from ..render.viewport import ViewportAccumulator, uniform_viewport
from ..render.overlay.vectors import (
    normalize_vector_overlays,
    paper_vector_label_annotations,
    resolve_vector_overlays,
    vector_mesh_traces,
    vector_overlay_bounds,
)
from ..render.viewport import uniform_viewport

__all__ = [name for name in globals() if not name.startswith("__")]
