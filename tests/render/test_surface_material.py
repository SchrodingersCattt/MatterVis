"""Phase-1 Blinn–Phong surface presets."""

from __future__ import annotations

import argparse
import io
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

import mat_viewer.render.surface_material as surface_material_module
from mat_viewer.cli import _build_render_parser
from mat_viewer.presets import DEFAULT_STYLE
from mat_viewer.render.contracts import CameraSpec, RenderPlan, RenderSpec, ViewportPlan
from mat_viewer.render.cpu import render
from mat_viewer.render.cpu.surface_shade import split_blinn_phong
from mat_viewer.render.fast_cli import render_batch_if_selected
from mat_viewer.render.geometry import (
    cylinder_primitive,
    mesh_primitive,
    sphere_mesh,
    sphere_primitive,
)
from mat_viewer.render.lighting import (
    LIGHTING_RIGS,
    STUDIO_SOFT_DIRECTION,
    CpuLightingError,
    get_lighting_rig,
    read_surface_response,
)
from mat_viewer.render.planning import prepare_render
from mat_viewer.render.surface_material import (
    MATERIAL_LIBRARY,
    VISUAL_PRESETS,
    MaterialSpec,
    apply_surface_material,
    get_surface_material,
    resolve_surface_material,
    shade_blinn_phong,
)
from mat_viewer.renderer import validate_style_schema


def test_preset_lookup_matches_the_agreed_coefficients() -> None:
    matte = get_surface_material("matte")
    plastic = get_surface_material("hard_plastic")
    metal_like = get_surface_material("metal-like")

    assert (matte.ambient, matte.diffuse, matte.specular, matte.shininess) == (
        0.68,
        0.32,
        0.0,
        32.0,
    )
    assert matte.specular_tint == "none"
    assert matte.specular_color is None
    assert (
        plastic.ambient,
        plastic.diffuse,
        plastic.specular,
        plastic.shininess,
    ) == (0.30, 0.60, 0.35, 48.0)
    assert plastic.specular_tint == "white"
    assert plastic.specular_color == (1.0, 1.0, 1.0)
    assert (
        metal_like.ambient,
        metal_like.diffuse,
        metal_like.specular,
        metal_like.shininess,
    ) == (0.22, 0.38, 0.75, 96.0)
    assert metal_like.specular_tint == "base"
    assert metal_like.specular_color is None
    assert MATERIAL_LIBRARY.ids() == (
        "matte",
        "hard_plastic",
        "metal_like",
        "glass_like",
    )
    assert not matte.modulates_opacity()
    assert not plastic.modulates_opacity()
    assert not metal_like.modulates_opacity()
    assert "metal" not in MATERIAL_LIBRARY


def test_metal_is_reserved_and_does_not_alias_metal_like() -> None:
    with pytest.raises(ValueError, match="metal_like"):
        get_surface_material("metal")
    with pytest.raises(ValueError, match="PBR"):
        MATERIAL_LIBRARY.get("metal")
    with pytest.raises(ValueError, match="unknown surface material"):
        get_surface_material("brushed_gold")


def test_material_schema_rejects_opacity_and_allows_unnormalized_lambert() -> None:
    broad = MaterialSpec(ambient=0.8, diffuse=0.8, specular=0.2, shininess=8)
    assert broad.ambient + broad.diffuse > 1.0
    with pytest.raises(TypeError):
        MaterialSpec(
            ambient=0.3,
            diffuse=0.6,
            specular=0.1,
            shininess=16,
            opacity=0.55,  # type: ignore[call-arg]
        )
    with pytest.raises(ValueError, match="opacity"):
        MaterialSpec.from_mapping(
            {
                "ambient": 0.3,
                "diffuse": 0.6,
                "specular": 0.1,
                "shininess": 16,
                "opacity": 0.55,
                "light_scale": 1.2,
            }
        )
    with pytest.raises(ValueError, match="shininess"):
        MaterialSpec(ambient=0.2, diffuse=0.2, specular=0.0, shininess=0.0)
    restored = MaterialSpec.from_mapping(broad.as_dict())
    assert restored.ambient == broad.ambient
    assert "opacity" not in broad.as_dict()
    assert "light_scale" not in broad.as_dict()


