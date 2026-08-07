from __future__ import annotations

import pytest

from crystal_viewer.render.traces_atoms import _atom_scatter_traces, _bond_scatter_traces
from crystal_viewer.renderer import build_figure
from crystal_viewer.render.viewport import flat_visual_pixel_scale


def _style() -> dict:
    return {
        "material": "flat",
        "style": "ball_stick",
        "display_mode": "unit_cell",
        "projection": "orthographic",
        "atom_scale": 1.0,
        "bond_radius": 0.1,
        "scatter_atom_scale": 0.45,
        "scatter_bond_scale": 1.0,
        "flat_visual_pixel_scale": 30.0,
        "disorder": "opacity",
        "major_opacity": 1.0,
        "minor_opacity": 0.35,
        "show_minor_only": False,
        "axis_key_fig_width": 1000.0,
        "axis_key_fig_height": 800.0,
        "camera": {
            "eye": {"x": 0.0, "y": 0.0, "z": 1.8},
            "center": {"x": 0.0, "y": 0.0, "z": 0.0},
            "up": {"x": 0.0, "y": 1.0, "z": 0.0},
            "projection": {"type": "orthographic"},
        },
    }


def _scene(half_span: float) -> dict:
    return {
        "display_mode": "unit_cell",
        "viewport": {
            "x": [-half_span, half_span],
            "y": [-half_span, half_span],
            "z": [-half_span, half_span],
        },
        "view_direction": [0.0, 0.0, 1.0],
        "up": [0.0, 1.0, 0.0],
        "draw_atoms": [
            {
                "label": "C1",
                "elem": "C",
                "cart": [0.0, 0.0, 0.0],
                "atom_radius": 0.2,
                "color": "#112233",
                "color_light": "#AABBCC",
                "is_minor": False,
                "occ": 1.0,
                "uiso": 0.04,
                "U": None,
            },
        ],
        "bonds": [
            {
                "i": 0,
                "j": 0,
                "start": [0.0, 0.0, 0.0],
                "end": [0.8, 0.0, 0.0],
                "color_i": "#112233",
                "color_j": "#112233",
                "is_minor": False,
                "is_disordered": False,
                "occ": 1.0,
            },
        ],
    }


def _trace_by_role(traces: list[dict], role: str) -> dict:
    return next(trace for trace in traces if trace["meta"]["mv_role"] == role)


def test_flat_atom_and_bond_sizes_use_fixed_visual_scale_across_viewports():
    style = _style()
    wide = _scene(10.0)
    close = _scene(5.0)

    assert flat_visual_pixel_scale(style) == pytest.approx(30.0)
    wide_figure = build_figure(wide, style)
    close_figure = build_figure(close, style)
    assert wide_figure.layout.meta["flat_visual_pixel_scale"] == pytest.approx(30.0)
    assert close_figure.layout.meta["flat_visual_pixel_scale"] == pytest.approx(30.0)

    wide_atom = _trace_by_role(_atom_scatter_traces(wide, style), "atom")
    close_atom = _trace_by_role(_atom_scatter_traces(close, style), "atom")
    wide_bond = _trace_by_role(_bond_scatter_traces(wide, style), "bond")
    close_bond = _trace_by_role(_bond_scatter_traces(close, style), "bond")

    assert close_atom["marker"]["size"][0] == pytest.approx(wide_atom["marker"]["size"][0])
    assert close_bond["line"]["width"] == pytest.approx(wide_bond["line"]["width"])
    assert wide_atom["marker"]["size"][0] / wide_bond["line"]["width"] == pytest.approx(
        close_atom["marker"]["size"][0] / close_bond["line"]["width"]
    )
