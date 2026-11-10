"""Instanced sphere, cylinder, and ellipsoid drawing for the GPU backend.

The unit meshes are uploaded once per frame. Each atom or bond is one
instance, so a crystal does not pay a unique high-resolution mesh per site.
Ellipsoid normals use the inverse-transpose of the principal-axis scale,
matching :func:`mat_viewer.render.geometry.ellipsoid_mesh`. Cylinder caps
use axial normals in world space. Lighting is the CPU camera-space light
expressed in world space.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .gpu_camera import uniform_bytes, viewport_pixel_bounds
from .gpu_errors import GPUUnsupportedError


def ellipsoid_shading_normal(
    local_normal: np.ndarray,
    lengths: np.ndarray,
    axes: np.ndarray,
) -> np.ndarray:
    """World shading normal for one unit-sphere direction.

    ``axes`` stores principal directions as columns. This is the same
    inverse-transpose transform used by ``ellipsoid_mesh`` and by the
    ellipsoid instance shader.
    """

    safe = np.maximum(np.asarray(lengths, dtype=np.float64), 1e-8)
    scaled = np.asarray(local_normal, dtype=np.float64) / safe
    normal = np.asarray(axes, dtype=np.float64) @ scaled
    norm = float(np.linalg.norm(normal))
    if norm < 1e-12:
        return np.zeros(3, dtype=np.float64)
    return normal / norm


def instance_batch_key(packet: Any) -> tuple[Any, ...]:
    """Group instances that share a shape, viewport camera, and rectangle."""

    return (packet.kind, packet.viewport_id, tuple(float(value) for value in packet.rect), bool(packet.metadata.get("flat", False)))


def _unit_sphere(lat: int = 8, lon: int = 12) -> tuple[np.ndarray, np.ndarray]:
    positions = [[0.0, 0.0, 1.0]]
    for i in range(1, lat):
        theta = math.pi * i / lat
        for j in range(lon):
            phi = 2.0 * math.pi * j / lon
            positions.append(
                [
                    math.sin(theta) * math.cos(phi),
                    math.sin(theta) * math.sin(phi),
                    math.cos(theta),
                ]
            )
    positions.append([0.0, 0.0, -1.0])
    points = np.asarray(positions, dtype=np.float32)
    triangles: list[list[int]] = []
    for j in range(lon):
        nxt = (j + 1) % lon
        triangles.append([0, 1 + j, 1 + nxt])
        south = len(points) - 1
        base = 1 + (lat - 2) * lon
        triangles.append([south, base + nxt, base + j])
    for i in range(lat - 2):
        row = 1 + i * lon
        below = row + lon
        for j in range(lon):
            nxt = (j + 1) % lon
            triangles.append([row + j, below + j, below + nxt])
            triangles.append([row + j, below + nxt, row + nxt])
    faces = np.asarray(triangles, dtype=np.uint32)
    interleaved = np.concatenate([points, points], axis=1).astype("<f4", copy=False)
    return interleaved, faces


def _unit_cylinder(sides: int = 12) -> tuple[np.ndarray, np.ndarray]:
    ring = []
    for j in range(sides):
        phi = 2.0 * math.pi * j / sides
        ring.append((math.cos(phi), math.sin(phi)))
    positions: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    for z in (0.0, 1.0):
        for x, y in ring:
            positions.append((x, y, z))
            normals.append((x, y, 0.0))
    bottom_center = len(positions)
    positions.append((0.0, 0.0, 0.0))
    normals.append((0.0, 0.0, -1.0))
    top_center = len(positions)
    positions.append((0.0, 0.0, 1.0))
    normals.append((0.0, 0.0, 1.0))
    cap_bottom: list[int] = []
    cap_top: list[int] = []
    for x, y in ring:
        cap_bottom.append(len(positions))
        positions.append((x, y, 0.0))
        normals.append((0.0, 0.0, -1.0))
        cap_top.append(len(positions))
        positions.append((x, y, 1.0))
        normals.append((0.0, 0.0, 1.0))
    points = np.asarray(positions, dtype=np.float32)
    norms = np.asarray(normals, dtype=np.float32)
    triangles: list[list[int]] = []
    for j in range(sides):
        nxt = (j + 1) % sides
        triangles.append([j, nxt, sides + j])
        triangles.append([nxt, sides + nxt, sides + j])
        triangles.append([bottom_center, cap_bottom[nxt], cap_bottom[j]])
        triangles.append([top_center, cap_top[j], cap_top[nxt]])
    faces = np.asarray(triangles, dtype=np.uint32)
    return np.concatenate([points, norms], axis=1).astype("<f4", copy=False), faces


_SPHERE_WGSL = """
struct Uniforms { view_proj: mat4x4<f32>, light: vec3<f32>, flat: f32, };
@group(0) @binding(0) var<uniform> uniforms: Uniforms;
struct VertexOut {
  @builtin(position) position: vec4<f32>,
  @location(0) color: vec4<f32>,
  @location(1) base: vec4<f32>,
  @location(2) world: vec3<f32>,
  @location(3) smooth_n: vec3<f32>,
};
fn face_lit(base: vec4<f32>, world: vec3<f32>, smooth_n: vec3<f32>) -> vec4<f32> {
  let derived = cross(dpdx(world), dpdy(world));
  let derived_len = length(derived);
  let n = select(smooth_n, derived / max(derived_len, 1e-8), derived_len > 1e-8);
  let lit = 0.68 + 0.32 * abs(dot(normalize(n), normalize(uniforms.light)));
  return vec4<f32>(base.rgb * lit, base.a);
}
@vertex fn vs_sphere(
  @location(0) local: vec3<f32>, @location(1) normal: vec3<f32>,
  @location(2) center: vec3<f32>, @location(3) radius: f32, @location(4) color: vec4<f32>
) -> VertexOut {
  var out: VertexOut;
  let world = center + local * radius;
  let n = normalize(normal);
  let lit = 0.68 + 0.32 * abs(dot(n, normalize(uniforms.light)));
  out.position = uniforms.view_proj * vec4<f32>(world, 1.0);
  out.color = vec4<f32>(color.rgb * lit, color.a);
  out.base = color;
  out.world = world;
  out.smooth_n = n;
  return out;
}
@fragment fn fs_sphere(in: VertexOut) -> @location(0) vec4<f32> {
  if (uniforms.flat > 0.5) { return face_lit(in.base, in.world, in.smooth_n); }
  return in.color;
}
"""

_CYLINDER_WGSL = """
struct Uniforms { view_proj: mat4x4<f32>, light: vec3<f32>, flat: f32, };
@group(0) @binding(0) var<uniform> uniforms: Uniforms;
struct VertexOut {
  @builtin(position) position: vec4<f32>,
  @location(0) color: vec4<f32>,
  @location(1) base: vec4<f32>,
  @location(2) world: vec3<f32>,
  @location(3) smooth_n: vec3<f32>,
};
fn face_lit(base: vec4<f32>, world: vec3<f32>, smooth_n: vec3<f32>) -> vec4<f32> {
  let derived = cross(dpdx(world), dpdy(world));
  let derived_len = length(derived);
  let n = select(smooth_n, derived / max(derived_len, 1e-8), derived_len > 1e-8);
  let lit = 0.68 + 0.32 * abs(dot(normalize(n), normalize(uniforms.light)));
  return vec4<f32>(base.rgb * lit, base.a);
}
@vertex fn vs_cylinder(
  @location(0) local: vec3<f32>, @location(1) normal: vec3<f32>,
  @location(2) start: vec3<f32>, @location(3) radius: f32,
  @location(4) end: vec3<f32>, @location(5) color: vec4<f32>
) -> VertexOut {
  var out: VertexOut;
  let axis = end - start;
  let axis_n = axis / max(length(axis), 1e-8);
  let helper = select(vec3<f32>(0.0, 0.0, 1.0), vec3<f32>(1.0, 0.0, 0.0), abs(axis_n.z) > 0.9);
  let side = normalize(cross(axis_n, helper));
  let side2 = cross(axis_n, side);
  let radial = side * local.x + side2 * local.y;
  let world = start + axis * local.z + radial * radius;
  let cap_n = axis_n * normal.z;
  let n = select(radial, cap_n, abs(normal.z) > 0.5);
  let lit = 0.68 + 0.32 * abs(dot(normalize(n), normalize(uniforms.light)));
  out.position = uniforms.view_proj * vec4<f32>(world, 1.0);
  out.color = vec4<f32>(color.rgb * lit, color.a);
  out.base = color;
  out.world = world;
  out.smooth_n = n;
  return out;
}
@fragment fn fs_cylinder(in: VertexOut) -> @location(0) vec4<f32> {
  if (uniforms.flat > 0.5) { return face_lit(in.base, in.world, in.smooth_n); }
  return in.color;
}
"""

_ELLIPSOID_WGSL = """
struct Uniforms { view_proj: mat4x4<f32>, light: vec3<f32>, flat: f32, };
@group(0) @binding(0) var<uniform> uniforms: Uniforms;
struct VertexOut {
  @builtin(position) position: vec4<f32>,
  @location(0) color: vec4<f32>,
  @location(1) base: vec4<f32>,
  @location(2) world: vec3<f32>,
  @location(3) smooth_n: vec3<f32>,
};
fn face_lit(base: vec4<f32>, world: vec3<f32>, smooth_n: vec3<f32>) -> vec4<f32> {
  let derived = cross(dpdx(world), dpdy(world));
  let derived_len = length(derived);
  let n = select(smooth_n, derived / max(derived_len, 1e-8), derived_len > 1e-8);
  let lit = 0.68 + 0.32 * abs(dot(normalize(n), normalize(uniforms.light)));
  return vec4<f32>(base.rgb * lit, base.a);
}
@vertex fn vs_ellipsoid(
  @location(0) local: vec3<f32>, @location(1) normal: vec3<f32>,
  @location(2) center: vec3<f32>, @location(3) length0: f32,
  @location(4) lengths: vec3<f32>, @location(5) axis0: vec3<f32>,
  @location(6) axis1: vec3<f32>, @location(7) axis2: vec3<f32>,
  @location(8) color: vec4<f32>
) -> VertexOut {
  var out: VertexOut;
  let lengths3 = vec3<f32>(length0, lengths.y, lengths.z);
  let safe = max(lengths3, vec3<f32>(1e-8));
  let scaled = local * lengths3;
  let world = center + axis0 * scaled.x + axis1 * scaled.y + axis2 * scaled.z;
  let n = normalize(
    axis0 * (normal.x / safe.x) + axis1 * (normal.y / safe.y) + axis2 * (normal.z / safe.z)
  );
  let lit = 0.68 + 0.32 * abs(dot(n, normalize(uniforms.light)));
  out.position = uniforms.view_proj * vec4<f32>(world, 1.0);
  out.color = vec4<f32>(color.rgb * lit, color.a);
  out.base = color;
  out.world = world;
  out.smooth_n = n;
  return out;
}
@fragment fn fs_ellipsoid(in: VertexOut) -> @location(0) vec4<f32> {
  if (uniforms.flat > 0.5) { return face_lit(in.base, in.world, in.smooth_n); }
  return in.color;
}
"""


def _buffer(device: Any, data: bytes, usage: int, resources: list[Any]) -> Any:
    buf = device.create_buffer(size=max(4, len(data)), usage=usage)
    resources.append(buf)
    device.queue.write_buffer(buf, 0, data)
    return buf


def _pipeline(
    device: Any,
    shader: Any,
    layout: Any,
    buffers: list[dict[str, Any]],
    *,
    entry: str,
    fragment: str,
    blend: dict[str, Any] | None,
) -> Any:
    target: dict[str, Any] = {"format": "rgba8unorm"}
    if blend is not None:
        target["blend"] = blend
    return device.create_render_pipeline(
        layout=layout,
        vertex={"module": shader, "entry_point": entry, "buffers": buffers},
        primitive={"topology": "triangle-list", "cull_mode": "none"},
        fragment={"module": shader, "entry_point": fragment, "targets": [target]},
        depth_stencil={
            "format": "depth32float",
            "depth_write_enabled": blend is None,
            "depth_compare": "less",
        },
        multisample={"count": 1},
    )


def _viewport(render_pass: Any, lowered: Any, rect: tuple[float, float, float, float]) -> None:
    left, top, right, bottom = viewport_pixel_bounds(rect, lowered.width, lowered.height)
    render_pass.set_viewport(left, top, right - left, bottom - top, 0.0, 1.0)
    render_pass.set_scissor_rect(left, top, right - left, bottom - top)


def draw_instanced_shapes(
    *,
    device: Any,
    render_pass: Any,
    lowered: Any,
    pipeline_layout: Any,
    uniform_layout: Any,
    uniform_usage: int,
    vertex_usage: int,
    index_usage: int,
    resources: list[Any],
    cache: dict[str, Any] | None = None,
) -> None:
    """Draw sphere, cylinder, and ellipsoid packets with one mesh per shape."""

    groups: dict[tuple[Any, ...], list[Any]] = {}
    for packet in lowered.packets:
        if packet.kind not in {"sphere", "cylinder", "ellipsoid"}:
            continue
        groups.setdefault(instance_batch_key(packet), []).append(packet)
    if not groups:
        return
    stored = cache if cache is not None else {}
    durable = stored.setdefault("_instance_resources", [])
    pipelines = stored.get("instance_pipelines")
    if pipelines is None:
        sphere_vertices, sphere_indices = _unit_sphere()
        cylinder_vertices, cylinder_indices = _unit_cylinder()
        sphere_shader = device.create_shader_module(code=_SPHERE_WGSL)
        cylinder_shader = device.create_shader_module(code=_CYLINDER_WGSL)
        ellipsoid_shader = device.create_shader_module(code=_ELLIPSOID_WGSL)
        mesh_buffers = [
            {
                "array_stride": 24,
                "attributes": [
                    {"format": "float32x3", "offset": 0, "shader_location": 0},
                    {"format": "float32x3", "offset": 12, "shader_location": 1},
                ],
            }
        ]
        sphere_instance = {
            "array_stride": 32,
            "step_mode": "instance",
            "attributes": [
                {"format": "float32x3", "offset": 0, "shader_location": 2},
                {"format": "float32", "offset": 12, "shader_location": 3},
                {"format": "float32x4", "offset": 16, "shader_location": 4},
            ],
        }
        cylinder_instance = {
            "array_stride": 48,
            "step_mode": "instance",
            "attributes": [
                {"format": "float32x3", "offset": 0, "shader_location": 2},
                {"format": "float32", "offset": 12, "shader_location": 3},
                {"format": "float32x3", "offset": 16, "shader_location": 4},
                {"format": "float32x4", "offset": 32, "shader_location": 5},
            ],
        }
        ellipsoid_instance = {
            "array_stride": 96,
            "step_mode": "instance",
            "attributes": [
                {"format": "float32x3", "offset": 0, "shader_location": 2},
                {"format": "float32", "offset": 12, "shader_location": 3},
                {"format": "float32x3", "offset": 16, "shader_location": 4},
                {"format": "float32x3", "offset": 32, "shader_location": 5},
                {"format": "float32x3", "offset": 48, "shader_location": 6},
                {"format": "float32x3", "offset": 64, "shader_location": 7},
                {"format": "float32x4", "offset": 80, "shader_location": 8},
            ],
        }
        pipelines = {
            "sphere": _pipeline(
                device,
                sphere_shader,
                pipeline_layout,
                mesh_buffers + [sphere_instance],
                entry="vs_sphere",
                fragment="fs_sphere",
                blend=None,
            ),
            "cylinder": _pipeline(
                device,
                cylinder_shader,
                pipeline_layout,
                mesh_buffers + [cylinder_instance],
                entry="vs_cylinder",
                fragment="fs_cylinder",
                blend=None,
            ),
            "ellipsoid": _pipeline(
                device,
                ellipsoid_shader,
                pipeline_layout,
                mesh_buffers + [ellipsoid_instance],
                entry="vs_ellipsoid",
                fragment="fs_ellipsoid",
                blend=None,
            ),
        }
        stored["instance_pipelines"] = pipelines
        stored["unit_vertex_buffers"] = {
            "sphere": _buffer(device, sphere_vertices.tobytes(), vertex_usage, durable),
            "cylinder": _buffer(device, cylinder_vertices.tobytes(), vertex_usage, durable),
        }
        stored["unit_index_buffers"] = {
            "sphere": _buffer(device, sphere_indices.astype("<u4").tobytes(), index_usage, durable),
            "cylinder": _buffer(
                device, cylinder_indices.astype("<u4").tobytes(), index_usage, durable
            ),
        }
        stored["unit_index_counts"] = {
            "sphere": len(sphere_indices.ravel()),
            "cylinder": len(cylinder_indices.ravel()),
        }
    index_buffers = stored["unit_index_buffers"]
    vertex_buffers = stored["unit_vertex_buffers"]
    index_counts = stored["unit_index_counts"]
    for key, packets in groups.items():
        kind = key[0]
        camera = lowered.cameras.get(packets[0].viewport_id)
        if camera is None:
            raise GPUUnsupportedError(
                f"GPU packet {packets[0].semantic_id!r} has no viewport camera"
            )
        rows = []
        for packet in packets:
            color = np.asarray(packet.rgba, dtype=np.float32)
            if kind == "sphere":
                rows.append(np.concatenate([packet.vertices[0], [packet.metadata["radius"]], color]))
            elif kind == "cylinder":
                rows.append(
                    np.concatenate(
                        [
                            packet.vertices[0],
                            [packet.metadata["radius"]],
                            packet.vertices[1],
                            [0.0],
                            color,
                        ]
                    )
                )
            else:
                lengths = np.asarray(packet.metadata["lengths"], dtype=np.float32)
                axes = np.asarray(packet.metadata["axes"], dtype=np.float32)
                row = np.zeros(24, dtype=np.float32)
                row[0:3] = packet.vertices[0]
                row[3] = lengths[0]
                row[4:7] = lengths
                row[8:11] = axes[:, 0]
                row[12:15] = axes[:, 1]
                row[16:19] = axes[:, 2]
                row[20:24] = color
                rows.append(row)
        payload = np.asarray(rows, dtype="<f4")
        instance_buffer = _buffer(device, payload.tobytes(), vertex_usage, resources)
        rect = packets[0].rect
        left, top, right, bottom = viewport_pixel_bounds(rect, lowered.width, lowered.height)
        aspect = (right - left) / max(bottom - top, 1)
        uniform_buffer = _buffer(
            device,
            uniform_bytes(
                camera,
                aspect=aspect,
                flat=bool(packets[0].metadata.get("flat", False)),
            ),
            uniform_usage,
            resources,
        )
        bind_group = device.create_bind_group(
            layout=uniform_layout,
            entries=[{"binding": 0, "resource": {"buffer": uniform_buffer}}],
        )
        resources.append(bind_group)
        _viewport(render_pass, lowered, rect)
        mesh_kind = "sphere" if kind == "ellipsoid" else kind
        render_pass.set_pipeline(pipelines[kind])
        render_pass.set_bind_group(0, bind_group)
        render_pass.set_vertex_buffer(0, vertex_buffers[mesh_kind])
        render_pass.set_vertex_buffer(1, instance_buffer)
        render_pass.set_index_buffer(index_buffers[mesh_kind], "uint32")
        render_pass.draw_indexed(index_counts[mesh_kind], len(packets), 0, 0, 0)