def test_historical_coefficients_do_not_conflict_with_a_preset() -> None:
    matte = resolve_surface_material("matte", ambient=0.68, diffuse=0.32)
    assert matte.id == "matte"
    plastic = resolve_surface_material("hard_plastic", ambient=0.68, diffuse=0.32)
    assert plastic.id == "hard_plastic"
    assert plastic.specular == 0.35
    matching = resolve_surface_material("hard_plastic", ambient=0.30, diffuse=0.60)
    assert matching.specular == 0.35
    with pytest.raises(ValueError, match="not a conflict"):
        resolve_surface_material("hard_plastic", ambient=0.50, diffuse=0.40)

    style = apply_surface_material(
        {
            "material": "mesh",
            "style": "ball_stick",
            "ambient": 0.68,
            "diffuse": 0.32,
        },
        "matte",
    )
    assert style["material"] == "mesh"
    assert style["surface_material"] == "matte"
    assert style["ambient"] == 0.68
    assert style["diffuse"] == 0.32

    builtin = validate_style_schema({**DEFAULT_STYLE, "surface_material": "matte"})
    assert builtin["material"] == "mesh"
    assert builtin["surface_material"] == "matte"
    assert builtin["isosurface_ambient"] == 0.68
    assert builtin["isosurface_diffuse"] == 0.32
    assert builtin["isosurface_opacity"] == 0.55

    plastic_style = validate_style_schema(
        {**DEFAULT_STYLE, "surface_material": "hard_plastic"}
    )
    assert plastic_style["surface_material"] == "hard_plastic"
    assert plastic_style["material"] == "mesh"
    assert plastic_style["isosurface_opacity"] == 0.55
    with pytest.raises(ValueError, match="conflict"):
        validate_style_schema(
            {
                "material": "mesh",
                "ambient": 0.50,
                "diffuse": 0.40,
                "surface_material": "hard_plastic",
            }
        )
    with pytest.raises(ValueError, match="metal_like"):
        validate_style_schema({"material": "mesh", "surface_material": "metal"})


def test_specular_presets_are_rejected_off_the_cpu_renderer() -> None:
    from mat_viewer.render.cli_controls import _validate_render_options

    for backend in ("plotly", "gpu"):
        with pytest.raises(ValueError, match="CPU renderer"):
            RenderSpec(backend=backend, surface_material="hard_plastic")
        with pytest.raises(ValueError, match="CPU renderer"):
            RenderSpec(backend=backend, surface_material="metal_like")
    assert RenderSpec(backend="plotly", surface_material="matte").surface_material == "matte"
    assert RenderSpec(backend="cpu", surface_material="hard_plastic").backend == "cpu"

    parser = argparse.ArgumentParser()
    render_parser = _build_render_parser(parser.add_subparsers())
    plotly_args = render_parser.parse_args(
        ["density.cube", "-o", "preview.html", "--backend", "plotly", "--surface-material", "hard_plastic"]
    )
    with pytest.raises(ValueError, match="CPU renderer"):
        _validate_render_options(plotly_args)


def test_render_spec_accepts_presets_and_rejects_metal() -> None:
    matte = RenderSpec(surface_material="matte")
    assert matte.surface_material == "matte"
    assert matte.isosurface_opacity == 0.55
    assert RenderSpec(surface_material="Hard-Plastic").surface_material == "hard_plastic"
    with pytest.raises(ValueError, match="metal_like"):
        RenderSpec(surface_material="metal")
    with pytest.raises(ValueError, match="not a conflict"):
        RenderSpec(
            surface_material="hard_plastic",
            isosurface_ambient=0.50,
            isosurface_diffuse=0.40,
        )


