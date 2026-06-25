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


_COMPASS_NAME = "mv_compass"


def _compass_arrows(fig):
    return [
        ann for ann in (fig.layout.annotations or [])
        if getattr(ann, "showarrow", False) and getattr(ann, "name", None) == _COMPASS_NAME
    ]


def _compass_dot_shapes(fig):
    return [
        shape for shape in (fig.layout.shapes or [])
        if getattr(shape, "type", None) == "circle"
        and getattr(shape, "name", None) == _COMPASS_NAME
    ]


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
    arrows = _compass_arrows(fig)
    dots = _compass_dot_shapes(fig)

    assert len(arrows) == 2, "axis projected along the camera should not use stale scene projection"
    assert len(dots) == 1
    # Looking down +x makes the a axis project nearly to a point. If the
    # stale scene["projected_axes"] were used, the a row would have a
    # horizontal arrow instead.


def test_compass_arrows_share_single_anchor():
    """All three compass arrows must originate from a single paper-coord
    anchor (the "single shared origin" invariant that distinguishes a
    compass from a stacked legend). Regression: an earlier row-stacked
    layout placed each label on its own y0, so the three arrow tails
    drifted ~0.1 paper units apart vertically and the triad lost its
    geometric meaning.
    """
    bundle = build_loaded_crystal(
        name="SY", cif_path="scripts/data/SY.cif", title="SY"
    )
    anchor = (0.07, 0.13)
    style = {
        **DEFAULT_STYLE,
        **bundle.scene.get("style", {}),
        "show_axis_key": True,
        "show_axes": False,
        "axis_key_anchor": list(anchor),
        # Oblique camera so every axis projects with non-zero magnitude
        # (no dot-fallback path).
        "camera": {
            "eye": {"x": 1.5, "y": -2.0, "z": 1.2},
            "center": {"x": 0.0, "y": 0.0, "z": 0.0},
            "up": {"x": 0.0, "y": 0.0, "z": 1.0},
        },
    }

    fig = build_figure(bundle.scene, style)
    arrows = _compass_arrows(fig)
    assert len(arrows) == 3, "expected one arrow per lattice axis"

    fig_w = float(style.get("axis_key_fig_width", 1024.0))
    fig_h = float(style.get("axis_key_fig_height", 720.0))
    # Plotly annotation arrows place the HEAD at (x, y) in paper coords
    # and offset the TAIL by (ax, ay) in PIXELS. Pixel y points DOWN so
    # back-converting to paper requires subtracting ay/fig_h:
    #   tail_paper_x = head_paper_x + ax/fig_w
    #   tail_paper_y = head_paper_y - ay/fig_h
    for ann in arrows:
        tail_x = float(ann.x) + float(ann.ax) / fig_w
        tail_y = float(ann.y) - float(ann.ay) / fig_h
        assert math.isclose(tail_x, anchor[0], abs_tol=1e-6), (
            f"arrow tail x {tail_x} does not match anchor {anchor[0]}"
        )
        assert math.isclose(tail_y, anchor[1], abs_tol=1e-6), (
            f"arrow tail y {tail_y} does not match anchor {anchor[1]}"
        )


def test_compass_arrow_lengths_use_equal_basis_vectors():
    """Compass arrows show basis-vector directions, not lattice lengths.

    For an orthorhombic cell viewed straight down +z, a and b should render
    with equal visible length even when |b| is roughly 3x |a|. The c basis
    vector collapses to a dot because it points into the camera.
    """
    scene = {
        "name": "test",
        "title": "Test",
        "M": np.diag([8.09, 24.72, 10.20]),
        "view_direction": np.array([0.0, 0.0, 1.0]),
        "up": np.array([0.0, 1.0, 0.0]),
        "draw_atoms": [],
        "bonds": [],
        "label_items": [],
    }
    style = {
        **DEFAULT_STYLE,
        "show_axis_key": True,
        "show_axes": False,
        "camera": {
            "eye": {"x": 0.0, "y": 0.0, "z": 1.8},
            "center": {"x": 0.0, "y": 0.0, "z": 0.0},
            "up": {"x": 0.0, "y": 1.0, "z": 0.0},
        },
    }
    fig = build_figure(scene, style)
    arrows = _compass_arrows(fig)
    # a and b should render as arrows; c collapses to a dot (looking
    # down z).
    assert len(arrows) == 2
    pix_lens = sorted(math.hypot(float(ann.ax), float(ann.ay)) for ann in arrows)
    assert math.isclose(pix_lens[1] / pix_lens[0], 1.0, rel_tol=1e-3)


def test_compass_arrow_lengths_follow_unit_basis_projection():
    """Equal 3D basis vectors still foreshorten under camera projection."""
    from crystal_viewer.renderer import _camera_axis_projections

    scene = {
        "name": "test",
        "title": "Test",
        "M": np.diag([8.09, 24.72, 10.20]),
        "view_direction": np.array([1.0, 0.0, 1.0]),
        "up": np.array([0.0, 0.0, 1.0]),
        "draw_atoms": [],
        "bonds": [],
        "label_items": [],
    }
    style = {
        **DEFAULT_STYLE,
        "show_axis_key": True,
        "show_axes": False,
        "camera": {
            "eye": {"x": 1.0, "y": 0.0, "z": 1.0},
            "center": {"x": 0.0, "y": 0.0, "z": 0.0},
            "up": {"x": 0.0, "y": 0.0, "z": 1.0},
        },
    }
    fig = build_figure(scene, style)
    arrows = _compass_arrows(fig)
    assert len(arrows) == 3

    expected_proj = _camera_axis_projections(scene, style)
    expected_lens = [math.hypot(*xy) for xy in expected_proj if math.hypot(*xy) > 1e-9]
    expected_ratio = max(expected_lens) / min(expected_lens)

    pix_lens = [math.hypot(float(ann.ax), float(ann.ay)) for ann in arrows]
    actual_ratio = max(pix_lens) / min(pix_lens)
    assert expected_ratio > 1.2
    assert math.isclose(actual_ratio, expected_ratio, rel_tol=1e-3)


