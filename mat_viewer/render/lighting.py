"""Named lighting rigs for the CPU general renderer.

Appearance is representation plus surface material plus lighting. This module
owns the lights. It does not own Blinn–Phong presets, opacity, or the
``mesh`` / ``flat`` draw technique (``style["material"]`` and the Dash
``#material-selector``).

``get_lighting_rig`` only looks up a record. It does not retune the CPU light
and it does not change pixels by itself. The CPU rasterizer changes only when
a render is asked for a rig, through ``RenderSpec.lighting`` or
``mat-vis render --lighting``.

``studio_soft`` is the historical camera-space direction ``(-0.32, 0.42, 1.0)``
with ``light_scale`` 1. Do not retune it. ``unlit`` disables that shade.
``studio_three_point`` is a named multi-light contract. The CPU path has no
three-light consumer and raises; it does not substitute ``studio_soft``.
The first real consumer is PyVista, which must evaluate the lights itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

import numpy as np

# Historical camera-space direction. The batch rasterizer and the general
# CPU rasterizer both start from this vector. Do not retune it.
STUDIO_SOFT_DIRECTION = (-0.32, 0.42, 1.0)
PUBLICATION_DEFAULT_LIGHTING = "studio_soft"

# Historical matte response. A stored record with these coefficients keeps
# today's Lambert / radial bond shade. Lighting does not replace them.
_LEGACY_AMBIENT = 0.68
_LEGACY_DIFFUSE = 0.32

LightingKind = Literal["camera_directional", "unlit", "multi_light"]


class CpuLightingError(ValueError):
    """The CPU general renderer was asked for a rig it does not evaluate."""


@dataclass(frozen=True, slots=True)
class LightingRig:
    """One named lighting setup.

    ``light_scale`` lives here, not on a surface-material record. The named
    presets use 1 so ``studio_soft`` keeps its historical intensity.
    ``cpu_evaluates`` is false for a contract the CPU rasterizer must reject
    instead of shading.
    """

    id: str
    kind: LightingKind
    direction: tuple[float, float, float] | None = None
    light_scale: float = 1.0
    cpu_evaluates: bool = False
    publication_default: bool = False
    # Color-critical scalar fields (NCI / IRI). Not the journal default for
    # orbitals, density, or Hirshfeld surfaces.
    color_critical: bool = False

    def __post_init__(self) -> None:
        if self.kind not in {"camera_directional", "unlit", "multi_light"}:
            raise ValueError(f"unknown lighting kind {self.kind!r}")
        scale = self.light_scale
        if isinstance(scale, bool) or not isinstance(scale, (int, float)):
            raise TypeError("light_scale must be a number")
        scale = float(scale)
        if not np.isfinite(scale) or scale < 0.0:
            raise ValueError("light_scale must be finite and >= 0")
        object.__setattr__(self, "light_scale", scale)
        if self.kind == "camera_directional":
            if self.direction is None:
                raise ValueError("a camera-directional rig needs a direction")
            direction = tuple(float(component) for component in self.direction)
            if len(direction) != 3 or not all(np.isfinite(direction)):
                raise ValueError("direction must be three finite components")
            object.__setattr__(self, "direction", direction)
        elif self.direction is not None:
            raise ValueError(f"{self.kind} does not store a CPU light direction")


@dataclass(frozen=True, slots=True)
class SurfaceResponse:
    """Coefficients already stored on a primitive.

    This is not a preset library. ``hard_plastic`` and ``metal_like`` stay on
    the surface-material record. The rig only decides which lights, if any,
    evaluate this response.
    """

    ambient: float
    diffuse: float
    specular: float
    shininess: float
    specular_tint: str = "none"
    specular_color: tuple[float, float, float] | None = None


LIGHTING_RIGS = MappingProxyType(
    {
        "studio_soft": LightingRig(
            id="studio_soft",
            kind="camera_directional",
            direction=STUDIO_SOFT_DIRECTION,
            light_scale=1.0,
            cpu_evaluates=True,
            publication_default=True,
        ),
        "unlit": LightingRig(
            id="unlit",
            kind="unlit",
            light_scale=1.0,
            cpu_evaluates=True,
            color_critical=True,
        ),
        "studio_three_point": LightingRig(
            id="studio_three_point",
            kind="multi_light",
            light_scale=1.0,
            cpu_evaluates=False,
        ),
    }
)

# Lighting column of the agreed visual presets. Representations and surface
# presets are other slices; this map does not apply them.
VISUAL_PRESET_LIGHTING = MappingProxyType(
    {
        "scientific_default": "studio_soft",
        "publication_flat": "unlit",
        "skeletal": "unlit",
        "vdw_standard": "studio_soft",
        "presentation_plastic": "studio_three_point",
        "metallic_showcase": "studio_three_point",
    }
)


def get_lighting_rig(rig_id: str) -> LightingRig:
    """Return a named rig.

    Does not retune the CPU light and does not switch the rasterizer. Callers
    that render must pass the id through ``RenderSpec.lighting`` or
    ``--lighting``.
    """

    if not isinstance(rig_id, str):
        raise TypeError("lighting rig id must be a string")
    key = rig_id.strip().lower().replace("-", "_")
    try:
        return LIGHTING_RIGS[key]
    except KeyError as exc:
        choices = ", ".join(LIGHTING_RIGS)
        raise ValueError(
            f"unknown lighting rig {rig_id!r}; choose {choices}"
        ) from exc


def require_cpu_lighting(rig: LightingRig) -> LightingRig:
    """Accept a rig the CPU rasterizer evaluates, or raise.

    ``studio_three_point`` raises. The error is the CPU behavior: there is no
    three-light shader and no substitution of ``studio_soft``.
    """

    if not isinstance(rig, LightingRig):
        raise TypeError("expected a LightingRig")
    if rig.kind == "multi_light" or not rig.cpu_evaluates:
        raise CpuLightingError(
            f"lighting {rig.id!r} is a named multi-light contract. The CPU "
            "general renderer has no three-light path and will not substitute "
            "studio_soft or any other one-light shade. A real consumer has to "
            "evaluate the lights; the first one is PyVista, not a CPU alias."
        )
    if rig.light_scale != 1.0:
        raise CpuLightingError(
            "light_scale lives on the lighting rig. The CPU path does not "
            "apply a non-unit light_scale and will not retune studio_soft."
        )
    return rig


def cpu_shader_id(lighting: str) -> str:
    """Return a rig id the CPU shader may evaluate.

    ``studio_three_point`` raises here so a direct raster call cannot fall
    through to the one-light shade.
    """

    return require_cpu_lighting(get_lighting_rig(lighting)).id


def lighting_id_for_cpu(metadata: Mapping[str, Any] | None) -> str:
    """Resolve a plan's lighting id for the CPU rasterizer.

    A missing key is ``studio_soft``. ``studio_three_point`` raises.
    """

    raw = "studio_soft"
    if isinstance(metadata, Mapping) and "lighting" in metadata:
        raw = metadata["lighting"]
    return require_cpu_lighting(get_lighting_rig(str(raw))).id


def read_surface_response(metadata: Mapping[str, Any] | None) -> SurfaceResponse | None:
    """Read a coefficient record. Absent means the legacy shade.

    A preset id string is not looked up here. That would hide a missing
    material record behind ``matte``. ``light_scale`` and ``opacity`` are
    rejected so they cannot ride along on the material record.
    """

    if not isinstance(metadata, Mapping) or "surface_material" not in metadata:
        return None
    raw = metadata["surface_material"]
    if isinstance(raw, str):
        raise TypeError(
            "surface_material on a primitive must be a coefficient record. "
            "The lighting rig will not look up a preset or ignore the field."
        )
    if not isinstance(raw, Mapping):
        raise TypeError("surface_material metadata must be a mapping")
    rejected = sorted({"light_scale", "opacity"} & set(raw))
    if rejected:
        raise ValueError(
            "surface material does not accept "
            f"{', '.join(rejected)}; light_scale stays on the lighting rig "
            "and opacity stays on the object"
        )
    missing = [
        name
        for name in ("ambient", "diffuse", "specular", "shininess")
        if name not in raw
    ]
    if missing:
        raise ValueError(
            "surface_material record is missing " + ", ".join(missing)
        )
    tint = str(raw.get("specular_tint", "none")).strip().lower()
    if tint not in {"none", "white", "base"}:
        raise ValueError("specular_tint must be none, white, or base")
    color = raw.get("specular_color")
    channels: tuple[float, float, float] | None = None
    if color is not None:
        values = tuple(float(channel) for channel in color)
        if len(values) != 3 or any(not np.isfinite(channel) or channel < 0.0 for channel in values):
            raise ValueError("specular_color must be three finite non-negative channels")
        channels = values
    return SurfaceResponse(
        ambient=float(raw["ambient"]),
        diffuse=float(raw["diffuse"]),
        specular=float(raw["specular"]),
        shininess=float(raw["shininess"]),
        specular_tint=tint,
        specular_color=channels,
    )


def flat_or_surface_rgb(
    base_rgb: Any,
    normals: Any,
    metadata: Mapping[str, Any] | None,
    lighting: str,
    *,
    two_sided: bool = True,
) -> np.ndarray | None:
    """RGB for ``unlit`` or a non-matte surface record.

    ``None`` means the caller should keep the historical ``studio_soft``
    shade, including its per-primitive light override.
    """

    base = np.asarray(base_rgb, dtype=float)
    normal = np.asarray(normals, dtype=float)
    if lighting == "unlit":
        return np.broadcast_to(base, normal.shape[:-1] + (3,)).astype(float, copy=True)
    surface = read_surface_response(metadata)
    if surface is None or (
        lighting == "studio_soft" and keeps_legacy_response(surface)
    ):
        return None
    return shade_with_rig(
        base,
        normal,
        surface,
        get_lighting_rig(lighting),
        two_sided=two_sided,
    )


def cylinder_rgb(
    base_rgb: Any,
    radial: Any,
    grid_x: Any,
    grid_y: Any,
    closest_x: Any,
    closest_y: Any,
    half_width: float,
    metadata: Mapping[str, Any] | None,
    lighting: str,
) -> np.ndarray:
    """Bond color. ``studio_soft`` keeps ``0.72 + 0.28 * radial`` for matte."""

    base = np.asarray(base_rgb, dtype=float)
    radial_values = np.asarray(radial, dtype=float)
    if lighting == "unlit":
        return np.broadcast_to(base, radial_values.shape + (3,)).astype(float, copy=True)
    surface = read_surface_response(metadata)
    if surface is not None and not keeps_legacy_response(surface):
        offset_x = (np.asarray(grid_x) - closest_x) / half_width
        offset_y = (np.asarray(grid_y) - closest_y) / half_width
        normals = np.stack((offset_x, -offset_y, radial_values), axis=-1)
        return shade_with_rig(base, normals, surface, get_lighting_rig(lighting))
    illumination = 0.72 + 0.28 * radial_values
    return base[None, None, :] * illumination[..., None]


def mesh_vertex_rgb(
    shaded_base: Any,
    camera_normals: Any,
    *,
    lighting: str,
    apply_surface: bool,
    surface: SurfaceResponse | None,
    ambient: float,
    diffuse: float,
    light: Any,
) -> np.ndarray:
    """Per-vertex color for a mesh that already has camera-space normals."""

    if lighting == "unlit":
        return np.array(shaded_base, dtype=float, copy=True)
    if apply_surface:
        return shade_with_rig(
            shaded_base, camera_normals, surface, get_lighting_rig(lighting)
        )
    illumination = float(ambient) + float(diffuse) * np.abs(
        np.asarray(camera_normals) @ np.asarray(light)
    )
    return np.asarray(shaded_base, dtype=float) * illumination[:, None]


def mesh_face_rgb(
    *,
    lighting: str,
    surface: SurfaceResponse | None,
    rgba: Any,
    shaded_base: Any,
    indices: Any,
    face_normal: Any,
    per_vertex: bool,
) -> np.ndarray:
    """One triangle's RGB when lighting or a surface record replaces Lambert."""

    if lighting == "unlit":
        if per_vertex:
            return np.asarray(shaded_base[indices], dtype=float)
        return np.tile(np.asarray(rgba[:3], dtype=float), (3, 1))
    base = shaded_base[indices] if per_vertex else np.asarray(rgba[:3], dtype=float)
    shaded = shade_with_rig(base, face_normal, surface, get_lighting_rig(lighting))
    triangle = np.asarray(shaded, dtype=float)
    if triangle.ndim == 1:
        return np.tile(triangle, (3, 1))
    return triangle


