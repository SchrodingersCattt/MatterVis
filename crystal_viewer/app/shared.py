from __future__ import annotations
# ruff: noqa: F401,F403,F405

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Any, Dict, Iterable, Optional

import numpy as np
import plotly.io as pio
from molcrys_kit.utils.geometry import minimum_image_distance

try:
    from dash import ALL, Dash, Input, Output, Patch, State, callback_context, dcc, html, no_update
except ImportError as exc:  # pragma: no cover - user-facing fallback
    raise SystemExit(
        "Dash is required for the browser viewer. "
        "Install it with `python -m pip install dash`."
    ) from exc

from .. import perf_log
from ..loader import LoadedCrystal, build_bundle_scene, build_empty_bundle, build_loaded_crystal, load_uploaded_cif
from ..presets import (
    DEFAULT_CATALOG,
    DEFAULT_STYLE,
    LOCAL_STATE_DIRNAME,
    default_preset,
    default_preset_path,
    get_default_catalog,
    load_preset,
    save_preset,
    workspace_root,
)
from ..renderer import build_figure, compose_axis_key_layout, style_from_controls, topology_histogram_figure, topology_results_markdown
from ..render.viewport import _scene_ranges, figure_axis_layout
from ..scene import scene_json
from ..scenes import Scene, SceneStore
from ..topology import DEFAULT_CENTROID_OFFSET_FRAC, analyze_topology, extract_coordination_shell

PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_DIR = workspace_root(PACKAGE_DIR)
DEFAULT_PRESET_PATH = default_preset_path(WORKSPACE_DIR)
STATIC_PUBLICATION_MODULE = "crystal_viewer.static_publication.plot_crystal"
LEGACY_EXPORT_MODULE = STATIC_PUBLICATION_MODULE
PLACEHOLDER_STRUCTURE = "__upload__"
_POLY_SHELL_MODE_ENCLOSURE = "gap_enclosure"
_POLY_SHELL_MODE_GAP = "gap"


class ApiError(RuntimeError):
    """Exception with an HTTP status that REST handlers can surface."""

    status_code = 400

    def __init__(self, message: str, *, hint: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.hint = hint
        if status_code is not None:
            self.status_code = int(status_code)


class TopologyUnavailable(ApiError):
    status_code = 409

__all__ = [name for name in globals() if not name.startswith("__")]
