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
