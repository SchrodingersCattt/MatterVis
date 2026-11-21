"""CPU lighting rig: studio_soft stays put, unlit is unlit, three-point errors."""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

import numpy as np
import pytest

from mat_viewer.cli import _build_render_parser
from mat_viewer.render.contracts import CameraSpec, RenderPlan, RenderSpec, ViewportPlan
from mat_viewer.render.cpu import render
from mat_viewer.render.cpu.raster import render_rgba
from mat_viewer.render.geometry import cylinder_primitive, sphere_primitive
from mat_viewer.render.lighting import (
    PUBLICATION_DEFAULT_LIGHTING,
    STUDIO_SOFT_DIRECTION,
    SURFACE_RESPONSE_FIELDS,
    VISUAL_PRESET_LIGHTING,
    CpuLightingError,
    get_lighting_rig,
    note_plan_lighting,
    read_surface_response,
    shade_with_rig,
)
from mat_viewer.render.morphology import _morphology_traces
from mat_viewer.render.planning import prepare_render

# PNG bytes of the scene below on the base branch, before this rig existed.
# A retune of direction, intensity, or the matte ambient/diffuse pair fails.
_BALL_STICK_PNG_SHA256 = (
    "a595f14b98a1649dff10c5de4e0d1b4478096bcf3a5c3b9f8a6dea54bf3b11ef"
)
_ISOSURFACE_PNG_SHA256 = (
    "6f4d358d349a97a09fa1580cdb4e29d5f4290eb79952a7b052c320e7f5deb363"
)

_ROOT = Path(__file__).resolve().parents[2]


def _camera() -> CameraSpec:
    return CameraSpec(
        position=(0.0, -4.0, 2.2),
        target=(0.55, 0.0, 0.0),
        up=(0.0, 0.0, 1.0),
        projection="orthographic",
        ortho_scale=1.6,
    )


def _ball_stick_scene() -> dict:
    return {
        "draw_atoms": [
            {"elem": "C", "cart": [0.0, 0.0, 0.0], "label": "C1", "color": "#404040"},
            {"elem": "O", "cart": [1.15, 0.0, 0.0], "label": "O1", "color": "#D55E00"},
        ],
        "bonds": [{"i": 0, "j": 1}],
    }


def _render_spec(**overrides):
    payload = {
        "representation": "ball_stick",
        "width": 64,
        "height": 64,
        "scale": 1,
        "show_cell": False,
        "show_labels": False,
        "show_axes": False,
        "sphere_detail": (8, 12),
        "cylinder_sides": 8,
    }
    payload.update(overrides)
    return payload


def _png(scene, render_spec) -> bytes:
    plan = prepare_render(scene, camera=_camera(), render=render_spec)
    return render(plan, format="png").data


def _hard_plastic() -> dict:
    return {
        "ambient": 0.30,
        "diffuse": 0.60,
        "specular": 0.35,
        "shininess": 48.0,
        "specular_tint": "white",
        "specular_color": (1.0, 1.0, 1.0),
    }


def test_lighting_names_and_historical_direction() -> None:
    assert tuple(get_lighting_rig("studio_soft").direction) == (-0.32, 0.42, 1.0)
    assert STUDIO_SOFT_DIRECTION == (-0.32, 0.42, 1.0)
    assert PUBLICATION_DEFAULT_LIGHTING == "studio_soft"
    soft = get_lighting_rig("studio_soft")
    assert soft.publication_default is True
    assert soft.color_critical is False
    assert soft.light_scale == 1.0
    assert soft.cpu_evaluates is True
    unlit = get_lighting_rig("unlit")
    assert unlit.kind == "unlit"
    assert unlit.direction is None
    assert unlit.publication_default is False
    assert unlit.color_critical is True
    assert unlit.cpu_evaluates is True
    three = get_lighting_rig("studio_three_point")
    assert three.kind == "multi_light"
    assert three.direction is None
    assert three.cpu_evaluates is False
    assert get_lighting_rig("studio-soft").id == "studio_soft"
    with pytest.raises(ValueError, match="unknown lighting rig"):
        get_lighting_rig("world_sun")
    with pytest.raises(ValueError, match="unknown lighting rig"):
        get_lighting_rig("flat")
    with pytest.raises(ValueError, match="unknown lighting rig"):
        get_lighting_rig("metal")
    # Lookup does not retune the batch light, which starts from the same vector.
    batch_source = (_ROOT / "mat_viewer/render/cpu/batch.py").read_text(encoding="utf-8")
    assert "_LIGHT = np.asarray([-0.32, 0.42, 1.0], dtype=np.float32)" in batch_source
    assert VISUAL_PRESET_LIGHTING["scientific_default"] == "studio_soft"
    assert VISUAL_PRESET_LIGHTING["presentation_plastic"] == "studio_three_point"
    assert VISUAL_PRESET_LIGHTING["metallic_showcase"] == "studio_three_point"


