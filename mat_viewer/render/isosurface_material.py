"""Shared shading material for cube isosurface meshes.

One record describes how the surface responds to light. Ambient, diffuse,
specular, and shininess are independent surface weights. They are not forced
to sum to one. ``light_scale`` multiplies only the directional terms, so the
weights stay a description of the surface and the light is scaled separately.

The shade is two-sided. A back-facing normal is flipped before the diffuse
and specular terms, and the scalar or phase color stays the albedo on both
sides. The returned color is clipped to the unit cube; coefficients are not
renormalized to make that happen.

Named presets:

- ``matte`` is for print figures: a bright floor, no highlight, and enough
  diffuse weight that folds stay readable.
- ``glossy`` adds a broad, soft highlight.

Omitting a preset and the light coefficients keeps the historical Lambert
pair, ambient ``0.68`` and diffuse ``0.32``, with specular ``0``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

DEFAULT_AMBIENT = 0.68
DEFAULT_DIFFUSE = 0.32
DEFAULT_SPECULAR = 0.0
DEFAULT_SHININESS = 32.0
DEFAULT_OPACITY = 0.55
DEFAULT_LIGHT_SCALE = 1.0

# Camera-space direction shared by the CPU rasterizer and vector export.
# The vector is normalized at use; its length is not a material weight.
CAMERA_LIGHT_DIRECTION = (-0.32, 0.42, 1.0)

# Same direction, placed far away for Plotly's camera-space light position.
PLOTLY_LIGHT_POSITION = {"x": -3200.0, "y": 4200.0, "z": 10000.0}

MATTE_AMBIENT = 0.62
MATTE_DIFFUSE = 0.55
GLOSSY_AMBIENT = 0.38
GLOSSY_DIFFUSE = 0.72
GLOSSY_SPECULAR = 0.18
GLOSSY_SHININESS = 16.0

_LIGHT_COEFFICIENTS = ("ambient", "diffuse", "specular", "shininess", "light_scale")


class IsosurfaceShadingUnsupported(ValueError):
    """A backend cannot evaluate this cube isosurface material."""


def _nonnegative(name: str, value: float) -> float:
    number = float(value)
    if not np.isfinite(number) or number < 0.0:
        raise ValueError(f"isosurface {name} must be finite and non-negative")
    return number


def _shininess(value: float) -> float:
    number = float(value)
    if not np.isfinite(number) or number < 1.0:
        raise ValueError("isosurface shininess must be finite and at least 1")
    return number


def _opacity(value: float) -> float:
    number = float(value)
    if not np.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError("isosurface opacity must lie in [0, 1]")
    return number


@dataclass(frozen=True, slots=True)
class IsosurfaceMaterial:
    """Light response of one cube isosurface.

    ``opacity`` lives on this record and is the surface alpha. Wireframe
    lines keep their own opacity and are not shaded by these weights.
    """

    ambient: float = DEFAULT_AMBIENT
    diffuse: float = DEFAULT_DIFFUSE
    specular: float = DEFAULT_SPECULAR
    shininess: float = DEFAULT_SHININESS
    opacity: float = DEFAULT_OPACITY
    light_scale: float = DEFAULT_LIGHT_SCALE

    def __post_init__(self) -> None:
        object.__setattr__(self, "ambient", _nonnegative("ambient", self.ambient))
        object.__setattr__(self, "diffuse", _nonnegative("diffuse", self.diffuse))
        object.__setattr__(self, "specular", _nonnegative("specular", self.specular))
        object.__setattr__(self, "shininess", _shininess(self.shininess))
        object.__setattr__(self, "opacity", _opacity(self.opacity))
        object.__setattr__(
            self, "light_scale", _nonnegative("light_scale", self.light_scale)
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "ambient": self.ambient,
            "diffuse": self.diffuse,
            "specular": self.specular,
            "shininess": self.shininess,
            "opacity": self.opacity,
            "light_scale": self.light_scale,
        }


MATTE_MATERIAL = IsosurfaceMaterial(
    ambient=MATTE_AMBIENT,
    diffuse=MATTE_DIFFUSE,
    specular=0.0,
    shininess=DEFAULT_SHININESS,
    opacity=DEFAULT_OPACITY,
    light_scale=DEFAULT_LIGHT_SCALE,
)
GLOSSY_MATERIAL = IsosurfaceMaterial(
    ambient=GLOSSY_AMBIENT,
    diffuse=GLOSSY_DIFFUSE,
    specular=GLOSSY_SPECULAR,
    shininess=GLOSSY_SHININESS,
    opacity=DEFAULT_OPACITY,
    light_scale=DEFAULT_LIGHT_SCALE,
)
_PRESETS = {"matte": MATTE_MATERIAL, "glossy": GLOSSY_MATERIAL}


def preset_material(name: str) -> IsosurfaceMaterial:
    """Return the named matte or glossy preset."""
    key = str(name).strip().lower()
    try:
        return _PRESETS[key]
    except KeyError as exc:
        raise ValueError("isosurface material must be 'matte' or 'glossy'") from exc


def resolve_isosurface_material(
    preset: str | None = None,
    *,
    ambient: float | None = None,
    diffuse: float | None = None,
    specular: float | None = None,
    shininess: float | None = None,
    opacity: float | None = None,
    light_scale: float | None = None,
) -> IsosurfaceMaterial:
    """Resolve a preset or explicit coefficients.

    A preset and any light coefficient together is an error. Opacity can be
    combined with either. Omitted coefficients use the historical material.
    """
    supplied = {
        "ambient": ambient,
        "diffuse": diffuse,
        "specular": specular,
        "shininess": shininess,
        "light_scale": light_scale,
    }
    if preset is not None and any(value is not None for value in supplied.values()):
        raise ValueError(
            "Pass an isosurface material preset or the light coefficients, not both. "
            "Opacity can be combined with either."
        )
    base = preset_material(preset) if preset is not None else IsosurfaceMaterial()
    return IsosurfaceMaterial(
        ambient=base.ambient if ambient is None else ambient,
        diffuse=base.diffuse if diffuse is None else diffuse,
        specular=base.specular if specular is None else specular,
        shininess=base.shininess if shininess is None else shininess,
        opacity=base.opacity if opacity is None else opacity,
        light_scale=base.light_scale if light_scale is None else light_scale,
    )


def material_from_record(
    record: Mapping[str, object] | None,
    *,
    opacity: float | None = None,
) -> IsosurfaceMaterial:
    """Fill omitted light keys from the historical material.

    Older meshes stored only ``ambient`` and ``diffuse``. Those still shade,
    with specular ``0`` and light scale ``1``.
    """
    data = record if isinstance(record, Mapping) else {}

    def pick(key: str) -> float | None:
        if key not in data or data[key] is None:
            return None
        return float(data[key])  # type: ignore[arg-type]

    return resolve_isosurface_material(
        ambient=pick("ambient"),
        diffuse=pick("diffuse"),
        specular=pick("specular"),
        shininess=pick("shininess"),
        light_scale=pick("light_scale"),
        opacity=opacity if opacity is not None else pick("opacity"),
    )


def material_from_style(style: Mapping[str, object]) -> IsosurfaceMaterial:
    """Read the shared material from a style mapping.

    ``isosurface_material`` replaces the light coefficients. Opacity always
    comes from ``isosurface_opacity`` when that key is present.
    """
    preset = style.get("isosurface_material") or None
    opacity = style.get("isosurface_opacity", DEFAULT_OPACITY)
    opacity_value = None if opacity is None else float(opacity)  # type: ignore[arg-type]
    if preset:
        return resolve_isosurface_material(str(preset), opacity=opacity_value)

    def pick(key: str) -> float | None:
        if key not in style or style[key] is None:
            return None
        return float(style[key])  # type: ignore[arg-type]

    return resolve_isosurface_material(
        ambient=pick("isosurface_ambient"),
        diffuse=pick("isosurface_diffuse"),
        specular=pick("isosurface_specular"),
        shininess=pick("isosurface_shininess"),
        light_scale=pick("isosurface_light_scale"),
        opacity=opacity_value,
    )


def camera_light_direction() -> np.ndarray:
    """Unit camera-space light used by CPU raster and vector shading."""
    light = np.asarray(CAMERA_LIGHT_DIRECTION, dtype=float)
    return light / max(float(np.linalg.norm(light)), 1.0e-12)


def shade_albedo(
    albedo,
    normals,
    material: IsosurfaceMaterial,
    *,
    light: np.ndarray | None = None,
) -> np.ndarray:
    """Shade albedo by a two-sided Blinn-Phong response.

    ``albedo`` and ``normals`` are camera-space arrays ending in 3. One
    color may be broadcast across many normals. A back-facing normal is
    flipped, so the scalar color is kept on both sides. Specular is an
    additive white highlight. The result is clipped to ``[0, 1]``.
    """
    colors = np.asarray(albedo, dtype=float)
    directions = np.asarray(normals, dtype=float)
    if colors.shape[-1] != 3 or directions.shape[-1] != 3:
        raise ValueError("albedo and normals must end with 3 channels")
    single = colors.shape == (3,) and directions.shape == (3,)
    if colors.shape == (3,) and directions.ndim == 2:
        colors = np.broadcast_to(colors, directions.shape)
    elif directions.shape == (3,) and colors.ndim == 2:
        directions = np.broadcast_to(directions, colors.shape)
    elif single:
        colors = colors.reshape(1, 3)
        directions = directions.reshape(1, 3)
    if directions.shape != colors.shape:
        raise ValueError("normals must match albedo")
    lengths = np.linalg.norm(directions, axis=-1, keepdims=True)
    unit = directions / np.maximum(lengths, 1.0e-12)
    lamp = camera_light_direction() if light is None else np.asarray(light, dtype=float)
    lamp = lamp / max(float(np.linalg.norm(lamp)), 1.0e-12)
    facing = np.sum(unit * lamp, axis=-1, keepdims=True)
    shaded_normals = np.where(facing < 0.0, -unit, unit)
    ndotl = np.abs(facing)
    scale = float(material.light_scale)
    lambert = float(material.ambient) + float(material.diffuse) * scale * ndotl
    rgb = colors * lambert
    specular = float(material.specular)
    if specular > 0.0 and scale > 0.0:
        view = np.array((0.0, 0.0, 1.0), dtype=float)
        half = lamp + view
        half = half / max(float(np.linalg.norm(half)), 1.0e-12)
        ndoth = np.clip(shaded_normals @ half, 0.0, 1.0)
        highlight = ndoth ** float(material.shininess)
        rgb = rgb + specular * scale * highlight[..., None]
    rgb = np.clip(rgb, 0.0, 1.0)
    if single:
        return rgb[0]
    return rgb


def _require_plotly_interval(name: str, value: float, low: float, high: float) -> None:
    """Reject a coefficient Plotly cannot store.

    Plotly's lighting properties are closed intervals. Clamping a value into
    that interval would change the shared material, so an out-of-range
    coefficient is an error.
    """
    if value < low - 1.0e-12 or value > high + 1.0e-12:
        raise IsosurfaceShadingUnsupported(
            f"Plotly lighting stores {name} in [{low:g}, {high:g}] and cannot "
            f"represent {value:g} from the shared isosurface material. "
            "The coefficient is not clamped. Use the CPU backend."
        )


def plotly_lighting(material: IsosurfaceMaterial) -> dict[str, float]:
    """Map the shared record onto Plotly lighting without rewriting it.

    ``diffuse`` and ``specular`` sent to Plotly are the shared weights times
    ``light_scale``, because Plotly has no separate light scale. Ambient is
    not scaled. Fresnel stays ``0`` so the scalar color is not rim-tinted.
    Roughness is ``1 / (1 + shininess / 32)``. Named presets are inside
    Plotly's stored intervals and are forwarded unchanged.

    Plotly then uses a different shader. Its Lambert term is
    ``min(ambient + diffuse * max(n·l, 0), 1) * color``, and the highlight
    is Cook-Torrance rather than Blinn-Phong. The CPU multiplies the albedo
    by ``ambient + diffuse * light_scale * |n·l|``, adds a Blinn-Phong
    highlight, and clips the product. Where the Lambert factor exceeds 1, a
    dark scalar color can stay brighter on the CPU than in Plotly. The
    numbers given to Plotly are still the shared record; they are not
    reduced to hide that difference. A coefficient outside the interval
    Plotly can store raises :class:`IsosurfaceShadingUnsupported`.
    """
    ambient = float(material.ambient)
    diffuse = float(material.diffuse) * float(material.light_scale)
    specular = float(material.specular) * float(material.light_scale)
    roughness = 1.0 / (1.0 + float(material.shininess) / DEFAULT_SHININESS)
    _require_plotly_interval("ambient", ambient, 0.0, 1.0)
    _require_plotly_interval("diffuse", diffuse, 0.0, 1.0)
    _require_plotly_interval("specular", specular, 0.0, 2.0)
    _require_plotly_interval("roughness", roughness, 0.0, 1.0)
    return {
        "ambient": ambient,
        "diffuse": diffuse,
        "specular": specular,
        "roughness": float(roughness),
        "fresnel": 0.0,
    }


def plotly_surface_kwargs(material: IsosurfaceMaterial) -> dict[str, object]:
    """Lighting and light position for one Plotly mesh trace."""
    return {
        "lighting": plotly_lighting(material),
        "lightposition": dict(PLOTLY_LIGHT_POSITION),
    }


def coefficient_matches_preset_or_default(
    current: float,
    *,
    default: float,
    preset_value: float,
) -> bool:
    """True when a coefficient was left at the historical or preset value."""
    number = float(current)
    return number == float(default) or number == float(preset_value)


__all__ = [
    "CAMERA_LIGHT_DIRECTION",
    "DEFAULT_AMBIENT",
    "DEFAULT_DIFFUSE",
    "DEFAULT_LIGHT_SCALE",
    "DEFAULT_OPACITY",
    "DEFAULT_SHININESS",
    "DEFAULT_SPECULAR",
    "GLOSSY_AMBIENT",
    "GLOSSY_DIFFUSE",
    "GLOSSY_MATERIAL",
    "GLOSSY_SHININESS",
    "GLOSSY_SPECULAR",
    "ISOSURFACE_LIGHT_COEFFICIENTS",
    "MATTE_AMBIENT",
    "MATTE_DIFFUSE",
    "MATTE_MATERIAL",
    "PLOTLY_LIGHT_POSITION",
    "IsosurfaceMaterial",
    "IsosurfaceShadingUnsupported",
    "camera_light_direction",
    "coefficient_matches_preset_or_default",
    "material_from_record",
    "material_from_style",
    "plotly_lighting",
    "plotly_surface_kwargs",
    "preset_material",
    "resolve_isosurface_material",
    "shade_albedo",
]

# Public alias used by callers that want the coefficient names.
ISOSURFACE_LIGHT_COEFFICIENTS = _LIGHT_COEFFICIENTS
