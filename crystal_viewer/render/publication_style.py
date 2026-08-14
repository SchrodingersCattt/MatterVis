"""Configuration and measured defaults for static publication figures."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

DENSE_COORDINATION_PRESET: dict[str, Any] = {
    "background": "#FFFFFF",
    "main": {
        "rect": [0.005, 0.280, 0.585, 0.675],
        "zoom": 1.23,
        "projection": "persp",
        "focal_length": 1.0,
    },
    "panels": {
        "layout": {
            "left": 0.030,
            "right": 0.030,
            "bottom": 0.012,
            "height": 0.247,
            "gap": 0.0575,
            "label_y": 0.264,
        },
        "center_alpha": 0.28,
        "center_radius": {"4": 0.38, "6": 0.38, "8": 0.38},
        "ligand_radius_front": 0.30,
        "ligand_radius_back": 0.20,
        "by_coordination": {
            "8": {
                "orientation": "raw",
                "zoom": 1.82,
            },
            "6": {
                "orientation": "first_two",
                "zoom": 1.82,
            },
            "4": {
                "orientation": "first_face",
                "in_plane_rotation": 35.0,
                "zoom": 1.42,
            },
        },
    },
    "materials": {
        "8": {
            "main": {
                "fill": "#4CB17A",
                "alpha": 0.52,
                "edge": "#315F4B",
                "edge_alpha": 0.52,
            },
            "panel": {
                "fill": "#41F288",
                "alpha": 0.52,
                "edge": "#3D7760",
                "edge_alpha": 0.72,
            },
        },
        "6": {
            "main": {
                "fill": "#8F50C2",
                "alpha": 0.65,
                "edge": "#59336D",
                "edge_alpha": 0.52,
            },
            "panel": {
                "fill": "#A352D7",
                "alpha": 0.53,
                "edge": "#68417D",
                "edge_alpha": 0.72,
            },
        },
        "4": {
            "main": {
                "fill": "#3D90CE",
                "alpha": 0.70,
                "edge": "#245D7D",
                "edge_alpha": 0.52,
            },
            "panel": {
                "fill": "#4E92D8",
                "alpha": 0.50,
                "edge": "#276D96",
                "edge_alpha": 0.72,
            },
        },
    },
    "lighting": {
        "polyhedron_shade_main": False,
        "polyhedron_shade_panel": False,
        "azimuth": 320.0,
        "altitude": 45.0,
    },
    "lines": {
        "main_edge_width": 0.34,
        "main_spoke_width": 0.24,
        "main_spoke_alpha": 0.30,
        "panel_edge_width": 0.72,
        "panel_spoke_width": 0.52,
        "panel_spoke_alpha": 0.55,
        "spoke_color": "#465852",
    },
    "atoms": {
        "ligand_color": "#FF6363",
        "ligand_radius_main": 0.20,
        "center_radius_default": 0.30,
        "sphere_detail_main": [12, 8],
        "sphere_detail_panel": [24, 14],
        "gloss_color": "#FFF7F7",
        "sphere_ambient": 0.72,
        "sphere_diffuse": 0.28,
        "sphere_clip_on": False,
    },
    "title": {"x": 0.290, "y": 0.982, "size": 9.2, "weight": "bold"},
    "panel_labels": {"size": 8.5, "weight": "bold"},
    "legend": {
        "rect": [0.620, 0.552, 0.300, 0.378],
        "title": "Legend",
        "title_x": 0.770,
        "title_y": 0.902,
        "title_size": 9.0,
        "icon_x": 0.666,
        "text_x": 0.701,
        "row_start": 0.859,
        "row_end": 0.597,
        "text_size": 8.4,
        "icon_height": 0.034,
        "footer_x": 0.770,
        "footer_y": 0.579,
        "footer_size": 6.0,
        "entries": [],
        "footer": "",
    },
    "compass": {
        "rect": [0.775, 0.365, 0.140, 0.140],
        "line_width": 2.0,
        "font_size": 9.5,
        "colors": ["#C7372F", "#22A660", "#2E86C1"],
    },
    "site_styles": [],
    "specs": {},
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def publication_config(style: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve the static publication preset and caller overrides."""
    requested = dict((style or {}).get("publication") or {})
    preset = str(requested.pop("preset", "dense_coordination"))
    if preset != "dense_coordination":
        raise ValueError(f"unknown publication preset: {preset}")
    return _deep_merge(DENSE_COORDINATION_PRESET, requested)