def test_lighting_rig_is_not_redefined_by_the_surface_contract() -> None:
    assert not hasattr(surface_material_module, "LightingRig")
    assert get_lighting_rig("studio_soft").direction == STUDIO_SOFT_DIRECTION
    assert get_lighting_rig("studio_soft").cpu_evaluates is True
    assert get_lighting_rig("unlit").direction is None
    three = LIGHTING_RIGS["studio_three_point"]
    assert three.cpu_evaluates is False
    for name in ("matte", "hard_plastic", "metal_like"):
        spec = get_surface_material(name)
        response = read_surface_response({"surface_material": spec.as_dict()})
        assert response is not None
        assert response.ambient == spec.ambient
        assert response.diffuse == spec.diffuse
        assert response.specular == spec.specular
        assert response.shininess == spec.shininess
    glass = get_surface_material("glass_like")
    with pytest.raises(CpuLightingError, match="Fresnel"):
        read_surface_response({"surface_material": glass.as_dict()})
    with pytest.raises(ValueError, match="light_scale"):
        MaterialSpec.from_mapping(
            {
                "ambient": 0.68,
                "diffuse": 0.32,
                "specular": 0.0,
                "shininess": 32.0,
                "light_scale": 2.0,
            }
        )
    for preset in VISUAL_PRESETS.values():
        assert preset["surface_material"] in MATERIAL_LIBRARY
        assert preset["lighting"] in LIGHTING_RIGS


def test_lighting_and_visual_preset_contracts_stay_named_only() -> None:
    assert set(VISUAL_PRESETS) == {
        "scientific_default",
        "publication_flat",
        "skeletal",
        "vdw_standard",
        "presentation_plastic",
        "metallic_showcase",
    }
    assert VISUAL_PRESETS["scientific_default"]["surface_material"] == "matte"
    assert VISUAL_PRESETS["vdw_standard"]["representation"] == "vdw"
    assert "vdw" not in MATERIAL_LIBRARY


def test_cli_surface_material_flag(capsys: pytest.CaptureFixture[str]) -> None:
    parser = argparse.ArgumentParser()
    render_parser = _build_render_parser(parser.add_subparsers())
    args = render_parser.parse_args(
        ["structure.cif", "-o", "figure.png", "--surface-material", "metal_like"]
    )
    assert args.surface_material == "metal_like"
    with pytest.raises(SystemExit):
        render_parser.parse_args(
            ["structure.cif", "-o", "figure.png", "--surface-material", "metal"]
        )
    assert "metal_like" in capsys.readouterr().err


def test_batch_renderer_does_not_silently_drop_a_specular_preset() -> None:
    args = SimpleNamespace(
        renderer="batch",
        bond_scale=None,
        bond_threshold_specs=(),
        cell_overlays=None,
        surface_material="hard_plastic",
    )
    with pytest.raises(ValueError, match="surface-material"):
        render_batch_if_selected(args, install_command="python -m pip install matter-vis")


def test_white_and_tinted_highlights_follow_the_base_color() -> None:
    plastic = get_surface_material("hard_plastic")
    metal_like = get_surface_material("metal_like")
    light = np.asarray(STUDIO_SOFT_DIRECTION, dtype=float)
    light /= np.linalg.norm(light)
    half_vector = light + np.array((0.0, 0.0, 1.0))
    half_vector /= np.linalg.norm(half_vector)
    base = np.array((1.0, 0.0, 0.0))
    plastic_rgb = shade_blinn_phong(base, half_vector, plastic)
    metal_rgb = shade_blinn_phong(base, half_vector, metal_like)
    assert plastic_rgb[1] > 0.2
    assert plastic_rgb[2] > 0.2
    assert metal_rgb[1] == pytest.approx(0.0)
    assert metal_rgb[2] == pytest.approx(0.0)
    assert metal_rgb[0] > plastic_rgb[0] * 0.5


def _camera() -> CameraSpec:
    return CameraSpec(
        position=(0.0, 0.0, 6.0),
        target=(0.0, 0.0, 0.0),
        up=(0.0, 1.0, 0.0),
        projection="orthographic",
        near=0.1,
        far=30.0,
        ortho_scale=1.4,
    )


def _plan(*primitives: object) -> RenderPlan:
    return RenderPlan(
        width=96,
        height=96,
        background=(1.0, 1.0, 1.0, 1.0),
        viewports=(
            ViewportPlan(
                semantic_id="main",
                camera=_camera(),
                primitives=tuple(primitives),
            ),
        ),
    )


