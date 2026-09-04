from __future__ import annotations

from ..render.api import render, FigureResult  # noqa: F401
from ..render.figures import build_figure, build_row_figure  # noqa: F401
from ..render.viewport import uniform_viewport  # noqa: F401
from ..render.compass import (
    _COMPASS_ITEM_NAME,  # noqa: F401
    axis_key_overlay,  # noqa: F401
    compose_axis_key_layout,  # noqa: F401
)
from ..render.style import style_from_controls, validate_style_schema  # noqa: F401
from ..render.topology import topology_histogram_figure, topology_results_markdown  # noqa: F401
from ..render.geometry import (  # noqa: F401
    cylinder_entity,
    geometry_entity_traces,
    implicit_entity,
    mesh_entity,
    through_cylinder_entity,
    validate_geometry_style,
)

# Re-export everything that render/scene_traces exported, so
# ``from crystal_viewer.renderer import *`` still works.
from ..render.scene_traces import *  # noqa: F401,F403

__all__ = [name for name in globals() if not name.startswith("__")]
