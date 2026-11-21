"""Isosurface adapter over the shared surface-response coefficients.

Ambient, diffuse, specular, and shininess keep the meaning they have on
``mat_viewer.render.surface_material``. A preset name is copied from that
library, including Fresnel and specular tint. This module does not keep a
private preset table.

``opacity`` is the object's alpha. It is not a surface-response
coefficient. ``light_scale`` belongs on ``LightingRig``; passing it
raises and is not folded into diffuse or specular.

``shade_albedo`` and the Plotly lighting dict are the limited shaders.
They add a white highlight and do not evaluate Fresnel, so they reject a
non-neutral Fresnel record or a non-white specular tint instead of
dropping it. The CPU surface shader evaluates the copied record, including
``glass_like`` and ``metal_like``.

The limited shade is two-sided. A back-facing normal is flipped before
the diffuse and specular terms, and the scalar or phase color stays the
albedo on both sides. The returned color is clipped to the unit cube;
coefficients are not renormalized to make that happen.

Omitting a preset and the light coefficients keeps the historical Lambert
pair, ambient ``0.68`` and diffuse ``0.32``, with specular ``0``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from .lighting import STUDIO_SOFT_DIRECTION

DEFAULT_AMBIENT = 0.68
DEFAULT_DIFFUSE = 0.32
DEFAULT_SPECULAR = 0.0
DEFAULT_SHININESS = 32.0
DEFAULT_OPACITY = 0.55

# Same camera-space direction as LightingRig.studio_soft. Normalized at
# use. This adapter does not own the lighting contract.
CAMERA_LIGHT_DIRECTION = STUDIO_SOFT_DIRECTION

# Same direction, placed far away for Plotly's camera-space light position.
PLOTLY_LIGHT_POSITION = {"x": -3200.0, "y": 4200.0, "z": 10000.0}

_LIGHT_COEFFICIENTS = ("ambient", "diffuse", "specular", "shininess")
_FRESNEL_NEUTRAL = {
    "fresnel_power": 0.0,
    "fresnel_center": 1.0,
    "fresnel_rim": 1.0,
    "fresnel_specular": 0.0,
    "fresnel_albedo": 0.0,
}


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


def _reject_light_scale(value: object) -> None:
    if value is None:
        return
    raise ValueError(
        "light_scale belongs on LightingRig and is not an isosurface "
        "material coefficient. It is not folded into diffuse or specular."
    )


def _reject_unsupported_response(data: Mapping[str, object]) -> None:
    """Reject surface-contract fields this shader would otherwise change."""
    _reject_light_scale(data.get("light_scale"))
    for field, neutral in _FRESNEL_NEUTRAL.items():
        if field not in data or data[field] is None:
            continue
        if float(data[field]) != neutral:  # type: ignore[arg-type]
            raise IsosurfaceShadingUnsupported(
                "This isosurface shader does not evaluate Fresnel and will "
                "not drop it. Use the CPU surface shader for that material."
            )
    tint = data.get("specular_tint")
    if tint not in (None, "none", "white"):
        raise IsosurfaceShadingUnsupported(
            "This isosurface shader adds a white highlight and will not "
            f"reinterpret specular_tint {tint!r}."
        )
    color = data.get("specular_color")
    if color is None or tint in (None, "none"):
        return
    rgb = tuple(float(channel) for channel in color)  # type: ignore[union-attr]
    if rgb != (1.0, 1.0, 1.0):
        raise IsosurfaceShadingUnsupported(
            "This isosurface shader adds a white highlight and will not "
            "reinterpret a non-white specular color."
        )


@dataclass(frozen=True, slots=True)
class IsosurfaceMaterial:
    """Light response copied onto one cube isosurface.

    ``opacity`` is the surface alpha. Wireframe lines keep their own
    opacity and are not shaded by these weights.
    """

    ambient: float = DEFAULT_AMBIENT
    diffuse: float = DEFAULT_DIFFUSE
    specular: float = DEFAULT_SPECULAR
    shininess: float = DEFAULT_SHININESS
    opacity: float = DEFAULT_OPACITY
    specular_tint: str = "none"
    specular_color: tuple[float, float, float] | None = None
    fresnel_power: float = 0.0
    fresnel_center: float = 1.0
    fresnel_rim: float = 1.0
    fresnel_specular: float = 0.0
    fresnel_albedo: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "ambient", _nonnegative("ambient", self.ambient))
        object.__setattr__(self, "diffuse", _nonnegative("diffuse", self.diffuse))
        object.__setattr__(self, "specular", _nonnegative("specular", self.specular))
        object.__setattr__(self, "shininess", _shininess(self.shininess))
        object.__setattr__(self, "opacity", _opacity(self.opacity))
        tint = str(self.specular_tint).strip().lower()
        if tint not in {"none", "white", "base"}:
            raise ValueError("specular_tint must be none, white, or base")
        object.__setattr__(self, "specular_tint", tint)
        for name in _FRESNEL_NEUTRAL:
            object.__setattr__(self, name, float(getattr(self, name)))

    def response_mapping(self) -> dict[str, object]:
        """Coefficient record. Opacity stays on the object."""

        payload: dict[str, object] = {
            "ambient": self.ambient,
            "diffuse": self.diffuse,
            "specular": self.specular,
            "shininess": self.shininess,
        }
        if self.specular_tint != "none":
            payload["specular_tint"] = self.specular_tint
        if self.specular_color is not None:
            payload["specular_color"] = self.specular_color
        if any(
            float(getattr(self, name)) != neutral
            for name, neutral in _FRESNEL_NEUTRAL.items()
        ):
            payload.update(
                {name: float(getattr(self, name)) for name in _FRESNEL_NEUTRAL}
            )
        return payload

    def as_dict(self) -> dict[str, object]:
        payload = self.response_mapping()
        payload["opacity"] = self.opacity
        return payload


def preset_material(name: str, *, opacity: float | None = None) -> IsosurfaceMaterial:
    """Copy one surface-material preset onto an isosurface record.

    The coefficients, Fresnel fields, and specular tint are the surface
    library's numbers. ``shade_albedo`` and Plotly still reject a record
    they cannot evaluate. The CPU surface shader evaluates it.
    """

    from .surface_material import get_surface_material

    spec = get_surface_material(str(name).strip())
    record = spec.coefficient_record()
    return IsosurfaceMaterial(
        ambient=float(record["ambient"]),
        diffuse=float(record["diffuse"]),
        specular=float(record["specular"]),
        shininess=float(record["shininess"]),
        opacity=DEFAULT_OPACITY if opacity is None else opacity,
        specular_tint=str(record.get("specular_tint", "none")),
        specular_color=record.get("specular_color"),
        fresnel_power=float(record.get("fresnel_power", 0.0)),
        fresnel_center=float(record.get("fresnel_center", 1.0)),
        fresnel_rim=float(record.get("fresnel_rim", 1.0)),
        fresnel_specular=float(record.get("fresnel_specular", 0.0)),
        fresnel_albedo=float(record.get("fresnel_albedo", 0.0)),
    )


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
    """Resolve a surface preset or explicit coefficients.

    A preset and any light coefficient together is an error. Opacity can
    be combined with either. Omitted coefficients use the historical
    material. ``light_scale`` is rejected.
    """
    _reject_light_scale(light_scale)
    supplied = {
        "ambient": ambient,
        "diffuse": diffuse,
        "specular": specular,
        "shininess": shininess,
    }
    conflicts = [name for name, value in supplied.items() if value is not None]
    if preset is not None and conflicts:
        raise ValueError(
            "Pass an isosurface material preset or the light coefficients, not both "
            f"({', '.join(conflicts)}). Opacity can be combined with either."
        )
    if preset is not None:
        return preset_material(preset, opacity=opacity)
    base = IsosurfaceMaterial()
    return IsosurfaceMaterial(
        ambient=base.ambient if ambient is None else ambient,
        diffuse=base.diffuse if diffuse is None else diffuse,
        specular=base.specular if specular is None else specular,
        shininess=base.shininess if shininess is None else shininess,
        opacity=base.opacity if opacity is None else opacity,
    )


def material_from_record(
    record: Mapping[str, object] | None,
    *,
    opacity: float | None = None,
) -> IsosurfaceMaterial:
    """Fill omitted light keys from the historical material.

    Older meshes stored only ``ambient`` and ``diffuse``. Those still
    shade, with specular ``0``. A stored ``light_scale`` or non-neutral
    Fresnel field is an error.
    """
    data = record if isinstance(record, Mapping) else {}
    _reject_unsupported_response(data)

    def pick(key: str) -> float | None:
        if key not in data or data[key] is None:
            return None
        return float(data[key])  # type: ignore[arg-type]

    return resolve_isosurface_material(
        ambient=pick("ambient"),
        diffuse=pick("diffuse"),
        specular=pick("specular"),
        shininess=pick("shininess"),
        opacity=opacity if opacity is not None else pick("opacity"),
    )


def material_from_style(style: Mapping[str, object]) -> IsosurfaceMaterial:
    """Read the shared material from a style mapping.

    A missing key or ``None`` is omitted. A stored number is explicit, even
    when it equals the historical default, and conflicts with
    ``isosurface_material``. Opacity always comes from
    ``isosurface_opacity`` when that key is present and may combine with a
    preset. ``isosurface_light_scale`` is rejected.
    """
    _reject_light_scale(style.get("isosurface_light_scale"))
    _reject_unsupported_response(
        {
            key.removeprefix("isosurface_"): value
            for key, value in style.items()
            if str(key).startswith("isosurface_")
        }
    )
    preset = style.get("isosurface_material") or None
    opacity = style.get("isosurface_opacity", DEFAULT_OPACITY)
    opacity_value = None if opacity is None else float(opacity)  # type: ignore[arg-type]

    def pick(key: str) -> float | None:
        if key not in style or style[key] is None:
            return None
        return float(style[key])  # type: ignore[arg-type]

    return resolve_isosurface_material(
        str(preset) if preset else None,
        ambient=pick("isosurface_ambient"),
        diffuse=pick("isosurface_diffuse"),
        specular=pick("isosurface_specular"),
        shininess=pick("isosurface_shininess"),
        opacity=opacity_value,
    )


def cpu_surface_from_metadata(metadata: Mapping[str, object] | None):
    """Return the surface record the CPU shader should evaluate.

    A ``surface_material`` coefficient mapping wins. Otherwise a mesh
    ``material`` dict that carries specular, tint, or Fresnel is promoted.
    Opacity stays on the object and is not part of the record. A dict that
    only has the historical ambient and diffuse pair stays on the Lambert
    path, so this returns ``None``.
    """

    from .surface_material import MaterialSpec, material_from_metadata

    if not isinstance(metadata, Mapping):
        return None
    stamped = metadata.get("surface_material")
    if stamped is not None:
        return material_from_metadata(stamped)
    record = metadata.get("material")
    if not isinstance(record, Mapping):
        return None
    if "light_scale" in record:
        raise ValueError(
            "light_scale belongs on LightingRig and is not an isosurface "
            "material coefficient. It is not folded into diffuse or specular."
        )
    carried = {"specular", "shininess", "specular_tint", "specular_color", *_FRESNEL_NEUTRAL}
    if not carried & set(record):
        return None
    payload = {
        key: value
        for key, value in record.items()
        if key not in {"opacity", "id"}
    }
    payload.setdefault("ambient", DEFAULT_AMBIENT)
    payload.setdefault("diffuse", DEFAULT_DIFFUSE)
    payload.setdefault("specular", DEFAULT_SPECULAR)
    payload.setdefault("shininess", DEFAULT_SHININESS)
    payload.setdefault(
        "specular_tint",
        "none" if float(payload["specular"]) == 0.0 else "white",
    )
    return MaterialSpec.from_mapping(payload)


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
    _reject_unsupported_response(material.response_mapping())
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
    lambert = float(material.ambient) + float(material.diffuse) * ndotl
    rgb = colors * lambert
    specular = float(material.specular)
    if specular > 0.0:
        view = np.array((0.0, 0.0, 1.0), dtype=float)
        half = lamp + view
        half = half / max(float(np.linalg.norm(half)), 1.0e-12)
        ndoth = np.clip(shaded_normals @ half, 0.0, 1.0)
        highlight = ndoth ** float(material.shininess)
        rgb = rgb + specular * highlight[..., None]
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
    """Copy the shared coefficients into Plotly lighting.

    Ambient, diffuse, and specular are forwarded unchanged. A non-neutral
    Fresnel record or a non-white specular tint raises before this dict is
    built, so Fresnel is 0 only for a record this shader can draw.
    Roughness is ``1 / (1 + shininess / 32)``.

    Plotly then uses a different shader. Its Lambert term is
    ``min(ambient + diffuse * max(n·l, 0), 1) * color``, and the highlight
    is Cook-Torrance rather than Blinn-Phong. The CPU multiplies the albedo
    by ``ambient + diffuse * |n·l|``, adds a Blinn-Phong highlight, and
    clips the product. The numbers given to Plotly are still the shared
    record; they are not reduced to hide that difference. A coefficient
    outside the interval Plotly can store raises
    :class:`IsosurfaceShadingUnsupported`.
    """
    _reject_unsupported_response(material.response_mapping())
    ambient = float(material.ambient)
    diffuse = float(material.diffuse)
    specular = float(material.specular)
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


__all__ = [
    "CAMERA_LIGHT_DIRECTION",
    "DEFAULT_AMBIENT",
    "DEFAULT_DIFFUSE",
    "DEFAULT_OPACITY",
    "DEFAULT_SHININESS",
    "DEFAULT_SPECULAR",
    "ISOSURFACE_LIGHT_COEFFICIENTS",
    "PLOTLY_LIGHT_POSITION",
    "IsosurfaceMaterial",
    "IsosurfaceShadingUnsupported",
    "camera_light_direction",
    "cpu_surface_from_metadata",
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