def _png(plan: RenderPlan) -> bytes:
    return render(plan, format="png").data


def _rgb(data: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))


def _foreground_max(rgb: np.ndarray, channel: int) -> int:
    mask = np.any(rgb < 250, axis=-1)
    assert np.any(mask)
    return int(rgb[mask, channel].max())


def _stamp(primitive, preset: str):
    metadata = dict(primitive.metadata)
    metadata["surface_material"] = get_surface_material(preset).as_dict()
    return replace(primitive, metadata=metadata)


def test_default_and_matte_cpu_pngs_match_and_specular_presets_highlight() -> None:
    sphere = sphere_primitive(
        "atom",
        (0.0, 0.0, 0.0),
        0.75,
        "#E04040",
        metadata={"kind": "atom"},
    )
    bond = cylinder_primitive(
        "bond",
        (-0.9, -0.9, 0.0),
        (0.9, -0.9, 0.0),
        0.28,
        "#E04040",
        metadata={"kind": "bond"},
    )
    vertices, triangles, normals = sphere_mesh(
        (0.0, 0.55, 0.0), 0.7, lat_steps=8, lon_steps=12
    )
    colors = np.zeros((len(vertices), 3), dtype=float)
    colors[:, 0] = np.clip(0.15 + 0.85 * np.maximum(normals[:, 0], 0.0), 0.0, 1.0)
    colors[:, 2] = np.clip(0.15 + 0.85 * np.maximum(-normals[:, 0], 0.0), 0.0, 1.0)
    surface = mesh_primitive(
        "isosurface",
        vertices,
        triangles,
        "#D55E00",
        normals=normals,
        alpha=0.55,
        vertex_colors=colors,
        metadata={
            "kind": "isosurface",
            "material": {"ambient": 0.68, "diffuse": 0.32},
        },
    )
    assert surface.rgba[3] == pytest.approx(0.55)

    plain = _png(_plan(sphere, bond, surface))
    matte = _png(
        _plan(_stamp(sphere, "matte"), _stamp(bond, "matte"), _stamp(surface, "matte"))
    )
    assert plain == matte

    def _preset_rgb(preset: str) -> np.ndarray:
        return _rgb(
            _png(
                _plan(
                    _stamp(sphere, preset),
                    _stamp(bond, preset),
                    _stamp(surface, preset),
                )
            )
        )

    plastic = _preset_rgb("hard_plastic")
    metal = _preset_rgb("metal_like")
    matte_rgb = _rgb(matte)
    assert not np.array_equal(plastic, matte_rgb)
    assert not np.array_equal(metal, matte_rgb)
    assert not np.array_equal(plastic, metal)
    # Foreground matte only scales #E04040 and the red/blue isosurface,
    # so green stays at or below 64. White specular lifts it.
    assert _foreground_max(matte_rgb, 1) <= 64
    assert _foreground_max(plastic, 1) > 64
    bond_matte = _rgb(_png(_plan(_stamp(bond, "matte"))))
    bond_plastic = _rgb(_png(_plan(_stamp(bond, "hard_plastic"))))
    assert _foreground_max(bond_matte, 1) <= 64
    assert _foreground_max(bond_plastic, 1) > 64
    atom_plastic = _rgb(_png(_plan(_stamp(sphere, "hard_plastic"))))
    assert _foreground_max(atom_plastic, 2) > 64
    foreground = np.any(metal < 250, axis=-1)
    assert np.any(metal[foreground, 0] > metal[foreground, 2] + 15)
    assert np.any(metal[foreground, 2] > metal[foreground, 0] + 15)


