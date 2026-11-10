"""Shared cube-isosurface material across CPU, vector, Plotly, and GPU."""

from __future__ import annotations

import numpy as np
import pytest

from mat_viewer.render.contracts import (
    CameraSpec,
    RenderPlan,
    RenderSpec,
    TriangleMeshPrimitive,
    ViewportPlan,
)
from mat_viewer.render.isosurface_material import (
    GLOSSY_MATERIAL,
    MATTE_MATERIAL,
    IsosurfaceShadingUnsupported,
    camera_light_direction,
    material_from_record,
    material_from_style,
    plotly_lighting,
    preset_material,
    resolve_isosurface_material,
    shade_albedo,
)


def test_historical_material_is_the_default_and_presets_are_independent() -> None:
    historical = resolve_isosurface_material()
    assert (historical.ambient, historical.diffuse, historical.specular) == (0.68, 0.32, 0.0)
    assert historical.shininess == 32.0
    assert historical.light_scale == 1.0
    assert historical.opacity == 0.55
    assert MATTE_MATERIAL.ambient + MATTE_MATERIAL.diffuse > 1.0
    assert MATTE_MATERIAL.specular == 0.0
    assert (MATTE_MATERIAL.ambient, MATTE_MATERIAL.diffuse) == (0.62, 0.55)
    assert GLOSSY_MATERIAL.specular == 0.18
    assert GLOSSY_MATERIAL.shininess == 16.0
    assert GLOSSY_MATERIAL.ambient + GLOSSY_MATERIAL.diffuse > 1.0
    with pytest.raises(ValueError, match="matte"):
        preset_material("metal")
    with pytest.raises(ValueError, match="not both"):
        resolve_isosurface_material("matte", ambient=0.2)
    custom = resolve_isosurface_material("glossy", opacity=0.25)
    assert custom.specular == GLOSSY_MATERIAL.specular
    assert custom.opacity == 0.25


def test_coefficients_above_one_are_kept_and_invalid_values_fail() -> None:
    bright = resolve_isosurface_material(ambient=0.8, diffuse=0.9, specular=1.4, light_scale=2.0)
    assert bright.ambient + bright.diffuse > 1.0
    assert bright.as_dict()["light_scale"] == 2.0
    with pytest.raises(ValueError, match="non-negative"):
        resolve_isosurface_material(specular=-0.1)
    with pytest.raises(ValueError, match="at least 1"):
        resolve_isosurface_material(shininess=0.5)
    with pytest.raises(ValueError, match="opacity"):
        resolve_isosurface_material(opacity=1.2)
    partial = material_from_record({"ambient": 0.45, "diffuse": 0.50})
    assert partial.specular == 0.0
    assert partial.light_scale == 1.0


def test_render_spec_preset_replaces_light_coefficients_only() -> None:
    matte = RenderSpec(isosurface_material="matte", isosurface_opacity=0.4)
    assert matte.isosurface_ambient == MATTE_MATERIAL.ambient
    assert matte.isosurface_diffuse == MATTE_MATERIAL.diffuse
    assert matte.isosurface_specular == 0.0
    assert matte.isosurface_opacity == 0.4
    RenderSpec(isosurface_ambient=0.8, isosurface_diffuse=0.8)
    with pytest.raises(ValueError, match="do not also set"):
        RenderSpec(isosurface_material="glossy", isosurface_specular=0.5)


def test_style_preset_ignores_stored_light_coefficients() -> None:
    material = material_from_style(
        {
            "isosurface_material": "glossy",
            "isosurface_ambient": 0.1,
            "isosurface_opacity": 0.33,
        }
    )
    assert material.ambient == GLOSSY_MATERIAL.ambient
    assert material.opacity == 0.33