def note_plan_lighting(metadata: dict[str, Any], *, backend: str, lighting: str) -> None:
    """Record a non-default rig and reject ones this backend cannot evaluate."""

    rig = get_lighting_rig(lighting)
    if str(backend) == "cpu":
        require_cpu_lighting(rig)
    elif rig.id != "studio_soft":
        raise ValueError(
            f"lighting {rig.id!r} is evaluated by the CPU general renderer; "
            f"backend {backend!r} will not silently keep its own lights"
        )
    if rig.id != "studio_soft":
        metadata["lighting"] = rig.id


def keeps_legacy_response(response: SurfaceResponse) -> bool:
    """True when the record is the historical matte Lambert pair.

    Bonds with this record keep the screen-space radial shade
    ``0.72 + 0.28 * radial``. That is the current default, not a new light.
    """

    return (
        response.specular == 0.0
        and response.specular_tint == "none"
        and response.specular_color is None
        and abs(response.ambient - _LEGACY_AMBIENT) <= 1.0e-9
        and abs(response.diffuse - _LEGACY_DIFFUSE) <= 1.0e-9
    )


def _unit_normals(normals: np.ndarray) -> np.ndarray:
    lengths = np.linalg.norm(normals, axis=-1, keepdims=True)
    return normals / np.maximum(lengths, 1.0e-12)