def test_prepare_render_wires_presets_without_recoloring_or_changing_opacity() -> None:
    scene = {
        "draw_atoms": [
            {"elem": "O", "cart": [-0.7, 0.0, 0.0], "color": "#E04040"},
            {"elem": "C", "cart": [0.7, 0.0, 0.0], "color": "#404040"},
        ],
        "bonds": [
            {
                "i": 0,
                "j": 1,
                "left_global_index": 0,
                "right_global_index": 1,
            }
        ],
        "isosurfaces": [
            {
                "vertices": [
                    [-0.4, -0.4, 0.2],
                    [0.4, -0.4, 0.2],
                    [0.0, 0.45, 0.2],
                    [0.0, 0.0, 0.9],
                ],
                "faces": [[0, 1, 2], [0, 1, 3], [1, 2, 3], [2, 0, 3]],
                "normals": [
                    [-1.0, 0.0, 0.0],
                    [1.0, 0.0, 0.0],
                    [0.0, 1.0, 0.2],
                    [0.0, 0.0, 1.0],
                ],
                "vertex_colors": [
                    [0.85, 0.1, 0.1],
                    [0.1, 0.2, 0.85],
                    [0.85, 0.45, 0.1],
                    [0.2, 0.75, 0.3],
                ],
                "color": "#D55E00",
                "opacity": 0.55,
                "metadata": {"material": {"ambient": 0.68, "diffuse": 0.32}},
            }
        ],
        "polyhedra": [
            {
                "vertices": [
                    [1.6, 0.0, 0.0],
                    [2.2, 0.0, 0.0],
                    [1.9, 0.4, 0.0],
                    [1.9, 0.15, 0.4],
                ],
                "faces": [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]],
                "color": "#7C5CBF",
                "opacity": 0.5,
            }
        ],
    }
    common = {
        "width": 120,
        "height": 96,
        "show_cell": False,
        "representation": "ball_stick",
        "bond_radius": 0.18,
        "sphere_detail": (8, 12),
    }
    camera = _camera()
    plain = prepare_render(scene, camera=camera, render=common)
    matte = prepare_render(
        scene, camera=camera, render={**common, "surface_material": "matte"}
    )
    plastic = prepare_render(
        scene, camera=camera, render={**common, "surface_material": "hard_plastic"}
    )
    assert _png(plain) == _png(matte)
    assert _png(plastic) != _png(matte)

    atoms = [item for item in plastic.primitives if item.metadata.get("kind") == "atom"]
    bonds = [item for item in plastic.primitives if item.metadata.get("kind") == "bond"]
    surfaces = [
        item for item in plastic.primitives if item.metadata.get("kind") == "isosurface"
    ]
    polyhedra = [
        item for item in plastic.primitives if item.metadata.get("kind") == "polyhedron"
    ]
    assert atoms and bonds and surfaces and polyhedra
    assert all(item.metadata["surface_material_id"] == "hard_plastic" for item in atoms)
    assert all("id" not in item.metadata["surface_material"] for item in atoms)
    assert all(item.metadata.get("_raster_shape") == "sphere" for item in atoms)
    assert all(item.metadata["surface_material_id"] == "hard_plastic" for item in bonds)
    assert all(item.metadata.get("_raster_shape") == "cylinder" for item in bonds)
    assert surfaces[0].rgba[3] == pytest.approx(0.55)
    assert surfaces[0].vertex_colors is not None
    assert surfaces[0].metadata["material"] == {"ambient": 0.68, "diffuse": 0.32}
    assert "surface_material" not in polyhedra[0].metadata
    assert "surface_material" not in plain.metadata

    wire = prepare_render(
        scene,
        camera=camera,
        render={**common, "representation": "wireframe", "surface_material": "metal_like"},
    )
    assert _png(wire).startswith(b"\x89PNG")
    assert all(
        item.metadata.get("kind") != "bond" or item.metadata.get("_raster_shape") != "cylinder"
        for item in wire.primitives
    )


