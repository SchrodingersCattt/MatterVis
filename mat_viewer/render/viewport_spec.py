from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np


@dataclass(frozen=True)
class ViewportSpec:
    """Resolved, renderer-facing description of one scene viewport.

    ``_scene_ranges`` and the Plotly layout used to be called independently by
    the figure and compass paths.  Keeping the result together prevents those
    paths from silently deriving different axis scales.  The signature is
    composed only of numeric viewport inputs and is therefore safe to use as a
    camera compatibility/cache key.
    """

    ranges: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]
    aspectmode: str
    aspectratio: Mapping[str, float] | None
    cube_scale: tuple[float, float, float]
    signature: tuple

    def __post_init__(self) -> None:
        """Own nested values so the frozen contract is deep, not shallow."""
        object.__setattr__(self, "ranges", _as_viewport_ranges(self.ranges))
        if self.aspectratio is not None:
            aspectratio = MappingProxyType(
                {axis: float(self.aspectratio[axis]) for axis in ("x", "y", "z")}
            )
        else:
            aspectratio = None
        object.__setattr__(self, "aspectratio", aspectratio)
        object.__setattr__(
            self,
            "cube_scale",
            tuple(float(value) for value in self.cube_scale),
        )
        object.__setattr__(self, "signature", tuple(self.signature))

    @property
    def axis_ranges(self):
        """Alias used by callers that want to make the axis ownership clear."""
        return self.ranges

    @property
    def x_range(self) -> tuple[float, float]:
        return self.ranges[0]

    @property
    def y_range(self) -> tuple[float, float]:
        return self.ranges[1]

    @property
    def z_range(self) -> tuple[float, float]:
        return self.ranges[2]

    @property
    def viewport_signature(self) -> tuple:
        """Descriptive alias for consumers storing camera compatibility."""
        return self.signature


def _as_viewport_ranges(ranges) -> tuple[tuple[float, float], ...]:
    """Convert ranges to finite immutable pairs for a viewport contract."""
    try:
        normalized = tuple(
            (float(axis_range[0]), float(axis_range[1])) for axis_range in ranges
        )
    except (TypeError, ValueError, IndexError):
        raise ValueError("viewport ranges must contain three numeric pairs") from None
    if len(normalized) != 3 or any(
        not (
            math.isfinite(start)
            and math.isfinite(end)
            and abs(end - start) > 1e-12
        )
        for start, end in normalized
    ):
        raise ValueError("viewport ranges must contain three finite non-zero pairs")
    return normalized


def _viewport_signature(
    ranges: tuple[tuple[float, float], ...],
    aspectmode: str,
    aspectratio: Mapping[str, float] | None,
    cube_scale: tuple[float, float, float],
) -> tuple:
    """Return a stable, hashable camera-compatibility signature."""
    # Ranges are generated from floating point geometry.  Rounding at the
    # contract boundary avoids cache misses from insignificant accumulation
    # noise while retaining sub-nanometre viewport changes.
    rounded_ranges = tuple(
        tuple(round(float(value), 12) for value in axis_range)
        for axis_range in ranges
    )
    rounded_aspect = (
        tuple(
            (axis, round(float(aspectratio[axis]), 12))
            for axis in ("x", "y", "z")
        )
        if aspectratio is not None
        else None
    )
    rounded_scale = tuple(round(float(value), 12) for value in cube_scale)
    return (rounded_ranges, str(aspectmode), rounded_aspect, rounded_scale)


def resolve_viewport(
    scene: dict,
    style: dict,
    topology_data: dict | None = None,
) -> ViewportSpec:
    """Resolve all scale inputs shared by the main viewer and compass.

    The returned object owns the final ranges, Plotly aspect configuration,
    Cartesian data-units per rendered cube unit, and a compatibility
    signature.  Callers should pass it through to layout builders rather than
    recomputing ranges from the scene a second time.
    """
    from .viewport import (
        _legacy_axis_cube_scale,
        _range_aspect_ratio,
        _scene_ranges,
    )

    resolved_ranges = _as_viewport_ranges(
        _scene_ranges(scene, style, topology_data=topology_data)
    )
    xr, yr, zr = resolved_ranges
    aspectratio = _range_aspect_ratio(xr, yr, zr)
    aspectmode = "manual" if aspectratio is not None else "data"

    if aspectratio is not None:
        spans = np.asarray(
            [axis_range[1] - axis_range[0] for axis_range in resolved_ranges],
            dtype=float,
        )
        normalized_aspect = np.asarray(
            [aspectratio[axis] for axis in ("x", "y", "z")], dtype=float
        )
        scale = spans / (2.0 * np.maximum(normalized_aspect, 1.0e-12))
    else:
        # This branch is retained for malformed/degenerate ranges where Plotly
        # must use ``data`` aspect mode.  It preserves the historical fallback
        # for scenes that expose only bounds or an explicit viewport.
        scale = _legacy_axis_cube_scale(scene, style)
        if scale is None:
            scale = np.ones(3, dtype=float)

    scale = np.asarray(scale, dtype=float)
    if scale.shape != (3,) or not np.all(np.isfinite(scale)) or np.any(scale <= 0):
        scale = np.ones(3, dtype=float)
    cube_scale = tuple(float(value) for value in scale)
    signature = _viewport_signature(
        resolved_ranges, aspectmode, aspectratio, cube_scale
    )
    return ViewportSpec(
        ranges=resolved_ranges,
        aspectmode=aspectmode,
        aspectratio=(
            {axis: float(aspectratio[axis]) for axis in ("x", "y", "z")}
            if aspectratio is not None
            else None
        ),
        cube_scale=cube_scale,
        signature=signature,
    )
