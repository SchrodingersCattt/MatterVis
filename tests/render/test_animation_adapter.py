from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from mat_viewer.render.animation_adapter import render_animation
from mat_viewer.render.contracts import (
    CameraSpec,
    RenderPlan,
    RenderSpec,
    ViewSpec,
    ViewportPlan,
)


def _scene(x: float) -> dict:
    return {
        "atoms": [
            {
                "cart": np.asarray([x, 0.0, 0.0]),
                "elem": "C",
                "label": "C1",
                "occ": 1.0,
            }
        ],
        "bonds": [],
        "matrix": np.eye(3),
    }


def test_gif_encodes_every_selected_cpu_frame(tmp_path) -> None:
    imageio = pytest.importorskip("imageio.v2")
    source = SimpleNamespace(frames=(_scene(-0.6), _scene(0.6)))
    output = tmp_path / "motion.gif"

    result = render_animation(
        source,
        output,
        view=ViewSpec(display="unit_cell"),
        camera=CameraSpec.looking_along(
            (0, 0, 1),
            up=(0, 1, 0),
            distance=5.0,
            ortho_scale=1.5,
            near=0.1,
            far=10.0,
        ),
        render_spec=RenderSpec(
            representation="ball",
            width=64,
            height=64,
            scale=1,
        ),
        fps=5.0,
    )

    decoded = imageio.mimread(output)
    assert len(decoded) == 2
    assert {frame.shape[:2] for frame in decoded} == {(64, 64)}
    assert result.backend == "cpu"
    assert result.format == "gif"
    assert result.metadata["frame_count"] == 2
    assert result.metadata["fps"] == 5.0
    assert result.metadata["frame_duration_ms"] == 200.0
    assert result.metadata["duration_seconds"] == 0.4
    assert len(result.metadata["frame_plan_sha256"]) == 2


def test_animation_preserves_unique_plan_warnings(tmp_path, monkeypatch) -> None:
    imageio = pytest.importorskip("imageio.v2")
    camera = CameraSpec.looking_along(
        (0, 0, 1), up=(0, 1, 0), distance=5.0, near=0.1, far=10.0
    )
    plan = RenderPlan(
        width=8,
        height=8,
        background=(1.0, 1.0, 1.0, 1.0),
        viewports=(ViewportPlan("main", camera=camera, primitives=()),),
        warnings=("frame contract warning",),
    )

    import mat_viewer.render.cpu as cpu_module
    import mat_viewer.render.planning as planning_module

    writer_options = {}

    class FakeWriter:
        def __init__(self, path) -> None:
            self.path = path

        def __enter__(self):
            return self

        def append_data(self, data) -> None:
            assert data.shape == (8, 8, 3)

        def __exit__(self, *args) -> None:
            self.path.write_bytes(b"GIF89a-test")

    def fake_get_writer(path, *, mode, **kwargs):
        writer_options.update(kwargs)
        assert mode == "I"
        return FakeWriter(path)

    monkeypatch.setattr(planning_module, "prepare_render", lambda *args, **kwargs: plan)
    from io import BytesIO

    from PIL import Image

    frame_buffer = BytesIO()
    Image.fromarray(np.full((8, 8, 4), 255, dtype=np.uint8), mode="RGBA").save(
        frame_buffer,
        format="PNG",
    )
    monkeypatch.setattr(
        cpu_module,
        "render_png",
        lambda *args, **kwargs: SimpleNamespace(
            data=frame_buffer.getvalue(),
            warnings=plan.warnings,
        ),
    )
    monkeypatch.setattr(imageio, "get_writer", fake_get_writer)

    result = render_animation(
        SimpleNamespace(frames=(object(), object())),
        tmp_path / "warnings.gif",
        camera=camera,
    )

    assert result.warnings == ("frame contract warning",)
    assert writer_options["duration"] == pytest.approx(1000.0 / 12.0)
    assert result.metadata["frame_duration_ms"] == pytest.approx(1000.0 / 12.0)
