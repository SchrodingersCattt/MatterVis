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


def test_axis_key_reprojects_from_current_camera():
    scene = _empty_scene()
    scene["projected_axes"] = [[1.0, 0.0], [0.0, 1.0], [0.0, 0.2]]
    style = {
        **DEFAULT_STYLE,
        "show_axes": True,
        "show_axis_key": False,
        "camera": {
            "eye": {"x": 1.0, "y": 0.0, "z": 0.0},
            "up": {"x": 0.0, "y": 0.0, "z": 1.0},
        },
    }

    fig = build_figure(scene, style)
    line_shapes = [shape for shape in (fig.layout.shapes or []) if shape.type == "line"]
    dot_shapes = [shape for shape in (fig.layout.shapes or []) if shape.type == "circle"]

    assert len(line_shapes) == 2, "axis projected along the camera should not use stale scene projection"
    assert len(dot_shapes) == 1
    # Looking down +x makes the a axis project nearly to a point. If the
    # stale scene["projected_axes"] were used, the a row would have a
    # horizontal arrow instead.


def test_compass_projects_orthogonal_axes_to_orthogonal_screen_vectors():
    """Regression: ``_camera_axis_projections`` used to take ``view = eye``
    instead of ``view = center - eye`` and pre-normalised every lattice
    vector. The combined bug made SY's compass draw the ``a`` and ``b``
    arrows into the same screen quadrant with near-equal lengths, even
    though SY is orthorhombic with ``|b| = 3 |a|``. Pin the math by
    asserting that for a diagonal cell viewed straight down ``+z``, the
    projected basis is genuinely orthogonal AND magnitudes track the
    real ``|a|``, ``|b|``, ``|c|``.
    """
    from crystal_viewer.renderer import _camera_axis_projections

    scene = {"M": np.diag([8.09, 24.72, 10.20])}
    style = {
        "camera": {
            "eye": {"x": 0.0, "y": 0.0, "z": 1.8},
            "center": {"x": 0.0, "y": 0.0, "z": 0.0},
            "up": {"x": 0.0, "y": 1.0, "z": 0.0},
        }
    }

    proj = _camera_axis_projections(scene, style)
    assert proj is not None
    a_xy, b_xy, c_xy = proj

    # Looking -z with up=+y: a projects to screen-left (since right = up x
    # view = (0,1,0) x (0,0,-1) = (-1,0,0)), b projects to screen-up, c
    # collapses to the origin.
    assert math.isclose(a_xy[0], -8.09, rel_tol=0, abs_tol=1e-4)
    assert math.isclose(a_xy[1], 0.0, rel_tol=0, abs_tol=1e-4)
    assert math.isclose(b_xy[0], 0.0, rel_tol=0, abs_tol=1e-4)
    assert math.isclose(b_xy[1], 24.72, rel_tol=0, abs_tol=1e-4)
    assert math.isclose(c_xy[0], 0.0, rel_tol=0, abs_tol=1e-4)
    assert math.isclose(c_xy[1], 0.0, rel_tol=0, abs_tol=1e-4)

    # a perp b on screen.
    assert math.isclose(a_xy[0] * b_xy[0] + a_xy[1] * b_xy[1], 0.0, abs_tol=1e-4)

    # Magnitudes must track real cell anisotropy; the pre-fix normalisation
    # would have collapsed all three to unit length.
    a_len = math.hypot(*a_xy)
    b_len = math.hypot(*b_xy)
    assert math.isclose(b_len / a_len, 24.72 / 8.09, rel_tol=1e-4)


def test_compass_uses_view_minus_eye_not_eye():
    """A camera viewing the origin from ``+z`` and the same camera viewing
    from ``-z`` are mirror images: their screen ``right`` flips sign, so
    the projected ``a`` arrow flips horizontally. If the function were
    still computing ``view = eye`` (wrong sign), both cameras would
    produce the SAME projection.
    """
    from crystal_viewer.renderer import _camera_axis_projections

    scene = {"M": np.diag([8.09, 24.72, 10.20])}
    above = {
        "camera": {
            "eye": {"x": 0.0, "y": 0.0, "z": 1.8},
            "center": {"x": 0.0, "y": 0.0, "z": 0.0},
            "up": {"x": 0.0, "y": 1.0, "z": 0.0},
        }
    }
    below = {
        "camera": {
            "eye": {"x": 0.0, "y": 0.0, "z": -1.8},
            "center": {"x": 0.0, "y": 0.0, "z": 0.0},
            "up": {"x": 0.0, "y": 1.0, "z": 0.0},
        }
    }
    a_above = _camera_axis_projections(scene, above)[0]
    a_below = _camera_axis_projections(scene, below)[0]
    assert math.isclose(a_above[0], -a_below[0], rel_tol=0, abs_tol=1e-6)
