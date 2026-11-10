from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

import mat_viewer.render.gpu as gpu
from mat_viewer.capabilities import requirements_for_render, resolve_requirements
from mat_viewer.cli import main
from mat_viewer.render.contracts import (
    CameraSpec,
    LinePrimitive,
    RenderPlan,
    TextPrimitive,
    TriangleMeshPrimitive,
    ViewportPlan,
)


def _plan(*primitives, background=(1.0, 1.0, 1.0, 1.0)) -> RenderPlan:
    camera = CameraSpec.looking_along((0.0, 0.0, 1.0), up=(0.0, 1.0, 0.0))
    return RenderPlan(
        width=32,
        height=24,
        background=background,
        viewports=(ViewportPlan("main", camera=camera, primitives=tuple(primitives)),),
        metadata={"requested_backend": "gpu"},
    )


def _triangle(*, alpha: float = 1.0) -> TriangleMeshPrimitive:
    return TriangleMeshPrimitive(
        semantic_id="triangle",
        vertices=np.asarray([[-1, -1, 0], [1, -1, 0], [0, 1, 0]], dtype=float),
        triangles=np.asarray([[0, 1, 2]], dtype=int),
        rgba=(0.2, 0.4, 0.8, alpha),
    )


def test_gpu_session_rejects_a_second_in_flight_frame() -> None:
    session = gpu.GpuSession()
    session._in_flight = 1
    with pytest.raises(gpu.GPUError, match="in-flight"):
        session.render_frame(_plan(_triangle()))
    session._in_flight = 0
    session.close()


