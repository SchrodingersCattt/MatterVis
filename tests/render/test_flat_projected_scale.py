from __future__ import annotations

import pytest

from crystal_viewer.render.traces_atoms import _atom_scatter_traces, _bond_scatter_traces
from crystal_viewer.render.viewport import flat_projected_pixel_scale


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


def test_flat_atom_and_bond_sizes_share_projected_viewport_scale():
    style = _style()
    wide = _scene(10.0)
    close = _scene(5.0)

    wide_scale = flat_projected_pixel_scale(wide, style)
    close_scale = flat_projected_pixel_scale(close, style)
    assert close_scale == pytest.approx(2.0 * wide_scale)

    wide_atom = _trace_by_role(_atom_scatter_traces(wide, style), "atom")
    close_atom = _trace_by_role(_atom_scatter_traces(close, style), "atom")
    wide_bond = _trace_by_role(_bond_scatter_traces(wide, style), "bond")
    close_bond = _trace_by_role(_bond_scatter_traces(close, style), "bond")

    assert close_atom["marker"]["size"][0] == pytest.approx(2.0 * wide_atom["marker"]["size"][0])
    assert close_bond["line"]["width"] == pytest.approx(2.0 * wide_bond["line"]["width"])
