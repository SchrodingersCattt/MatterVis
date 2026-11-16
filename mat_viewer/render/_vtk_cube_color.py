"""Color mapping helpers for the optional VTK cube renderer."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ..cube.color_field import resolve_color_range

_DEFAULT_STOPS = (
    (0, 0, 255),
    (0, 128, 255),
    (0, 255, 0),
    (255, 255, 0),
    (255, 0, 0),
)

def _colormap(
    stops: Sequence[tuple[int, int, int]] | None,
    color_min: float,
    color_max: float,
    color_center: float | None = None,
) -> np.ndarray:
    """Return a 256-row RGB lookup table in 0..1.

    Stops are spaced evenly across the table. ``color_center`` is placed at
    the middle row; omit it for a linear map between ``color_min`` and
    ``color_max``.
    """

    control = np.asarray(_DEFAULT_STOPS if stops is None else list(stops), dtype=float)
    if control.ndim != 2 or control.shape[1] != 3 or len(control) < 2:
        raise ValueError("colormap_stops must contain at least 2 RGB triples")
    if np.any(~np.isfinite(control) | (control < 0.0) | (control > 255.0)):
        raise ValueError("colormap stops must be finite RGB values in 0..255")
    low = float(color_min)
    high = float(color_max)
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        raise ValueError("color range must be finite and increasing")
    fraction = np.linspace(0.0, 1.0, 256)
    if color_center is None:
        normalized = fraction
    else:
        center = float(color_center)
        if not np.isfinite(center) or not low < center < high:
            raise ValueError("color_center must lie strictly inside the color range")
        data = low + fraction * (high - low)
        normalized = np.empty_like(data)
        below = data <= center
        normalized[below] = 0.5 * (data[below] - low) / (center - low)
        normalized[~below] = 0.5 + 0.5 * (data[~below] - center) / (high - center)
    positions = np.linspace(0.0, 1.0, len(control))
    channels = [
        np.interp(normalized, positions, control[:, channel]) for channel in range(3)
    ]
    return np.column_stack(channels) / 255.0


def _color_limits(
    color: np.ndarray,
    color_min: float | None,
    color_max: float | None,
) -> tuple[float, float] | None:
    if (color_min is None) != (color_max is None):
        raise ValueError("color_min and color_max must both be set or both be omitted")
    supplied = None if color_min is None else (float(color_min), float(color_max))
    return resolve_color_range(color, supplied)


def _paint(samples, limits, color_center, stops):
    if limits is None:
        painted = np.full(samples.shape, 0.5, dtype=float)
        return painted, (0.0, 1.0), _colormap(stops, 0.0, 1.0, None)
    low, high = limits
    painted = np.clip(samples, low, high)
    return painted, (low, high), _colormap(stops, low, high, color_center)