def test_two_sided_shade_keeps_scalar_color_and_scales_light() -> None:
    red = np.array([0.8, 0.05, 0.05])
    front = shade_albedo(red, np.array([0.0, 0.0, 1.0]), MATTE_MATERIAL)
    back = shade_albedo(red, np.array([0.0, 0.0, -1.0]), MATTE_MATERIAL)
    assert np.allclose(front, back)
    assert back[0] > 0.4
    assert back[0] > back[1]
    assert back[0] > back[2]

    facing = camera_light_direction()
    plain = resolve_isosurface_material(
        ambient=GLOSSY_MATERIAL.ambient,
        diffuse=GLOSSY_MATERIAL.diffuse,
        specular=0.0,
        shininess=GLOSSY_MATERIAL.shininess,
    )
    unlit = shade_albedo(red, facing, plain)
    glossy = shade_albedo(red, facing, GLOSSY_MATERIAL)
    assert glossy[1] > unlit[1]
    assert glossy[2] > unlit[2]
    assert glossy[0] > glossy[1]

    dim = np.array([0.2, 0.2, 0.2])
    floor = shade_albedo(
        dim,
        facing,
        resolve_isosurface_material(ambient=0.2, diffuse=0.3, light_scale=0.0),
    )
    boosted = shade_albedo(
        dim,
        facing,
        resolve_isosurface_material(ambient=0.2, diffuse=0.3, light_scale=2.0),
    )
    assert np.allclose(floor, dim * 0.2)
    assert boosted[0] > floor[0]
    clipped = shade_albedo(
        np.ones(3),
        facing,
        resolve_isosurface_material(ambient=2.0, diffuse=0.0),
    )
    assert np.allclose(clipped, np.ones(3))


def test_plotly_forwards_presets_and_rejects_unstorable_coefficients() -> None:
    lighting = plotly_lighting(resolve_isosurface_material())
    assert lighting == {
        "ambient": 0.68,
        "diffuse": 0.32,
        "specular": 0.0,
        "roughness": pytest.approx(0.5),
        "fresnel": 0.0,
    }
    matte = plotly_lighting(MATTE_MATERIAL)
    glossy = plotly_lighting(GLOSSY_MATERIAL)
    assert matte["ambient"] == MATTE_MATERIAL.ambient
    assert matte["diffuse"] == MATTE_MATERIAL.diffuse
    assert matte["specular"] == 0.0
    assert matte["fresnel"] == 0.0
    assert glossy["ambient"] == GLOSSY_MATERIAL.ambient
    assert glossy["diffuse"] == GLOSSY_MATERIAL.diffuse
    assert glossy["specular"] == GLOSSY_MATERIAL.specular
    assert glossy["roughness"] == pytest.approx(1.0 / (1.0 + GLOSSY_MATERIAL.shininess / 32.0))
    assert matte["ambient"] + matte["diffuse"] > 1.0
    assert glossy["ambient"] + glossy["diffuse"] > 1.0
    with pytest.raises(IsosurfaceShadingUnsupported, match="not clamped"):
        plotly_lighting(resolve_isosurface_material(ambient=1.5, diffuse=0.0))
    with pytest.raises(IsosurfaceShadingUnsupported, match="not clamped"):
        plotly_lighting(resolve_isosurface_material(ambient=0.2, diffuse=0.4, light_scale=3.0))


def _camera() -> CameraSpec:
    return CameraSpec.looking_along(
        (0.0, 0.0, 1.0),
        up=(0.0, 1.0, 0.0),
        distance=8.0,
        ortho_scale=2.0,
    )


def _plan(*primitives: TriangleMeshPrimitive) -> RenderPlan:
    return RenderPlan(
        width=80,
        height=80,
        background=(1.0, 1.0, 1.0, 1.0),
        viewports=(ViewportPlan("main", camera=_camera(), primitives=tuple(primitives)),),
    )


def _folded_surfaces(*, vertex_colors: bool) -> tuple[TriangleMeshPrimitive, TriangleMeshPrimitive]:
    front = np.asarray([[-0.9, -0.45, 0.0], [-0.15, -0.45, 0.0], [-0.5, 0.55, 0.0]])
    back = np.asarray([[0.15, -0.45, 0.0], [0.5, 0.55, 0.0], [0.9, -0.45, 0.0]])
    red = np.array([[0.85, 0.08, 0.05], [0.85, 0.08, 0.05], [0.85, 0.08, 0.05]])
    blue = np.array([[0.05, 0.12, 0.82], [0.05, 0.12, 0.82], [0.05, 0.12, 0.82]])
    return (
        TriangleMeshPrimitive(
            semantic_id="front",
            vertices=front,
            triangles=np.asarray([[0, 1, 2]]),
            rgba=(0.2, 0.2, 0.2, 1.0),
            vertex_colors=red if vertex_colors else None,
            metadata={"kind": "isosurface", "material": MATTE_MATERIAL.as_dict()},
        ),
        TriangleMeshPrimitive(
            semantic_id="back",
            vertices=back,
            triangles=np.asarray([[0, 1, 2]]),
            rgba=(0.2, 0.2, 0.2, 1.0),
            vertex_colors=blue if vertex_colors else None,
            metadata={"kind": "isosurface", "material": MATTE_MATERIAL.as_dict()},
        ),
    )