def test_light_scale_is_not_a_surface_field() -> None:
    with pytest.raises(ValueError, match="light_scale stays on the lighting rig"):
        read_surface_response(
            {
                "surface_material": {
                    "ambient": 0.68,
                    "diffuse": 0.32,
                    "specular": 0.0,
                    "shininess": 32.0,
                    "light_scale": 1.5,
                }
            }
        )


def test_omitted_lighting_matches_explicit_studio_soft_bytes() -> None:
    omitted = _png(_ball_stick_scene(), _render_spec())
    explicit = _png(_ball_stick_scene(), _render_spec(lighting="studio_soft"))
    assert hashlib.sha256(omitted).hexdigest() == _BALL_STICK_PNG_SHA256
    assert explicit == omitted
    omitted_plan = prepare_render(
        _ball_stick_scene(), camera=_camera(), render=_render_spec()
    )
    explicit_plan = prepare_render(
        _ball_stick_scene(),
        camera=_camera(),
        render=_render_spec(lighting="studio_soft"),
    )
    assert "lighting" not in omitted_plan.metadata
    assert omitted_plan.fingerprint() == explicit_plan.fingerprint()
    assert RenderSpec().lighting == "studio_soft"
    iso = {
        "draw_atoms": [],
        "bonds": [],
        "isosurfaces": [
            {
                "vertices": [
                    [-0.7, -0.7, 0.0],
                    [0.7, -0.7, 0.0],
                    [0.0, 0.7, 0.0],
                    [0.0, 0.0, 0.9],
                ],
                "faces": [[0, 1, 2], [0, 1, 3], [1, 2, 3], [2, 0, 3]],
                "color": "#D55E00",
                "opacity": 1.0,
                "phase": "positive",
                "name": "+orbital",
            }
        ],
    }
    iso_png = _png(iso, {"width": 64, "height": 64, "scale": 1, "show_cell": False})
    assert hashlib.sha256(iso_png).hexdigest() == _ISOSURFACE_PNG_SHA256


def _foreground(rgba: np.ndarray) -> np.ndarray:
    mask = np.any(rgba[:, :, :3] < 250, axis=2)
    return rgba[mask][:, :3]


def test_unlit_removes_directional_shade_on_atoms_bonds_and_isosurface() -> None:
    soft = _png(_ball_stick_scene(), _render_spec())
    flat = _png(_ball_stick_scene(), _render_spec(lighting="unlit"))
    assert flat != soft
    soft_colors = _foreground(render_rgba(
        prepare_render(_ball_stick_scene(), camera=_camera(), render=_render_spec()),
        scale=1,
    ))
    flat_colors = _foreground(render_rgba(
        prepare_render(
            _ball_stick_scene(),
            camera=_camera(),
            render=_render_spec(lighting="unlit"),
        ),
        scale=1,
    ))
    assert len(np.unique(soft_colors, axis=0)) > len(np.unique(flat_colors, axis=0))

    camera = CameraSpec(
        position=(0.0, 0.0, 5.0),
        target=(0.0, 0.0, 0.0),
        up=(0.0, 1.0, 0.0),
        projection="orthographic",
        ortho_scale=1.2,
    )
    sphere = sphere_primitive("atom", (0.0, 0.0, 0.0), 0.55, "#2266AA")
    bond = cylinder_primitive(
        "bond",
        (-0.2, 0.0, 0.0),
        (0.2, 0.0, 0.0),
        0.08,
        "#333333",
        sides=8,
    )

    def picture(lighting: str | None) -> np.ndarray:
        metadata = {} if lighting is None else {"lighting": lighting}
        plan = RenderPlan(
            width=64,
            height=64,
            background=(1.0, 1.0, 1.0, 1.0),
            viewports=(
                ViewportPlan(
                    semantic_id="main",
                    camera=camera,
                    primitives=(sphere, bond),
                ),
            ),
            metadata=metadata,
        )
        return render_rgba(plan, scale=1)

    soft_rgba = picture(None)
    unlit_rgba = picture("unlit")
    assert not np.array_equal(soft_rgba, unlit_rgba)
    unlit_fg = _foreground(unlit_rgba)
    # Two flat colors: the atom and the bond. No Lambert or radial gradient.
    assert len(np.unique(unlit_fg, axis=0)) <= 2
    assert len(np.unique(_foreground(soft_rgba), axis=0)) > 2

    iso = {
        "draw_atoms": [],
        "bonds": [],
        "isosurfaces": [
            {
                "vertices": [
                    [-0.7, -0.7, 0.0],
                    [0.7, -0.7, 0.0],
                    [0.0, 0.7, 0.0],
                    [0.0, 0.0, 0.9],
                ],
                "faces": [[0, 1, 2], [0, 1, 3], [1, 2, 3], [2, 0, 3]],
                "color": "#D55E00",
                "opacity": 1.0,
                "phase": "positive",
                "name": "+orbital",
            }
        ],
    }
    spec = {"width": 64, "height": 64, "scale": 1, "show_cell": False}
    soft_iso = render_rgba(prepare_render(iso, camera=_camera(), render=spec), scale=1)
    flat_iso = render_rgba(
        prepare_render(iso, camera=_camera(), render={**spec, "lighting": "unlit"}),
        scale=1,
    )
    assert not np.array_equal(soft_iso, flat_iso)
    # #D55E00 is the inherited color. studio_soft darkens it; unlit does not.
    assert _foreground(flat_iso).max(axis=0).tolist() == [213, 94, 0]


