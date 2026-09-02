from __future__ import annotations

import numpy as np
import pytest

from mat_viewer.render import LinePrimitive, prepare_render
from mat_viewer.render.geometry import unit_cell_primitive
from mat_viewer.render.overlay.cells import normalize_cell_overlays


def _overlay(identifier: str = "auxiliary", *, origin=(0.0, 0.0, 0.0)):
    return {
        "id": identifier,
        "matrix": np.diag([4.0, 5.0, 6.0]).tolist(),
        "origin": list(origin),
        "color": "#CC79A7",
        "width_px": 2.2,
        "dash": [10.0, 6.0],
        "alpha": 0.75,
        "depth_test": False,
    }


def _empty_scene(*, cell_overlays=None):
    scene = {
        "name": "empty",
        "matrix": np.eye(3).tolist(),
        "atoms": [],
        "bonds": [],
    }
    if cell_overlays is not None:
        scene["cell_overlays"] = cell_overlays
    return scene


def test_cell_overlay_schema_and_line_primitive() -> None:
    normalized = normalize_cell_overlays([_overlay()])

    assert normalized[0]["dash"] == [10.0, 6.0]
    primitive = unit_cell_primitive(
        "auxiliary",
        normalized[0]["matrix"],
        origin=normalized[0]["origin"],
        color=normalized[0]["color"],
        width_px=normalized[0]["width_px"],
        dash=normalized[0]["dash"],
        alpha=normalized[0]["alpha"],
        depth_test=normalized[0]["depth_test"],
        metadata={"kind": "cell_overlay"},
    )
    assert primitive.dash == (10.0, 6.0)
    assert primitive.metadata["kind"] == "cell_overlay"


@pytest.mark.parametrize(
    ("payload", "message", "error_type"),
    [
        ({}, "must be a list", TypeError),
        ([{"matrix": np.eye(2).tolist()}], "finite 3x3", ValueError),
        ([{"matrix": np.zeros((3, 3)).tolist()}], "non-singular", ValueError),
        ([{**_overlay(), "dash": [1.0, 0.0]}], "dash entries", ValueError),
        ([{**_overlay(), "mystery": True}], "unsupported key", ValueError),
        ([_overlay("same"), _overlay("same")], "duplicate or empty", ValueError),
    ],
)
def test_cell_overlay_schema_rejects_invalid_payload(payload, message, error_type) -> None:
    with pytest.raises(error_type, match=message):
        normalize_cell_overlays(payload)


def test_explicit_cell_overlays_override_scene_value() -> None:
    plan = prepare_render(
        _empty_scene(cell_overlays=[_overlay("scene")]),
        render={"show_cell": False, "show_hydrogen": False},
        cell_overlays=[_overlay("explicit")],
    )

    overlays = [
        primitive
        for primitive in plan.primitives
        if isinstance(primitive, LinePrimitive)
        and primitive.metadata.get("kind") == "cell_overlay"
    ]
    assert [primitive.semantic_id for primitive in overlays] == [
        "cell-overlay:explicit"
    ]
    assert plan.metadata["cell_overlays"][0]["id"] == "explicit"


def test_auxiliary_cell_corners_participate_in_auto_camera_fit() -> None:
    overlay = _overlay(origin=(100.0, 0.0, 0.0))
    plan = prepare_render(
        _empty_scene(),
        render={"show_cell": False, "show_hydrogen": False},
        cell_overlays=[overlay],
    )

    assert np.allclose(plan.camera.target, [102.0, 2.5, 3.0])
    assert plan.camera.ortho_scale > 4.0


@pytest.mark.parametrize("backend", ["cpu", "matplotlib", "plotly"])
def test_cell_overlay_is_backend_neutral(backend: str) -> None:
    plan = prepare_render(
        _empty_scene(),
        render={
            "backend": backend,
            "show_cell": False,
            "show_hydrogen": False,
        },
        cell_overlays=[_overlay()],
    )

    overlay = next(
        primitive
        for primitive in plan.primitives
        if primitive.semantic_id == "cell-overlay:auxiliary"
    )
    assert isinstance(overlay, LinePrimitive)
    assert overlay.dash == (10.0, 6.0)
    assert overlay.width_px == 2.2
    assert overlay.depth_test is False
