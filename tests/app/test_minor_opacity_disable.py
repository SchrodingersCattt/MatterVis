from __future__ import annotations

import pytest

from mat_viewer.app import _minor_opacity_control_style, _minor_opacity_disabled


@pytest.mark.parametrize(
    ("disorder", "disabled"),
    [
        ("opacity", False),
        ("outline_rings", True),
        ("dashed_bonds", True),
        ("color_shift", True),
        ("none", True),
    ],
)
def test_minor_opacity_slider_is_only_enabled_for_opacity_disorder(disorder, disabled):
    assert _minor_opacity_disabled(disorder) is disabled


def test_minor_opacity_control_is_visually_dimmed_when_disabled():
    assert "opacity" not in _minor_opacity_control_style("opacity")
    assert _minor_opacity_control_style("outline_rings")["opacity"] == 0.4
