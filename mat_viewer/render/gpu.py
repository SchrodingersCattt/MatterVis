"""Small optional WebGPU backend for backend-neutral :class:`RenderPlan` objects.

The GPU backend draws opaque meshes and instanced spheres, cylinders, and
ellipsoids.  Text stays on the CPU.  Transparent primitives are rejected:
on an A800, source-alpha blending of two overlapping triangles did not match
the CPU image, so there is no silent CPU fallback.

``wgpu`` is an optional dependency and is imported only from :func:`probe` or
:func:`render`.  Importing :mod:`mat_viewer.render.gpu` is therefore safe in a
minimal CPU installation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import importlib
import importlib.metadata
import importlib.util
from pathlib import Path
import time
from types import MappingProxyType
from typing import Any, Literal, Mapping

import numpy as np

from .contracts import (
    CameraSpec,
    LinePrimitive,
    RenderPlan,
    RenderResult,
    RENDER_RESULT_SCHEMA,
    TextPrimitive,
    TriangleMeshPrimitive,
)
from .gpu_readback import (
    map_readback_sync as _map_readback_sync_impl,
    release_gpu_resources as _release_gpu_resources,
    submit_and_wait as _submit_and_wait,
)


class GPUError(RuntimeError):
    """Base class for explicit GPU backend failures."""


class GPUUnavailableError(GPUError):
    """Raised when ``wgpu`` or a usable adapter/device is unavailable."""


class GPUUnsupportedError(GPUError):
    """Raised when a render plan is outside the GPU MVP contract."""


@dataclass(frozen=True, slots=True)
class GPUProbe:
    """JSON-safe description of the optional WebGPU runtime."""

    available: bool
    backend: str = "wgpu"
    wgpu_version: str | None = None
    adapter_name: str | None = None
    adapter_backend: str | None = None
    device_name: str | None = None
    hardware_accelerated: bool | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": bool(self.available),
            "backend": self.backend,
            "wgpu_version": self.wgpu_version,
            "adapter_name": self.adapter_name,
            "adapter_backend": self.adapter_backend,
            "device_name": self.device_name,
            "hardware_accelerated": self.hardware_accelerated,
            "reason": self.reason,
        }

    # A small mapping-like surface makes probe payloads convenient for callers
    # while retaining a typed record for Python users.
    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


@dataclass(frozen=True, slots=True)
class DrawPacket:
    """Validated GPU draw packet produced by :func:`lower_plan`.

    ``vertices`` are world-space positions.  For meshes, ``indices`` contains
    triangle indices.  For lines, each pair of vertices is one line segment
    and ``indices`` is ``None``.  The MVP intentionally stores one constant
    opaque colour per packet rather than inventing a material system.
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
            indices = None
        else:
            if self.indices is not None or len(vertices) != 1:
                raise ValueError("ellipsoid packets need one center and no indices")
            lengths = np.asarray(self.metadata.get("lengths", []), dtype=float)
            axes = np.asarray(self.metadata.get("axes", []), dtype=float)
            if lengths.shape != (3,) or axes.shape != (3, 3):
                raise ValueError("ellipsoid packets require lengths (3,) and axes (3, 3)")
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


def _import_wgpu() -> Any:
    """Import ``wgpu`` lazily and raise a stable, actionable error."""

    if not _wgpu_spec_available():
        raise GPUUnavailableError(
            "The GPU backend requires the optional 'wgpu' package; install "
            "matter-vis[gpu]."
        )
    try:
        return importlib.import_module("wgpu")
    except Exception as exc:  # backend DLL/driver imports can fail broadly
        raise GPUUnavailableError(
            f"The wgpu runtime could not be imported: {type(exc).__name__}: {exc}"
        ) from exc


def _version() -> str | None:
    try:
        return importlib.metadata.version("wgpu")
    except importlib.metadata.PackageNotFoundError:
        return None


def _wgpu_spec_available() -> bool:
    try:
        return importlib.util.find_spec("wgpu") is not None
    except (ImportError, ValueError):
        return False


