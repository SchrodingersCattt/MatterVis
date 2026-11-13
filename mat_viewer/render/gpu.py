"""Optional WebGPU backend for backend-neutral :class:`RenderPlan` objects.

The GPU backend draws opaque meshes and instanced spheres, cylinders, and
ellipsoids. Text stays unsupported. Transparent primitives are rejected:
overlapping source-alpha blending did not match the CPU composite, so there
is no silent CPU fallback. Lines wider than one pixel, including unit-cell
edges and ORTEP hatch or principal-axis strokes, are composited on the CPU
from the colour and ``depth32float`` read-back.

``wgpu`` is an optional dependency and is imported only from :func:`probe` or
:func:`render`. Importing :mod:`mat_viewer.render.gpu` is therefore safe in a
minimal CPU installation.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import time
from typing import Any

from .contracts import (
    RENDER_RESULT_SCHEMA,
    RenderPlan,
    RenderResult,
)
from .gpu_errors import GPUError, GPUUnavailableError, GPUUnsupportedError
from .gpu_packets import (
    DrawPacket,
    LoweredPlan,
    lower_plan,
    lower_render_plan,
    lower_to_draw_packets,
)
from .gpu_raster import wgpu_rgba as _wgpu_rgba
from .gpu_readback import map_readback_sync as _map_readback_sync_impl
from .gpu_readback import release_gpu_resources
from .gpu_runtime import (
    GPUProbe,
    is_available,
    probe,
    probe_gpu,
)


def _map_readback_sync(readback: Any, *, wgpu: Any, device: Any) -> bytes:
    """Map a staging buffer and return its bytes after GPU completion."""

    return _map_readback_sync_impl(
        readback,
        wgpu=wgpu,
        device=device,
        error_type=GPUUnavailableError,
    )


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
    """Render an opaque plan through WebGPU without CPU fallback.

    The current implementation validates and lowers plans fully and then
    requires a synchronous adapter/device. A host without a graphics runtime
    receives a ``GPUUnavailableError``; unsupported plan features receive
    ``GPUUnsupportedError``. No CPU renderer is invoked on either path.
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

    # The packet-to-device path is intentionally isolated. A clear, explicit
    # error is preferable to claiming a successful GPU render when a wgpu
    # version does not provide the synchronous off-screen APIs this path uses.
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

    At most one frame is in flight. Pipelines stay on the device; colour
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

        The caller keeps the device. A second call waits until this one
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
        durable = list(self._cache.get("_instance_resources") or [])
        release_gpu_resources(durable, device, release_device=True)
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