def test_frame_batch_lowers_to_instanced_packets() -> None:
    from mat_viewer.loader.frame_batch import FrameBatch
    from mat_viewer.render.gpu_batch import lowered_from_frame

    frame = FrameBatch(
        positions=np.asarray([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=float),
        atomic_numbers=np.asarray([11, 17], dtype=np.uint8),
        atom_ids=None,
        origin=np.zeros(3),
        cell=np.eye(3) * 4.0,
        pbc=np.asarray([True, True, True]),
        timestep=0,
        source_index=0,
    )
    camera = CameraSpec.looking_along((0.0, 0.0, 1.0), up=(0.0, 1.0, 0.0))
    lowered = lowered_from_frame(
        frame,
        camera,
        width=32,
        height=24,
        background=(1.0, 1.0, 1.0, 1.0),
        atom_scale=1.0,
        bond_radius=0.15,
        show_hydrogen=True,
        show_cell=True,
        cell_color="#333333",
        cell_width_px=2.0,
        bonded=False,
        tracker=None,
    )
    assert [packet.kind for packet in lowered.packets] == ["sphere", "sphere", "line"]
    assert lowered.packets[-1].metadata["depth_test"] is False


def test_gpu_animation_formats_are_declared() -> None:
    assert requirements_for_render("clip.gif", "gpu") == ("gif", "gpu", "animation")
    assert requirements_for_render("clip.mp4", "gpu") == ("mp4", "gpu", "animation")


def test_gpu_requirements_are_explicit_and_png_only() -> None:
    assert requirements_for_render("figure.png", "gpu") == ("png", "gpu")
    assert resolve_requirements("gpu").capabilities == ("core", "gpu")
    with pytest.raises(ValueError, match="GPU backend supports PNG"):
        requirements_for_render("figure.svg", "gpu")


def test_lower_plan_preserves_camera_and_semantic_ids() -> None:
    lowered = gpu.lower_plan(_plan(_triangle()))
    assert lowered.plan_sha256 == _plan(_triangle()).fingerprint()
    assert lowered.cameras["main"].projection == "orthographic"
    assert lowered.packets[0].semantic_id == "triangle"
    assert lowered.packets[0].vertices.flags.writeable is False


def test_transparent_triangle_is_rejected() -> None:
    with pytest.raises(gpu.GPUUnsupportedError, match="transparent"):
        gpu.lower_plan(_plan(_triangle(alpha=0.5)))


def test_cell_edges_lower_when_width_is_not_one_pixel() -> None:
    edges = LinePrimitive(
        "unit-cell",
        np.asarray([[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]], dtype=float),
        rgba=(0.2, 0.2, 0.2, 1.0),
        width_px=2.0,
        depth_test=False,
        metadata={"kind": "unit_cell"},
    )
    lowered = gpu.lower_plan(_plan(edges))
    assert lowered.packets[0].kind == "line"
    assert lowered.packets[0].metadata["depth_test"] is False
    assert lowered.packets[0].metadata["width_px"] == pytest.approx(2.0)


def test_sphere_and_bond_lower_to_instances() -> None:
    sphere = TriangleMeshPrimitive(
        semantic_id="atom",
        vertices=np.zeros((4, 3)),
        triangles=np.asarray([[0, 1, 2]]),
        rgba=(0.1, 0.2, 0.3, 1.0),
        metadata={
            "_raster_shape": "sphere",
            "_raster_center": (1.0, 2.0, 3.0),
            "_raster_radius": 0.4,
        },
    )
    bond = TriangleMeshPrimitive(
        semantic_id="bond",
        vertices=np.zeros((4, 3)),
        triangles=np.asarray([[0, 1, 2]]),
        rgba=(0.4, 0.5, 0.6, 1.0),
        metadata={
            "_raster_shape": "cylinder",
            "_raster_start": (0.0, 0.0, 0.0),
            "_raster_end": (1.0, 0.0, 0.0),
            "_raster_radius": 0.15,
        },
    )
    lowered = gpu.lower_plan(_plan(sphere, bond))
    assert [packet.kind for packet in lowered.packets] == ["sphere", "cylinder"]


@pytest.mark.parametrize(
    "primitive",
    [
        LinePrimitive(
            "dashed",
            np.asarray([[[0, 0, 0], [1, 0, 0]]], dtype=float),
            rgba=(0.1, 0.1, 0.1, 1.0),
            dash=(2.0, 1.0),
        ),
        TextPrimitive("label", (0, 0, 0), "C"),
    ],
)
def test_lower_plan_rejects_features_without_gpu_parity(primitive) -> None:
    with pytest.raises(gpu.GPUUnsupportedError):
        gpu.lower_plan(_plan(primitive))


def test_gpu_render_never_falls_back_when_runtime_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(gpu, "probe", lambda: gpu.GPUProbe(False, reason="no adapter"))
    with pytest.raises(gpu.GPUUnavailableError, match="no adapter"):
        gpu.render(_plan(_triangle()))


def test_readback_waits_for_async_map_callback() -> None:
    class FakeBuffer:
        def __init__(self) -> None:
            self.callback = None
            self.mapped = False

        def map_async(self, mode, callback):
            self.callback = callback

        def get_mapped_range(self):
            assert self.mapped
            return b"rgba"

    class FakeDevice:
        def __init__(self, buffer) -> None:
            self.buffer = buffer

        def poll(self, _wait=True):
            if self.buffer.callback is not None and not self.buffer.mapped:
                self.buffer.mapped = True
                self.buffer.callback("success")

    class FakeWgpu:
        class MapMode:
            READ = 1

    buffer = FakeBuffer()
    assert gpu._map_readback_sync(buffer, wgpu=FakeWgpu, device=FakeDevice(buffer)) == b"rgba"


def test_readback_polls_one_argument_map_async_before_reading() -> None:
    class FakeBuffer:
        def __init__(self) -> None:
            self.mapped = False

        def map_async(self, mode):
            del mode

        def get_mapped_range(self):
            assert self.mapped
            return b"rgba"

    class FakeDevice:
        def __init__(self, buffer) -> None:
            self.buffer = buffer
            self.poll_count = 0

        def poll(self, _wait=True):
            self.poll_count += 1
            self.buffer.mapped = True

    class FakeWgpu:
        class MapMode:
            READ = 1

    buffer = FakeBuffer()
    device = FakeDevice(buffer)
    assert gpu._map_readback_sync(buffer, wgpu=FakeWgpu, device=device) == b"rgba"
    assert device.poll_count == 1


def test_readback_supports_wgpu_python_read_mapped_api() -> None:
    class FakeBuffer:
        def map_sync(self, mode):
            assert mode == 1

        def read_mapped(self):
            return memoryview(b"rgba")

    class FakeWgpu:
        class MapMode:
            READ = 1

    assert gpu._map_readback_sync(FakeBuffer(), wgpu=FakeWgpu, device=object()) == b"rgba"


def test_gpu_render_receipt_has_stage_timings_without_device(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gpu, "probe", lambda: gpu.GPUProbe(True, adapter_name="test"))
    monkeypatch.setattr(
        gpu,
        "_wgpu_rgba",
        lambda lowered, probe_info, cache=None: (
            bytes(lowered.width * lowered.height * 4),
            {"upload": 0.01, "draw": 0.02, "readback": 0.03},
        ),
    )
    result = gpu.render(_plan(_triangle()), tmp_path / "figure.png")
    assert result.backend == "gpu"
    assert result.output and result.output.exists()
    assert result.metadata["gpu"]["adapter_name"] == "test"
    assert {
        "plan",
        "packet_assembly",
        "upload",
        "draw",
        "readback",
        "overlay_encode",
        "total",
    } <= set(result.metadata["timing_s"])


def test_gpu_cli_check_reports_missing_device_without_loading_input(capsys, tmp_path: Path, monkeypatch) -> None:
    import mat_viewer.capabilities as capability_module

    monkeypatch.setattr(
        capability_module,
        "gpu_probe",
        lambda: {
            "installed": False,
            "adapter_detected": False,
            "device_initialized": False,
            "hardware_accelerated": False,
            "adapter": None,
            "error": "wgpu is not installed",
            "available": False,
        },
    )
    with pytest.raises(SystemExit) as raised:
        main(
            [
                "render",
                str(tmp_path / "missing.cif"),
                "-o",
                str(tmp_path / "out.png"),
                "--backend",
                "gpu",
                "--check",
                "--json",
            ]
        )
    assert raised.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["backend"] == "gpu"
    assert payload["ok"] is False
    assert payload["requirements"]["missing_capabilities"] == ["gpu"]


def test_cylinder_packet_rejects_coincident_endpoints() -> None:
    with pytest.raises(ValueError, match="distinct endpoints"):
        gpu.DrawPacket(
            kind="cylinder",
            semantic_id="bond",
            viewport_id="main",
            vertices=np.zeros((2, 3)),
            indices=None,
            rgba=(1.0, 1.0, 1.0, 1.0),
            rect=(0.0, 0.0, 1.0, 1.0),
            metadata={"radius": 0.1},
        )


def test_wide_and_ortep_lines_use_the_cpu_overlay() -> None:
    from mat_viewer.render.gpu_packets import line_needs_cpu_overlay

    wide = LinePrimitive(
        "unit-cell",
        np.asarray([[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]], dtype=float),
        rgba=(0.2, 0.2, 0.2, 1.0),
        width_px=2.0,
        depth_test=False,
        metadata={"kind": "unit_cell"},
    )
    thin = LinePrimitive(
        "overlay",
        np.asarray([[[0.0, 0.0, 0.0], [0.0, 1.0, 0.0]]], dtype=float),
        rgba=(0.0, 0.0, 0.0, 1.0),
        width_px=1.0,
        depth_test=False,
    )
    depth_tested = LinePrimitive(
        "axis",
        np.asarray([[[0.0, 0.0, 0.0], [0.0, 0.0, 1.0]]], dtype=float),
        rgba=(0.0, 0.0, 0.0, 1.0),
        width_px=1.0,
        depth_test=True,
    )
    hatch = LinePrimitive(
        "hatch",
        np.asarray([[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]], dtype=float),
        rgba=(0.1, 0.1, 0.1, 1.0),
        width_px=0.7,
        metadata={"kind": "ortep_hatch"},
    )
    lowered = gpu.lower_plan(_plan(wide, thin, depth_tested, hatch))
    assert [line_needs_cpu_overlay(packet) for packet in lowered.packets] == [
        True,
        True,
        False,
        True,
    ]


def test_ellipsoid_shading_normal_matches_mesh() -> None:
    from mat_viewer.render.geometry import ellipsoid_mesh, ellipsoid_principal_axes
    from mat_viewer.render.gpu_draw import _ELLIPSOID_WGSL, ellipsoid_shading_normal

    displacement = np.diag([1.0, 0.25, 0.05])
    lengths, axes = ellipsoid_principal_axes(displacement)
    _vertices, _triangles, normals = ellipsoid_mesh(
        (0.0, 0.0, 0.0), displacement, lat_steps=4, lon_steps=6
    )
    got = ellipsoid_shading_normal(np.array([0.0, 0.0, 1.0]), lengths, axes)
    assert np.allclose(got, normals[0], atol=1e-5)
    assert "normal.x / safe.x" in _ELLIPSOID_WGSL


def test_cylinder_caps_use_axial_normals() -> None:
    from mat_viewer.render.gpu_draw import _CYLINDER_WGSL, _unit_cylinder

    interleaved, faces = _unit_cylinder(sides=12)
    normals = interleaved[:, 3:6]
    saw_cap = False
    saw_side = False
    for triangle in faces:
        axial = np.abs(normals[triangle, 2])
        if np.any(axial > 0.5):
            assert np.all(axial > 0.5)
            saw_cap = True
        else:
            assert np.all(axial < 1e-6)
            saw_side = True
    assert saw_cap and saw_side
    assert "axis_n * normal.z" in _CYLINDER_WGSL


def test_instance_batches_do_not_merge_viewports() -> None:
    from mat_viewer.render.gpu_draw import instance_batch_key

    def packet(viewport: str) -> gpu.DrawPacket:
        return gpu.DrawPacket(
            kind="sphere",
            semantic_id="atom",
            viewport_id=viewport,
            vertices=np.zeros((1, 3)),
            indices=None,
            rgba=(0.2, 0.4, 0.8, 1.0),
            rect=(0.0, 0.0, 1.0, 1.0),
            metadata={"radius": 0.3},
        )

    assert instance_batch_key(packet("left")) != instance_batch_key(packet("right"))


def test_gpu_light_matches_the_cpu_camera_light() -> None:
    from mat_viewer.render.gpu_camera import uniform_bytes, world_light

    camera = CameraSpec.looking_along((1.0, 0.2, 0.4), up=(0.0, 0.0, 1.0))
    light = world_light(camera)
    assert light.shape == (3,)
    assert float(np.linalg.norm(light)) == pytest.approx(1.0, abs=1e-5)
    uploaded = np.frombuffer(uniform_bytes(camera, aspect=1.25)[64:76], dtype="<f4")
    assert np.allclose(uploaded, light)


def test_session_close_releases_cached_instance_buffers() -> None:
    destroyed: list[str] = []

    class Buffer:
        def destroy(self) -> None:
            destroyed.append("buffer")

    class Device:
        def destroy(self) -> None:
            destroyed.append("device")

    session = gpu.GpuSession()
    session._cache["device"] = Device()
    session._cache["_instance_resources"] = [Buffer()]
    session.close()
    assert destroyed == ["buffer", "device"]
    assert session._cache == {}
    with pytest.raises(gpu.GPUError, match="closed"):
        session.render_frame(_plan(_triangle()))


def test_ready_session_keeps_its_device_between_frames() -> None:
    from mat_viewer.render.gpu_raster import _release_frame

    destroyed: list[str] = []

    class Buffer:
        def __init__(self, name: str) -> None:
            self.name = name

        def destroy(self) -> None:
            destroyed.append(self.name)

    state = {
        "ready": True,
        "ephemeral": False,
        "device": Buffer("device"),
        "_instance_resources": [Buffer("unit-mesh")],
    }
    _release_frame(state, [Buffer("frame")], drop_device=False)
    assert destroyed == ["frame"]
    assert state["ready"] is True
    assert state["_instance_resources"][0].name == "unit-mesh"



def _batch_frame() -> object:
    from mat_viewer.loader.frame_batch import FrameBatch

    return FrameBatch(
        positions=np.asarray([[0.0, 0.0, 0.0]], dtype=float),
        atomic_numbers=np.asarray([1], dtype=np.uint8),
        atom_ids=None,
        origin=np.zeros(3),
        cell=np.eye(3),
        pbc=np.asarray([False, False, False]),
        timestep=0,
        source_index=0,
    )


def test_frame_batch_hydrogen_uses_the_bond_radius_floor() -> None:
    from mat_viewer.render.cpu.batch import element_style_tables
    from mat_viewer.render.gpu_batch import lowered_from_frame

    camera = CameraSpec.looking_along((0.0, 0.0, 1.0), up=(0.0, 1.0, 0.0))
    lowered = lowered_from_frame(
        _batch_frame(),
        camera,
        width=16,
        height=16,
        background=(1.0, 1.0, 1.0, 1.0),
        atom_scale=1.0,
        bond_radius=0.2,
        show_hydrogen=True,
        show_cell=False,
        cell_color="#333333",
        cell_width_px=1.0,
        bonded=False,
        tracker=None,
    )
    _colors, radii = element_style_tables(1.0, bond_radius=0.2)
    assert lowered.packets[0].metadata["radius"] == pytest.approx(float(radii[1]))


def test_empty_group_lists_keep_the_frame_batch_path() -> None:
    from types import SimpleNamespace

    from mat_viewer.render.gpu_batch import frame_batch_fast_path

    frame = _batch_frame()
    source = SimpleNamespace(frames=(frame, frame))
    assert frame_batch_fast_path(
        source,
        representation="ball",
        atom_groups=[],
        bond_groups=[],
    )


def test_structure_frames_use_the_planned_gpu_session(monkeypatch) -> None:
    from types import SimpleNamespace

    from mat_viewer.render.gpu_batch import render_gpu_animation

    sentinel = object()
    monkeypatch.setattr(
        "mat_viewer.render.gpu_batch.render_animation",
        lambda *args, **kwargs: sentinel,
    )
    source = SimpleNamespace(
        frames=(SimpleNamespace(bundle=object()), SimpleNamespace(bundle=object()))
    )
    assert (
        render_gpu_animation(
            source,
            "movie.gif",
            render_spec=SimpleNamespace(representation="ball"),
            atom_groups=[],
            bond_groups=[],
        )
        is sentinel
    )


def test_frame_batch_property_color_is_not_ignored() -> None:
    from types import SimpleNamespace

    from mat_viewer.render.gpu_batch import render_gpu_animation

    frame = _batch_frame()
    with pytest.raises(gpu.GPUUnsupportedError, match="property"):
        render_gpu_animation(
            SimpleNamespace(frames=(frame, frame)),
            "movie.gif",
            render_spec=SimpleNamespace(representation="ball"),
            atom_property_color={"field": "charge"},
        )


def test_frame_batch_animation_requires_two_frames() -> None:
    from types import SimpleNamespace

    from mat_viewer.render.gpu_batch import render_gpu_animation

    with pytest.raises(ValueError, match="at least two"):
        render_gpu_animation(
            SimpleNamespace(frames=(_batch_frame(),)),
            "movie.gif",
            render_spec=SimpleNamespace(representation="ball"),
        )


def test_viewport_pixel_origin_matches_the_cpu_top() -> None:
    from mat_viewer.render.contracts import ViewportPlan
    from mat_viewer.render.cpu.raster import _viewport_bounds
    from mat_viewer.render.gpu_camera import viewport_pixel_bounds

    rect = (0.05, 0.1, 0.4, 0.2)
    camera = CameraSpec.looking_along((0.0, 0.0, 1.0), up=(0.0, 1.0, 0.0))
    panel = ViewportPlan("lower", camera=camera, primitives=(), rect=rect)
    assert viewport_pixel_bounds(rect, 200, 100) == _viewport_bounds(panel, 200, 100)
    _left, top, _right, bottom = viewport_pixel_bounds(rect, 200, 100)
    assert (top, bottom) == (10, 30)


def test_wide_lines_composite_inside_each_viewport() -> None:
    from mat_viewer.render.cpu.raster import composite_primitives
    from mat_viewer.render.gpu_raster import composite_overlay_lines

    def stroke(name: str, rgba: tuple[float, float, float, float]) -> LinePrimitive:
        return LinePrimitive(
            name,
            np.asarray([[[-0.4, 0.0, 0.0], [0.4, 0.0, 0.0]]], dtype=float),
            rgba=rgba,
            width_px=4.0,
            depth_test=False,
        )

    def packet(name: str, viewport: str, rect: tuple[float, float, float, float], rgba):
        primitive = stroke(name, rgba)
        return gpu.DrawPacket(
            kind="line",
            semantic_id=name,
            viewport_id=viewport,
            vertices=primitive.segments.reshape(-1, 3),
            indices=None,
            rgba=rgba,
            rect=rect,
            metadata={"depth_test": False, "width_px": 4.0},
        )

    top_camera = CameraSpec.looking_along(
        (0.0, 0.0, 1.0), up=(0.0, 1.0, 0.0), distance=8.0, ortho_scale=1.5, near=0.1, far=30.0
    )
    bottom_camera = CameraSpec.looking_along(
        (0.0, 0.0, 1.0), up=(0.0, 1.0, 0.0), distance=8.0, ortho_scale=3.0, near=0.1, far=30.0
    )
    width, height = 48, 24
    background = np.full((height, width, 4), 255, dtype=np.uint8)
    depth = np.ones((height, width), dtype=np.float32)
    lowered = gpu.LoweredPlan(
        width=width,
        height=height,
        background=(1.0, 1.0, 1.0, 1.0),
        packets=(),
        plan_sha256="crop",
        cameras={"top": top_camera, "bottom": bottom_camera},
    )
    painted = np.frombuffer(
        composite_overlay_lines(
            background.tobytes(),
            depth,
            lowered,
            (
                packet("top-line", "top", (0.0, 0.0, 1.0, 0.5), (1.0, 0.0, 0.0, 1.0)),
                packet("bottom-line", "bottom", (0.0, 0.5, 1.0, 0.5), (0.0, 0.0, 1.0, 1.0)),
            ),
        ),
        dtype=np.uint8,
    ).reshape(height, width, 4)

    def reference(camera: CameraSpec, rgba: tuple[float, float, float, float], rows: slice):
        result, _depth = composite_primitives(
            background[rows],
            depth[rows],
            camera,
            [stroke("only", rgba)],
        )
        image = np.frombuffer(result, dtype=np.uint8).reshape(background[rows].shape)
        assert np.any(np.any(image[:, :, :3] != 255, axis=2))
        return image

    assert np.array_equal(
        painted[:12], reference(top_camera, (1.0, 0.0, 0.0, 1.0), slice(0, 12))
    )
    assert np.array_equal(
        painted[12:], reference(bottom_camera, (0.0, 0.0, 1.0, 1.0), slice(12, 24))
    )


def test_depth_disabled_lines_share_the_cpu_overlay() -> None:
    from mat_viewer.render.gpu_packets import line_needs_cpu_overlay
    from mat_viewer.render.gpu_raster import split_raster_packets

    edge = LinePrimitive(
        "edge",
        np.asarray([[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]], dtype=float),
        rgba=(0.0, 0.0, 0.0, 1.0),
        width_px=1.0,
        depth_test=False,
    )
    lowered = gpu.lower_plan(_plan(edge, _triangle()))
    assert line_needs_cpu_overlay(lowered.packets[0])
    main = split_raster_packets(lowered.packets)
    assert [packet.kind for packet in main] == ["mesh"]


def test_depth_disabled_line_stays_in_front_of_a_wide_depth_tested_line() -> None:
    from mat_viewer.render.gpu_raster import composite_overlay_lines

    def packet(name: str, end: tuple[float, float, float], rgba, width: float, depth: bool):
        return gpu.DrawPacket(
            kind="line",
            semantic_id=name,
            viewport_id="main",
            vertices=np.asarray([[(0.0, 0.0, 0.0), end]], dtype=np.float32).reshape(-1, 3),
            indices=None,
            rgba=rgba,
            rect=(0.0, 0.0, 1.0, 1.0),
            metadata={"depth_test": depth, "width_px": width},
        )

    camera = CameraSpec.looking_along(
        (0.0, 0.0, 1.0), up=(0.0, 1.0, 0.0), distance=8.0, ortho_scale=2.0, near=0.1, far=30.0
    )
    width = height = 32
    background = np.full((height, width, 4), 255, dtype=np.uint8)
    lowered = gpu.LoweredPlan(
        width=width,
        height=height,
        background=(1.0, 1.0, 1.0, 1.0),
        packets=(),
        plan_sha256="cross",
        cameras={"main": camera},
    )
    # The wide stroke is listed second. Depth-disabled colour must still win.
    painted = np.frombuffer(
        composite_overlay_lines(
            background.tobytes(),
            np.ones((height, width), dtype=np.float32),
            lowered,
            (
                packet("thin", (1.0, 0.0, 0.0), (1.0, 0.0, 0.0, 1.0), 1.0, False),
                packet("wide", (0.0, 1.0, 0.0), (0.0, 0.0, 1.0, 1.0), 8.0, True),
            ),
        ),
        dtype=np.uint8,
    ).reshape(height, width, 4)
    center = painted[height // 2, width // 2]
    assert center[0] > center[2]
    assert np.any((painted[:, :, 2] > 200) & (painted[:, :, 0] < 30))


def test_frame_identity_tracks_positions_colors_and_camera() -> None:
    from mat_viewer.loader.frame_batch import FrameBatch
    from mat_viewer.render.gpu_batch import lowered_from_frame

    def frame(x: float = 0.0, color: tuple[int, int, int] | None = None) -> FrameBatch:
        return FrameBatch(
            positions=np.asarray([[x, 0.0, 0.0]], dtype=float),
            atomic_numbers=np.asarray([6], dtype=np.uint8),
            atom_ids=None,
            origin=np.zeros(3),
            cell=np.eye(3),
            pbc=np.asarray([False, False, False]),
            timestep=0,
            source_index=0,
            atom_colors=None if color is None else np.asarray([color], dtype=np.uint8),
        )

    def digest(item: FrameBatch, camera: CameraSpec) -> str:
        return lowered_from_frame(
            item,
            camera,
            width=16,
            height=16,
            background=(1.0, 1.0, 1.0, 1.0),
            atom_scale=1.0,
            bond_radius=0.15,
            show_hydrogen=True,
            show_cell=False,
            cell_color="#333333",
            cell_width_px=2.0,
            bonded=False,
            tracker=None,
        ).plan_sha256

    camera_a = CameraSpec.looking_along((0.0, 0.0, 1.0), up=(0.0, 1.0, 0.0), ortho_scale=2.0)
    camera_b = CameraSpec.looking_along((1.0, 0.0, 0.0), up=(0.0, 0.0, 1.0), ortho_scale=4.0)
    base = digest(frame(), camera_a)
    moved = digest(frame(x=1.0), camera_a)
    recolored = digest(frame(color=(10, 20, 30)), camera_a)
    reframed = digest(frame(), camera_b)
    assert len({base, moved, recolored, reframed}) == 4
    flat = lowered_from_frame(
        frame(),
        camera_a,
        width=16,
        height=16,
        background=(1.0, 1.0, 1.0, 1.0),
        atom_scale=1.0,
        bond_radius=0.15,
        show_hydrogen=True,
        show_cell=False,
        cell_color="#333333",
        cell_width_px=2.0,
        bonded=False,
        tracker=None,
        shading="flat",
    )
    assert flat.packets[0].metadata["flat"] is True
    smooth = lowered_from_frame(
        frame(),
        camera_a,
        width=16,
        height=16,
        background=(1.0, 1.0, 1.0, 1.0),
        atom_scale=1.0,
        bond_radius=0.15,
        show_hydrogen=True,
        show_cell=False,
        cell_color="#333333",
        cell_width_px=2.0,
        bonded=False,
        tracker=None,
        shading="smooth",
    )
    assert smooth.packets[0].metadata["flat"] is False
    assert flat.plan_sha256 != smooth.plan_sha256
    import hashlib

    same_length = hashlib.sha256("\n".join((base, base)).encode()).hexdigest()
    changed_frame = hashlib.sha256("\n".join((base, moved)).encode()).hexdigest()
    assert same_length != changed_frame


def test_uniform_binding_includes_the_fragment_stage() -> None:
    from mat_viewer.render.gpu_raster import uniform_binding_visibility

    class Stages:
        VERTEX = 0x1
        FRAGMENT = 0x2
        COMPUTE = 0x4

    class Runtime:
        ShaderStage = Stages

    visibility = uniform_binding_visibility(Runtime)
    assert visibility & Stages.VERTEX
    assert visibility & Stages.FRAGMENT
    assert not visibility & Stages.COMPUTE


def test_instance_pipelines_accept_fragment_uniform_visibility() -> None:
    pytest.importorskip("wgpu")
    import importlib

    from mat_viewer.render.gpu_draw import (
        _CYLINDER_WGSL,
        _ELLIPSOID_WGSL,
        _SPHERE_WGSL,
        _pipeline,
    )
    from mat_viewer.render.gpu_raster import uniform_binding_visibility
    from mat_viewer.render.gpu_runtime import _request_adapter, _request_device

    wgpu = importlib.import_module("wgpu")
    try:
        adapter = _request_adapter(wgpu)
        device = None if adapter is None else _request_device(adapter)
    except Exception as exc:
        pytest.skip(f"no wgpu device: {type(exc).__name__}: {exc}")
    if device is None:
        pytest.skip("wgpu did not provide a device")
    try:
        visibility = uniform_binding_visibility(wgpu)
        assert visibility & int(wgpu.ShaderStage.FRAGMENT)
        layout = device.create_bind_group_layout(
            entries=[
                {
                    "binding": 0,
                    "visibility": visibility,
                    "buffer": {"type": "uniform"},
                }
            ]
        )
        pipeline_layout = device.create_pipeline_layout(bind_group_layouts=[layout])
        mesh = {
            "array_stride": 24,
            "attributes": [
                {"format": "float32x3", "offset": 0, "shader_location": 0},
                {"format": "float32x3", "offset": 12, "shader_location": 1},
            ],
        }
        cases = (
            (_SPHERE_WGSL, "vs_sphere", "fs_sphere", {"array_stride": 32, "step_mode": "instance", "attributes": [
                {"format": "float32x3", "offset": 0, "shader_location": 2},
                {"format": "float32", "offset": 12, "shader_location": 3},
                {"format": "float32x4", "offset": 16, "shader_location": 4},
            ]}),
            (_CYLINDER_WGSL, "vs_cylinder", "fs_cylinder", {"array_stride": 48, "step_mode": "instance", "attributes": [
                {"format": "float32x3", "offset": 0, "shader_location": 2},
                {"format": "float32", "offset": 12, "shader_location": 3},
                {"format": "float32x3", "offset": 16, "shader_location": 4},
                {"format": "float32x4", "offset": 32, "shader_location": 5},
            ]}),
            (_ELLIPSOID_WGSL, "vs_ellipsoid", "fs_ellipsoid", {"array_stride": 96, "step_mode": "instance", "attributes": [
                {"format": "float32x3", "offset": 0, "shader_location": 2},
                {"format": "float32", "offset": 12, "shader_location": 3},
                {"format": "float32x3", "offset": 16, "shader_location": 4},
                {"format": "float32x3", "offset": 32, "shader_location": 5},
                {"format": "float32x3", "offset": 48, "shader_location": 6},
                {"format": "float32x3", "offset": 64, "shader_location": 7},
                {"format": "float32x4", "offset": 80, "shader_location": 8},
            ]}),
        )
        for code, entry, fragment, instance in cases:
            shader = device.create_shader_module(code=code)
            pipeline = _pipeline(
                device,
                shader,
                pipeline_layout,
                [mesh, instance],
                entry=entry,
                fragment=fragment,
                blend=None,
            )
            assert pipeline is not None
    finally:
        destroy = getattr(device, "destroy", None)
        if callable(destroy):
            destroy()


def test_missing_vertex_normals_select_flat_instance_shading() -> None:
    from mat_viewer.render.gpu_camera import uniform_bytes
    from mat_viewer.render.gpu_draw import _ELLIPSOID_WGSL, instance_batch_key

    def ellipsoid(normals: np.ndarray | None) -> TriangleMeshPrimitive:
        return TriangleMeshPrimitive(
            "ellipsoid",
            vertices=np.zeros((4, 3)),
            triangles=np.asarray([[0, 1, 2]]),
            rgba=(0.2, 0.3, 0.4, 1.0),
            vertex_normals=normals,
            metadata={
                "_raster_shape": "ellipsoid",
                "_raster_center": (0.0, 0.0, 0.0),
                "_raster_lengths": (1.0, 0.4, 0.2),
                "_raster_axes": np.eye(3).reshape(-1).tolist(),
            },
        )

    smooth = gpu.lower_plan(_plan(ellipsoid(np.zeros((4, 3))))).packets[0]
    flat = gpu.lower_plan(_plan(ellipsoid(None))).packets[0]
    assert smooth.metadata["flat"] is False
    assert flat.metadata["flat"] is True
    assert instance_batch_key(smooth) != instance_batch_key(flat)
    assert "uniforms.flat" in _ELLIPSOID_WGSL
    assert "dpdx" in _ELLIPSOID_WGSL
    camera = CameraSpec.looking_along((0.0, 0.0, 1.0), up=(0.0, 1.0, 0.0))
    assert np.frombuffer(uniform_bytes(camera, aspect=1.0, flat=True)[76:80], dtype="<f4")[
        0
    ] == pytest.approx(1.0)
    assert np.frombuffer(uniform_bytes(camera, aspect=1.0, flat=False)[76:80], dtype="<f4")[
        0
    ] == pytest.approx(0.0)


