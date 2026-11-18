"""Fit frames cell corners or atom extents in both projections."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from mat_viewer.app import ViewerBackend
from mat_viewer.app.camera_helpers import confirmed_camera_fields, fit_target_points
from mat_viewer.render.screen_projection import (
    FIT_MARGIN_FRACTION,
    FIT_MAX_ASPECT_SCALE,
    project_gl3d_pixels,
)
from mat_viewer.render.viewport import scaled_scene_aspect
from mat_viewer.render.viewport_spec import resolve_viewport

ROOT = Path(__file__).resolve().parents[2]
NACL = ROOT / "tests" / "fixtures" / "structures" / "synthetic_nacl.cif"
WATER = ROOT / "benchmarks" / "structures" / "molecular_water.cif"
VIEWPORTS = ((939, 800), (1400, 500), (520, 900))
TRICLINIC = """data_triclinic
_cell_length_a 7.0
_cell_length_b 8.2
_cell_length_c 9.4
_cell_angle_alpha 80
_cell_angle_beta 75
_cell_angle_gamma 70
_space_group_name_H-M_alt 'P 1'
loop_
_space_group_symop_operation_xyz
'x, y, z'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
C1 C 0.20 0.30 0.40 1
O1 O 0.55 0.42 0.61 1
"""


def _backend(tmp_path: Path, name: str, cif: Path) -> ViewerBackend:
    local = tmp_path / ".local"
    local.mkdir(parents=True, exist_ok=True)
    (local / "catalog.local.json").write_text(
        json.dumps({"structures": {name: {"cif_path": str(cif.resolve()), "title": name}}}),
        encoding="utf-8",
    )
    return ViewerBackend(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
        names=[name],
    )


def _assert_framed(backend: ViewerBackend, scene_id: str, projection: str, width: int, height: int, *, boundary: bool):
    state = backend.get_state(scene_id)
    options = [item for item in (state.get("display_options") or []) if item != "unit_cell_box"]
    if boundary:
        options.append("unit_cell_box")
    backend.patch_state(
        {"display_mode": "unit_cell", "display_options": options, "projection": projection},
        scene_id=scene_id,
        broadcast=False,
    )
    backend.set_projection(projection, scene_id=scene_id, broadcast=False)
    camera = backend.camera_action(
        "fit",
        scene_id=scene_id,
        broadcast=False,
        width=width,
        height=height,
    )
    state = backend.get_state(scene_id)
    scene = backend.scene_for_state(state)
    style = backend.style_for_state(state, scene=scene)
    base_style = dict(style)
    base_style.pop("camera_aspect_scale", None)
    viewport = resolve_viewport(scene, base_style)
    aspect = scaled_scene_aspect(viewport.aspectratio, style)
    points = fit_target_points(
        scene,
        style,
        camera=camera,
        ranges=viewport.ranges,
        aspectratio=viewport.aspectratio,
    )
    assert len(points) >= 2
    xy = project_gl3d_pixels(
        points,
        camera=camera,
        ranges=viewport.ranges,
        aspectratio=aspect,
        width=width,
        height=height,
        projection=projection,
    )
    pad = FIT_MARGIN_FRACTION * min(width, height)
    assert np.all(xy[:, 0] >= pad - 1.5)
    assert np.all(xy[:, 0] <= width - pad + 1.5)
    assert np.all(xy[:, 1] >= pad - 1.5)
    assert np.all(xy[:, 1] <= height - pad + 1.5)
    gaps = (
        float(xy[:, 0].min()),
        float(width - xy[:, 0].max()),
        float(xy[:, 1].min()),
        float(height - xy[:, 1].max()),
    )
    # Opposite gaps match when the box is centered. A min-gap check
    # alone accepts a box that sits on the canvas center while the
    # clear window is shorter.
    assert abs(gaps[0] - gaps[1]) <= 4.0, gaps
    assert abs(gaps[2] - gaps[3]) <= 4.0, gaps
    limit = float(state.get("camera_aspect_scale") or 1.0)
    eye = np.array([camera["eye"]["x"], camera["eye"]["y"], camera["eye"]["z"]], dtype=float)
    at_limit = (
        limit >= FIT_MAX_ASPECT_SCALE - 0.1
        or limit <= 0.26
        or float(np.linalg.norm(eye)) >= 23.5
    )
    if not at_limit:
        assert min(gaps) <= pad + 0.04 * min(width, height), (
            projection,
            boundary,
            width,
            height,
            limit,
            float(np.linalg.norm(eye)),
            gaps,
            style.get("display_mode"),
            style.get("show_unit_cell"),
        )


def test_fit_frames_cubic_elongated_triclinic_and_molecule(tmp_path: Path):
    triclinic = tmp_path / "triclinic.cif"
    triclinic.write_text(TRICLINIC, encoding="utf-8")
    cases = {
        "nacl": NACL,
        "water": WATER,
        "triclinic": triclinic,
    }
    for name, cif in cases.items():
        backend = _backend(tmp_path / name, name, cif)
        try:
            scene_id = backend.active_scene_id()
            if name == "nacl":
                grown = backend.operate(
                    "build_supercell",
                    {"a": 4, "b": 1, "c": 1},
                    scene_id=scene_id,
                    expected_revision=1,
                )
                elongated = grown["scene_id"]
            else:
                elongated = None
            for projection in ("perspective", "orthographic"):
                for width, height in VIEWPORTS:
                    for boundary in (True, False):
                        _assert_framed(
                            backend,
                            scene_id,
                            projection,
                            width,
                            height,
                            boundary=boundary,
                        )
                        if elongated is not None and boundary:
                            _assert_framed(
                                backend,
                                elongated,
                                projection,
                                width,
                                height,
                                boundary=True,
                            )
        finally:
            backend.close(wait=False)


def _extreme_cif(path: Path, length: float) -> None:
    path.write_text(
        "\n".join(
            [
                "data_extreme",
                f"_cell_length_a {length}",
                f"_cell_length_b {length}",
                f"_cell_length_c {length}",
                "_cell_angle_alpha 90",
                "_cell_angle_beta 90",
                "_cell_angle_gamma 90",
                "_space_group_name_H-M_alt 'P 1'",
                "loop_",
                "_space_group_symop_operation_xyz",
                "'x, y, z'",
                "loop_",
                "_atom_site_label",
                "_atom_site_type_symbol",
                "_atom_site_fract_x",
                "_atom_site_fract_y",
                "_atom_site_fract_z",
                "_atom_site_occupancy",
                "C1 C 0.02 0.04 0.08 1",
                "O1 O 0.70 0.80 0.90 1",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_fit_frames_very_large_and_very_small_cells(tmp_path: Path):
    for length in (500.0, 0.25):
        cif = tmp_path / f"cell_{length}.cif"
        _extreme_cif(cif, length)
        backend = _backend(tmp_path / f"cell_{length}", "extreme", cif)
        try:
            scene_id = backend.active_scene_id()
            _assert_framed(backend, scene_id, "orthographic", 939, 750, boundary=True)
            _assert_framed(backend, scene_id, "perspective", 520, 900, boundary=False)
            camera = backend.get_camera(scene_id)
            assert "position" not in camera
            assert "focal_point" not in camera
            eye = np.array([camera["eye"][axis] for axis in ("x", "y", "z")], dtype=float)
            assert np.all(np.isfinite(eye))
            assert 1e-3 < float(np.linalg.norm(eye)) < 40.0
        finally:
            backend.close(wait=False)


def test_eye_wins_over_a_mixed_position_focal_camera() -> None:
    from mat_viewer.render.viewport import _scene_ranges

    matrix = np.eye(3) * 5.64
    atoms = [
        {"cart": np.array([0.0, 0.0, 0.0]), "elem": "Na", "atom_radius": 0.28},
        {"cart": np.array([5.64, 5.64, 5.64]), "elem": "Cl", "atom_radius": 0.24},
    ]
    scene = {"M": matrix, "display_mode": "unit_cell", "draw_atoms": atoms}
    focal = {
        "position": [1.8, 1.8, 1.8],
        "focal_point": [0.0, 0.0, 0.0],
        "up": [0.0, 0.0, 1.0],
        "projection": {"type": "perspective"},
    }
    eye = {
        "eye": {"x": 1.0392304845413265, "y": 1.0392304845413265, "z": 1.0392304845413265},
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "up": {"x": 0.0, "y": 0.0, "z": 1.0},
        "projection": {"type": "perspective"},
    }
    wide = _scene_ranges(scene, {"display_mode": "unit_cell", "show_unit_cell": True, "camera": focal, "style": "ball", "atom_scale": 1.0})
    mixed = _scene_ranges(
        scene,
        {
            "display_mode": "unit_cell",
            "show_unit_cell": True,
            "camera": {**focal, **eye},
            "style": "ball",
            "atom_scale": 1.0,
        },
    )
    wide_span = float(wide[0][1] - wide[0][0])
    mixed_span = float(mixed[0][1] - mixed[0][0])
    assert wide_span > 10.0
    assert mixed_span < 8.0


def test_cached_figure_refreshes_axis_ranges_for_an_eye_camera(tmp_path: Path):
    backend = _backend(tmp_path, "nacl", NACL)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state(
            {
                "display_mode": "unit_cell",
                "display_options": ["unit_cell_box"],
                "projection": "perspective",
                "camera": {
                    "position": [1.8, 1.8, 1.8],
                    "focal_point": [0.0, 0.0, 0.0],
                    "up": [0.0, 0.0, 1.0],
                    "projection": {"type": "perspective"},
                },
            },
            scene_id=scene_id,
            broadcast=False,
        )
        first, _topology = backend.figure_for_state(backend.get_state(scene_id))
        wide = float(first.layout.scene.xaxis.range[1] - first.layout.scene.xaxis.range[0])
        before = backend.get_state(scene_id)
        backend.patch_state(
            {
                "camera": {
                    "eye": {"x": 1.0392304845413265, "y": 1.0392304845413265, "z": 1.0392304845413265},
                    "center": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "up": {"x": 0.0, "y": 0.0, "z": 1.0},
                    "projection": {"type": "perspective"},
                }
            },
            scene_id=scene_id,
            broadcast=False,
        )
        after = backend.get_state(scene_id)
        assert backend._figure_state_cache_key(before) == backend._figure_state_cache_key(after)
        second, _topology = backend.figure_for_state(after)
        tight = float(second.layout.scene.xaxis.range[1] - second.layout.scene.xaxis.range[0])
        assert wide > 10.0
        assert tight < 8.0
    finally:
        backend.close(wait=False)


def test_fit_centers_the_clear_viewport(tmp_path: Path):
    backend = _backend(tmp_path, "nacl", NACL)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state(
            {"display_mode": "unit_cell", "display_options": ["unit_cell_box"], "projection": "orthographic"},
            scene_id=scene_id,
            broadcast=False,
        )
        camera = backend.camera_action(
            "fit",
            scene_id=scene_id,
            broadcast=False,
            width=939,
            height=750,
            visible_height=682,
        )
        state = backend.get_state(scene_id)
        scene = backend.scene_for_state(state)
        style = backend.style_for_state(state, scene=scene)
        base = dict(style)
        base.pop("camera_aspect_scale", None)
        viewport = resolve_viewport(scene, base)
        aspect = scaled_scene_aspect(viewport.aspectratio, style)
        points = fit_target_points(
            scene, style, camera=camera, ranges=viewport.ranges, aspectratio=viewport.aspectratio
        )
        xy = project_gl3d_pixels(
            points,
            camera=camera,
            ranges=viewport.ranges,
            aspectratio=aspect,
            width=939,
            height=750,
            projection="orthographic",
        )
        shown = 682.0
        gaps = (
            float(xy[:, 0].min()),
            939.0 - float(xy[:, 0].max()),
            float(xy[:, 1].min()),
            shown - float(xy[:, 1].max()),
        )
        pad = FIT_MARGIN_FRACTION * shown
        assert min(gaps) >= pad - 2.0, gaps
        assert abs(gaps[0] - gaps[1]) <= 4.0, gaps
        assert abs(gaps[2] - gaps[3]) <= 4.0, gaps
    finally:
        backend.close(wait=False)


def test_reset_clears_orthographic_aspect_scale(tmp_path: Path):
    backend = _backend(tmp_path, "nacl", NACL)
    try:
        scene_id = backend.active_scene_id()
        backend.set_projection("orthographic", scene_id=scene_id, broadcast=False)
        backend.camera_action("fit", scene_id=scene_id, broadcast=False, width=939, height=800)
        scaled = float(backend.get_state(scene_id).get("camera_aspect_scale") or 1.0)
        assert abs(scaled - 1.0) > 0.02
        backend.camera_action("reset", scene_id=scene_id, broadcast=False)
        assert float(backend.get_state(scene_id).get("camera_aspect_scale") or 1.0) == 1.0
    finally:
        backend.close(wait=False)


def test_confirmed_camera_delivery_keeps_the_fitted_aspect(tmp_path: Path):
    """A state-confirmed camera echo carries the ratio Fit stored."""
    backend = _backend(tmp_path, "nacl", NACL)
    try:
        scene_id = backend.active_scene_id()
        backend.set_projection("orthographic", scene_id=scene_id, broadcast=False)
        backend.camera_action(
            "fit",
            scene_id=scene_id,
            broadcast=False,
            width=939,
            height=750,
            visible_height=682,
        )
        state = backend.get_state(scene_id)
        fields = confirmed_camera_fields(backend, state)
        scale = float(state["camera_aspect_scale"])
        assert abs(scale - 1.0) > 0.02
        for axis in ("x", "y", "z"):
            assert abs(fields["aspectratio"][axis] - scale) / scale < 0.01
        span = abs(fields["ranges"]["x"][1] - fields["ranges"]["x"][0])
        assert abs(span - 6.944) / 6.944 < 0.02
    finally:
        backend.close(wait=False)
