"""One synchronous WebGPU pass for an already lowered plan.

Frame textures and staging buffers are released before the function
returns, including when submission or readback fails. A session cache
keeps the device and the instanced unit meshes; a one-shot call destroys
the device as well.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from .contracts import LinePrimitive
from .gpu_camera import _ndc_to_camera_depth, uniform_bytes, viewport_pixel_bounds
from .gpu_draw import draw_instanced_shapes
from .gpu_errors import GPUUnavailableError, GPUUnsupportedError
from .gpu_packets import DrawPacket, LoweredPlan, line_needs_cpu_overlay
from .gpu_readback import (
    map_readback_sync,
    release_gpu_resources,
    submit_and_wait,
)
from .gpu_runtime import _import_wgpu, _request_adapter, _request_device


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


def _cpu_overlay_lines(packets: tuple[DrawPacket, ...]) -> tuple[DrawPacket, ...]:
    return tuple(packet for packet in packets if line_needs_cpu_overlay(packet))


def uniform_binding_visibility(wgpu: Any) -> int:
    """Return bind-group visibility for the shared frame uniform.

    Instance fragment shaders read ``uniforms.light`` and ``uniforms.flat``.
    Vertex-only visibility makes those pipelines invalid.
    """

    stage = getattr(wgpu, "ShaderStage", None)
    vertex = int(getattr(stage, "VERTEX", 1))
    fragment = int(getattr(stage, "FRAGMENT", 2))
    return vertex | fragment


def split_raster_packets(
    packets: tuple[DrawPacket, ...],
) -> tuple[DrawPacket, ...]:
    """Return the packets drawn in the depth-tested GPU pass.

    Instanced shapes are drawn separately. Lines that need the CPU stroker,
    including every depth-disabled stroke, are omitted so the later composite
    can place them above depth-tested colour.
    """

    return tuple(
        packet
        for packet in packets
        if packet.kind not in {"sphere", "cylinder", "ellipsoid"}
        and not line_needs_cpu_overlay(packet)
    )


def composite_overlay_lines(
    rgba: bytes,
    depth_ndc: np.ndarray,
    lowered: LoweredPlan,
    packets: tuple[DrawPacket, ...],
) -> bytes:
    """Composite wide lines inside each viewport crop.

    Each crop uses that viewport's camera and pixel aspect. A second panel
    does not reuse the first camera or the full-image projection.
    """

    width = int(lowered.width)
    height = int(lowered.height)
    image = np.frombuffer(rgba, dtype=np.uint8).reshape(height, width, 4).copy()
    depth = np.asarray(depth_ndc, dtype=np.float32)
    if depth.shape != (height, width):
        raise GPUUnsupportedError("GPU depth overlay does not match the colour image")
    groups: dict[tuple[Any, ...], list[DrawPacket]] = {}
    for packet in packets:
        groups.setdefault((packet.viewport_id, tuple(packet.rect)), []).append(packet)
    from .cpu.raster import composite_primitives

    for (viewport_id, rect), group in groups.items():
        camera = lowered.cameras.get(viewport_id)
        if camera is None:
            raise GPUUnsupportedError(
                f"GPU packet {group[0].semantic_id!r} has no viewport camera"
            )
        left, top, right, bottom = viewport_pixel_bounds(rect, width, height)
        primitives = [
            LinePrimitive(
                packet.semantic_id,
                np.asarray(packet.vertices, dtype=float).reshape(-1, 2, 3),
                rgba=tuple(packet.rgba),
                width_px=float(packet.metadata.get("width_px", 1.0)),
                depth_test=bool(packet.metadata.get("depth_test", True)),
                metadata={"kind": packet.metadata.get("kind")},
            )
            for packet in group
        ]
        composited, _depth = composite_primitives(
            image[top:bottom, left:right],
            _ndc_to_camera_depth(depth[top:bottom, left:right], camera),
            camera,
            primitives,
        )
        image[top:bottom, left:right] = np.frombuffer(composited, dtype=np.uint8).reshape(
            bottom - top, right - left, 4
        )
    return np.ascontiguousarray(image).tobytes()


def _composite_depth_overlays(
    rgba: bytes,
    lowered: LoweredPlan,
    packets: tuple[DrawPacket, ...],
    *,
    device: Any,
    depth_texture: Any,
    wgpu: Any,
) -> bytes:
    """Draw width-sensitive lines on the read-back colour and depth."""

    width = int(lowered.width)
    height = int(lowered.height)
    row_bytes = width * 4
    padded = ((row_bytes + 255) // 256) * 256
    usage = _usage(wgpu, "COPY_DST") | _usage(wgpu, "MAP_READ")
    buffer = device.create_buffer(size=padded * height, usage=usage)
    try:
        encoder = device.create_command_encoder()
        encoder.copy_texture_to_buffer(
            {"texture": depth_texture},
            {"buffer": buffer, "bytes_per_row": padded, "rows_per_image": height},
            (width, height, 1),
        )
        submit_and_wait(device, [encoder.finish()])
        raw = map_readback_sync(
            buffer, wgpu=wgpu, device=device, error_type=GPUUnavailableError
        )
        depth = np.empty((height, width), dtype=np.float32)
        for row in range(height):
            start = row * padded
            depth[row] = np.frombuffer(
                raw[start : start + row_bytes], dtype="<f4", count=width
            )
        return composite_overlay_lines(rgba, depth, lowered, packets)
    finally:
        release_gpu_resources([buffer], device, release_device=False)


def _release_frame(
    state: dict[str, Any],
    resources: list[Any],
    *,
    drop_device: bool,
) -> None:
    device = state.get("device")
    release_gpu_resources(resources, device, release_device=False)
    if not drop_device:
        return
    durable = list(state.get("_instance_resources") or [])
    release_gpu_resources(durable, device, release_device=True)
    state.clear()


def wgpu_rgba(
    lowered: LoweredPlan,
    *,
    probe_info: Any,
    cache: dict[str, Any] | None = None,
) -> tuple[bytes, dict[str, float]]:
    """Render packets to an off-screen RGBA texture and read it back.

    The function intentionally uses only synchronous ``wgpu`` calls.  A few
    wgpu releases expose asynchronous-only adapter/device APIs; those are
    reported as :class:`GPUUnavailableError` instead of being hidden behind
    a CPU substitute.
    """

    del probe_info  # retained in the signature for callers that cache probes
    started = time.perf_counter()
    state = cache if cache is not None else {}
    state["ephemeral"] = cache is None
    resources: list[Any] = []
    try:
        if not state.get("ready"):
            wgpu = _import_wgpu()
            adapter = _request_adapter(wgpu)
            if adapter is None:
                raise GPUUnavailableError("wgpu could not find a compatible adapter")
            device = _request_device(adapter)
            if device is None:
                raise GPUUnavailableError("wgpu adapter did not provide a device")
            state["device"] = device
            state["wgpu"] = wgpu

            usage_texture = getattr(
                getattr(wgpu, "TextureUsage", object()), "RENDER_ATTACHMENT", None
            )
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
                raise GPUUnavailableError(
                    "wgpu runtime does not expose the required buffer/texture usages"
                )
            # Every staging buffer is populated with ``queue.write_buffer`` and must
            # therefore include COPY_DST in addition to its binding usage.
            uniform_usage |= usage_buffer
            vertex_usage |= usage_buffer
            index_usage |= usage_buffer
            buffer_usage = usage_buffer | map_read
            shader = device.create_shader_module(code=_WGSL)
            uniform_layout = device.create_bind_group_layout(
                entries=[
                    {
                        "binding": 0,
                        "visibility": uniform_binding_visibility(wgpu),
                        "buffer": {"type": "uniform"},
                    }
                ]
            )
            pipeline_layout = device.create_pipeline_layout(
                bind_group_layouts=[uniform_layout]
            )

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
        overlay_packets = _cpu_overlay_lines(tuple(lowered.packets))
        main_packets = split_raster_packets(tuple(lowered.packets))
        camera_by_viewport = lowered.cameras
        for packet in main_packets:
            camera = camera_by_viewport.get(packet.viewport_id)
            if camera is None:
                raise GPUUnsupportedError(
                    f"GPU packet {packet.semantic_id!r} has no viewport camera"
                )
            left, top, right, bottom = viewport_pixel_bounds(
                packet.rect, lowered.width, lowered.height
            )
            aspect = (right - left) / max(bottom - top, 1)
            uniform_data = uniform_bytes(camera, aspect=aspect)
            uniform_buffer = device.create_buffer(
                size=len(uniform_data),
                usage=uniform_usage,
            )
            resources.append(uniform_buffer)
            device.queue.write_buffer(uniform_buffer, 0, uniform_data)
            bind_group = device.create_bind_group(
                layout=uniform_layout,
                entries=[{"binding": 0, "resource": {"buffer": uniform_buffer}}],
            )
            resources.append(bind_group)
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
            render_pass.set_viewport(left, top, right - left, bottom - top, 0.0, 1.0)
            render_pass.set_scissor_rect(left, top, right - left, bottom - top)
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
            {
                "buffer": readback,
                "bytes_per_row": padded_row,
                "rows_per_image": lowered.height,
            },
            (lowered.width, lowered.height, 1),
        )
        commands = encoder.finish()
        upload_elapsed = time.perf_counter() - started
        draw_started = time.perf_counter()
        submit_and_wait(device, [commands])
        draw_elapsed = time.perf_counter() - draw_started
        readback_started = time.perf_counter()
        mapped_bytes = map_readback_sync(
            readback, wgpu=wgpu, device=device, error_type=GPUUnavailableError
        )
        readback_elapsed = time.perf_counter() - readback_started
        rgba = b"".join(
            mapped_bytes[row * padded_row : row * padded_row + unpadded_row]
            for row in range(lowered.height)
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
        elapsed = time.perf_counter() - started
        return rgba, {
            "device_render": elapsed,
            "upload": upload_elapsed,
            "draw": draw_elapsed,
            "readback": readback_elapsed,
        }
    finally:
        _release_frame(
            state,
            resources,
            drop_device=bool(state.get("ephemeral")) or not bool(state.get("ready")),
        )
