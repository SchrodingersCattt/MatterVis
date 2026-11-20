"""Experimental VTK lowering of shared shade coefficients.

``FigureShade`` stores ambient, diffuse, specular, and shininess. Those
names are the Blinn–Phong coefficients used by a surface response: three
independent weights and a highlight exponent. This module does not define
``SurfaceMaterial``, ``MaterialSpec``, or ``LightingRig``, and it does not
look up presets or rewrite a coefficient to fit VTK.

The cube figure's default three-light rig is experimental. It is not
``studio_soft`` and it is not ``studio_three_point``. ``evaluate_vtk_shade``
copies a record onto a VTK property, or rejects a capability this figure
does not evaluate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any

import numpy as np

# Shared coefficient bounds. Weights may exceed 1; shininess is an exponent.
_MIN_COEFFICIENT = 0.0
_MIN_SHININESS = 1.0
# Historical camera-space light. +Z points toward the camera, the same
# triple the CPU rasterizer dots with a normal. This is not a new rig.
STUDIO_SOFT_CAMERA_DIRECTION = (-0.32, 0.42, 1.0)
_COEFFICIENTS = ("ambient", "diffuse", "specular", "shininess")
_NEUTRAL_FRESNEL = {
    "fresnel_power": 0.0,
    "fresnel_center": 1.0,
    "fresnel_rim": 1.0,
    "fresnel_specular": 0.0,
    "fresnel_albedo": 0.0,
}
_MISSING = object()


def _finite(name: str, value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _nonnegative(name: str, value: float) -> float:
    number = _finite(name, value)
    if number < _MIN_COEFFICIENT:
        raise ValueError(f"{name} must be finite and non-negative")
    return number


def _shininess(value: float) -> float:
    number = _finite("shininess", value)
    if number < _MIN_SHININESS:
        raise ValueError("shininess must be finite and at least 1")
    return number


@dataclass(frozen=True, slots=True)
class FigureShade:
    """Ambient, diffuse, specular, and shininess of one isosurface.

    Callers pass this record into a figure entry. These are the same four
    numbers as ``IsosurfaceMaterial`` when that API is present.
    ``shininess`` is the highlight exponent.
    """

    ambient: float
    diffuse: float
    specular: float
    shininess: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "ambient", _nonnegative("ambient", self.ambient))
        object.__setattr__(self, "diffuse", _nonnegative("diffuse", self.diffuse))
        object.__setattr__(self, "specular", _nonnegative("specular", self.specular))
        object.__setattr__(self, "shininess", _shininess(self.shininess))


class VtkShadeUnsupported(ValueError):
    """The experimental VTK figure cannot evaluate this material or light."""


@dataclass(frozen=True, slots=True)
class VtkShadeEvaluation:
    """Coefficients copied onto a VTK property, plus the light mode used.

    ``shade`` keeps the caller's ambient, diffuse, specular, and shininess.
    The property fields are what VTK evaluates. ``unlit`` draws the inherited
    color (ambient 1, diffuse 0, specular 0) and does not rewrite ``shade``.
    """

    shade: FigureShade
    lighting: str
    ambient: float
    diffuse: float
    specular: float
    shininess: float


def _field(record: Any, name: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, _MISSING)
    return getattr(record, name, _MISSING)


def _as_shade(material: FigureShade | Mapping) -> tuple[FigureShade, Mapping[str, Any] | None]:
    if isinstance(material, FigureShade):
        return material, None
    if not isinstance(material, Mapping):
        raise TypeError(
            "surface must be a FigureShade or a mapping of ambient, diffuse, "
            "specular, and shininess"
        )
    missing = [name for name in _COEFFICIENTS if name not in material]
    if missing:
        raise ValueError(
            "surface mapping must provide ambient, diffuse, specular, "
            "and shininess"
        )
    shade = FigureShade(
        ambient=material["ambient"],
        diffuse=material["diffuse"],
        specular=material["specular"],
        shininess=material["shininess"],
    )
    return shade, material


def _reject_unsupported_material(record: Mapping[str, Any] | None) -> None:
    if record is None:
        return
    rejected = sorted({"light_scale", "opacity"} & set(record))
    if rejected:
        raise VtkShadeUnsupported(
            "surface material does not accept "
            f"{', '.join(rejected)}; light_scale stays on the lighting rig "
            "and opacity stays on the object"
        )
    unknown = sorted(
        set(record) - set(_COEFFICIENTS) - set(_NEUTRAL_FRESNEL) - {"specular_tint", "specular_color"}
    )
    if unknown:
        raise VtkShadeUnsupported(
            "VTK figure does not evaluate surface field(s) " + ", ".join(unknown)
        )
    for name, neutral in _NEUTRAL_FRESNEL.items():
        value = record.get(name, _MISSING)
        if value is _MISSING:
            continue
        number = _finite(name, value)
        if abs(number - neutral) > 1.0e-9:
            raise VtkShadeUnsupported(
                "VTK figure does not evaluate Fresnel "
                f"({name}={number:g}). glass_like is not lowered by dropping "
                "the rim and keeping the Blinn-Phong weights."
            )
    tint = record.get("specular_tint", _MISSING)
    if tint is _MISSING:
        return
    if not isinstance(tint, str) or tint.strip().lower() not in {"none", "white", "base"}:
        raise ValueError("specular_tint must be none, white, or base")
    tint = tint.strip().lower()
    if tint == "base":
        raise VtkShadeUnsupported(
            "specular_tint 'base' follows the surface color. The VTK property "
            "has one specular color and will not treat that tint as white."
        )
    color = record.get("specular_color", _MISSING)
    if color is not _MISSING and color is not None:
        channels = tuple(float(channel) for channel in color)
        if len(channels) != 3 or any(not isfinite(channel) or channel < 0.0 for channel in channels):
            raise ValueError("specular_color must be three finite non-negative channels")
        if any(abs(channel - 1.0) > 1.0e-9 for channel in channels):
            raise VtkShadeUnsupported(
                "VTK figure specular is white. A non-white specular_color is "
                "not recolored and is not dropped."
            )


def _lighting_id(lighting: Any) -> str:
    if lighting is None:
        return "experimental"
    scale = _MISSING
    kind = _MISSING
    direction = _MISSING
    if isinstance(lighting, str):
        key = lighting.strip().lower().replace("-", "_")
    elif isinstance(lighting, Mapping):
        raw = lighting.get("id", lighting.get("lighting", _MISSING))
        if raw is _MISSING or not isinstance(raw, str):
            raise TypeError("lighting rig id must be a string")
        key = raw.strip().lower().replace("-", "_")
        scale = lighting.get("light_scale", _MISSING)
        kind = lighting.get("kind", _MISSING)
        direction = lighting.get("direction", _MISSING)
    else:
        raw = getattr(lighting, "id", _MISSING)
        if raw is _MISSING or not isinstance(raw, str):
            raise TypeError("lighting rig id must be a string")
        key = raw.strip().lower().replace("-", "_")
        scale = getattr(lighting, "light_scale", _MISSING)
        kind = getattr(lighting, "kind", _MISSING)
        direction = getattr(lighting, "direction", _MISSING)
    if scale is not _MISSING and scale is not None and float(scale) != 1.0:
        raise VtkShadeUnsupported(
            "light_scale lives on the lighting rig. The VTK figure does not "
            "apply a non-unit light_scale and will not retune studio_soft."
        )
    if key == "studio_three_point" or kind == "multi_light":
        raise VtkShadeUnsupported(
            "lighting 'studio_three_point' stores no light directions. The "
            "VTK figure will not invent a three-light rig and call it "
            "studio_three_point."
        )
    if key == "unlit" or kind == "unlit":
        return "unlit"
    if key == "studio_soft" or kind == "camera_directional":
        if key not in {"studio_soft"} and kind == "camera_directional":
            raise VtkShadeUnsupported(
                "the only camera-directional rig this figure evaluates is "
                f"'studio_soft', not {key!r}"
            )
        if direction is not _MISSING and direction is not None:
            values = tuple(float(component) for component in direction)
            if len(values) != 3 or not np.allclose(
                values, STUDIO_SOFT_CAMERA_DIRECTION, rtol=0.0, atol=1.0e-12
            ):
                raise VtkShadeUnsupported(
                    "studio_soft uses the camera-space direction "
                    f"{STUDIO_SOFT_CAMERA_DIRECTION}; the VTK figure will not "
                    "substitute another direction under that name."
                )
        return "studio_soft"
    if key == "experimental":
        return "experimental"
    raise VtkShadeUnsupported(
        "VTK figure evaluates only its experimental rig, 'studio_soft', "
        f"and 'unlit'; {key!r} is rejected"
    )


def evaluate_vtk_shade(
    material: FigureShade | Mapping,
    lighting: Any = None,
) -> VtkShadeEvaluation:
    """Copy shared coefficients or reject an unsupported capability.

    ``ambient``, ``diffuse``, ``specular``, and ``shininess`` are stored
    unchanged. Fresnel (``glass_like``), ``specular_tint='base'``, a
    non-white ``specular_color``, material ``opacity`` / ``light_scale``,
    and ``studio_three_point`` raise. ``unlit`` leaves ``shade`` intact and
    reports a flat property. ``studio_soft`` keeps the coefficients and
    selects the one historical camera-space light.
    """

    shade, record = _as_shade(material)
    _reject_unsupported_material(record)
    mode = _lighting_id(lighting)
    specular = shade.specular
    if record is not None:
        tint = record.get("specular_tint", _MISSING)
        if tint is not _MISSING and str(tint).strip().lower() == "none":
            specular = 0.0
    if mode == "unlit":
        return VtkShadeEvaluation(
            shade=shade,
            lighting="unlit",
            ambient=1.0,
            diffuse=0.0,
            specular=0.0,
            shininess=shade.shininess,
        )
    return VtkShadeEvaluation(
        shade=shade,
        lighting=mode,
        ambient=shade.ambient,
        diffuse=shade.diffuse,
        specular=specular,
        shininess=shade.shininess,
    )


def studio_soft_world_direction(look, up) -> np.ndarray:
    """Map ``STUDIO_SOFT_CAMERA_DIRECTION`` into the figure view basis.

    Camera-space +x is screen right, +y is screen up, and +z points toward
    the camera. ``look`` points from the camera toward the scene, so world
    +z is ``-look``. The returned vector is not normalized.
    """

    forward = np.asarray(look, dtype=float)
    screen_up = np.asarray(up, dtype=float)
    if forward.shape != (3,) or screen_up.shape != (3,):
        raise ValueError("look and up must have shape (3,)")
    if not np.all(np.isfinite(forward)) or not np.all(np.isfinite(screen_up)):
        raise ValueError("look and up must be finite")
    forward_norm = float(np.linalg.norm(forward))
    up_norm = float(np.linalg.norm(screen_up))
    if forward_norm < 1.0e-12 or up_norm < 1.0e-12:
        raise ValueError("look and up must be non-zero")
    forward = forward / forward_norm
    screen_up = screen_up / up_norm
    side = np.cross(forward, screen_up)
    side_norm = float(np.linalg.norm(side))
    if side_norm < 1.0e-8:
        raise ValueError("look and up must not be parallel")
    side = side / side_norm
    screen_up = np.cross(side, forward)
    lx, ly, lz = STUDIO_SOFT_CAMERA_DIRECTION
    return lx * side + ly * screen_up + lz * (-forward)


__all__ = [
    "STUDIO_SOFT_CAMERA_DIRECTION",
    "FigureShade",
    "VtkShadeEvaluation",
    "VtkShadeUnsupported",
    "evaluate_vtk_shade",
    "studio_soft_world_direction",
]
