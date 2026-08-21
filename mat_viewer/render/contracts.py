"""Backend-neutral contracts for deterministic MatterVis rendering.

The objects in this module deliberately carry data only.  Geometry builders,
the CPU renderer, and optional interactive adapters all consume the same
``RenderPlan`` without importing one another.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping, TypeAlias

import numpy as np


Projection = Literal["orthographic", "perspective"]
Backend = Literal["cpu", "matplotlib", "plotly"]
DisplayMode = Literal["formula_unit", "unit_cell", "asymmetric_unit", "cluster"]
RGBA: TypeAlias = tuple[float, float, float, float]
RENDER_PLAN_SCHEMA = "mattervis.render-plan/v1"
RENDER_RESULT_SCHEMA = "mattervis.render-result/v1"


def _readonly_array(
    value: Any,
    *,
    shape_tail: tuple[int, ...],
    dtype: Any = float,
    name: str,
) -> np.ndarray:
    array = np.array(value, dtype=dtype, copy=True)
    if array.ndim < len(shape_tail) or array.shape[-len(shape_tail) :] != shape_tail:
        raise ValueError(f"{name} must end with shape {shape_tail}; got {array.shape}")
    if np.issubdtype(array.dtype, np.floating) and not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must contain finite values")
    array.setflags(write=False)
    return array


def _vector3(value: Any, *, name: str) -> tuple[float, float, float]:
    array = np.asarray(value, dtype=float)
    if array.shape != (3,) or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must be a finite three-dimensional vector")
    return tuple(float(component) for component in array)


def _rgba(value: Any, *, name: str = "rgba") -> RGBA:
    array = np.asarray(value, dtype=float)
    if array.shape not in {(3,), (4,)} or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must contain three or four finite channels")
    if array.shape == (3,):
        array = np.append(array, 1.0)
    if np.any(array < 0.0) or np.any(array > 1.0):
        raise ValueError(f"{name} channels must lie in [0, 1]")
    return tuple(float(channel) for channel in array)  # type: ignore[return-value]


def _metadata(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


@dataclass(frozen=True, slots=True)
class CameraSpec:
    """Complete physical camera specification.

    ``position`` and ``target`` are world-space Cartesian coordinates.  The
    camera looks from position to target; camera-space points in front of the
    eye have negative z and a positive depth of ``-z``.
    """

    position: tuple[float, float, float] = (8.0, 8.0, 8.0)
    target: tuple[float, float, float] = (0.0, 0.0, 0.0)
    up: tuple[float, float, float] = (0.0, 0.0, 1.0)
    projection: Projection = "orthographic"
    fov_y_deg: float = 45.0
    near: float = 0.01
    far: float = 10_000.0
    ortho_scale: float = 5.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "position", _vector3(self.position, name="position"))
        object.__setattr__(self, "target", _vector3(self.target, name="target"))
        object.__setattr__(self, "up", _vector3(self.up, name="up"))
        if self.projection not in ("orthographic", "perspective"):
            raise ValueError("projection must be 'orthographic' or 'perspective'")
        if not 0.0 < float(self.fov_y_deg) < 179.0:
            raise ValueError("fov_y_deg must lie in (0, 179)")
        if not 0.0 < float(self.near) < float(self.far):
            raise ValueError("camera clipping distances must satisfy 0 < near < far")
        if not np.isfinite(self.ortho_scale) or float(self.ortho_scale) <= 0.0:
            raise ValueError("ortho_scale must be positive")
        eye = np.asarray(self.position) - np.asarray(self.target)
        if float(np.linalg.norm(eye)) < 1e-12:
            raise ValueError("camera position must differ from target")
        if float(np.linalg.norm(np.cross(eye, np.asarray(self.up)))) < 1e-12:
            raise ValueError("camera up must not be parallel to its view direction")

    @classmethod
    def looking_along(
        cls,
        direction: Any,
        *,
        target: Any = (0.0, 0.0, 0.0),
        up: Any = (0.0, 0.0, 1.0),
        distance: float = 10.0,
        projection: Projection = "orthographic",
        ortho_scale: float = 5.0,
        fov_y_deg: float = 45.0,
        near: float = 0.01,
        far: float = 10_000.0,
    ) -> "CameraSpec":
        """Construct a camera from a direction pointing toward the viewer."""
        target_vec = np.asarray(_vector3(target, name="target"), dtype=float)
        view = np.asarray(_vector3(direction, name="direction"), dtype=float)
        norm = float(np.linalg.norm(view))
        if norm < 1e-12:
            raise ValueError("direction must be non-zero")
        position = target_vec + view / norm * float(distance)
        up_vector = np.asarray(_vector3(up, name="up"), dtype=float)
        if float(np.linalg.norm(np.cross(view, up_vector))) < 1e-10:
            for fallback in (
                np.asarray([0.0, 1.0, 0.0]),
                np.asarray([1.0, 0.0, 0.0]),
                np.asarray([0.0, 0.0, 1.0]),
            ):
                if float(np.linalg.norm(np.cross(view, fallback))) >= 1e-10:
                    up_vector = fallback
                    break
        return cls(
            position=tuple(position),
            target=tuple(target_vec),
            up=tuple(up_vector),
            projection=projection,
            ortho_scale=ortho_scale,
            fov_y_deg=fov_y_deg,
            near=near,
            far=far,
        )

    def with_fit(
        self, *, target: Any, distance: float, ortho_scale: float
    ) -> "CameraSpec":
        """Return a fitted camera while retaining its viewing direction."""
        old_direction = np.asarray(self.position) - np.asarray(self.target)
        old_direction /= max(float(np.linalg.norm(old_direction)), 1e-12)
        target_vec = np.asarray(_vector3(target, name="target"), dtype=float)
        return replace(
            self,
            position=tuple(target_vec + old_direction * float(distance)),
            target=tuple(target_vec),
            ortho_scale=float(ortho_scale),
        )


@dataclass(frozen=True, slots=True)
class ViewSpec:
    display: DisplayMode = "formula_unit"

    def __post_init__(self) -> None:
        supported = {"formula_unit", "unit_cell", "asymmetric_unit", "cluster"}
        value = str(self.display).strip().lower()
        if value not in supported:
            choices = ", ".join(sorted(supported))
            raise ValueError(
                f"unsupported display mode {self.display!r}; choose one of {choices}"
            )
        object.__setattr__(self, "display", value)


@dataclass(frozen=True, slots=True)
class RenderSpec:
    """Backend-independent visual intent."""

    representation: str = "ball_stick"
    shading: Literal["smooth", "flat"] = "smooth"
    backend: Backend = "cpu"
    width: int = 900
    height: int = 720
    scale: int = 2
    background: RGBA = (1.0, 1.0, 1.0, 1.0)
    atom_scale: float = 1.0
    bond_radius: float = 0.15
    show_hydrogen: bool = False
    show_cell: bool = True
    show_labels: bool = False
    aromatic_rings: Literal["bonds", "circle", "disk"] = "bonds"
    ortep_probability: float = 0.5
    ortep_mode: Literal["solid", "axes", "hatch"] = "solid"
    missing_adp_policy: Literal["error", "sphere"] = "error"
    sphere_detail: tuple[int, int] = (12, 20)
    cylinder_sides: int = 12

    def __post_init__(self) -> None:
        if self.backend not in ("cpu", "matplotlib", "plotly"):
            raise ValueError("backend must be 'cpu', 'matplotlib', or 'plotly'")
        if any(
            int(value) != value or int(value) <= 0
            for value in (self.width, self.height, self.scale)
        ):
            raise ValueError("width, height, and scale must be positive integers")
        if self.representation not in {
            "ball_stick",
            "ball",
            "space_filling",
            "stick",
            "wireframe",
            "ortep",
        }:
            raise ValueError("unknown representation")
        if self.shading not in {"smooth", "flat"}:
            raise ValueError("unknown shading")
        if self.ortep_mode not in {"solid", "axes", "hatch"}:
            raise ValueError("unknown ORTEP mode")
        if self.missing_adp_policy not in {"error", "sphere"}:
            raise ValueError("missing_adp_policy must be 'error' or 'sphere'")
        if self.representation != "ortep" and self.ortep_mode != "solid":
            raise ValueError("ortep_mode axes/hatch requires representation='ortep'")
        object.__setattr__(
            self, "background", _rgba(self.background, name="background")
        )
        if not np.isfinite(self.atom_scale) or float(self.atom_scale) <= 0.0:
            raise ValueError("atom_scale must be positive")
        if not np.isfinite(self.bond_radius) or float(self.bond_radius) <= 0.0:
            raise ValueError("bond_radius must be positive")
        if self.aromatic_rings not in ("bonds", "circle", "disk"):
            raise ValueError("aromatic_rings must be bonds, circle, or disk")
        if not 0.0 < float(self.ortep_probability) < 1.0:
            raise ValueError("ortep_probability must lie in (0, 1)")
        lat, lon = self.sphere_detail
        if int(lat) != lat or int(lon) != lon or int(lat) < 2 or int(lon) < 3:
            raise ValueError("sphere_detail must be at least (2, 3)")
        if (
            int(self.cylinder_sides) != self.cylinder_sides
            or int(self.cylinder_sides) < 3
        ):
            raise ValueError("cylinder_sides must be at least 3")


@dataclass(frozen=True, slots=True)
class TriangleMeshPrimitive:
    semantic_id: str
    vertices: np.ndarray
    triangles: np.ndarray
    rgba: RGBA = (0.5, 0.5, 0.5, 1.0)
    vertex_normals: np.ndarray | None = None
    double_sided: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.semantic_id:
            raise ValueError("semantic_id must be non-empty")
        vertices = _readonly_array(self.vertices, shape_tail=(3,), name="vertices")
        if vertices.ndim != 2:
            raise ValueError("vertices must have shape (N, 3)")
        triangles = _readonly_array(
            self.triangles, shape_tail=(3,), dtype=np.int64, name="triangles"
        )
        if triangles.ndim != 2:
            raise ValueError("triangles must have shape (M, 3)")
        if triangles.size and (triangles.min() < 0 or triangles.max() >= len(vertices)):
            raise ValueError("triangles contain an out-of-range vertex index")
        normals = self.vertex_normals
        if normals is not None:
            normals = _readonly_array(normals, shape_tail=(3,), name="vertex_normals")
            if normals.shape != vertices.shape:
                raise ValueError("vertex_normals must match vertices")
        object.__setattr__(self, "vertices", vertices)
        object.__setattr__(self, "triangles", triangles)
        object.__setattr__(self, "vertex_normals", normals)
        object.__setattr__(self, "rgba", _rgba(self.rgba))
        object.__setattr__(self, "metadata", _metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class LinePrimitive:
    semantic_id: str
    segments: np.ndarray
    rgba: RGBA = (0.1, 0.1, 0.1, 1.0)
    width_px: float = 1.5
    dash: tuple[float, ...] = ()
    depth_test: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.semantic_id:
            raise ValueError("semantic_id must be non-empty")
        segments = _readonly_array(self.segments, shape_tail=(2, 3), name="segments")
        if segments.ndim != 3:
            raise ValueError("segments must have shape (N, 2, 3)")
        if not np.isfinite(self.width_px) or float(self.width_px) <= 0.0:
            raise ValueError("width_px must be positive")
        dash = tuple(float(item) for item in self.dash)
        if any(not np.isfinite(item) or item <= 0.0 for item in dash):
            raise ValueError("dash entries must be positive")
        object.__setattr__(self, "segments", segments)
        object.__setattr__(self, "rgba", _rgba(self.rgba))
        object.__setattr__(self, "dash", dash)
        object.__setattr__(self, "metadata", _metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class TextPrimitive:
    """World-anchored text with optional opaque-surface anchor occlusion.

    Depth testing hides the whole label when its anchor is behind nearer
    opaque mesh or line geometry.  Transparent geometry does not hide labels,
    and the pixel offset does not move the depth-test sample away from the
    anchor.  Camera clipping applies regardless of ``depth_test``.
    """

    semantic_id: str
    position: tuple[float, float, float]
    text: str
    rgba: RGBA = (0.05, 0.05, 0.05, 1.0)
    size_pt: float = 8.0
    offset_px: tuple[float, float] = (0.0, 0.0)
    depth_test: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.semantic_id:
            raise ValueError("semantic_id must be non-empty")
        object.__setattr__(self, "position", _vector3(self.position, name="position"))
        object.__setattr__(self, "rgba", _rgba(self.rgba))
        if not np.isfinite(self.size_pt) or float(self.size_pt) <= 0.0:
            raise ValueError("size_pt must be positive")
        offset = np.asarray(self.offset_px, dtype=float)
        if offset.shape != (2,) or not np.all(np.isfinite(offset)):
            raise ValueError("offset_px must contain two finite values")
        object.__setattr__(self, "offset_px", tuple(float(value) for value in offset))
        object.__setattr__(self, "metadata", _metadata(self.metadata))


Primitive: TypeAlias = TriangleMeshPrimitive | LinePrimitive | TextPrimitive


@dataclass(frozen=True, slots=True)
class ViewportPlan:
    """One independently projected panel inside a render plan."""

    semantic_id: str
    camera: CameraSpec
    primitives: tuple[Primitive, ...]
    rect: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)

    def __post_init__(self) -> None:
        if not self.semantic_id:
            raise ValueError("semantic_id must be non-empty")
        rect = np.asarray(self.rect, dtype=float)
        if rect.shape != (4,) or not np.all(np.isfinite(rect)):
            raise ValueError("rect must contain four finite values")
        x, y, width, height = rect
        if x < 0.0 or y < 0.0 or width <= 0.0 or height <= 0.0:
            raise ValueError(
                "viewport rect must have non-negative origin and positive size"
            )
        if x + width > 1.0 + 1e-12 or y + height > 1.0 + 1e-12:
            raise ValueError("viewport rect must fit inside [0, 1] x [0, 1]")
        primitive_ids = [primitive.semantic_id for primitive in self.primitives]
        if len(primitive_ids) != len(set(primitive_ids)):
            raise ValueError("primitive semantic_id values must be unique per viewport")
        object.__setattr__(self, "rect", tuple(float(item) for item in rect))
        object.__setattr__(self, "primitives", tuple(self.primitives))


@dataclass(frozen=True, slots=True)
class RenderPlan:
    """Immutable, backend-neutral render plan."""

    width: int
    height: int
    background: RGBA
    viewports: tuple[ViewportPlan, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    schema: str = RENDER_PLAN_SCHEMA

    def __post_init__(self) -> None:
        if int(self.width) <= 0 or int(self.height) <= 0:
            raise ValueError("render dimensions must be positive")
        if not self.viewports:
            raise ValueError("render plan needs at least one viewport")
        viewport_ids = [viewport.semantic_id for viewport in self.viewports]
        if len(viewport_ids) != len(set(viewport_ids)):
            raise ValueError("viewport semantic_id values must be unique")
        object.__setattr__(
            self, "background", _rgba(self.background, name="background")
        )
        object.__setattr__(self, "viewports", tuple(self.viewports))
        object.__setattr__(self, "metadata", _metadata(self.metadata))
        object.__setattr__(self, "warnings", tuple(str(item) for item in self.warnings))

    @property
    def primitives(self) -> tuple[Primitive, ...]:
        """Primitives in the sole viewport; convenient for ordinary figures."""
        if len(self.viewports) != 1:
            raise AttributeError("multi-viewport plans do not have one primitive list")
        return self.viewports[0].primitives

    @property
    def camera(self) -> CameraSpec:
        if len(self.viewports) != 1:
            raise AttributeError("multi-viewport plans do not have one camera")
        return self.viewports[0].camera

    def fingerprint(self) -> str:
        """Return a deterministic SHA-256 over all render-affecting data."""
        digest = sha256()
        header = {
            "schema": self.schema,
            "width": self.width,
            "height": self.height,
            "background": self.background,
            "warnings": self.warnings,
            "metadata": _json_safe(self.metadata),
        }
        digest.update(
            json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
        )
        for viewport in sorted(self.viewports, key=lambda item: item.semantic_id):
            digest.update(viewport.semantic_id.encode())
            digest.update(json.dumps(viewport.rect, separators=(",", ":")).encode())
            digest.update(
                json.dumps(_camera_dict(viewport.camera), sort_keys=True).encode()
            )
            for primitive in sorted(
                viewport.primitives, key=lambda item: item.semantic_id
            ):
                digest.update(type(primitive).__name__.encode())
                digest.update(primitive.semantic_id.encode())
                digest.update(
                    json.dumps(
                        _primitive_scalar_dict(primitive), sort_keys=True
                    ).encode()
                )
                if isinstance(primitive, TriangleMeshPrimitive):
                    digest.update(
                        primitive.vertices.astype("<f8", copy=False).tobytes()
                    )
                    digest.update(
                        primitive.triangles.astype("<i8", copy=False).tobytes()
                    )
                    if primitive.vertex_normals is not None:
                        digest.update(
                            primitive.vertex_normals.astype("<f8", copy=False).tobytes()
                        )
                elif isinstance(primitive, LinePrimitive):
                    digest.update(
                        primitive.segments.astype("<f8", copy=False).tobytes()
                    )
        return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class RenderResult:
    schema: str
    backend: str
    format: str
    width: int
    height: int
    plan_sha256: str
    output_sha256: str
    output: Path | None = None
    data: bytes | None = field(default=None, repr=False)
    warnings: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "output", Path(self.output) if self.output is not None else None
        )
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "metadata", _metadata(self.metadata))


def _camera_dict(camera: CameraSpec) -> dict[str, Any]:
    return {
        name: getattr(camera, name)
        for name in (
            "position",
            "target",
            "up",
            "projection",
            "fov_y_deg",
            "near",
            "far",
            "ortho_scale",
        )
    }


def _primitive_scalar_dict(primitive: Primitive) -> dict[str, Any]:
    result: dict[str, Any] = {
        "rgba": primitive.rgba,
        "metadata": _json_safe(primitive.metadata),
    }
    if isinstance(primitive, TriangleMeshPrimitive):
        result["double_sided"] = primitive.double_sided
    elif isinstance(primitive, LinePrimitive):
        result.update(
            width_px=primitive.width_px,
            dash=primitive.dash,
            depth_test=primitive.depth_test,
        )
    else:
        result.update(
            position=primitive.position,
            text=primitive.text,
            size_pt=primitive.size_pt,
            offset_px=primitive.offset_px,
            depth_test=primitive.depth_test,
        )
    return result


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


__all__ = [
    "Backend",
    "CameraSpec",
    "LinePrimitive",
    "Primitive",
    "Projection",
    "RGBA",
    "RENDER_PLAN_SCHEMA",
    "RENDER_RESULT_SCHEMA",
    "RenderPlan",
    "RenderResult",
    "RenderSpec",
    "TextPrimitive",
    "TriangleMeshPrimitive",
    "ViewSpec",
    "ViewportPlan",
]