def test_surface_record_composes_with_the_rig_and_is_not_rewritten() -> None:
    camera = CameraSpec(
        position=(0.0, 0.0, 5.0),
        target=(0.0, 0.0, 0.0),
        up=(0.0, 1.0, 0.0),
        projection="orthographic",
        ortho_scale=1.2,
    )
    plain = sphere_primitive("atom", (0.0, 0.0, 0.0), 0.55, "#2266AA")
    matte = sphere_primitive(
        "atom",
        (0.0, 0.0, 0.0),
        0.55,
        "#2266AA",
        metadata={
            "kind": "atom",
            "surface_material": {
                "ambient": 0.68,
                "diffuse": 0.32,
                "specular": 0.0,
                "shininess": 32.0,
                "specular_tint": "none",
            },
        },
    )
    plastic = sphere_primitive(
        "atom",
        (0.0, 0.0, 0.0),
        0.55,
        "#2266AA",
        metadata={"kind": "atom", "surface_material": _hard_plastic()},
    )

    def rgba(primitive, lighting: str | None = None) -> np.ndarray:
        metadata = {} if lighting is None else {"lighting": lighting}
        plan = RenderPlan(
            width=48,
            height=48,
            background=(1.0, 1.0, 1.0, 1.0),
            viewports=(
                ViewportPlan(semantic_id="main", camera=camera, primitives=(primitive,)),
            ),
            metadata=metadata,
        )
        return render_rgba(plan, scale=1)

    assert np.array_equal(rgba(plain), rgba(matte))
    plastic_soft = rgba(plastic)
    plastic_unlit = rgba(plastic, "unlit")
    assert not np.array_equal(plastic_soft, plastic_unlit)
    assert plastic.metadata["surface_material"]["specular"] == 0.35
    assert _foreground(plastic_unlit).max(axis=0).tolist() == [0x22, 0x66, 0xAA]
    # The white specular of hard_plastic shows up under the one studio light.
    assert int(_foreground(plastic_soft).max()) > int(_foreground(plastic_unlit).max())


def test_three_point_with_a_surface_record_is_not_rewritten_to_studio_soft() -> None:
    """An unsupported rig plus a valid surface record must not draw studio_soft."""

    response_metadata = {"surface_material": _hard_plastic()}
    response = read_surface_response(response_metadata)
    assert response is not None
    assert response.specular == 0.35
    assert tuple(SURFACE_RESPONSE_FIELDS) == (
        "ambient",
        "diffuse",
        "specular",
        "shininess",
        "specular_tint",
        "specular_color",
    )
    with pytest.raises(CpuLightingError, match="will not substitute studio_soft"):
        shade_with_rig(
            np.array([0.2, 0.4, 0.8]),
            np.array([0.0, 0.0, 1.0]),
            response,
            get_lighting_rig("studio_three_point"),
        )
    plastic = sphere_primitive(
        "atom",
        (0.0, 0.0, 0.0),
        0.4,
        "#2266AA",
        metadata={"kind": "atom", "surface_material": _hard_plastic()},
    )
    plan = RenderPlan(
        width=24,
        height=24,
        background=(1.0, 1.0, 1.0, 1.0),
        viewports=(
            ViewportPlan(semantic_id="main", camera=_camera(), primitives=(plastic,)),
        ),
        metadata={"lighting": "studio_three_point"},
    )
    with pytest.raises(CpuLightingError, match="will not substitute studio_soft"):
        render_rgba(plan, scale=1)
    assert plastic.metadata["surface_material"]["ambient"] == 0.30
    assert plastic.metadata["surface_material"]["specular"] == 0.35


