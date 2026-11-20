"""Validated draw packets lowered from a backend-neutral :class:`RenderPlan`."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from types import MappingProxyType
from typing import Any, Literal, Mapping

import numpy as np

from .contracts import (
    CameraSpec,
    LinePrimitive,
    RenderPlan,
    TextPrimitive,
    TriangleMeshPrimitive,
)
from .gpu_errors import GPUUnsupportedError


# ``cylinder_mesh`` rejects endpoints closer than this. A zero-length
# instance makes the cylinder shader's axis normalisation non-finite.
_MIN_CYLINDER_LENGTH = 1e-12
# WebGPU line-list rasters one pixel. Any other width is composited on the CPU.
_GPU_LINE_WIDTH_PX = 1.0
_LINE_WIDTH_EPSILON_PX = 1e-6
_CPU_OVERLAY_LINE_KINDS = frozenset({"ortep_hatch", "ortep_axes"})


@dataclass(frozen=True, slots=True)
class DrawPacket:
    """Validated GPU draw packet produced by :func:`lower_plan`.

    ``vertices`` are world-space positions.  For meshes, ``indices`` contains
    triangle indices.  For lines, each pair of vertices is one line segment
    and ``indices`` is ``None``.  The contract stores one constant opaque
    colour per packet rather than inventing a material system.
    """

    kind: Literal["mesh", "line", "sphere", "cylinder", "ellipsoid"]
    semantic_id: str
    viewport_id: str
    vertices: np.ndarray
    indices: np.ndarray | None
    rgba: tuple[float, float, float, float]
    rect: tuple[float, float, float, float]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in {"mesh", "line", "sphere", "cylinder", "ellipsoid"}:
            raise ValueError(
                "DrawPacket.kind must be 'mesh', 'line', 'sphere', 'cylinder', or 'ellipsoid'"
            )
        if not self.semantic_id or not self.viewport_id:
            raise ValueError("DrawPacket ids must be non-empty")
        vertices = np.asarray(self.vertices, dtype=np.float32)
        if vertices.ndim != 2 or vertices.shape[1] != 3:
            raise ValueError("DrawPacket.vertices must have shape (N, 3)")
        if not np.all(np.isfinite(vertices)):
            raise ValueError("DrawPacket.vertices must be finite")
        if self.kind == "mesh":
            if self.indices is None:
                raise ValueError("mesh packets require indices")
            indices = np.asarray(self.indices, dtype=np.uint32)
            if indices.ndim != 2 or indices.shape[1] != 3:
                raise ValueError("mesh packet indices must have shape (M, 3)")
            if indices.size and int(indices.max()) >= len(vertices):
                raise ValueError("mesh packet index is out of range")
        elif self.kind == "line":
            if self.indices is not None:
                raise ValueError("line packets do not use indices")
            if len(vertices) % 2:
                raise ValueError("line packet vertices must contain pairs")
            indices = None
        elif self.kind == "sphere":
            if self.indices is not None or len(vertices) != 1:
                raise ValueError("sphere packets need one center and no indices")
            if float(self.metadata.get("radius", 0.0)) <= 0.0:
                raise ValueError("sphere packets require a positive radius")
            indices = None
        elif self.kind == "cylinder":
            if self.indices is not None or len(vertices) != 2:
                raise ValueError("cylinder packets need two endpoints and no indices")
            if float(self.metadata.get("radius", 0.0)) <= 0.0:
                raise ValueError("cylinder packets require a positive radius")
            if float(np.linalg.norm(vertices[1] - vertices[0])) < _MIN_CYLINDER_LENGTH:
                raise ValueError("cylinder packets require distinct endpoints")
            indices = None
        else:
            if self.indices is not None or len(vertices) != 1:
                raise ValueError("ellipsoid packets need one center and no indices")
            lengths = np.asarray(self.metadata.get("lengths", []), dtype=float)
            axes = np.asarray(self.metadata.get("axes", []), dtype=float)
            if lengths.shape != (3,) or axes.shape != (3, 3):
                raise ValueError("ellipsoid packets require lengths (3,) and axes (3, 3)")
            if not np.all(np.isfinite(lengths)) or not np.all(np.isfinite(axes)):
                raise ValueError("ellipsoid lengths and axes must be finite")
            indices = None
        rgba = tuple(float(channel) for channel in self.rgba)
        if len(rgba) != 4 or not np.all(np.isfinite(rgba)):
            raise ValueError("DrawPacket.rgba must contain four finite channels")
        if any(channel < 0.0 or channel > 1.0 for channel in rgba):
            raise ValueError("DrawPacket.rgba channels must lie in [0, 1]")
        if rgba[3] < 1.0 - 1e-7 and self.kind == "line":
            raise GPUUnsupportedError(
                f"GPU line packets must be opaque; {self.semantic_id!r} is transparent"
            )
        rect = tuple(float(item) for item in self.rect)
        if len(rect) != 4 or not np.all(np.isfinite(rect)):
            raise ValueError("DrawPacket.rect must contain four finite values")
        if rect[2] <= 0.0 or rect[3] <= 0.0:
            raise ValueError("DrawPacket.rect must have positive size")
        vertices.setflags(write=False)
        if indices is not None:
            indices.setflags(write=False)
        object.__setattr__(self, "vertices", vertices)
        object.__setattr__(self, "indices", indices)
        object.__setattr__(self, "rgba", rgba)
        object.__setattr__(self, "rect", rect)
        object.__setattr__(
            self, "metadata", MappingProxyType(dict(self.metadata or {}))
        )


@dataclass(frozen=True, slots=True)
class LoweredPlan:
    """The validated packet stream consumed by the WebGPU renderer."""

    width: int
    height: int
    background: tuple[float, float, float, float]
    packets: tuple[DrawPacket, ...]
    plan_sha256: str
    cameras: Mapping[str, CameraSpec] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "background": list(self.background),
            "packet_count": len(self.packets),
            "plan_sha256": self.plan_sha256,
            "viewport_count": len(self.cameras),
            "raster_passes": raster_passes(self),
        }


def _json_value(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return np.asarray(value).tolist()
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, (np.floating, float)):
        return float(value)
    if isinstance(value, (np.integer, int)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def lowered_plan_digest(lowered: LoweredPlan) -> str:
    """Hash the camera, canvas, and packet fields that change the picture.

    Positions alone are not enough: the same atom count can change colour,
    bonds, the cell, or the camera without changing that one array.
    """

    cameras = {
        viewport_id: {
            "position": list(camera.position),
            "target": list(camera.target),
            "up": list(camera.up),
            "projection": str(camera.projection),
            "fov_y_deg": float(camera.fov_y_deg),
            "near": float(camera.near),
            "far": float(camera.far),
            "ortho_scale": float(camera.ortho_scale),
        }
        for viewport_id, camera in lowered.cameras.items()
    }
    packets = [
        {
            "kind": packet.kind,
            "rgba": list(packet.rgba),
            "rect": list(packet.rect),
            "vertices": np.asarray(packet.vertices).tolist(),
            "metadata": {
                key: _json_value(packet.metadata[key])
                for key in sorted(packet.metadata)
            },
        }
        for packet in lowered.packets
    ]
    payload = {
        "width": int(lowered.width),
        "height": int(lowered.height),
        "background": list(lowered.background),
        "cameras": cameras,
        "packets": packets,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def raster_passes(lowered: LoweredPlan) -> dict[str, Any]:
    """Report which packets are drawn on the GPU and which are composited.

    The CPU overlay is the intended path for wide lines, depth-disabled
    strokes, ORTEP hatch, and ORTEP principal axes. It is not a fallback
    for geometry the GPU packet cannot represent.
    """

    gpu_ids: list[str] = []
    overlay_ids: list[str] = []
    for packet in lowered.packets:
        if line_needs_cpu_overlay(packet):
            overlay_ids.append(packet.semantic_id)
        else:
            gpu_ids.append(packet.semantic_id)
    return {
        "gpu": gpu_ids,
        "cpu_overlay": overlay_ids,
        "cpu_overlay_is_fallback": False,
        "cpu_overlay_reason": (
            "Intended composite for lines wider than one pixel, "
            "depth-disabled strokes, ORTEP hatch, and ORTEP principal axes. "
            "Unsupported geometry raises GPUUnsupportedError instead of "
            "moving to this pass."
        ),
    }


def line_needs_cpu_overlay(packet: DrawPacket) -> bool:
    """Return whether a line must be composited on the CPU.

    The GPU line list is one pixel wide and depth-tested. ORTEP hatch and
    principal-axis strokes, every other width, and every depth-disabled line
    use the CPU stroker. Depth-disabled strokes then share that stroker's
    negative-infinity priority, so a later depth-tested wide line cannot
    cover them.
    """

    if packet.kind != "line":
        return False
    if str(packet.metadata.get("kind", "")) in _CPU_OVERLAY_LINE_KINDS:
        return True
    if not bool(packet.metadata.get("depth_test", True)):
        return True
    width = float(packet.metadata.get("width_px", _GPU_LINE_WIDTH_PX))
    return abs(width - _GPU_LINE_WIDTH_PX) > _LINE_WIDTH_EPSILON_PX


def _opaque_rgba(value: Any, semantic_id: str) -> tuple[float, float, float, float]:
    try:
        rgba = tuple(float(channel) for channel in value)
    except (TypeError, ValueError) as exc:
        raise GPUUnsupportedError(
            f"primitive {semantic_id!r} does not provide an RGBA colour"
        ) from exc
    if len(rgba) != 4 or not all(np.isfinite(rgba)):
        raise GPUUnsupportedError(f"primitive {semantic_id!r} has an invalid RGBA colour")
    if rgba[3] < 1.0 - 1e-7:
        raise GPUUnsupportedError(
            f"GPU backend does not support transparent primitives ({semantic_id!r})"
        )
    if any(channel < 0.0 or channel > 1.0 for channel in rgba):
        raise GPUUnsupportedError(f"primitive {semantic_id!r} has out-of-range colour channels")
    return rgba  # type: ignore[return-value]


def _appearance_name(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, Mapping):
        raw = value.get("id", value.get("preset"))
    else:
        raw = getattr(value, "id", None)
        if raw is None:
            raw = getattr(value, "preset", None)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GPUUnsupportedError(
            f"GPU appearance field {value!r} is not a number"
        ) from exc
    if not np.isfinite(number):
        raise GPUUnsupportedError(f"GPU appearance field {value!r} is not finite")
    return number


def _reject_surface_material(value: Any, where: str) -> None:
    """Accept only the historical no-highlight shade.

    The GPU packet stores one opaque colour. It does not look up a preset
    and it does not apply specular or Fresnel coefficients. A missing
    record is that historical shade. A coefficient record is accepted only
    when it asks for ``matte`` with no highlight and no Fresnel. Any other
    preset is an error, not a silent flatten to that shade.
    """

    if value is None:
        return
    if isinstance(value, str):
        raise GPUUnsupportedError(
            "surface_material must be a coefficient record. The GPU backend "
            f"will not look up preset {value!r} or ignore the field ({where})"
        )
    if not isinstance(value, Mapping) and not hasattr(value, "id"):
        raise GPUUnsupportedError(
            f"surface_material on {where} must be a coefficient record"
        )
    if isinstance(value, Mapping):
        rejected = sorted({"light_scale", "opacity"} & set(value))
        if rejected:
            raise GPUUnsupportedError(
                "surface material does not accept "
                f"{', '.join(rejected)}; light_scale stays on the lighting rig "
                "and opacity stays on the object"
            )
        specular = _as_float(value.get("specular"), 0.0)
        fresnel = _as_float(value.get("fresnel_power"), 0.0)
        fresnel_specular = _as_float(value.get("fresnel_specular"), 0.0)
        tint = value.get("specular_tint", "none")
    else:
        specular = _as_float(getattr(value, "specular", 0.0), 0.0)
        fresnel = _as_float(getattr(value, "fresnel_power", 0.0), 0.0)
        fresnel_specular = _as_float(getattr(value, "fresnel_specular", 0.0), 0.0)
        tint = getattr(value, "specular_tint", "none")
    name = _appearance_name(value)
    highlight = (
        abs(specular) > 1e-8
        or abs(fresnel) > 1e-8
        or abs(fresnel_specular) > 1e-8
        or str(tint or "none").strip().lower() not in {"", "none"}
    )
    if name in {None, "matte"} and not highlight:
        return
    shown = name or "unnamed"
    raise GPUUnsupportedError(
        f"GPU backend does not evaluate surface material {shown!r} on {where}. "
        "The draw packet stores one opaque colour and would not apply that "
        "preset's specular or Fresnel response"
    )


def _unit_light_scale(scale: Any) -> bool:
    if scale is None:
        return True
    return abs(_as_float(scale) - 1.0) <= 1e-6


def _reject_lighting(value: Any, scale: Any, where: str) -> None:
    """Accept only ``studio_soft`` at unit scale.

    Instanced meshes use the historical camera-space light. ``unlit`` would
    still receive that light. ``studio_three_point`` is not replaced with
    ``studio_soft``.
    """

    if isinstance(value, Mapping) and scale is None:
        scale = value.get("light_scale")
    elif scale is None and value is not None and not isinstance(value, (str, Mapping)):
        scale = getattr(value, "light_scale", None)
    name = _appearance_name(value)
    if name in {None, "studio_soft"} and _unit_light_scale(scale):
        return
    if name in {None, "studio_soft"}:
        raise GPUUnsupportedError(
            f"GPU backend does not evaluate light_scale {scale!r} on {where}. "
            "Instanced shading uses the historical camera-space light at unit scale"
        )
    if name == "unlit":
        raise GPUUnsupportedError(
            "GPU backend does not evaluate lighting rig 'unlit' on "
            f"{where}. Instanced meshes would still be shaded by the "
            "camera-space studio_soft light"
        )
    if name == "studio_three_point":
        raise GPUUnsupportedError(
            "GPU backend does not evaluate lighting rig 'studio_three_point' "
            f"on {where}. It will not substitute studio_soft"
        )
    raise GPUUnsupportedError(
        f"GPU backend does not evaluate lighting rig {name!r} on {where}. "
        "It will not substitute studio_soft"
    )


def _reject_gpu_appearance(plan: RenderPlan) -> None:
    metadata = plan.metadata
    _reject_surface_material(metadata.get("surface_material"), "the render plan")
    _reject_lighting(
        metadata.get("lighting"),
        metadata.get("light_scale"),
        "the render plan",
    )
    for viewport in plan.viewports:
        for primitive in viewport.primitives:
            primitive_metadata = getattr(primitive, "metadata", None) or {}
            where = str(getattr(primitive, "semantic_id", "") or type(primitive).__name__)
            if not isinstance(primitive_metadata, Mapping):
                continue
            _reject_surface_material(primitive_metadata.get("surface_material"), where)
            _reject_lighting(
                primitive_metadata.get("lighting"),
                primitive_metadata.get("light_scale", metadata.get("light_scale")),
                where,
            )


def lower_plan(plan: RenderPlan) -> LoweredPlan:
    """Lower a :class:`RenderPlan` into validated opaque GPU draw packets.

    Validation is intentionally complete before a GPU adapter is requested,
    so callers receive a deterministic ``GPUUnsupportedError`` even on hosts
    without graphics drivers.
    """

    if not isinstance(plan, RenderPlan):
        raise TypeError("GPU backend expects a RenderPlan")
    _reject_gpu_appearance(plan)
    packets: list[DrawPacket] = []
    for viewport in plan.viewports:
        for primitive in viewport.primitives:
            semantic_id = str(getattr(primitive, "semantic_id", ""))
            if isinstance(primitive, TriangleMeshPrimitive):
                if primitive.vertex_colors is not None:
                    raise GPUUnsupportedError(
                        "GPU backend does not render per-vertex colors "
                        f"({semantic_id!r}); use the CPU or Plotly backend"
                    )
                rgba = _opaque_rgba(primitive.rgba, semantic_id)
                shape = str(primitive.metadata.get("_raster_shape") or "")
                if shape == "sphere":
                    packets.append(
                        DrawPacket(
                            kind="sphere",
                            semantic_id=semantic_id,
                            viewport_id=viewport.semantic_id,
                            vertices=np.asarray(
                                [primitive.metadata["_raster_center"]], dtype=np.float32
                            ),
                            indices=None,
                            rgba=rgba,
                            rect=viewport.rect,
                            metadata={
                                "radius": float(primitive.metadata["_raster_radius"]),
                                "flat": primitive.vertex_normals is None,
                            },
                        )
                    )
                elif shape == "cylinder":
                    packets.append(
                        DrawPacket(
                            kind="cylinder",
                            semantic_id=semantic_id,
                            viewport_id=viewport.semantic_id,
                            vertices=np.asarray(
                                [
                                    primitive.metadata["_raster_start"],
                                    primitive.metadata["_raster_end"],
                                ],
                                dtype=np.float32,
                            ),
                            indices=None,
                            rgba=rgba,
                            rect=viewport.rect,
                            metadata={
                                "radius": float(primitive.metadata["_raster_radius"]),
                                "flat": primitive.vertex_normals is None,
                            },
                        )
                    )
                elif shape == "ellipsoid":
                    packets.append(
                        DrawPacket(
                            kind="ellipsoid",
                            semantic_id=semantic_id,
                            viewport_id=viewport.semantic_id,
                            vertices=np.asarray(
                                [primitive.metadata["_raster_center"]], dtype=np.float32
                            ),
                            indices=None,
                            rgba=rgba,
                            rect=viewport.rect,
                            metadata={
                                "lengths": tuple(primitive.metadata["_raster_lengths"]),
                                "axes": np.asarray(
                                    primitive.metadata["_raster_axes"], dtype=float
                                ).reshape(3, 3),
                                "flat": primitive.vertex_normals is None,
                            },
                        )
                    )
                else:
                    packets.append(
                        DrawPacket(
                            kind="mesh",
                            semantic_id=semantic_id,
                            viewport_id=viewport.semantic_id,
                            vertices=primitive.vertices,
                            indices=primitive.triangles,
                            rgba=rgba,
                            rect=viewport.rect,
                            metadata=primitive.metadata,
                        )
                    )
            elif isinstance(primitive, LinePrimitive):
                if primitive.endpoint_colors is not None:
                    raise GPUUnsupportedError(
                        "GPU backend does not render per-endpoint line colors "
                        f"({semantic_id!r}); use the CPU backend"
                    )
                rgba = _opaque_rgba(primitive.rgba, semantic_id)
                if primitive.dash:
                    raise GPUUnsupportedError(
                        f"GPU backend does not support dashed lines ({semantic_id!r})"
                    )
                packets.append(
                    DrawPacket(
                        kind="line",
                        semantic_id=semantic_id,
                        viewport_id=viewport.semantic_id,
                        vertices=primitive.segments.reshape(-1, 3),
                        indices=None,
                        rgba=rgba,
                        rect=viewport.rect,
                        metadata={
                            **dict(primitive.metadata),
                            "depth_test": bool(primitive.depth_test),
                            "width_px": float(primitive.width_px),
                        },
                    )
                )
            elif isinstance(primitive, TextPrimitive):
                raise GPUUnsupportedError(
                    f"GPU MVP does not support text primitives ({semantic_id!r})"
                )
            else:
                raise GPUUnsupportedError(
                    f"GPU MVP does not support primitive type {type(primitive).__name__}"
                )
    background = tuple(float(channel) for channel in plan.background)
    if len(background) != 4 or not np.all(np.isfinite(background)):
        raise GPUUnsupportedError("render plan background is invalid")
    return LoweredPlan(
        width=int(plan.width),
        height=int(plan.height),
        background=background,  # type: ignore[arg-type]
        packets=tuple(packets),
        plan_sha256=plan.fingerprint(),
        cameras=MappingProxyType(
            {viewport.semantic_id: viewport.camera for viewport in plan.viewports}
        ),
    )


def lower_render_plan(plan: RenderPlan) -> LoweredPlan:
    """Alias for :func:`lower_plan`."""

    return lower_plan(plan)


def lower_to_draw_packets(plan: RenderPlan) -> LoweredPlan:
    """Descriptive alias for callers that use the issue's packet vocabulary."""

    return lower_plan(plan)
