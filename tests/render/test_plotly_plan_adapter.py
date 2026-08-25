from __future__ import annotations

import numpy as np
import pytest

from mat_viewer.render.contracts import (
    CameraSpec,
    LinePrimitive,
    RenderPlan,
    TextPrimitive,
    TriangleMeshPrimitive,
    ViewportPlan,
)
from mat_viewer.render.plotly import build_figure, render


def _plan() -> RenderPlan:
    mesh = TriangleMeshPrimitive(
        "triangle",
        vertices=np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float),
        triangles=np.asarray([[0, 1, 2]], dtype=np.int64),
        rgba=(1.0, 0.0, 0.0, 0.6),
    )
    line = LinePrimitive(
        "line",
        segments=np.asarray([[[0, 0, 0], [0, 0, 1]]], dtype=float),
    )
    text = TextPrimitive("label", (0.0, 0.0, 1.0), "A")
    viewport = ViewportPlan(
        "main",
        camera=CameraSpec.looking_along((0, 0, 1), up=(0, 1, 0)),
        primitives=(mesh, line, text),
    )
    return RenderPlan(
        width=320,
        height=240,
        background=(1.0, 1.0, 1.0, 1.0),
        viewports=(viewport,),
    )


def test_plotly_adapter_consumes_render_plan_and_writes_html(tmp_path) -> None:
    pytest.importorskip("plotly")
    output = tmp_path / "figure.html"

    result = render(_plan(), output)

    assert result.backend == "plotly"
    assert result.format == "html"
    assert output.read_text(encoding="utf-8").lower().find("plotly") >= 0
    assert result.output_sha256


def test_plotly_adapter_preserves_static_failure_without_fallback(monkeypatch) -> None:
    pio = pytest.importorskip("plotly.io")
    monkeypatch.setattr(
        pio,
        "to_image",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no chrome")),
    )

    with pytest.raises(RuntimeError, match="no fallback was attempted.*no chrome"):
        render(_plan(), "figure.png")


def test_plotly_figure_has_one_trace_per_backend_neutral_primitive() -> None:
    pytest.importorskip("plotly")

    figure = build_figure(_plan())

    assert [trace.name for trace in figure.data] == ["triangle", "line", "label"]


def test_plotly_layout_uses_explicit_target_direction_and_ranges() -> None:
    pytest.importorskip("plotly")
    target = np.asarray([10.0, -4.0, 2.0])
    mesh = TriangleMeshPrimitive(
        "offset",
        vertices=np.asarray(
            [target + [-1, 0, 0], target + [1, 0, 0], target + [0, 1, 0]],
            dtype=float,
        ),
        triangles=np.asarray([[0, 1, 2]], dtype=np.int64),
    )
    camera = CameraSpec(
        position=tuple(target + [0.0, 0.0, 8.0]),
        target=tuple(target),
        up=(0.0, 1.0, 0.0),
        projection="orthographic",
        ortho_scale=3.0,
    )
    plan = RenderPlan(
        width=400,
        height=200,
        background=(1.0, 1.0, 1.0, 1.0),
        viewports=(ViewportPlan("main", camera=camera, primitives=(mesh,)),),
    )

    scene = build_figure(plan).layout.scene
    range_centres = np.asarray(
        [
            np.mean(scene.xaxis.range),
            np.mean(scene.yaxis.range),
            np.mean(scene.zaxis.range),
        ]
    )
    eye = np.asarray([scene.camera.eye.x, scene.camera.eye.y, scene.camera.eye.z])
    assert np.allclose(range_centres, target)
    assert scene.xaxis.autorange is False
    assert scene.yaxis.autorange is False
    assert scene.zaxis.autorange is False
    assert np.allclose(eye[:2], 0.0)
    assert eye[2] > 0.0


def test_plotly_figure_paints_requested_lattice_compass() -> None:
    pytest.importorskip("plotly")
    base = _plan()
    plan = RenderPlan(
        width=base.width,
        height=base.height,
        background=base.background,
        viewports=base.viewports,
        metadata={
            "lattice_compass": {
                "visible": True,
                "matrix": np.eye(3).tolist(),
                "labels": ["a", "b", "c"],
                "colors": ["#C7372F", "#22A660", "#2E86C1"],
            }
        },
    )

    figure = build_figure(plan)
    assert len(figure.layout.annotations) == 5
    assert len(figure.layout.shapes) == 1