def _colored_pixels(image: np.ndarray, *, half: str) -> np.ndarray:
    mid = image.shape[1] // 2
    region = image[:, :mid] if half == "left" else image[:, mid:]
    rgb = region[..., :3].astype(float)
    colored = np.any(rgb < 250.0, axis=-1)
    assert np.any(colored)
    return rgb[colored]


def test_cpu_raster_keeps_scalar_colors_on_both_sides_of_a_fold() -> None:
    pytest.importorskip("PIL")
    from mat_viewer.render.cpu import render_rgba

    image = render_rgba(_plan(*_folded_surfaces(vertex_colors=True)))
    left = _colored_pixels(image, half="left")
    right = _colored_pixels(image, half="right")
    assert np.mean(left[:, 0]) > np.mean(left[:, 1])
    assert np.mean(left[:, 0]) > 80.0
    assert np.mean(right[:, 2]) > np.mean(right[:, 0])
    assert np.mean(right[:, 2]) > 80.0


def test_cpu_raster_gloss_changes_the_folded_sheet() -> None:
    pytest.importorskip("PIL")
    from mat_viewer.render.cpu import render_rgba

    front, _back = _folded_surfaces(vertex_colors=False)
    matte = render_rgba(_plan(front))
    glossy_mesh = TriangleMeshPrimitive(
        semantic_id=front.semantic_id,
        vertices=front.vertices,
        triangles=front.triangles,
        rgba=(0.85, 0.2, 0.05, 1.0),
        metadata={"kind": "isosurface", "material": GLOSSY_MATERIAL.as_dict()},
    )
    glossy = render_rgba(_plan(glossy_mesh))
    assert not np.array_equal(matte, glossy)


def test_vector_export_shades_the_back_face_without_dropping_its_color() -> None:
    from mat_viewer.render.cpu.vector import vector_scene

    _front, back = _folded_surfaces(vertex_colors=True)
    projected, _lines = vector_scene(_plan(back).viewports[0], 80, 80)
    assert projected
    red, _green, blue, alpha = projected[0].polygon.rgba
    assert alpha == 1.0
    assert blue > red
    assert blue > 0.3


def test_matplotlib_shades_uniform_isosurface_faces() -> None:
    pytest.importorskip("matplotlib")
    from mat_viewer.render.matplotlib import _viewport_artists

    front, _back = _folded_surfaces(vertex_colors=False)
    artists = _viewport_artists(
        _plan(front).viewports[0], width=80, height=80, dpi=100
    )
    colors = [artist[3].get_facecolor() for artist in artists]
    assert colors
    red, green, blue, _alpha = colors[0]
    assert not np.allclose((red, green, blue), front.rgba[:3])
    assert red > 0.2


def test_matplotlib_still_rejects_vertex_colors() -> None:
    pytest.importorskip("matplotlib")
    from mat_viewer.render.matplotlib import _viewport_artists

    front, _back = _folded_surfaces(vertex_colors=True)
    with pytest.raises(ValueError, match="per-vertex colors"):
        _viewport_artists(_plan(front).viewports[0], width=80, height=80, dpi=100)


def test_gpu_rejects_isosurface_material_instead_of_drawing_flat() -> None:
    from mat_viewer.render.gpu import GPUUnsupportedError, lower_plan

    front, _back = _folded_surfaces(vertex_colors=False)
    opaque = TriangleMeshPrimitive(
        semantic_id="sheet",
        vertices=front.vertices,
        triangles=front.triangles,
        rgba=(0.8, 0.2, 0.1, 1.0),
        metadata=front.metadata,
    )
    with pytest.raises(GPUUnsupportedError, match="shared cube isosurface material"):
        lower_plan(_plan(opaque))
    with pytest.raises(GPUUnsupportedError, match="shared cube isosurface material"):
        lower_plan(_plan(front))