def _adapter_name(adapter: Any) -> str | None:
    info = getattr(adapter, "info", None)
    if callable(info):
        try:
            info = info()
        except Exception:
            info = None
    if isinstance(info, Mapping):
        value = info.get("device") or info.get("name") or info.get("description")
    else:
        value = getattr(info, "device", None) or getattr(info, "description", None)
    value = value or getattr(adapter, "name", None)
    return str(value) if value else None


def _request_adapter(wgpu: Any) -> Any:
    gpu = getattr(wgpu, "gpu", None)
    if gpu is None:
        raise GPUUnavailableError("wgpu.gpu is unavailable in this runtime")
    request = getattr(gpu, "request_adapter_sync", None)
    if callable(request):
        try:
            return request(power_preference="high-performance")
        except TypeError:
            return request()
    request = getattr(gpu, "request_adapter", None)
    if callable(request):
        result = request(power_preference="high-performance")
        if hasattr(result, "__await__"):
            raise GPUUnavailableError(
                "This GPU backend requires synchronous wgpu adapter requests"
            )
        return result
    raise GPUUnavailableError("wgpu does not expose an adapter request method")


def _request_device(adapter: Any) -> Any:
    request = getattr(adapter, "request_device_sync", None)
    if callable(request):
        try:
            return request()
        except TypeError:
            return request(required_limits={})
    request = getattr(adapter, "request_device", None)
    if callable(request):
        result = request()
        if hasattr(result, "__await__"):
            raise GPUUnavailableError(
                "This GPU backend requires synchronous wgpu device requests"
            )
        return result
    raise GPUUnavailableError("wgpu adapter does not expose a device request method")


def probe() -> GPUProbe:
    """Probe the wgpu runtime and a usable adapter/device without rendering."""

    version = _version()
    if not _wgpu_spec_available():
        return GPUProbe(
            available=False,
            wgpu_version=version,
            reason="optional 'wgpu' package is not installed",
        )
    try:
        wgpu = _import_wgpu()
        adapter = _request_adapter(wgpu)
        if adapter is None:
            return GPUProbe(
                available=False,
                wgpu_version=version,
                reason="wgpu could not find a compatible adapter",
            )
        device = _request_device(adapter)
        if device is None:
            return GPUProbe(
                available=False,
                wgpu_version=version,
                adapter_name=_adapter_name(adapter),
                reason="wgpu adapter did not provide a device",
            )
        info = getattr(adapter, "info", None)
        adapter_backend = None
        adapter_type = None
        if isinstance(info, Mapping):
            adapter_backend = info.get("backend_type") or info.get("backend")
            adapter_type = info.get("adapter_type") or info.get("type")
        else:
            adapter_backend = getattr(info, "backend_type", None) or getattr(
                info, "backend", None
            )
            adapter_type = getattr(info, "adapter_type", None) or getattr(
                info, "type", None
            )
        result = GPUProbe(
            available=True,
            wgpu_version=version,
            adapter_name=_adapter_name(adapter),
            adapter_backend=str(adapter_backend) if adapter_backend else None,
            device_name=_adapter_name(adapter),
            hardware_accelerated=(
                str(adapter_type).lower() not in {"", "cpu", "software", "unknown"}
                if adapter_type is not None
                else None
            ),
        )
        destroy = getattr(device, "destroy", None)
        if callable(destroy):
            destroy()
        return result
    except GPUUnavailableError as exc:
        return GPUProbe(available=False, wgpu_version=version, reason=str(exc))
    except Exception as exc:
        return GPUProbe(
            available=False,
            wgpu_version=version,
            reason=f"{type(exc).__name__}: {exc}",
        )


def probe_gpu() -> GPUProbe:
    """Alias for :func:`probe` used by capability callers."""

    return probe()


def is_available() -> bool:
    """Return whether a synchronous wgpu adapter and device can be created."""

    return probe().available


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


def _ndc_to_camera_depth(depth_ndc: np.ndarray, camera: Any) -> np.ndarray:
    """Convert WebGPU window depth to the CPU composite's positive camera depth."""

    near = float(camera.near)
    far = float(camera.far)
    span = near - far
    if str(camera.projection) == "perspective":
        p22 = far / span
        p23 = near * far / span
        z_view = p23 / (-depth_ndc - p22)
    else:
        z_view = (depth_ndc - near / span) * span
    return np.asarray(-z_view, dtype=np.float32)


