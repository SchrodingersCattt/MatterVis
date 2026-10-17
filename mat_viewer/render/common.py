from __future__ import annotations
# ruff: noqa: F401,F403,F405

from importlib import import_module
import math
from typing import Dict, Iterable, Tuple

import numpy as np

from .. import perf_log
from ..style.disorder import bond_effective_opacity, minor_opacity_for
from ..presets import ORTEP_MODES


class _LazyPlotly:
    """Resolve ``plotly.graph_objects`` only when a trace is requested.

    Most modules in this package import the shared ``common`` namespace with a
    wildcard.  Keeping the historical ``go`` symbol here lets those modules
    retain their compact trace builders while keeping backend-neutral and CPU
    imports free of the optional Plotly dependency.
    """

    def __getattr__(self, name: str):
        module = import_module("plotly.graph_objects")
        return getattr(module, name)


go = _LazyPlotly()

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
