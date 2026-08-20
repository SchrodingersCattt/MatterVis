from __future__ import annotations

from mat_viewer.loader import build_empty_bundle
from mat_viewer.renderer import build_figure


def test_topology_disabled_styles_do_not_require_topology_payload():
    scene = build_empty_bundle().scene
    scene["draw_atoms"] = [
        {
            "label": "C1",
            "elem": "C",
            "cart": [0.0, 0.0, 0.0],
            "atom_radius": 0.18,
            "color": "#555555",
            "color_light": "#888888",
            "is_minor": False,
            "uiso": 0.04,
            "U": None,
        }
    ]
    for render_style in ("ball_stick", "ortep", "wireframe"):
        fig = build_figure(
            scene,
            {
                "material": "mesh",
                "style": render_style,
                "disorder": "opacity",
                "atom_scale": 1.0,
                "bond_radius": 0.1,
                "axis_scale": 0.1,
                "show_axes": False,
                "show_labels": False,
                "topology_enabled": False,
            },
        )
        assert fig.data
