"""Backend-neutral auxiliary unit-cell overlays."""

from __future__ import annotations

from typing import Any

import numpy as np

from ..contracts import LinePrimitive
from ..geometry import unit_cell_primitive
from .io import load_overlay_file

_CELL_OVERLAY_DETERMINANT_TOL = 1e-12
_CELL_OVERLAY_KEYS = {
    "id",
    "matrix",
    "origin",
    "color",
    "width_px",
    "dash",
    "alpha",
    "depth_test",
}


def _finite_vector(value: Any, *, shape: tuple[int, ...], name: str) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.shape != shape or not np.all(np.isfinite(array)):
        dimensions = "x".join(str(item) for item in shape)
        raise ValueError(f"{name} must be a finite {dimensions} array")
    return array


def normalize_cell_overlays(raw: Any) -> list[dict[str, Any]]:
    """Validate auxiliary-cell JSON and return JSON-safe dictionaries."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TypeError("cell_overlays must be a list")
    overlays: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TypeError(f"cell overlay {index} must be a dict")
        unknown = sorted(set(item) - _CELL_OVERLAY_KEYS)
        if unknown:
            raise ValueError(
                f"cell overlay {index}: unsupported key(s): {', '.join(unknown)}"
            )
        identifier = str(item.get("id") or f"cell_{index}").strip()
        if not identifier or identifier in identifiers:
            raise ValueError(f"duplicate or empty cell overlay id: {identifier!r}")
        identifiers.add(identifier)
        matrix = _finite_vector(
            item.get("matrix"), shape=(3, 3), name=f"cell overlay {identifier} matrix"
        )
        if abs(float(np.linalg.det(matrix))) <= _CELL_OVERLAY_DETERMINANT_TOL:
            raise ValueError(f"cell overlay {identifier} matrix must be non-singular")
        origin = _finite_vector(
            item.get("origin", (0.0, 0.0, 0.0)),
            shape=(3,),
            name=f"cell overlay {identifier} origin",
        )
        color = str(item.get("color") or "#333333").strip()
        if not color:
            raise ValueError(f"cell overlay {identifier} color must not be empty")
        width_px = float(item.get("width_px", 1.0))
        if not np.isfinite(width_px) or width_px <= 0.0:
            raise ValueError(f"cell overlay {identifier} width_px must be positive")
        dash_raw = item.get("dash", ())
        if not isinstance(dash_raw, (list, tuple)):
            raise TypeError(f"cell overlay {identifier} dash must be a list")
        dash = tuple(float(value) for value in dash_raw)
        if any(not np.isfinite(value) or value <= 0.0 for value in dash):
            raise ValueError(
                f"cell overlay {identifier} dash entries must be positive"
            )
        alpha = float(item.get("alpha", 1.0))
        if not np.isfinite(alpha) or not 0.0 <= alpha <= 1.0:
            raise ValueError(f"cell overlay {identifier} alpha must lie in [0, 1]")
        depth_test = item.get("depth_test", False)
        if not isinstance(depth_test, bool):
            raise TypeError(
                f"cell overlay {identifier} depth_test must be a JSON boolean"
            )
        overlays.append(
            {
                "id": identifier,
                "matrix": matrix.tolist(),
                "origin": origin.tolist(),
                "color": color,
                "width_px": width_px,
                "dash": list(dash),
                "alpha": alpha,
                "depth_test": depth_test,
            }
        )
    return overlays


def attach_cell_overlays(scene: dict[str, Any], cell_overlays: Any) -> None:
    """Attach explicit overlays; None preserves an existing scene value."""
    if cell_overlays is not None:
        scene["cell_overlays"] = normalize_cell_overlays(cell_overlays)
    elif "cell_overlays" in scene:
        scene["cell_overlays"] = normalize_cell_overlays(scene["cell_overlays"])


def cell_overlay_primitives(cell_overlays: Any) -> list[LinePrimitive]:
    """Compile auxiliary cells into ordinary dashed/solid line primitives."""
    primitives: list[LinePrimitive] = []
    for overlay in normalize_cell_overlays(cell_overlays):
        identifier = overlay["id"]
        primitives.append(
            unit_cell_primitive(
                f"cell-overlay:{identifier}",
                overlay["matrix"],
                color=overlay["color"],
                origin=overlay["origin"],
                width_px=overlay["width_px"],
                dash=overlay["dash"],
                alpha=overlay["alpha"],
                depth_test=overlay["depth_test"],
                metadata={"kind": "cell_overlay", "cell_overlay_id": identifier},
            )
        )
    return primitives


__all__ = [
    "attach_cell_overlays",
    "cell_overlay_primitives",
    "load_overlay_file",
    "normalize_cell_overlays",
]