def test_fresnel_and_foreign_backends_are_capability_errors() -> None:
    neutral = {**_hard_plastic(), "id": "hard_plastic", "fresnel_power": 0.0}
    assert read_surface_response({"surface_material": neutral}).specular == 0.35
    rim = {**_hard_plastic(), "fresnel_power": 1.5, "fresnel_center": 0.12}
    with pytest.raises(CpuLightingError, match="Fresnel"):
        read_surface_response({"surface_material": rim})
    for backend in ("plotly", "gpu"):
        for rig_id in ("unlit", "studio_three_point"):
            with pytest.raises(ValueError, match="will not silently"):
                note_plan_lighting({}, backend=backend, lighting=rig_id)
    kept = {}
    note_plan_lighting(kept, backend="cpu", lighting="studio_soft")
    assert "lighting" not in kept


def test_studio_three_point_is_rejected_on_cpu_ball_stick() -> None:
    with pytest.raises(CpuLightingError, match="will not substitute studio_soft") as raised:
        prepare_render(
            _ball_stick_scene(),
            camera=_camera(),
            render=_render_spec(lighting="studio_three_point"),
        )
    message = str(raised.value).lower()
    assert "studio_three_point" in message
    assert "three-light" in message
    assert "using studio_soft" not in message
    plan = RenderPlan(
        width=16,
        height=16,
        background=(1, 1, 1, 1),
        viewports=(
            ViewportPlan(
                semantic_id="main",
                camera=_camera(),
                primitives=(
                    sphere_primitive("atom", (0.0, 0.0, 0.0), 0.2, "#2266AA"),
                ),
            ),
        ),
        metadata={"lighting": "studio_three_point"},
    )
    with pytest.raises(CpuLightingError, match="will not substitute"):
        render_rgba(plan, scale=1)


def test_bfdh_lighting_dict_is_unchanged_and_dash_selector_is_untouched() -> None:
    source = inspect.getsource(_morphology_traces)
    assert "ambient=0.75" in source
    assert "diffuse=0.9" in source
    assert "specular=0.2" in source
    assert "roughness=0.25" in source
    layout = (_ROOT / "mat_viewer/app/layout_left_panel.py").read_text(encoding="utf-8")
    assert 'id="material-selector"' in layout
    assert '"value": "mesh"' in layout
    assert '"value": "flat"' in layout
    assert "lighting-selector" not in layout
    assert "studio_soft" not in layout
    assert "studio_three_point" not in layout


def test_cli_lighting_flag_and_publication_style_stays_rejected() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    render = _build_render_parser(parser.add_subparsers())
    args = render.parse_args(
        ["structure.cif", "-o", "figure.png", "--lighting", "unlit"]
    )
    assert args.lighting == "unlit"
    with pytest.raises(SystemExit):
        render.parse_args(
            ["structure.cif", "-o", "figure.png", "--lighting", "world_sun"]
        )
    from mat_viewer.cli import main

    with pytest.raises(SystemExit) as raised:
        main(
            [
                "render",
                "missing.cif",
                "-o",
                "missing.png",
                "--publication-style",
                "blender",
                "--lighting",
                "unlit",
                "--check",
                "--json",
            ]
        )
    assert raised.value.code == 2


def test_lighting_docs_describe_the_flag_without_a_fake_cpu_rig() -> None:
    document = (_ROOT / "docs/agents/lighting.md").read_text(encoding="utf-8")
    cli = (_ROOT / "docs/cli.md").read_text(encoding="utf-8")
    assert "--lighting" in document
    assert "--lighting" in cli
    assert "studio_three_point" in document
    assert "will not substitute" in document or "does not substitute" in document
    assert "hard_plastic" in document
    assert "does not select" in document
    assert "NCI" in document
    assert "Hirshfeld" in document
    # The named contract must not be described as an active CPU shade.
    assert "CPU raises" in document or "CPU error" in cli
