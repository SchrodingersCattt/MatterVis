from __future__ import annotations

import math

import numpy as np

from crystal_viewer.loader import build_loaded_crystal
from crystal_viewer.presets import DEFAULT_STYLE
from crystal_viewer.renderer import build_figure


def _empty_scene():
    return {
        "name": "test",
        "title": "Test",
        "M": np.eye(3),
        "view_direction": np.array([0.0, 0.0, 1.0]),
        "up": np.array([0.0, 1.0, 0.0]),
        "draw_atoms": [],
        "bonds": [],
        "label_items": [],
    }


def test_build_figure_honors_orthographic_projection_and_eye_distance():
    scene = _empty_scene()
    style = {
        **DEFAULT_STYLE,
        "projection": "orthographic",
        "camera_eye_distance": 3.0,
        "show_title": False,
        "show_axes": False,
    }

    fig = build_figure(scene, style)
    camera = fig.layout.scene.camera

    assert camera.projection.type == "orthographic"
    eye_norm = math.sqrt(camera.eye.x**2 + camera.eye.y**2 + camera.eye.z**2)
    assert math.isclose(eye_norm, 3.0, rel_tol=1e-9)


def _annotation_labels(fig):
    labels = set()
    for ann in fig.layout.annotations or []:
        text = ann.text or ""
        for token in ("<b>", "</b>", "<i>", "</i>"):
            text = text.replace(token, "")
        labels.add(text)
    return labels


def test_show_axes_uses_paper_compass_for_sy():
    """``show_axes`` must drive the paper-coord compass overlay, not a
    3D cylinder triad in world space. The 3D shaft path used to either
    foreshorten to a tiny stub (cameras aligned with a lattice vector)
    or cut a long line straight through the structure (oblique cameras
    on long cells like EMAP); the paper overlay sits in a stable figure
    corner and is immune to both.
    """
    bundle = build_loaded_crystal(
        name="SY", cif_path="scripts/data/SY.cif", title="SY"
    )
    style_on = {
        **DEFAULT_STYLE,
        **bundle.scene.get("style", {}),
        "show_axes": True,
        "show_axis_key": False,
    }

    fig_on = build_figure(bundle.scene, style_on)
    trace_dicts = fig_on.to_dict().get("data", [])
    assert all(
        (trace.get("meta") or {}).get("mv_role") != "axes"
        for trace in trace_dicts
    ), "show_axes must no longer emit any 3D axis traces"
    assert {"a", "b", "c"}.issubset(_annotation_labels(fig_on))

    style_off = {**style_on, "show_axes": False}
    fig_off = build_figure(bundle.scene, style_off)
    assert not {"a", "b", "c"}.intersection(_annotation_labels(fig_off))
