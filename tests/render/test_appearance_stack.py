"""The four appearance axes stay one contract when the stack is together.

Representation, surface material, lighting, and the isosurface adapter are
separate. This file is the joint check that a later merge still has one
LightingRig, one surface library, and one radius read for van der Waals.
"""

from __future__ import annotations

import numpy as np
import pytest

import mat_viewer.render.surface_material as surface_material_module
from mat_viewer import config
from mat_viewer.render.contracts import RenderSpec
from mat_viewer.render.isosurface_material import (
    IsosurfaceShadingUnsupported,
    plotly_lighting,
    preset_material,
)
from mat_viewer.render.lighting import (
    STUDIO_SOFT_DIRECTION,
    CpuLightingError,
    get_lighting_rig,
    read_surface_response,
    require_cpu_lighting,
    shade_with_rig,
)
from mat_viewer.render.radius import display_sphere_radius
from mat_viewer.render.representations import (
    REPRESENTATIONS,
    SPHERE_ONLY_REPRESENTATIONS,
)
from mat_viewer.render.surface_material import (
    get_surface_material,
)


def test_each_axis_keeps_its_owner() -> None:
    assert not hasattr(surface_material_module, "LightingRig")
    assert get_lighting_rig("studio_soft").direction == STUDIO_SOFT_DIRECTION
    assert STUDIO_SOFT_DIRECTION == (-0.32, 0.42, 1.0)
    spec = RenderSpec(
        representation="vdw",
        surface_material="matte",
        lighting="unlit",
    )
    assert spec.representation == "vdw"
    assert spec.surface_material == "matte"
    assert spec.lighting == "unlit"
    assert spec.shading == "smooth"
    assert spec.isosurface_stride == 2
    assert spec.isosurface_smooth is False
    assert "vdw" in SPHERE_ONLY_REPRESENTATIONS
    assert "flat_illustration" in REPRESENTATIONS
    assert "flat" not in REPRESENTATIONS
    with pytest.raises(CpuLightingError, match="will not substitute studio_soft"):
        require_cpu_lighting(get_lighting_rig("studio_three_point"))


def test_isosurface_presets_copy_the_surface_library() -> None:
    for name in ("matte", "hard_plastic", "metal_like", "glass_like"):
        library = get_surface_material(name)
        copied = preset_material(name)
        assert (copied.ambient, copied.diffuse, copied.specular, copied.shininess) == (
            library.ambient,
            library.diffuse,
            library.specular,
            library.shininess,
        )
        assert copied.specular_tint == library.specular_tint
        assert "opacity" not in copied.response_mapping()
        assert "light_scale" not in copied.response_mapping()
        assert "id" not in copied.response_mapping()
    glass = preset_material("glass_like")
    with pytest.raises(CpuLightingError, match="Fresnel"):
        read_surface_response({"surface_material": glass.response_mapping()})
    with pytest.raises(IsosurfaceShadingUnsupported, match="Fresnel"):
        plotly_lighting(glass)
    metal = preset_material("metal_like")
    with pytest.raises(IsosurfaceShadingUnsupported, match="specular_tint"):
        plotly_lighting(metal)


def test_unlit_does_not_rewrite_a_surface_record() -> None:
    plastic = get_surface_material("hard_plastic")
    record = plastic.coefficient_record()
    response = read_surface_response({"surface_material": record})
    base = np.array([0.2, 0.5, 0.8])
    shaded = shade_with_rig(
        base,
        np.array([0.0, 0.0, 1.0]),
        response,
        get_lighting_rig("unlit"),
    )
    assert np.allclose(shaded, base)
    assert plastic.coefficient_record() == record


def test_van_der_waals_radius_is_read_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}
    original = config.vdw_radius

    def counted(element: str) -> float:
        calls["count"] += 1
        return original(element)

    monkeypatch.setattr(config, "vdw_radius", counted)
    radius = display_sphere_radius(
        {"symbol": "C", "atom_radius": 0.18},
        {"style": "vdw", "atom_scale": 1.25},
    )
    assert calls["count"] == 1
    assert radius == pytest.approx(original("C") * 1.25)
