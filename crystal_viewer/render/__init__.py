# ruff: noqa: F401,F403 - public render facade
from __future__ import annotations

from .figures import build_figure, build_publication_figure, build_row_figure
from .publication import build_static_publication_figure
from .publication import DENSE_COORDINATION_PRESET
from .scene_traces import *  # noqa: F401,F403
from .overlay.vectors import (
	normalize_vector_overlays,
	paper_vector_label_annotations,
	resolve_vector_overlays,
	vector_mesh_traces,
	vector_overlay_bounds,
)
