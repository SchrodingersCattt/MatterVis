from __future__ import annotations
# ruff: noqa: F401,F403,F405

import math
from typing import Dict, Iterable, Tuple

import numpy as np
import plotly.graph_objects as go

from .. import perf_log
from ..style.disorder import bond_effective_opacity, minor_opacity_for
from ..presets import ORTEP_MODES

MATERIAL_DISPATCH = {"flat": "_scatter_atom_base", "mesh": "_mesh3d_atom_base"}
STYLE_DISPATCH = {
    "ball": "_sphere_geom",
    "ball_stick": "_sphere_geom",
    "stick": "_stick_only_geom",
    "ortep": "_ellipsoid_geom",
    "wireframe": "_ring_geom",
}
DISORDER_DISPATCH = {
    "opacity": "_disorder_alpha",
    "dashed_bonds": "_disorder_dash",
    "outline_rings": "_disorder_outline",
    "color_shift": "_disorder_color",
    "none": "_disorder_noop",
}

__all__ = [name for name in globals() if not name.startswith("__")]
