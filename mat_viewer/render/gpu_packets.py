"""Validated draw packets lowered from a backend-neutral :class:`RenderPlan`."""

from __future__ import annotations

from dataclasses import dataclass, field
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
        }


def line_needs_cpu_overlay(packet: DrawPacket) -> bool:
    """Return whether a line must be composited on the CPU.

    The GPU line list is one pixel wide. ORTEP hatch and principal-axis
    strokes, and every other non-unit width, keep the CPU stroker so
    ``width_px`` and depth testing match the raster backend.
    """

    if packet.kind != "line":
        return False
    if str(packet.metadata.get("kind", "")) in _CPU_OVERLAY_LINE_KINDS:
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


def lower_plan(plan: RenderPlan) -> LoweredPlan:
    """Lower a :class:`RenderPlan` into validated opaque GPU draw packets.

    Validation is intentionally complete before a GPU adapter is requested,
    so callers receive a deterministic ``GPUUnsupportedError`` even on hosts
    without graphics drivers.
    """

    if not isinstance(plan, RenderPlan):
        raise TypeError("GPU backend expects a RenderPlan")
    packets: list[DrawPacket] = []
    for viewport in plan.viewports:
        for primitive in viewport.primitives:
            semantic_id = str(getattr(primitive, "semantic_id", ""))
            if isinstance(primitive, TriangleMeshPrimitive):
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
                            metadata={"radius": float(primitive.metadata["_raster_radius"])},
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
                            metadata={"radius": float(primitive.metadata["_raster_radius"])},
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
