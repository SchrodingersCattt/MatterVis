"""Pure checks and copies for terminal view state.

These stay outside :class:`TerminalViewController` so the state owner stays
under the module line cap. They do not touch crystal chemistry.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from ..math.camera import Camera, ProjectionMode
from .annotations import normalize_label_mode
from .compositor import DISPLAY_LEVELS
from .projection import Viewport


def copy_camera(camera: Camera) -> Camera:
    basis = None if camera.basis is None else camera.basis.copy()
    return replace(camera, target=camera.target.copy(), basis=basis)


def copy_viewport(viewport: Viewport) -> Viewport:
    return Viewport(
        x_min=float(viewport.x_min),
        x_max=float(viewport.x_max),
        y_min=float(viewport.y_min),
        y_max=float(viewport.y_max),
        scale=float(viewport.scale),
        width=int(viewport.width),
        height=int(viewport.height),
    )


def validate_snapshot_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("snapshot name must be a non-empty string")
    return name.strip()


def validate_dimensions(width: int, height: int) -> tuple[int, int]:
    if isinstance(width, bool) or isinstance(height, bool):
        raise TypeError("viewport dimensions must be integers")
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        raise ValueError("viewport dimensions must be greater than zero")
    return width, height


def validate_finite(name: str, *values: float) -> None:
    if not all(np.isfinite(value) for value in values):
        raise ValueError(f"{name} values must be finite")


def validate_label_mode(label_mode: str) -> str:
    """Accept aliases via :func:`normalize_label_mode`, then return the canonical mode."""
    return normalize_label_mode(label_mode)


def validate_display_level(display_level: str) -> str:
    if display_level not in DISPLAY_LEVELS:
        raise ValueError(f"display_level must be one of {DISPLAY_LEVELS}")
    return display_level


def validate_projection(projection: str | ProjectionMode) -> ProjectionMode:
    if isinstance(projection, ProjectionMode):
        return projection
    try:
        return ProjectionMode(str(projection))
    except ValueError as exc:
        raise ValueError(
            "projection must be 'orthographic' or 'perspective'"
        ) from exc


def with_zoom(camera: Camera, zoom: float) -> Camera:
    if not np.isfinite(zoom) or zoom <= 0:
        raise ValueError("zoom must be greater than zero")
    return replace(camera, viewport_zoom=float(np.clip(zoom, 0.5, 20.0)))
