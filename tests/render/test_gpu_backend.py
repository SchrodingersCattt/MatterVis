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


def test_gpu_requirements_are_explicit_and_png_only() -> None:
    assert requirements_for_render("figure.png", "gpu") == ("png", "gpu")
    assert resolve_requirements("gpu").capabilities == ("core", "gpu")
    with pytest.raises(ValueError, match="GPU backend currently supports PNG"):
        requirements_for_render("figure.svg", "gpu")


def test_lower_plan_preserves_camera_and_semantic_ids() -> None:
    lowered = gpu.lower_plan(_plan(_triangle()))
    assert lowered.plan_sha256 == _plan(_triangle()).fingerprint()
    assert lowered.cameras["main"].projection == "orthographic"
    assert lowered.packets[0].semantic_id == "triangle"
    assert lowered.packets[0].vertices.flags.writeable is False


@pytest.mark.parametrize(
    "primitive",
    [
        _triangle(alpha=0.5),
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


def test_gpu_render_receipt_has_stage_timings_without_device(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gpu, "probe", lambda: gpu.GPUProbe(True, adapter_name="test"))
    monkeypatch.setattr(
        gpu,
        "_wgpu_rgba",
        lambda lowered, probe_info: (
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