def test_glass_like_round_trips_and_glass_is_reserved() -> None:
    glass = get_surface_material("glass_like")
    assert glass.specular_tint == "white"
    assert glass.specular_color == (1.0, 1.0, 1.0)
    assert glass.modulates_opacity()
    assert glass.fresnel_center < 1.0 < glass.fresnel_rim
    restored = MaterialSpec.from_mapping(glass.as_dict())
    assert restored == glass
    assert "opacity" not in glass.as_dict()
    assert "light_scale" not in glass.as_dict()
    neutral = MaterialSpec(ambient=0.2, diffuse=0.2, specular=0.0, shininess=8)
    assert "fresnel_power" not in neutral.as_dict()
    assert MaterialSpec.from_mapping(neutral.as_dict()) == neutral
    with pytest.raises(ValueError, match="glass_like"):
        get_surface_material("glass")
    with pytest.raises(ValueError, match="refractive"):
        get_surface_material("glass")
    resolved = resolve_surface_material("glass_like", ambient=0.68, diffuse=0.32)
    assert resolved.fresnel_power == glass.fresnel_power
    diffuse, specular = split_blinn_phong(
        np.array((0.8, 0.2, 0.1)),
        np.array([[0.2, 0.3, 0.9], [-0.4, 0.2, 0.8]]),
        get_surface_material("hard_plastic"),
    )
    assert np.allclose(
        diffuse + specular,
        shade_blinn_phong(
            np.array((0.8, 0.2, 0.1)),
            np.array([[0.2, 0.3, 0.9], [-0.4, 0.2, 0.8]]),
            get_surface_material("hard_plastic"),
        ),
    )


def test_glass_like_is_rejected_on_plotly_and_gpu() -> None:
    from mat_viewer.render.cli_controls import _validate_render_options

    for backend in ("plotly", "gpu"):
        with pytest.raises(ValueError, match="CPU renderer"):
            RenderSpec(backend=backend, surface_material="glass_like")
    assert RenderSpec(surface_material="glass_like").backend == "cpu"
    assert RenderSpec(backend="plotly", surface_material="matte").surface_material == "matte"
    assert RenderSpec(backend="gpu").surface_material is None

    parser = argparse.ArgumentParser()
    render_parser = _build_render_parser(parser.add_subparsers())
    accepted = render_parser.parse_args(
        ["density.cube", "-o", "glass.png", "--backend", "cpu", "--surface-material", "glass_like"]
    )
    assert accepted.surface_material == "glass_like"
    _validate_render_options(accepted)
    rejected = render_parser.parse_args(
        ["density.cube", "-o", "preview.html", "--backend", "plotly", "--surface-material", "glass_like"]
    )
    with pytest.raises(ValueError, match="CPU renderer"):
        _validate_render_options(rejected)


def _transparent_plan(*primitives: object, background=(0.0, 0.0, 0.0, 0.0)) -> RenderPlan:
    return RenderPlan(
        width=96,
        height=96,
        background=background,
        viewports=(
            ViewportPlan(
                semantic_id="main",
                camera=_camera(),
                primitives=tuple(primitives),
            ),
        ),
    )


def _shell(alpha: float, preset: str, color: str = "#D55E00"):
    vertices, triangles, normals = sphere_mesh(
        (0.0, 0.0, 0.0), 0.85, lat_steps=16, lon_steps=24
    )
    mesh = mesh_primitive(
        "shell",
        vertices,
        triangles,
        color,
        normals=normals,
        alpha=alpha,
        metadata={
            "kind": "isosurface",
            "material": {"ambient": 0.68, "diffuse": 0.32},
        },
    )
    return _stamp(mesh, preset)


def _image(plan: RenderPlan) -> np.ndarray:
    from mat_viewer.render.cpu.raster import render_rgba

    return render_rgba(plan)


def _coverage_alpha(image: np.ndarray) -> tuple[float, float, float]:
    alpha = image[..., 3]
    mask = alpha > 8
    assert np.any(mask)
    ys, xs = np.nonzero(mask)
    distance = np.hypot(ys - (image.shape[0] - 1) / 2.0, xs - (image.shape[1] - 1) / 2.0)
    limit = float(distance.max())
    center = alpha[mask][distance < 0.22 * limit]
    rim = alpha[mask][distance > 0.82 * limit]
    assert center.size and rim.size
    return float(np.median(center)), float(np.median(rim)), float(alpha[mask].mean())


