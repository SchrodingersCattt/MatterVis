from __future__ import annotations

from pathlib import Path

from mat_viewer.render.contracts import CameraSpec, RenderPlan, ViewportPlan
from mat_viewer.render.geometry import bond_primitives, sphere_primitive
from mat_viewer.render.matplotlib import render


def _plan() -> RenderPlan:
    camera = CameraSpec(
        position=(0.0, 0.0, 5.0),
        target=(0.0, 0.0, 0.0),
        up=(0.0, 1.0, 0.0),
        projection="orthographic",
        near=0.1,
        far=20.0,
        ortho_scale=1.5,
    )
    primitives = (
        *bond_primitives(
            "bond:0:0-1",
            (-0.7, 0.0, 0.0),
            (0.7, 0.0, 0.0),
            0.12,
            "#4A4A4A",
            "#FF0D0D",
            metadata={"kind": "bond", "atom_indices": [0, 1]},
        ),
        sphere_primitive(
            "atom:0:C1",
            (-0.7, 0.0, 0.0),
            0.3,
            "#4A4A4A",
            metadata={"kind": "atom", "element": "C"},
        ),
        sphere_primitive(
            "atom:1:O1",
            (0.7, 0.0, 0.0),
            0.3,
            "#FF0D0D",
            metadata={"kind": "atom", "element": "O"},
        ),
    )
    return RenderPlan(
        width=160,
        height=120,
        background=(1.0, 1.0, 1.0, 1.0),
        viewports=(ViewportPlan("main", camera=camera, primitives=primitives),),
        metadata={"requested_backend": "matplotlib", "scale": 1},
    )


def test_matplotlib_backend_writes_real_2d_png(tmp_path: Path) -> None:
    output = tmp_path / "structure-2d.png"

    result = render(_plan(), output=output)

    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert result.backend == "matplotlib"
    assert result.metadata["projection"] == "2d"
    assert result.metadata["fallback"] is None
    assert result.width == 160
    assert result.height == 120


def test_matplotlib_backend_keeps_svg_vector(tmp_path: Path) -> None:
    output = tmp_path / "structure-2d.svg"

    render(_plan(), output=output)

    document = output.read_text(encoding="utf-8")
    assert "<svg" in document
    assert "<image" not in document
    assert "<path" in document
