from __future__ import annotations

import numpy as np

from crystal_viewer.loader import build_empty_bundle
from crystal_viewer.presets import DEFAULT_STYLE
from crystal_viewer.renderer import build_figure


def _scene_with_predecorated_bond():
    scene = build_empty_bundle().scene
    scene["draw_atoms"] = [
        {
            "label": "C1",
            "elem": "C",
            "cart": np.array([0.0, 0.0, 0.0]),
            "atom_radius": 0.18,
            "color": "#555555",
            "color_light": "#888888",
            "is_minor": False,
            "U": None,
            "uiso": 0.04,
        },
        {
            "label": "O1",
            "elem": "O",
            "cart": np.array([1.2, 0.0, 0.0]),
            "atom_radius": 0.17,
            "color": "#B85060",
            "color_light": "#D48A88",
            "is_minor": False,
            "U": None,
            "uiso": 0.04,
        },
    ]
    scene["bonds"] = [
        {
            "i": 0,
            "j": 1,
            "start": np.array([0.0, 0.0, 0.0]),
            "end": np.array([1.2, 0.0, 0.0]),
            "color_i": "#555555",
            "color_j": "#B85060",
            "is_minor": False,
            "_render_opacity_scale": 0.4,
        }
    ]
    return scene


def _bond_opacities(fig):
    out = []
    for trace in fig.to_dict().get("data", []):
        meta = trace.get("meta") if isinstance(trace.get("meta"), dict) else {}
        if meta.get("mv_role") == "bond":
            out.append(float(trace.get("opacity", 1.0)))
    return out


def test_bond_opacity_scale_survives_mesh_cache_replay():
    scene = _scene_with_predecorated_bond()
    style = {
        **DEFAULT_STYLE,
        "material": "mesh",
        "style": "ball_stick",
        "disorder": "outline_rings",
        "show_axes": False,
        "show_axis_key": False,
        "show_labels": False,
        "topology_enabled": False,
    }

    first = _bond_opacities(build_figure(scene, style))
    second = _bond_opacities(build_figure(scene, style))

    assert first == second
    assert first
    assert all(opacity == 0.4 for opacity in second)
