from __future__ import annotations

from mat_viewer.renderer import _minor_opacity_for


def test_disorder_opacity_is_independent_from_minor_identity():
    assert _minor_opacity_for({"disorder": "opacity", "minor_opacity": 0.25}, True) == 0.25
    assert _minor_opacity_for({"disorder": "outline_rings", "minor_opacity": 0.25}, True) == 1.0
    assert _minor_opacity_for({"disorder": "none", "major_opacity": 0.8}, False) == 0.8
