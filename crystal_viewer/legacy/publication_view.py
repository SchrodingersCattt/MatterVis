"""Redirect — auto_view_dir has moved to crystal_viewer.viewpoint.

This shim exists only for backwards compatibility with any external
scripts that may import ``auto_view_dir`` from this legacy path. New
code should import from ``crystal_viewer.viewpoint`` directly.
"""
from __future__ import annotations

from ..viewpoint import auto_view_dir, VIEW_SCORE_WEIGHTS  # noqa: F401

__all__ = ["auto_view_dir", "VIEW_SCORE_WEIGHTS"]