def test_plotly_mesh_trace_forwards_the_historical_material_and_matte() -> None:
    pytest.importorskip("skimage")
    from mat_viewer.cube.io import CubeAtom, CubeData
    from mat_viewer.render.traces_isosurface import isosurface_overlay_traces

    coordinates = np.indices((8, 8, 8), dtype=float)
    radius = np.sqrt(sum((coordinates[axis] - 3.5) ** 2 for axis in range(3)))
    cube = CubeData(
        title="field",
        comment="radial",
        atoms=[CubeAtom(6, 6.0, np.zeros(3))],
        origin=np.zeros(3),
        axes=np.eye(3) * 0.4,
        values=2.0 - radius,
        path=__import__("pathlib").Path("field.cube"),
    )
    scene = {"cube_data": cube, "draw_atoms": []}
    traces = isosurface_overlay_traces(
        scene,
        {"isosurface_isovalue": 0.5, "isosurface_stride": 1, "isosurface_opacity": 0.7},
    )
    assert traces
    assert traces[0]["lighting"]["ambient"] == pytest.approx(0.68)
    assert traces[0]["lighting"]["diffuse"] == pytest.approx(0.32)
    assert traces[0]["lighting"]["fresnel"] == 0.0
    assert traces[0]["opacity"] == pytest.approx(0.7)
    assert "lightposition" in traces[0]
    preset_traces = isosurface_overlay_traces(
        scene,
        {
            "isosurface_isovalue": 0.5,
            "isosurface_stride": 1,
            "isosurface_material": "matte",
        },
    )
    assert preset_traces[0]["lighting"]["ambient"] == MATTE_MATERIAL.ambient
    assert preset_traces[0]["lighting"]["diffuse"] == MATTE_MATERIAL.diffuse
    assert preset_traces[0]["lighting"]["specular"] == 0.0
    with pytest.raises(IsosurfaceShadingUnsupported, match="not clamped"):
        isosurface_overlay_traces(
            scene,
            {
                "isosurface_isovalue": 0.5,
                "isosurface_stride": 1,
                "isosurface_ambient": 1.5,
                "isosurface_diffuse": 0.0,
            },
        )


def _radial_cube():
    from pathlib import Path

    from mat_viewer.cube.io import CubeAtom, CubeData

    coordinates = np.indices((8, 8, 8), dtype=float)
    radius = np.sqrt(sum((coordinates[axis] - 3.5) ** 2 for axis in range(3)))
    return CubeData(
        title="field",
        comment="radial",
        atoms=[CubeAtom(6, 6.0, np.zeros(3))],
        origin=np.zeros(3),
        axes=np.eye(3) * 0.4,
        values=2.0 - radius,
        path=Path("field.cube"),
    )


def test_volume_shells_apply_the_shared_material() -> None:
    pytest.importorskip("plotly")
    from mat_viewer.cube.core import orbital_isosurface_traces

    cube = _radial_cube()
    historical = orbital_isosurface_traces(cube, isovalue=0.5)
    assert historical
    assert historical[0].opacity == pytest.approx(0.55)
    assert historical[0].lighting.ambient == pytest.approx(0.68)
    assert historical[0].lighting.diffuse == pytest.approx(0.32)
    assert historical[0].lighting.specular == pytest.approx(0.0)
    assert historical[0].lighting.fresnel == pytest.approx(0.0)
    assert historical[0].lightposition.x == pytest.approx(-3200.0)

    glossy = orbital_isosurface_traces(cube, isovalue=0.5, material="glossy", opacity=0.4)
    assert glossy[0].opacity == pytest.approx(0.4)
    assert glossy[0].lighting.ambient == pytest.approx(GLOSSY_MATERIAL.ambient)
    assert glossy[0].lighting.diffuse == pytest.approx(GLOSSY_MATERIAL.diffuse)
    assert glossy[0].lighting.specular == pytest.approx(GLOSSY_MATERIAL.specular)


def test_orbital_mesh_default_lighting_stays_historical() -> None:
    pytest.importorskip("plotly")
    pytest.importorskip("skimage")
    from mat_viewer.cube.core import orbital_mesh_traces

    cube = _radial_cube()
    traces = orbital_mesh_traces(cube, isovalue=0.5, stride=2)
    assert traces
    assert traces[0].opacity == pytest.approx(0.6)
    assert traces[0].lighting.ambient == pytest.approx(0.68)
    assert traces[0].lighting.diffuse == pytest.approx(0.32)
    assert traces[0].lighting.specular == pytest.approx(0.0)
    assert traces[0].lighting.fresnel == pytest.approx(0.0)

    glossy = orbital_mesh_traces(cube, isovalue=0.5, stride=2, material="glossy", opacity=0.6)
    assert glossy[0].opacity == pytest.approx(0.6)
    assert glossy[0].lighting.ambient == pytest.approx(GLOSSY_MATERIAL.ambient)
    assert glossy[0].lighting.diffuse == pytest.approx(GLOSSY_MATERIAL.diffuse)
    assert glossy[0].lighting.specular == pytest.approx(GLOSSY_MATERIAL.specular)
    assert (
        glossy[0].lighting.ambient + glossy[0].lighting.diffuse > 1.0
    )
