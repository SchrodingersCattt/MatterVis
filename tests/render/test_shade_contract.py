"""Lighting rig and surface material share one coefficient meaning.

The VTK FigureShade evaluator on pull request 281 reads this same mapping.
It is not imported here. These checks lock the fields that evaluator accepts
and the capabilities this CPU path evaluates or rejects.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from mat_viewer.render.contracts import CameraSpec, RenderPlan, RenderSpec, ViewportPlan
from mat_viewer.render.cpu import render
from mat_viewer.render.geometry import sphere_primitive
from mat_viewer.render.lighting import (
    STUDIO_SOFT_DIRECTION,
    CpuLightingError,
    get_lighting_rig,
    read_surface_response,
    shade_with_rig,
)
from mat_viewer.render.surface_material import (
    MaterialSpec,
    get_surface_material,
    shade_blinn_phong,
)

# evaluate_vtk_shade accepts these keys and rejects id, opacity, and light_scale.
SHARED_RESPONSE_FIELDS = frozenset(
    {
        "ambient",
        "diffuse",
        "specular",
        "shininess",
        "specular_tint",
        "specular_color",
        "fresnel_power",
        "fresnel_center",
        "fresnel_rim",
        "fresnel_specular",
        "fresnel_albedo",
    }
)


def test_historical_defaults_and_the_shared_light_direction() -> None:
    spec = RenderSpec()
    assert spec.lighting == "studio_soft"
    assert spec.surface_material is None
    assert spec.isosurface_stride == 2
    assert spec.isosurface_smooth is False
    assert (spec.isosurface_ambient, spec.isosurface_diffuse) == (0.68, 0.32)
    assert STUDIO_SOFT_DIRECTION == (-0.32, 0.42, 1.0)
    assert get_lighting_rig("studio_soft").direction == STUDIO_SOFT_DIRECTION
    assert get_lighting_rig("unlit").kind == "unlit"


def test_coefficient_record_matches_the_figure_shade_field_set() -> None:
    matte = get_surface_material("matte")
    plastic = get_surface_material("hard_plastic")
    metal = get_surface_material("metal_like")
    glass = get_surface_material("glass_like")
    for spec in (matte, plastic, metal, glass):
        record = spec.coefficient_record()
        assert set(record) <= SHARED_RESPONSE_FIELDS
        assert "id" not in record
        assert "opacity" not in record
        assert "light_scale" not in record
    assert matte.coefficient_record() == {
        "ambient": 0.68,
        "diffuse": 0.32,
        "specular": 0.0,
        "shininess": 32.0,
        "specular_tint": "none",
    }
    assert plastic.coefficient_record()["specular_tint"] == "white"
    assert plastic.coefficient_record()["specular_color"] == (1.0, 1.0, 1.0)
    assert metal.coefficient_record()["specular_tint"] == "base"
    assert "specular_color" not in metal.coefficient_record()
    assert glass.coefficient_record()["fresnel_power"] == 1.5
    for name in ("matte", "hard_plastic", "metal_like"):
        spec = get_surface_material(name)
        response = read_surface_response(
            {"surface_material": spec.coefficient_record()}
        )
        assert response.ambient == spec.ambient
        assert response.specular_tint == spec.specular_tint
    with pytest.raises(CpuLightingError, match="Fresnel"):
        read_surface_response({"surface_material": glass.coefficient_record()})


def test_unlit_returns_the_inherited_color_and_keeps_the_record() -> None:
    plastic = get_surface_material("hard_plastic")
    record = plastic.coefficient_record()
    response = read_surface_response({"surface_material": record})
    base = np.array([0.2, 0.4, 0.8])
    shaded = shade_with_rig(
        base,
        np.array([0.0, 0.0, 1.0]),
        response,
        get_lighting_rig("unlit"),
    )
    assert np.allclose(shaded, base)
    assert plastic.coefficient_record() == record
    with pytest.raises(CpuLightingError, match="will not substitute studio_soft"):
        shade_with_rig(
            base,
            np.array([0.0, 0.0, 1.0]),
            response,
            get_lighting_rig("studio_three_point"),
        )


def test_metal_like_specular_uses_the_base_color() -> None:
    metal = get_surface_material("metal_like")
    normal = np.array([[0.0, 0.2, 1.0]])
    red = shade_blinn_phong(
        np.array([[0.8, 0.1, 0.1]]), normal, metal, two_sided=False
    )
    blue = shade_blinn_phong(
        np.array([[0.1, 0.1, 0.8]]), normal, metal, two_sided=False
    )
    assert red[0, 0] > blue[0, 0]
    assert blue[0, 2] > red[0, 2]


def test_glass_like_fresnel_changes_the_translucent_picture() -> None:
    glass = get_surface_material("glass_like")
    neutral = MaterialSpec(
        ambient=glass.ambient,
        diffuse=glass.diffuse,
        specular=glass.specular,
        shininess=glass.shininess,
        specular_tint=glass.specular_tint,
        specular_color=glass.specular_color,
        id="glass_neutral",
    )
    rim = _png(_sphere(glass))
    flat = _png(_sphere(neutral))
    assert rim != flat


def _sphere(material: MaterialSpec):
    sphere = sphere_primitive(
        "shell",
        (0.0, 0.0, 0.0),
        0.7,
        "#66AACC",
        alpha=0.45,
        metadata={"kind": "isosurface", "surface_material": material.as_dict()},
    )
    camera = CameraSpec(
        position=(0.0, 0.0, 6.0),
        target=(0.0, 0.0, 0.0),
        up=(0.0, 1.0, 0.0),
        projection="orthographic",
        near=0.1,
        far=30.0,
        ortho_scale=1.4,
    )
    return RenderPlan(
        width=64,
        height=64,
        background=(0.0, 0.0, 0.0, 1.0),
        viewports=(
            ViewportPlan(semantic_id="main", camera=camera, primitives=(sphere,)),
        ),
    )


def _png(plan: RenderPlan) -> bytes:
    image = np.asarray(Image.open(io.BytesIO(render(plan, format="png").data)))
    assert image.shape[2] == 4
    assert np.any(image[..., 3] > 0)
    return image.tobytes()