def test_glass_like_rim_is_denser_than_the_face_and_tracks_opacity() -> None:
    center_55, rim_55, mean_55 = _coverage_alpha(_image(_transparent_plan(_shell(0.55, "glass_like"))))
    center_35, rim_35, mean_35 = _coverage_alpha(_image(_transparent_plan(_shell(0.35, "glass_like"))))
    center_75, rim_75, mean_75 = _coverage_alpha(_image(_transparent_plan(_shell(0.75, "glass_like"))))
    assert rim_55 > center_55 * 2.5
    assert center_55 < 0.55 * 255.0 * 0.5
    assert rim_55 > 140.0
    assert center_35 < center_55 < center_75
    assert mean_35 < mean_55 < mean_75
    assert rim_35 < rim_75

    atom = _stamp(
        sphere_primitive(
            "atom",
            (0.0, 0.0, 0.0),
            0.45,
            "#E04040",
            metadata={"kind": "atom"},
        ),
        "glass_like",
    )
    opaque = _image(_transparent_plan(atom))
    covered = opaque[..., 3] > 0
    assert np.any(covered)
    assert int(opaque[..., 3][covered].min()) == 255


def test_translucent_specular_is_not_crushed_by_alpha() -> None:
    plastic = _image(_transparent_plan(_shell(0.25, "hard_plastic", "#E04040")))
    matte = _image(_transparent_plan(_shell(0.25, "matte", "#E04040")))
    peak = np.unravel_index(int(np.argmax(plastic[..., 1])), plastic.shape[:2])
    assert int(plastic[peak][1]) > 200
    assert int(plastic[peak][3]) < 180
    assert int(matte[..., 1].max()) < 80


