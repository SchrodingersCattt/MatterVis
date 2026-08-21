"""Automatic viewpoint selection for crystal structure visualization.

This module scores candidate camera directions by projecting atoms onto
the screen plane and evaluating occlusion, organic-plane coverage, depth
spread, cluster separation, and other heuristics.

Public API
----------
auto_view_dir
    Pick the best viewing direction for a crystal. Supports custom scoring
    weights via the *weights* kwarg or through pre-registered profiles.
register_view_score_weights
    Register a new named weight profile (or override an existing one).
list_view_score_weights
    List all available weight profile names.
VIEW_SCORE_WEIGHTS
    Built-in weight profiles (default, MPEP, HPEP).
"""
from __future__ import annotations

from .core import (
    VIEW_SCORE_WEIGHTS,
    auto_view_dir,
    list_view_score_weights,
    register_view_score_weights,
)

__all__ = [
    "VIEW_SCORE_WEIGHTS",
    "auto_view_dir",
    "list_view_score_weights",
    "register_view_score_weights",
]