def _composite_depth_overlays(
    rgba: bytes,
    lowered: LoweredPlan,
    packets: tuple[DrawPacket, ...],
    *,
    device: Any,
    depth_texture: Any,
    wgpu: Any,
) -> bytes:
    """Draw ORTEP hatch and octant lines on the read-back colour and depth."""

    width = int(lowered.width)
    height = int(lowered.height)
    row_bytes = width * 4
    padded = ((row_bytes + 255) // 256) * 256
    usage = _usage(wgpu, "COPY_DST") | _usage(wgpu, "MAP_READ")
    buffer = device.create_buffer(size=padded * height, usage=usage)
    encoder = device.create_command_encoder()
    encoder.copy_texture_to_buffer(
        {"texture": depth_texture},
        {"buffer": buffer, "bytes_per_row": padded, "rows_per_image": height},
        (width, height, 1),
    )
    _submit_and_wait(device, [encoder.finish()])
    raw = _map_readback_sync(buffer, wgpu=wgpu, device=device)
    depth = np.empty((height, width), dtype=np.float32)
    for row in range(height):
        start = row * padded
        depth[row] = np.frombuffer(raw[start : start + row_bytes], dtype="<f4", count=width)
    viewports = {packet.viewport_id for packet in packets}
    if len(viewports) != 1:
        raise GPUUnsupportedError("ORTEP depth overlay requires one viewport")
    camera = lowered.cameras[next(iter(viewports))]
    linear = _ndc_to_camera_depth(depth, camera)
    image = np.frombuffer(rgba, dtype=np.uint8).reshape(height, width, 4).copy()
    from .contracts import LinePrimitive
    from .cpu.raster import composite_primitives

    primitives = [
        LinePrimitive(
            packet.semantic_id,
            np.asarray(packet.vertices, dtype=float).reshape(-1, 2, 3),
            rgba=tuple(packet.rgba),
            width_px=float(packet.metadata.get("width_px", 1.0)),
            depth_test=bool(packet.metadata.get("depth_test", True)),
            metadata={"kind": packet.metadata.get("kind")},
        )
        for packet in packets
    ]
    composited, _depth = composite_primitives(image, linear, camera, primitives)
    destroy = getattr(buffer, "destroy", None)
    if callable(destroy):
        destroy()
    return np.ascontiguousarray(composited).tobytes()


def _camera_matrix(camera: Any, *, aspect: float) -> np.ndarray:
    """Build a column-major world-to-clip matrix for the MVP shader."""

    position = np.asarray(camera.position, dtype=np.float32)
    target = np.asarray(camera.target, dtype=np.float32)
    up = np.asarray(camera.up, dtype=np.float32)
    forward = target - position
    forward /= max(float(np.linalg.norm(forward)), 1e-12)
    right = np.cross(forward, up)
    right /= max(float(np.linalg.norm(right)), 1e-12)
    true_up = np.cross(right, forward)
    view = np.eye(4, dtype=np.float32)
    view[0, :3] = right
    view[1, :3] = true_up
    view[2, :3] = -forward
    view[:3, 3] = -np.array(
        [np.dot(right, position), np.dot(true_up, position), -np.dot(forward, position)],
        dtype=np.float32,
    )
    if str(camera.projection) == "perspective":
        f = 1.0 / np.tan(np.deg2rad(float(camera.fov_y_deg)) / 2.0)
        near, far = float(camera.near), float(camera.far)
        projection = np.array(
            [
                [f / max(aspect, 1e-12), 0, 0, 0],
                [0, f, 0, 0],
                [0, 0, far / (near - far), (near * far) / (near - far)],
                [0, 0, -1, 0],
            ],
            dtype=np.float32,
        )
    else:
        # CameraSpec.ortho_scale is the half-height of the orthographic
        # view volume (matching render.camera._projection_matrix), not the
        # full viewport height.
        half_y = float(camera.ortho_scale)
        half_x = half_y * max(aspect, 1e-12)
        near, far = float(camera.near), float(camera.far)
        projection = np.array(
            [
                [1 / half_x, 0, 0, 0],
                [0, 1 / half_y, 0, 0],
                [0, 0, 1 / (near - far), near / (near - far)],
                [0, 0, 0, 1],
            ],
            dtype=np.float32,
        )
    # WGSL mat4x4 is column-major; numpy's transpose gives bytes in the
    # expected layout for the shader's matrix multiply.
    return (projection @ view).T.astype(np.float32, copy=False)


_WGSL = """
struct Uniforms { view_proj: mat4x4<f32>, };
@group(0) @binding(0) var<uniform> uniforms: Uniforms;
struct VertexOut { @builtin(position) position: vec4<f32>, @location(0) color: vec4<f32>, };
@vertex fn vs_main(@location(0) position: vec3<f32>, @location(1) color: vec4<f32>) -> VertexOut {
  var out: VertexOut;
  out.position = uniforms.view_proj * vec4<f32>(position, 1.0);
  out.color = color;
  return out;
}
@fragment fn fs_main(in: VertexOut) -> @location(0) vec4<f32> { return in.color; }
"""


def _usage(wgpu: Any, name: str) -> Any:
    value = getattr(getattr(wgpu, "BufferUsage", object()), name, None)
    if value is None:
        value = getattr(getattr(wgpu, "TextureUsage", object()), name, None)
    return value


def _map_readback_sync(readback: Any, *, wgpu: Any, device: Any) -> bytes:
    """Map a staging buffer and return its bytes after GPU completion."""

    return _map_readback_sync_impl(
        readback,
        wgpu=wgpu,
        device=device,
        error_type=GPUUnavailableError,
    )


def _wgpu_rgba(
    lowered: LoweredPlan,
    *,
    probe_info: GPUProbe,
    cache: dict[str, Any] | None = None,
) -> tuple[bytes, dict[str, float]]:
    """Render packets to an off-screen RGBA texture and read it back.

    The function intentionally uses only synchronous ``wgpu`` calls.  A few
    wgpu releases expose asynchronous-only adapter/device APIs; those are
    reported as :class:`GPUUnavailableError` by the helpers above instead of
    being hidden behind a CPU substitute.
    """

    del probe_info  # retained in the signature for callers that cache probes
    started = time.perf_counter()
    state = cache if cache is not None else {}
    if not state.get("ready"):
        wgpu = _import_wgpu()
        adapter = _request_adapter(wgpu)
        if adapter is None:
            raise GPUUnavailableError("wgpu could not find a compatible adapter")
        device = _request_device(adapter)
        if device is None:
            raise GPUUnavailableError("wgpu adapter did not provide a device")

        usage_texture = getattr(getattr(wgpu, "TextureUsage", object()), "RENDER_ATTACHMENT", None)
        # BufferUsage.COPY_SRC and TextureUsage.COPY_SRC have different bit values;
        # do not use the generic buffer-first helper for a texture descriptor.
        copy_src = getattr(getattr(wgpu, "TextureUsage", object()), "COPY_SRC", None)
        usage_buffer = _usage(wgpu, "COPY_DST")
        map_read = _usage(wgpu, "MAP_READ")
        uniform_usage = _usage(wgpu, "UNIFORM")
        vertex_usage = _usage(wgpu, "VERTEX")
        index_usage = _usage(wgpu, "INDEX")
        if None in (
            usage_texture,
            copy_src,
            usage_buffer,
            map_read,
            uniform_usage,
            vertex_usage,
            index_usage,
        ):
            raise GPUUnavailableError("wgpu runtime does not expose the required buffer/texture usages")
        # Every staging buffer is populated with ``queue.write_buffer`` and must
        # therefore include COPY_DST in addition to its binding usage.
        uniform_usage |= usage_buffer
        vertex_usage |= usage_buffer
        index_usage |= usage_buffer
        buffer_usage = usage_buffer | map_read
        shader = device.create_shader_module(code=_WGSL)
        stage = getattr(wgpu, "ShaderStage", None)
        vertex_stage = getattr(stage, "VERTEX", 1)
        uniform_layout = device.create_bind_group_layout(
            entries=[
                {
                    "binding": 0,
                    "visibility": vertex_stage,
                    "buffer": {"type": "uniform"},
                }
            ]
        )
        pipeline_layout = device.create_pipeline_layout(bind_group_layouts=[uniform_layout])

        def make_pipeline(topology: str) -> Any:
            return device.create_render_pipeline(
                layout=pipeline_layout,
                vertex={
                    "module": shader,
                    "entry_point": "vs_main",
                    "buffers": [
                        {
                            "array_stride": 28,
                            "attributes": [
                                {"format": "float32x3", "offset": 0, "shader_location": 0},
                                {"format": "float32x4", "offset": 12, "shader_location": 1},
                            ],
                        }
                    ],
                },
                primitive={"topology": topology, "cull_mode": "none"},
                fragment={
                    "module": shader,
                    "entry_point": "fs_main",
                    "targets": [{"format": "rgba8unorm"}],
                },
                depth_stencil={
                "format": "depth32float",
                "depth_write_enabled": True,
                "depth_compare": "less",
                },
                multisample={"count": 1},
            )

        mesh_pipeline = make_pipeline("triangle-list")
        line_pipeline = make_pipeline("line-list")
        overlay_pipeline = device.create_render_pipeline(
            layout=pipeline_layout,
            vertex={
                "module": shader,
                "entry_point": "vs_main",
                "buffers": [
                    {
                        "array_stride": 28,
                        "attributes": [
                            {"format": "float32x3", "offset": 0, "shader_location": 0},
                            {"format": "float32x4", "offset": 12, "shader_location": 1},
                        ],
                    }
                ],
            },
            primitive={"topology": "line-list", "cull_mode": "none"},
            fragment={
                "module": shader,
                "entry_point": "fs_main",
                "targets": [{"format": "rgba8unorm"}],
            },
            depth_stencil={
                "format": "depth32float",
                "depth_write_enabled": False,
                "depth_compare": "always",
            },
            multisample={"count": 1},
        )
        state.update(
            {
                "ready": True,
                "wgpu": wgpu,
                "device": device,
                "uniform_layout": uniform_layout,
                "pipeline_layout": pipeline_layout,
                "mesh_pipeline": mesh_pipeline,
                "line_pipeline": line_pipeline,
                "overlay_pipeline": overlay_pipeline,
                "uniform_usage": uniform_usage,
                "vertex_usage": vertex_usage,
                "index_usage": index_usage,
                "buffer_usage": buffer_usage,
                "usage_texture": usage_texture,
                "copy_src": copy_src,
                "ephemeral": cache is None,
            }
        )
    wgpu = state["wgpu"]
    device = state["device"]
    uniform_layout = state["uniform_layout"]
    pipeline_layout = state["pipeline_layout"]
    mesh_pipeline = state["mesh_pipeline"]
    line_pipeline = state["line_pipeline"]
    overlay_pipeline = state["overlay_pipeline"]
    uniform_usage = state["uniform_usage"]
    vertex_usage = state["vertex_usage"]
    index_usage = state["index_usage"]
    buffer_usage = state["buffer_usage"]
    usage_texture = state["usage_texture"]
    copy_src = state["copy_src"]
    resources: list[Any] = []
    texture = device.create_texture(
        size=(lowered.width, lowered.height, 1),
        format="rgba8unorm",
        usage=usage_texture | copy_src,
    )
    resources.append(texture)
    target = texture.create_view()
    depth_texture = device.create_texture(
        size=(lowered.width, lowered.height, 1),
        format="depth32float",
        usage=usage_texture | copy_src,
    )
    resources.append(depth_texture)
    depth_view = depth_texture.create_view()
    encoder = device.create_command_encoder()
    clear = tuple(float(c) for c in lowered.background)
    render_pass = encoder.begin_render_pass(
        color_attachments=[
            {
                "view": target,
                "resolve_target": None,
                "clear_value": clear,
                "load_op": "clear",
                "store_op": "store",
            }
        ],
        depth_stencil_attachment={
            "view": depth_view,
            "depth_clear_value": 1.0,
            "depth_load_op": "clear",
            "depth_store_op": "store",
        },
    )

    camera_by_viewport = lowered.cameras
    from .gpu_draw import draw_instanced_shapes

    draw_instanced_shapes(
        device=device,
        render_pass=render_pass,
        lowered=lowered,
        pipeline_layout=pipeline_layout,
        uniform_layout=uniform_layout,
        uniform_usage=uniform_usage,
        vertex_usage=vertex_usage,
        index_usage=index_usage,
        resources=resources,
        cache=state,
    )
    for packet in lowered.packets:
        if packet.kind in {"sphere", "cylinder", "ellipsoid"}:
            continue
        camera = camera_by_viewport.get(packet.viewport_id)
        if camera is None:
            raise GPUUnsupportedError(
                f"GPU packet {packet.semantic_id!r} has no viewport camera"
            )
        aspect = (lowered.width * packet.rect[2]) / max(
            lowered.height * packet.rect[3], 1.0e-12
        )
        matrix = _camera_matrix(camera, aspect=aspect)
        uniform_data = matrix.astype("<f4", copy=False).tobytes()
        uniform_size = max(256, len(uniform_data))
        uniform_data = uniform_data + bytes(uniform_size - len(uniform_data))
        uniform_buffer = device.create_buffer(
            size=uniform_size,
            usage=uniform_usage,
        )
        resources.append(uniform_buffer)
        device.queue.write_buffer(uniform_buffer, 0, uniform_data)
        bind_group = device.create_bind_group(
            layout=uniform_layout,
            entries=[{"binding": 0, "resource": {"buffer": uniform_buffer}}],
        )
        colour = np.asarray(packet.rgba, dtype=np.float32)
        colours = np.repeat(colour[None, :], len(packet.vertices), axis=0)
        interleaved = np.concatenate(
            [np.asarray(packet.vertices, dtype=np.float32), colours], axis=1
        ).astype("<f4", copy=False)
        vertex_buffer = device.create_buffer(
            size=max(28, interleaved.nbytes),
            usage=vertex_usage,
        )
        resources.append(vertex_buffer)
        device.queue.write_buffer(vertex_buffer, 0, interleaved.tobytes())
        x, y, width, height = packet.rect
        render_pass.set_viewport(
            x * lowered.width,
            (1.0 - y - height) * lowered.height,
            width * lowered.width,
            height * lowered.height,
            0.0,
            1.0,
        )
        render_pass.set_scissor_rect(
            max(0, int(round(x * lowered.width))),
            max(0, int(round((1.0 - y - height) * lowered.height))),
            max(1, int(round(width * lowered.width))),
            max(1, int(round(height * lowered.height))),
        )
        render_pass.set_bind_group(0, bind_group)
        render_pass.set_vertex_buffer(0, vertex_buffer)
        if packet.kind == "mesh":
            index_data = np.asarray(packet.indices, dtype="<u4").ravel().tobytes()
            index_buffer = device.create_buffer(
                size=max(4, len(index_data)),
                usage=index_usage,
            )
            resources.append(index_buffer)
            device.queue.write_buffer(index_buffer, 0, index_data)
            render_pass.set_pipeline(mesh_pipeline)
            render_pass.set_index_buffer(index_buffer, "uint32")
            render_pass.draw_indexed(len(index_data) // 4, 1, 0, 0, 0)
        else:
            if str(packet.metadata.get("kind", "")) in {"ortep_hatch", "ortep_axes"}:
                continue
            depth_test = bool(packet.metadata.get("depth_test", True))
            render_pass.set_pipeline(line_pipeline if depth_test else overlay_pipeline)
            render_pass.draw(len(packet.vertices), 1, 0, 0)
    render_pass.end()

    bytes_per_pixel = 4
    unpadded_row = lowered.width * bytes_per_pixel
    padded_row = (unpadded_row + 255) // 256 * 256
    readback = device.create_buffer(
        size=padded_row * lowered.height,
        usage=buffer_usage,
    )
    resources.append(readback)
    encoder.copy_texture_to_buffer(
        {"texture": texture},
        {"buffer": readback, "bytes_per_row": padded_row, "rows_per_image": lowered.height},
        (lowered.width, lowered.height, 1),
    )
    commands = encoder.finish()
    upload_elapsed = time.perf_counter() - started
    draw_started = time.perf_counter()
    _submit_and_wait(device, [commands])
    draw_elapsed = time.perf_counter() - draw_started
    readback_started = time.perf_counter()
    mapped_bytes = _map_readback_sync(readback, wgpu=wgpu, device=device)
    readback_elapsed = time.perf_counter() - readback_started
    rgba = b"".join(
        mapped_bytes[row * padded_row : row * padded_row + unpadded_row]
        for row in range(lowered.height)
    )
    overlay_packets = tuple(
        packet
        for packet in lowered.packets
        if packet.kind == "line"
        and str(packet.metadata.get("kind", "")) in {"ortep_hatch", "ortep_axes"}
    )
    if overlay_packets:
        rgba = _composite_depth_overlays(
            rgba,
            lowered,
            overlay_packets,
            device=device,
            depth_texture=depth_texture,
            wgpu=wgpu,
        )
    _release_gpu_resources(
        resources,
        device,
        release_device=bool(state.get("ephemeral")),
    )
    elapsed = time.perf_counter() - started
    return rgba, {
        "device_render": elapsed,
        "upload": upload_elapsed,
        "draw": draw_elapsed,
        "readback": readback_elapsed,
    }


def _encode_png(rgba: bytes, width: int, height: int) -> bytes:
    try:
        from PIL import Image
    except Exception as exc:  # pragma: no cover - Pillow is a core dependency
        raise GPUUnavailableError("PNG encoding requires Pillow") from exc
    if len(rgba) != width * height * 4:
        raise GPUError(
            f"GPU readback returned {len(rgba)} bytes; expected {width * height * 4}"
        )
    import io

    stream = io.BytesIO()
    Image.frombytes("RGBA", (width, height), rgba).save(stream, format="PNG")
    return stream.getvalue()


def render_animation(source: Any, output: str | Path, **kwargs: Any) -> RenderResult:
    """Encode GIF/MP4 one frame at a time through the same GPU device path.

    At most one frame is on the device. The animation extra supplies the
    encoder; a missing encoder is an explicit error, not a CPU render.
    """

    from .animation_adapter import render_animation as _encode

    with GpuSession() as session:
        return _encode(source, output, frame_renderer=session.render_frame, **kwargs)


def render(
    plan: RenderPlan,
    output: str | Path | None = None,
    *,
    cache: dict[str, Any] | None = None,
) -> RenderResult:
    """Render an opaque MVP plan through WebGPU without CPU fallback.

    The current implementation validates/lower plans fully and then requires a
    synchronous adapter/device.  A host without a graphics runtime receives a
    ``GPUUnavailableError``; unsupported plan features receive
    ``GPUUnsupportedError``.  No CPU renderer is invoked on either path.
    """

    started = time.perf_counter()
    lower_started = started
    lowered = lower_plan(plan)
    lower_elapsed = time.perf_counter() - lower_started
    destination = Path(output).expanduser().resolve() if output is not None else None
    output_format = destination.suffix.lower().lstrip(".") if destination else "png"
    if output_format != "png":
        raise GPUUnsupportedError(
            f"GPU MVP only supports PNG output; got {output_format or output!r}"
        )
    probe_started = time.perf_counter()
    if cache is not None and cache.get("probe") is not None:
        probed = cache["probe"]
    else:
        probed = probe()
        if cache is not None and probed.available:
            cache["probe"] = probed
    probe_elapsed = time.perf_counter() - probe_started
    if not probed.available:
        raise GPUUnavailableError(probed.reason or "no usable WebGPU adapter")

    # The packet-to-device path is intentionally isolated.  A clear, explicit
    # error is preferable to claiming a successful GPU render when a wgpu
    # version does not provide the synchronous off-screen APIs this MVP uses.
    try:
        rgba, gpu_timing = _wgpu_rgba(lowered, probe_info=probed, cache=cache)
    except GPUError:
        raise
    except Exception as exc:
        raise GPUError(
            f"WebGPU render failed; no CPU fallback was attempted: {type(exc).__name__}: {exc}"
        ) from exc
    encode_started = time.perf_counter()
    data = _encode_png(rgba, lowered.width, lowered.height)
    encode_elapsed = time.perf_counter() - encode_started
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    elapsed = time.perf_counter() - started
    import hashlib

    return RenderResult(
        schema=RENDER_RESULT_SCHEMA,
        backend="gpu",
        format="png",
        width=lowered.width,
        height=lowered.height,
        plan_sha256=lowered.plan_sha256,
        output_sha256=hashlib.sha256(data).hexdigest(),
        output=destination,
        data=None if destination is not None else data,
        warnings=tuple(plan.warnings),
        metadata={
            "requested_backend": "gpu",
            "actual_backend": "gpu",
            "gpu": probed.to_dict(),
            "formats": {"color": "rgba8unorm", "depth": "depth32float"},
            "packet_count": len(lowered.packets),
            "timing_s": {
                # Keep stage names stable for machine-readable receipts. The
                # packet compiler currently runs as part of plan lowering;
                # the device helper reports one combined draw/readback span.
                "plan": lower_elapsed,
                "packet_assembly": lower_elapsed,
                "upload": float(gpu_timing.get("upload", gpu_timing.get("device_render", 0.0))),
                "draw": float(gpu_timing.get("draw", gpu_timing.get("device_render", 0.0))),
                "readback": float(gpu_timing.get("readback", 0.0)),
                "overlay_encode": encode_elapsed,
                "probe": probe_elapsed,
                **gpu_timing,
                "total": elapsed,
            },
            "fallback": None,
        },
    )


class GpuSession:
    """One WebGPU device shared by a PNG sequence or an animation.

    At most one frame is in flight.  Pipelines stay on the device; colour
    and depth textures are created for that frame and released before the
    next one starts.
    """

    def __init__(self) -> None:
        self._closed = False
        self._in_flight = 0
        self._cache: dict[str, Any] = {}
        self._last_result: RenderResult | None = None

    def _ensure_open(self) -> None:
        if self._closed:
            raise GPUError("GPU session is closed")

    def probe(self) -> GPUProbe:
        self._ensure_open()
        return probe()

    def compile(self, plan: RenderPlan) -> LoweredPlan:
        self._ensure_open()
        return lower_plan(plan)

    def render_frame(
        self, plan: RenderPlan, output: str | Path | None = None
    ) -> RenderResult:
        self._ensure_open()
        if self._in_flight:
            raise GPUError("GPU session allows one in-flight frame")
        self._in_flight += 1
        try:
            self._last_result = render(plan, output=output, cache=self._cache)
        finally:
            self._in_flight -= 1
        return self._last_result

    def render_lowered(self, lowered: LoweredPlan) -> bytes:
        """Render one already-lowered frame and return PNG bytes.

        The caller keeps the device.  A second call waits until this one
        has finished readback, because ``_in_flight`` admits only one frame.
        """

        self._ensure_open()
        if self._in_flight:
            raise GPUError("GPU session allows one in-flight frame")
        self._in_flight += 1
        try:
            if self._cache.get("probe") is None:
                probed = probe()
                if not probed.available:
                    raise GPUUnavailableError(probed.reason or "no usable WebGPU adapter")
                self._cache["probe"] = probed
            rgba, _timing = _wgpu_rgba(
                lowered, probe_info=self._cache["probe"], cache=self._cache
            )
            return _encode_png(rgba, lowered.width, lowered.height)
        finally:
            self._in_flight -= 1

    def readback(self) -> bytes:
        self._ensure_open()
        if self._last_result is None:
            raise GPUError("no GPU frame has been rendered")
        if self._last_result.data is not None:
            return self._last_result.data
        if self._last_result.output is not None:
            return self._last_result.output.read_bytes()
        raise GPUError("the last GPU frame has no readable output")

    def close(self) -> None:
        device = self._cache.get("device")
        destroy = getattr(device, "destroy", None)
        if callable(destroy):
            destroy()
        self._cache.clear()
        self._last_result = None
        self._closed = True

    def __enter__(self) -> "GpuSession":
        self._ensure_open()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()


GPUBackend = GpuSession
GpuDrawPacket = DrawPacket


__all__ = [
    "DrawPacket",
    "GpuDrawPacket",
    "GPUError",
    "GPUBackend",
    "GPUProbe",
    "GPUUnavailableError",
    "GPUUnsupportedError",
    "LoweredPlan",
    "GpuSession",
    "is_available",
    "lower_plan",
    "lower_render_plan",
    "lower_to_draw_packets",
    "probe",
    "probe_gpu",
    "render",
    "render_animation",
]