def test_compass_projection_rescales_to_cube_for_aspectmode_data():
    """``aspectmode="data"`` (the default for anisotropic cells like SY)
    means Plotly's camera operates in normalised cube coords, not data
    coords. The compass first maps lattice rows into the same cube space
    before normalising them to unit basis directions, so it tracks visible
    direction without encoding cell-axis lengths.
    """
    from crystal_viewer.renderer import _camera_axis_projections

    scene = {
        "M": np.diag([8.09, 24.72, 10.20]),
        "bounds": {
            "mins": [-0.2, -0.2, -0.2],
            "maxs": [8.29, 24.92, 10.40],
        },
    }
    style = {
        "camera": {
            "eye": {"x": 0.0, "y": 0.0, "z": 1.8},
            "center": {"x": 0.0, "y": 0.0, "z": 0.0},
            "up": {"x": 0.0, "y": 1.0, "z": 0.0},
        }
    }
    proj = _camera_axis_projections(scene, style)
    assert proj is not None
    a_xy, b_xy, _c_xy = proj
    a_len = math.hypot(*a_xy)
    b_len = math.hypot(*b_xy)
    # After cube rescaling and basis normalisation, both visible basis
    # directions have unit projected length when viewed along +z.
    ratio = max(b_len, a_len) / max(min(b_len, a_len), 1e-12)
    assert ratio < 1.10, (
        f"expected b_len ~= a_len under aspectmode=data; got "
        f"a={a_len} b={b_len} ratio={ratio}"
    )


def test_compass_projection_skips_cube_rescaling_for_cube_aspectmode():
    """When :func:`uniform_viewport` stamps a shared cube on a scene
    (or every axis happens to span the same range) Plotly renders
    with ``aspectmode="cube"``. In that case data == cube already, and
    the compass still normalises the lattice rows to unit basis directions
    so it does not encode cell lengths.
    """
    from crystal_viewer.renderer import _camera_axis_projections

    scene = {
        "M": np.diag([8.09, 24.72, 10.20]),
        # All three half-ranges identical -> aspectmode="cube"
        "bounds": {
            "mins": [-12.0, -12.0, -12.0],
            "maxs": [12.0, 12.0, 12.0],
        },
    }
    style = {
        "camera": {
            "eye": {"x": 0.0, "y": 0.0, "z": 1.8},
            "center": {"x": 0.0, "y": 0.0, "z": 0.0},
            "up": {"x": 0.0, "y": 1.0, "z": 0.0},
        }
    }
    proj = _camera_axis_projections(scene, style)
    a_len = math.hypot(*proj[0])
    b_len = math.hypot(*proj[1])
    assert math.isclose(b_len / a_len, 1.0, rel_tol=1e-3)


def test_compass_overlay_python_bakes_plotly_layout_for_all_paths():
    """The SVG compass overlay was removed; the compass now rides in
    Plotly layout annotations/shapes for Dash and static export alike.
    """
    from crystal_viewer.render.compass import axis_key_overlay

    scene = {
        "M": np.eye(3) * 5,
        "axis_labels": ["a", "b", "c"],
        "projected_axes": [(1.0, 0.0), (0.0, 1.0), (0.5, 0.5)],
        "camera": {
            "eye": {"x": 1.0, "y": 1.0, "z": 1.0},
            "center": {"x": 0.0, "y": 0.0, "z": 0.0},
            "up": {"x": 0.0, "y": 0.0, "z": 1.0},
        },
    }
    style_static = {"show_axis_key": True}
    static_ann, static_sh = axis_key_overlay(scene, style_static)
    assert static_ann or static_sh, (
        "axis_key_overlay must produce annotations/shapes "
        "for all paths (compass is always baked into Plotly layout)."
    )


def test_compass_projects_orthogonal_axes_to_orthogonal_screen_vectors():
    """The compass projects unit basis directions with the right view sign.

    For a diagonal cell viewed straight down +z, a and b must remain
    orthogonal on screen, but their lengths are equal because the compass
    is a basis-direction triad rather than a cell-length scale bar.
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

    # Looking -z with up=+y: a projects to screen-right (since right = view x
    # up = (0,0,-1) x (0,1,0) = (1,0,0)), b projects to screen-up, c
    # collapses to the origin.
    assert math.isclose(a_xy[0], 1.0, rel_tol=0, abs_tol=1e-4)
    assert math.isclose(a_xy[1], 0.0, rel_tol=0, abs_tol=1e-4)
    assert math.isclose(b_xy[0], 0.0, rel_tol=0, abs_tol=1e-4)
    assert math.isclose(b_xy[1], 1.0, rel_tol=0, abs_tol=1e-4)
    assert math.isclose(c_xy[0], 0.0, rel_tol=0, abs_tol=1e-4)
    assert math.isclose(c_xy[1], 0.0, rel_tol=0, abs_tol=1e-4)

    # a perp b on screen.
    assert math.isclose(a_xy[0] * b_xy[0] + a_xy[1] * b_xy[1], 0.0, abs_tol=1e-4)

    a_len = math.hypot(*a_xy)
    b_len = math.hypot(*b_xy)
    assert math.isclose(b_len / a_len, 1.0, rel_tol=1e-4)


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