def shade_with_rig(
    base_rgb: Any,
    normals: Any,
    response: SurfaceResponse,
    rig: LightingRig,
    *,
    two_sided: bool = True,
) -> np.ndarray:
    """Evaluate ``response`` under ``rig``.

    ``unlit`` returns the inherited color and does not apply ambient, diffuse,
    or specular. ``studio_soft`` uses the one historical camera-space light.
    ``studio_three_point`` raises. RGB is not clipped.
    """

    require_cpu_lighting(rig)
    normal = np.asarray(normals, dtype=float)
    if normal.shape[-1] != 3:
        raise ValueError("normals must end with 3 components")
    base = np.asarray(base_rgb, dtype=float)
    if rig.kind == "unlit":
        target = normal.shape[:-1] + (3,)
        if base.shape == (3,) or base.shape == target:
            return np.broadcast_to(base, target).astype(float, copy=True)
        if normal.shape == (3,) and base.ndim >= 1 and base.shape[-1] == 3:
            return np.array(base, dtype=float, copy=True)
        raise ValueError("base_rgb does not match normals")
    if rig.direction is None:
        raise CpuLightingError(
            f"lighting {rig.id!r} has no CPU light to evaluate"
        )
    light = np.asarray(rig.direction, dtype=float)
    light = light / max(float(np.linalg.norm(light)), 1.0e-12)
    unit = _unit_normals(normal)
    ndotl = np.tensordot(unit, light, axes=([-1], [0]))
    if two_sided:
        ndotl = np.abs(ndotl)
    else:
        ndotl = np.clip(ndotl, 0.0, 1.0)
    factor = float(response.ambient) + float(response.diffuse) * ndotl
    if base.shape == (3,) and normal.ndim > 1:
        base = np.broadcast_to(base, normal.shape).astype(float, copy=True)
    else:
        base = np.array(base, dtype=float, copy=True)
    if np.ndim(factor) == 0:
        rgb = base * float(factor)
    else:
        rgb = base * np.asarray(factor)[..., None]
    if response.specular > 0.0 and response.specular_tint != "none":
        view = np.array((0.0, 0.0, 1.0), dtype=float)
        half = light + view
        half = half / max(float(np.linalg.norm(half)), 1.0e-12)
        highlight = np.clip(np.tensordot(unit, half, axes=([-1], [0])), 0.0, 1.0)
        highlight = highlight ** float(response.shininess)
        if response.specular_tint == "base":
            specular_rgb = base
        elif response.specular_color is not None:
            specular_rgb = np.asarray(response.specular_color, dtype=float)
        else:
            specular_rgb = np.ones(3, dtype=float)
        if np.ndim(highlight) == 0:
            rgb = rgb + float(response.specular) * float(highlight) * specular_rgb
        else:
            rgb = rgb + float(response.specular) * highlight[..., None] * specular_rgb
    return np.asarray(rgb, dtype=float)


__all__ = [
    "LIGHTING_RIGS",
    "PUBLICATION_DEFAULT_LIGHTING",
    "STUDIO_SOFT_DIRECTION",
    "VISUAL_PRESET_LIGHTING",
    "CpuLightingError",
    "LightingRig",
    "SurfaceResponse",
    "cpu_shader_id",
    "cylinder_rgb",
    "flat_or_surface_rgb",
    "get_lighting_rig",
    "keeps_legacy_response",
    "lighting_id_for_cpu",
    "note_plan_lighting",
    "read_surface_response",
    "require_cpu_lighting",
    "shade_with_rig",
]