def test_glass_center_shows_an_opaque_interior_atom() -> None:
    atom = sphere_primitive(
        "atom",
        (0.0, 0.0, 0.0),
        0.28,
        "#E04040",
        metadata={"kind": "atom"},
    )
    shell = _shell(0.55, "glass_like", "#0072B2")
    white = (1.0, 1.0, 1.0, 1.0)
    both = _image(_transparent_plan(atom, shell, background=white))
    atom_only = _image(_transparent_plan(atom, background=white))
    shell_only = _image(_transparent_plan(shell, background=white))
    center = both[both.shape[0] // 2, both.shape[1] // 2, :3].astype(float)
    atom_center = atom_only[atom_only.shape[0] // 2, atom_only.shape[1] // 2, :3].astype(float)
    shell_center = shell_only[shell_only.shape[0] // 2, shell_only.shape[1] // 2, :3].astype(float)
    assert np.linalg.norm(center - atom_center) < np.linalg.norm(center - shell_center)


def test_perspective_cylinder_view_uses_the_pixel_ray() -> None:
    from mat_viewer.render.camera import CameraTransform
    from mat_viewer.render.cpu.raster import _cylinder_view

    camera = CameraSpec(
        position=(0.0, 0.0, 6.0),
        target=(0.0, 0.0, 0.0),
        up=(0.0, 1.0, 0.0),
        projection="perspective",
        near=0.1,
        far=30.0,
        fov_y_deg=40.0,
    )
    transform = CameraTransform(camera, 80, 80)
    center = _cylinder_view(transform, 40.0, 40.0, 4.0)
    center = center / np.linalg.norm(center)
    assert np.allclose(center, (0.0, 0.0, 1.0), atol=1e-6)
    side = _cylinder_view(transform, 10.0, 40.0, 4.0)
    side = side / np.linalg.norm(side)
    assert float(side[0]) > 0.2
    ortho = CameraTransform(
        CameraSpec(
            position=(0.0, 0.0, 6.0),
            target=(0.0, 0.0, 0.0),
            up=(0.0, 1.0, 0.0),
            projection="orthographic",
            near=0.1,
            far=30.0,
            ortho_scale=2.0,
        ),
        80,
        80,
    )
    assert np.allclose(_cylinder_view(ortho, 10.0, 20.0, 4.0), (0.0, 0.0, 1.0))


def test_perspective_specular_uses_the_camera_view() -> None:
    plastic = get_surface_material("hard_plastic")
    light = np.asarray(STUDIO_SOFT_DIRECTION, dtype=float)
    light = light / np.linalg.norm(light)
    view = np.array((-0.55, 0.15, -1.0), dtype=float)
    view = view / np.linalg.norm(view)
    half = light + view
    half = half / np.linalg.norm(half)
    base = np.array((1.0, 0.0, 0.0))
    _diffuse, specular = split_blinn_phong(base, half[None, :], plastic, view=view)
    _ortho_diffuse, ortho = split_blinn_phong(
        base, half[None, :], plastic, view=np.array((0.0, 0.0, 1.0))
    )
    assert float(specular[0, 1]) > 0.30
    assert float(specular[0, 1]) > float(ortho[0, 1]) + 0.15


def test_fresnel_alpha_of_one_keeps_the_specular() -> None:
    from mat_viewer.render.cpu.fragments import _Fragment, _composite_fragments  # noqa: I001

    diffuse = (0.25, 0.04, 0.0)
    specular = (0.0, 0.46, 0.46)

    def painted(alpha: float) -> np.ndarray:
        buffer = np.zeros((1, 1, 4), dtype=float)
        depth = np.full((1, 1), np.inf)
        _composite_fragments(
            buffer,
            depth,
            {0: [_Fragment(0.2, 0, "rim", (*diffuse, alpha), specular)]},
        )
        return buffer[0, 0, :3]

    near = painted(0.9999)
    full = painted(1.0)
    assert np.allclose(near, full, atol=0.002)
    assert float(full[1]) > 0.40
    for opacity in (0.70, 0.75):
        image = _image(_transparent_plan(_shell(opacity, "glass_like", "#D55E00")))
        assert int(image[..., 1].max()) > 180


def test_coarse_mesh_specular_is_a_single_smooth_peak() -> None:
    vertices, triangles, normals = sphere_mesh(
        (0.0, 0.0, 0.0), 0.8, lat_steps=4, lon_steps=6
    )
    mesh = mesh_primitive(
        "coarse",
        vertices,
        triangles,
        "#E04040",
        normals=normals,
        alpha=0.55,
        metadata={"kind": "isosurface"},
    )
    image = _image(
        _transparent_plan(_stamp(mesh, "hard_plastic"), background=(0.0, 0.0, 0.0, 1.0))
    )
    peak = np.unravel_index(int(np.argmax(image[..., 1])), image.shape[:2])
    assert int(image[peak][1]) > 80
    left = max(0, peak[1] - 20)
    right = min(image.shape[1], peak[1] + 21)
    samples = image[peak[0], left:right, 1].astype(float)
    delta = np.diff(samples)
    delta[np.abs(delta) < 3.0] = 0.0
    signs = np.sign(delta)
    signs = signs[signs != 0.0]
    turns = int(np.sum(signs[1:] * signs[:-1] < 0.0)) if signs.size else 0
    assert turns <= 2


def test_unlit_keeps_the_surface_record_and_three_point_does_not_shade() -> None:
    plastic = sphere_primitive(
        "atom",
        (0.0, 0.0, 0.0),
        0.45,
        "#2266AA",
        metadata={
            "kind": "atom",
            "surface_material": get_surface_material("hard_plastic").as_dict(),
        },
    )
    camera = CameraSpec(
        position=(0.0, 0.0, 4.0),
        target=(0.0, 0.0, 0.0),
        up=(0.0, 1.0, 0.0),
        projection="orthographic",
        ortho_scale=1.4,
    )
    black = (0.0, 0.0, 0.0, 1.0)

    def picture(lighting: str | None) -> np.ndarray:
        metadata = {} if lighting is None else {"lighting": lighting}
        plan = RenderPlan(
            width=32,
            height=32,
            background=black,
            viewports=(
                ViewportPlan(semantic_id="main", camera=camera, primitives=(plastic,)),
            ),
            metadata=metadata,
        )
        return _image(plan)

    shaded = picture(None)
    flat = picture("unlit")
    assert not np.array_equal(shaded, flat)
    covered = np.any(flat[..., :3] > 0, axis=-1)
    assert covered.any()
    assert flat[covered][:, :3].max(axis=0).tolist() == [0x22, 0x66, 0xAA]
    assert plastic.metadata["surface_material"]["specular"] == pytest.approx(0.35)
    with pytest.raises(CpuLightingError, match="will not substitute studio_soft"):
        picture("studio_three_point")
