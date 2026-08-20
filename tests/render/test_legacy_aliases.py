from __future__ import annotations

from mat_viewer.renderer import style_from_controls


def test_style_from_controls_translates_legacy_aliases():
    style = style_from_controls(1.0, 0.16, 0.4, 0.14, ["minor_wireframe"])
    assert style["disorder"] == "outline_rings"

    style = style_from_controls(1.0, 0.16, 0.4, 0.14, ["fast_rendering"])
    assert style["material"] == "flat"
    assert style["fast_rendering"] is True

    style = style_from_controls(1.0, 0.16, 0.4, 0.14, [], material="mesh", render_style="ortep", disorder="none")
    assert style["material"] == "mesh"
    assert style["style"] == "ortep"
    assert style["disorder"] == "none"
