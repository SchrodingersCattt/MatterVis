"""Blinn–Phong surface presets shared by the CPU rasterizer.

``style["material"]`` stays the draw technique (``mesh`` or ``flat``).
Surface response is a separate record, ``surface_material``. This module is
a preset registry, not a per-atom material graph and not a second renderer.

Opacity and ``light_scale`` are not fields of the record. Opacity stays on
the object (isosurfaces remain 0.55 unless the caller sets opacity). Lights
belong to :class:`mat_viewer.render.lighting.LightingRig`, the only lighting
owner. This module does not define a second rig. Ambient and diffuse are
independent and do not have to sum to 1.

``glass_like`` is the Fresnel rim preset. It scales the object's own opacity
by view angle; it does not store an opacity. ``glass`` is reserved because
this rasterizer does not raytrace a refractive index. Specular and Fresnel
evaluation live in ``render.cpu.surface_shade``. The default light direction
is :data:`mat_viewer.render.lighting.STUDIO_SOFT_DIRECTION`.

The historical coefficients 0.68 and 0.32 are the ``matte`` preset and the
builtin isosurface defaults. Storing those defaults beside
``surface_material="matte"`` (or any other preset) is not a conflict.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

import numpy as np

from .lighting import STUDIO_SOFT_DIRECTION

_SPECULAR_TINTS = frozenset({"none", "white", "base"})
_RESERVED_PRESETS = {
    "metal": (
        "surface material 'metal' is reserved for a later PBR model; "
        "use 'metal_like' for the Blinn-Phong preset"
    ),
    "glass": (
        "surface material 'glass' is reserved; CPU rasterization does not "
        "raytrace a refractive index. Use 'glass_like' for the Fresnel rim preset"
    ),
}

# Evaluated by the CPU rasterizer. Plotly and GPU reject these ids.
CPU_ONLY_SURFACE_MATERIALS = frozenset({"hard_plastic", "metal_like", "glass_like"})


@dataclass(frozen=True, slots=True)
class MaterialSpec:
    """Frozen Blinn–Phong surface response.

    ``specular_tint`` selects the highlight color: ``none`` (no highlight),
    ``white``, or ``base`` (the shaded object's own color). ``specular_color``
    is an optional explicit highlight RGB used when tint is ``white``.

    Fresnel fields default to a neutral rim: ``fresnel_power`` 0 leaves
    opacity unchanged. When power is positive, translucent opacity is
    ``base_alpha * lerp(fresnel_center, fresnel_rim, (1-|n·v|)^power)``.
    ``fresnel_specular`` is extra specular coefficient at a grazing angle.
    """

    ambient: float
    diffuse: float
    specular: float
    shininess: float
    specular_tint: str = "none"
    specular_color: tuple[float, float, float] | None = None
    fresnel_power: float = 0.0
    fresnel_center: float = 1.0
    fresnel_rim: float = 1.0
    fresnel_specular: float = 0.0
    fresnel_albedo: float = 0.0
    id: str = ""

    def __post_init__(self) -> None:
        for name in ("ambient", "diffuse", "specular"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a number")
            value = float(value)
            if not np.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and >= 0")
            object.__setattr__(self, name, value)
        shininess = self.shininess
        if isinstance(shininess, bool) or not isinstance(shininess, (int, float)):
            raise TypeError("shininess must be a number")
        shininess = float(shininess)
        if not np.isfinite(shininess) or shininess < 1.0:
            raise ValueError("shininess must be finite and >= 1")
        object.__setattr__(self, "shininess", shininess)
        tint = str(self.specular_tint).strip().lower()
        if tint not in _SPECULAR_TINTS:
            raise ValueError("specular_tint must be none, white, or base")
        object.__setattr__(self, "specular_tint", tint)
        color = self.specular_color
        if color is not None:
            channels = tuple(float(channel) for channel in color)
            if len(channels) != 3 or any(
                not np.isfinite(channel) or channel < 0.0 for channel in channels
            ):
                raise ValueError(
                    "specular_color must be three finite non-negative channels"
                )
            object.__setattr__(self, "specular_color", channels)
        for name, minimum in (
            ("fresnel_power", 0.0),
            ("fresnel_center", 0.0),
            ("fresnel_rim", 0.0),
            ("fresnel_specular", 0.0),
            ("fresnel_albedo", 0.0),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a number")
            value = float(value)
            if not np.isfinite(value) or value < minimum:
                raise ValueError(f"{name} must be finite and >= {minimum:g}")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "id", str(self.id))

    def modulates_opacity(self) -> bool:
        """True when translucent opacity changes with view angle."""

        return self.fresnel_power > 0.0 and (
            abs(self.fresnel_center - 1.0) > 1.0e-9
            or abs(self.fresnel_rim - 1.0) > 1.0e-9
        )

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly record. Opacity and light_scale are omitted."""

        payload = self.coefficient_record()
        if self.id:
            payload = {"id": self.id, **payload}
        return payload

    def coefficient_record(self) -> dict[str, Any]:
        """Shared coefficient mapping for other backends.

        ``id`` stays on the object. The VTK FigureShade evaluator rejects
        an unknown ``id`` field, and it also rejects ``opacity`` and
        ``light_scale``. This mapping is the one those backends can read.
        """

        payload: dict[str, Any] = {
            "ambient": self.ambient,
            "diffuse": self.diffuse,
            "specular": self.specular,
            "shininess": self.shininess,
            "specular_tint": self.specular_tint,
        }
        if self.specular_color is not None:
            payload["specular_color"] = self.specular_color
        if (
            self.fresnel_power > 0.0
            or abs(self.fresnel_center - 1.0) > 1.0e-9
            or abs(self.fresnel_rim - 1.0) > 1.0e-9
            or self.fresnel_specular > 0.0
            or self.fresnel_albedo > 0.0
        ):
            payload["fresnel_power"] = self.fresnel_power
            payload["fresnel_center"] = self.fresnel_center
            payload["fresnel_rim"] = self.fresnel_rim
            payload["fresnel_specular"] = self.fresnel_specular
            payload["fresnel_albedo"] = self.fresnel_albedo
        return payload

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> MaterialSpec:
        """Build a record. Unknown keys such as opacity are rejected."""

        if not isinstance(payload, Mapping):
            raise TypeError("surface material must be a mapping")
        allowed = {
            "id",
            "ambient",
            "diffuse",
            "specular",
            "shininess",
            "specular_tint",
            "specular_color",
            "fresnel_power",
            "fresnel_center",
            "fresnel_rim",
            "fresnel_specular",
            "fresnel_albedo",
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError(
                "surface material does not accept "
                f"{', '.join(unknown)}; opacity and light_scale are not "
                "material fields"
            )
        missing = sorted(
            name
            for name in ("ambient", "diffuse", "specular", "shininess")
            if name not in payload
        )
        if missing:
            raise ValueError(
                f"surface material is missing {', '.join(missing)}"
            )
        return cls(
            ambient=payload["ambient"],
            diffuse=payload["diffuse"],
            specular=payload["specular"],
            shininess=payload["shininess"],
            specular_tint=str(payload.get("specular_tint", "none")),
            specular_color=payload.get("specular_color"),
            fresnel_power=payload.get("fresnel_power", 0.0),
            fresnel_center=payload.get("fresnel_center", 1.0),
            fresnel_rim=payload.get("fresnel_rim", 1.0),
            fresnel_specular=payload.get("fresnel_specular", 0.0),
            fresnel_albedo=payload.get("fresnel_albedo", 0.0),
            id=str(payload.get("id", "")),
        )


def _preset(
    preset_id: str,
    ambient: float,
    diffuse: float,
    specular: float,
    shininess: float,
    tint: str,
    color: tuple[float, float, float] | None = None,
    *,
    fresnel_power: float = 0.0,
    fresnel_center: float = 1.0,
    fresnel_rim: float = 1.0,
    fresnel_specular: float = 0.0,
    fresnel_albedo: float = 0.0,
) -> MaterialSpec:
    return MaterialSpec(
        ambient=ambient,
        diffuse=diffuse,
        specular=specular,
        shininess=shininess,
        specular_tint=tint,
        specular_color=color,
        fresnel_power=fresnel_power,
        fresnel_center=fresnel_center,
        fresnel_rim=fresnel_rim,
        fresnel_specular=fresnel_specular,
        fresnel_albedo=fresnel_albedo,
        id=preset_id,
    )


class MaterialLibrary:
    """Read-only preset registry. Callers cannot mutate the stored records."""

    def __init__(self, presets: Mapping[str, MaterialSpec]) -> None:
        self._presets = MappingProxyType(
            {str(key): value for key, value in presets.items()}
        )

    def ids(self) -> tuple[str, ...]:
        return tuple(self._presets)

    def get(self, preset_id: str) -> MaterialSpec:
        """Return one preset. ``metal`` is reserved and never aliases ``metal_like``."""

        if not isinstance(preset_id, str):
            raise TypeError("surface material id must be a string")
        key = preset_id.strip().lower().replace("-", "_")
        if key in _RESERVED_PRESETS:
            raise ValueError(_RESERVED_PRESETS[key])
        try:
            return self._presets[key]
        except KeyError as exc:
            choices = ", ".join(self._presets)
            raise ValueError(
                f"unknown surface material {preset_id!r}; choose {choices}"
            ) from exc

    def __contains__(self, preset_id: object) -> bool:
        return isinstance(preset_id, str) and preset_id.strip().lower().replace(
            "-", "_"
        ) in self._presets


MATERIAL_LIBRARY = MaterialLibrary(
    {
        "matte": _preset("matte", 0.68, 0.32, 0.0, 32.0, "none"),
        "hard_plastic": _preset(
            "hard_plastic",
            0.30,
            0.60,
            0.35,
            48.0,
            "white",
            (1.0, 1.0, 1.0),
        ),
        "metal_like": _preset("metal_like", 0.22, 0.38, 0.75, 96.0, "base"),
        # Clear face, saturated grazing rim, white sheen. Opacity stays on
        # the object and is scaled by these Fresnel factors.
        "glass_like": _preset(
            "glass_like",
            0.36,
            0.54,
            0.32,
            112.0,
            "white",
            (1.0, 1.0, 1.0),
            fresnel_power=1.5,
            fresnel_center=0.12,
            fresnel_rim=1.6,
            fresnel_specular=0.28,
            fresnel_albedo=1.0,
        ),
    }
)


def get_surface_material(preset_id: str) -> MaterialSpec:
    """Look up a phase-1 Blinn–Phong preset by id."""

    return MATERIAL_LIBRARY.get(preset_id)


def _close(left: object, right: float) -> bool:
    if isinstance(left, bool) or not isinstance(left, (int, float)):
        return False
    value = float(left)
    return np.isfinite(value) and abs(value - right) <= 1.0e-9


def _pair_is_acceptable(
    spec: MaterialSpec,
    ambient: float | None,
    diffuse: float | None,
) -> bool:
    if ambient is None and diffuse is None:
        return True
    matte = MATERIAL_LIBRARY.get("matte")
    historical = (ambient is None or _close(ambient, matte.ambient)) and (
        diffuse is None or _close(diffuse, matte.diffuse)
    )
    matches = (ambient is None or _close(ambient, spec.ambient)) and (
        diffuse is None or _close(diffuse, spec.diffuse)
    )
    return historical or matches


def resolve_surface_material(
    surface_material: str | MaterialSpec,
    *,
    ambient: float | None = None,
    diffuse: float | None = None,
    specular: float | None = None,
    shininess: float | None = None,
) -> MaterialSpec:
    """Resolve a preset without treating historical 0.68/0.32 as a conflict.

    Stored ambient and diffuse that equal the builtin defaults are defaults.
    Coefficients that already equal the requested preset are not a conflict
    either. Any other stored pair is an explicit override and raises.
    """

    if isinstance(surface_material, MaterialSpec):
        spec = surface_material
    else:
        spec = MATERIAL_LIBRARY.get(surface_material)
    label = spec.id or "custom"
    if not _pair_is_acceptable(spec, ambient, diffuse):
        raise ValueError(
            "stored ambient/diffuse "
            f"({ambient}, {diffuse}) conflict with surface material {label!r} "
            f"({spec.ambient:g}, {spec.diffuse:g}); historical defaults "
            "0.68 and 0.32 are not a conflict"
        )
    if specular is not None and not _close(specular, spec.specular):
        raise ValueError(
            f"stored specular {specular} conflicts with surface material "
            f"{label!r} ({spec.specular:g})"
        )
    if shininess is not None and not _close(shininess, spec.shininess):
        raise ValueError(
            f"stored shininess {shininess} conflicts with surface material "
            f"{label!r} ({spec.shininess:g})"
        )
    return spec


def apply_surface_material(
    style: Mapping[str, Any],
    surface_material: str | MaterialSpec,
) -> dict[str, Any]:
    """Return a style copy with ``surface_material`` set.

    Does not change ``material`` (mesh versus flat). Builtin ambient 0.68 and
    diffuse 0.32, including ``isosurface_ambient`` / ``isosurface_diffuse``,
    stay on the style and do not block the preset.
    """

    if not isinstance(style, Mapping):
        raise TypeError("style must be a mapping")
    updated = dict(style)
    ambient = updated["ambient"] if "ambient" in updated else updated.get(
        "isosurface_ambient"
    )
    diffuse = updated["diffuse"] if "diffuse" in updated else updated.get(
        "isosurface_diffuse"
    )
    spec = resolve_surface_material(
        surface_material,
        ambient=ambient,
        diffuse=diffuse,
        specular=updated.get("specular"),
        shininess=updated.get("shininess"),
    )
    updated["surface_material"] = spec.id
    return updated


def attach_surface_material(primitives: list[Any], surface_material: str | MaterialSpec | None) -> list[Any]:
    """Stamp a preset onto atom, bond, and isosurface triangle meshes.

    Line strokes stay untouched, so wireframe, ORTEP hatch, and axes do not
    grow a fake highlight. Polyhedra and BFDH are not in the stamped kinds.
    """

    if surface_material is None:
        return primitives
    spec = (
        surface_material
        if isinstance(surface_material, MaterialSpec)
        else get_surface_material(surface_material)
    )
    payload = spec.coefficient_record()
    stamped: list[Any] = []
    for primitive in primitives:
        metadata = getattr(primitive, "metadata", None)
        kind = metadata.get("kind") if isinstance(metadata, Mapping) else None
        if getattr(primitive, "triangles", None) is not None and kind in {
            "atom",
            "bond",
            "isosurface",
        }:
            updated = dict(metadata)
            updated["surface_material"] = payload
            updated["surface_material_id"] = spec.id
            primitive = replace(primitive, metadata=updated)
        stamped.append(primitive)
    return stamped


def material_from_metadata(value: Any) -> MaterialSpec | None:
    """Read a primitive metadata record, or return None when it is absent."""

    if value is None:
        return None
    if isinstance(value, MaterialSpec):
        return value
    if isinstance(value, Mapping):
        return MaterialSpec.from_mapping(value)
    raise TypeError("surface_material metadata must be a mapping")


def preserves_legacy_cylinder_shade(material: MaterialSpec) -> bool:
    """True when a bond should keep today's screen-space radial shade.

    Analytic bonds are shaded as ``0.72 + 0.28 * radial``, not as the
    0.68/0.32 Lambert term used for spheres and isosurfaces. ``matte``
    keeps that bond shade so the default PNG stays the same.
    """

    matte = MATERIAL_LIBRARY.get("matte")
    return (
        material.specular == 0.0
        and material.specular_tint == "none"
        and material.specular_color is None
        and material.ambient == matte.ambient
        and material.diffuse == matte.diffuse
    )


# Agreed visual-preset names. Only ``scientific_default`` is selectable
# through ``surface_material`` today. ``vdw`` and ``flat_illustration`` are
# not representations yet; ``space_filling`` is not a van der Waals alias.
# ``surface_material`` values are preset ids from :data:`MATERIAL_LIBRARY`.
# ``unlit`` is a lighting rig, not a surface preset.
VISUAL_PRESETS: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "scientific_default": MappingProxyType(
            {
                "representation": "ball_stick",
                "surface_material": "matte",
                "lighting": "studio_soft",
            }
        ),
        "publication_flat": MappingProxyType(
            {
                "representation": "flat_illustration",
                "surface_material": "matte",
                "lighting": "unlit",
            }
        ),
        "skeletal": MappingProxyType(
            {
                "representation": "wireframe",
                "surface_material": "matte",
                "lighting": "unlit",
            }
        ),
        "vdw_standard": MappingProxyType(
            {
                "representation": "vdw",
                "surface_material": "matte",
                "lighting": "studio_soft",
            }
        ),
        "presentation_plastic": MappingProxyType(
            {
                "representation": "ball_stick",
                "surface_material": "hard_plastic",
                "lighting": "studio_three_point",
            }
        ),
        "metallic_showcase": MappingProxyType(
            {
                "representation": "vdw",
                "surface_material": "metal_like",
                "lighting": "studio_three_point",
            }
        ),
    }
)


def shade_blinn_phong(
    base_rgb: Any,
    normals: Any,
    material: MaterialSpec,
    *,
    light: Any = None,
    two_sided: bool = True,
) -> np.ndarray:
    """Shade ``base_rgb`` with one camera-space Blinn–Phong light.

    ``base_rgb`` is ``(..., 3)`` and ``normals`` is ``(..., 3)`` in camera
    space. The default light is :data:`studio_soft`. Final RGB is not clipped.
    """

    base = np.asarray(base_rgb, dtype=float)
    normal = np.asarray(normals, dtype=float)
    if light is None:
        direction = STUDIO_SOFT_DIRECTION
    else:
        direction = light
    light_vector = np.asarray(direction, dtype=float)
    light_vector = light_vector / max(float(np.linalg.norm(light_vector)), 1.0e-12)
    lengths = np.linalg.norm(normal, axis=-1, keepdims=True)
    unit = normal / np.maximum(lengths, 1.0e-12)
    unit = np.where(lengths > 1.0e-12, unit, 0.0)
    ndotl = unit @ light_vector
    if two_sided:
        ndotl = np.abs(ndotl)
    else:
        ndotl = np.clip(ndotl, 0.0, 1.0)
    rgb = base * (float(material.ambient) + float(material.diffuse) * ndotl[..., None])
    if material.specular > 0.0 and material.specular_tint != "none":
        view = np.array((0.0, 0.0, 1.0), dtype=float)
        half_vector = light_vector + view
        half_vector = half_vector / max(float(np.linalg.norm(half_vector)), 1.0e-12)
        highlight = np.clip(unit @ half_vector, 0.0, 1.0) ** float(material.shininess)
        if material.specular_tint == "base":
            specular_rgb = base
        elif material.specular_color is not None:
            specular_rgb = np.asarray(material.specular_color, dtype=float)
        else:
            specular_rgb = np.ones(3, dtype=float)
        rgb = rgb + float(material.specular) * highlight[..., None] * specular_rgb
    return rgb


__all__ = [
    "CPU_ONLY_SURFACE_MATERIALS",
    "MATERIAL_LIBRARY",
    "VISUAL_PRESETS",
    "MaterialLibrary",
    "MaterialSpec",
    "apply_surface_material",
    "attach_surface_material",
    "get_surface_material",
    "material_from_metadata",
    "preserves_legacy_cylinder_shade",
    "resolve_surface_material",
    "shade_blinn_phong",
]
