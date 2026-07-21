from __future__ import annotations

import copy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from . import colors


def _freeze_mapping(data: Mapping[str, Any]) -> Mapping[str, Any]:
    frozen: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, Mapping):
            frozen[str(key)] = _freeze_mapping(value)
        elif isinstance(value, list):
            frozen[str(key)] = tuple(value)
        elif isinstance(value, tuple):
            frozen[str(key)] = tuple(value)
        else:
            frozen[str(key)] = value
    return MappingProxyType(frozen)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return copy.deepcopy(value)


@dataclass(frozen=True)
class ConfigSection:
    values: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", _freeze_mapping(self.values))

    def as_dict(self) -> dict[str, Any]:
        return _plain(self.values)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


@dataclass(frozen=True)
class Config:
    style: ConfigSection
    colors: ConfigSection
    cube: ConfigSection
    mck_overrides: ConfigSection
    source_paths: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "style": self.style.as_dict(),
            "colors": self.colors.as_dict(),
            "cube": self.cube.as_dict(),
            "mck_overrides": self.mck_overrides.as_dict(),
            "source_paths": list(self.source_paths),
        }


BUILTIN_STYLE: dict[str, Any] = {
    "display_mode": "formula_unit",
    "atom_scale": 1.0,
    "bond_radius": 0.15,
    "material": "mesh",
    "style": "ball_stick",
    "disorder": "outline_rings",
    "major_opacity": 1.0,
    "minor_opacity": 0.35,
    "minor_wireframe": False,
    "minor_bond_scale": 0.82,
    "show_labels": False,
    "show_axes": True,
    "show_title": True,
    "show_hydrogen": True,
    "show_unit_cell": True,
    "show_minor_only": False,
    "depth_cue_enabled": False,
    "projection": "perspective",
    "camera_eye_distance": 1.8,
    "background": "#FFFFFF",
    "label_color": "#111111",
    "minor_label_color": "#666666",
    "axis_scale": 0.14,
    "axis_color": "#666666",
    "axis_opacity": 0.72,
    "axes_labels": ["a", "b", "c"],
    "show_axis_key": False,
    "axis_key_anchor": [0.10, 0.18],
    "axis_key_row_gap": 0.095,
    "axis_key_arrow_len": 0.085,
    "axis_key_label_pad": 0.045,
    "axis_key_pixel_length": 50.0,
    "axis_key_label_pixel_offset": 10.0,
    "axis_key_arrow_head": 3,
    "axis_key_dot_threshold": 0.05,
    "axis_key_dot_radius_px": 4.0,
    "axis_key_font_size": 13,
    "axis_key_color": "#2F2F2F",
    "axis_key_label_order": ["c", "b", "a"],
    "axis_key_italic": True,
    "fast_rendering": False,
    "topology_enabled": False,
    "monochrome": False,
    "ortep_probability": 0.5,
    "ortep_mode": "ortep_solid",
    "ortep_mode_minor": None,
    "ortep_octant_shading": False,
    "ortep_octant_shadow_color": "#000000",
    "ortep_octant_shadow_alpha": 0.18,
    "ortep_octant_hatching": False,
    "ortep_octant_hatch_color": "#1A1A1A",
    "ortep_octant_hatch_linewidth": 1.4,
    "ortep_octant_hatch_lines": 5,
    "ortep_octant_hatch_arc_pts": 16,
    "ortep_octant_edge_color": "#0F0F0F",
    "ortep_octant_edge_linewidth": 1.9,
    "ortep_silhouette_outline": False,
    "ortep_silhouette_color": "#1A1A1A",
    "ortep_silhouette_linewidth": 1.4,
    "ortep_atom_fill": False,
    "ortep_atom_fill_color": "#FFFFFF",
    "ortep_z_lift_fill": 0.04,
    "ortep_z_lift_hatch": 0.06,
    "ortep_z_lift_outline": 0.07,
    # Mesh density for ORTEP ellipsoids and ball-stick spheres.
    # None = auto-LOD based on atom count; explicit int overrides.
    "ortep_lat_steps": None,
    "ortep_lon_steps": None,
    # Plotly Mesh3d lighting dict. None = Plotly default.
    # Example: {"ambient": 0.5, "diffuse": 0.9, "specular": 0.2, "roughness": 0.8, "fresnel": 0.06}
    "mesh_lighting": None,
    # Fixed sphere radius (Å) for H atoms in ORTEP mode. None = use ADP ellipsoid.
    "ortep_hydrogen_radius": None,
    "force_bond_color": "",
    "element_colors": {},
    "element_colors_light": {},
}

BUILTIN_COLORS: dict[str, Any] = {
    "elements": dict(colors.ELEMENT_COLORS),
    "elements_light": dict(colors.ELEMENT_COLORS_LIGHT),
    "atom_radius": dict(colors.ATOM_RADIUS),
    "covalent_radius": dict(colors.COVALENT_RADIUS),
    "polyhedron_auto": list(colors.POLYHEDRON_AUTO_COLORS),
    "topology_hull_default": "#7C5CBF",
    "bond_default": None,
    "background_default": "#FFFFFF",
    "selection_highlight": colors.SELECTION_HIGHLIGHT,
}

BUILTIN_CUBE: dict[str, Any] = {
    "element_symbols": dict(colors.CUBE_ELEMENT_SYMBOLS),
    "element_colors": dict(colors.CUBE_ELEMENT_COLORS),
    "covalent_radii_ang": dict(colors.CUBE_COVALENT_RADII_ANG),
    "atom_display_radii_ang": dict(colors.CUBE_ATOM_DISPLAY_RADII_ANG),
}

BUILTIN_MCK_OVERRIDES: dict[str, Any] = {
    "gap_threshold": None,
    "enclosure_expand_max": None,
    "default_search_cutoff": None,
    "vdw_radius_overrides": {},
}


def builtin_config(*, source_paths: tuple[str, ...] = ()) -> Config:
    return Config(
        style=ConfigSection(BUILTIN_STYLE),
        colors=ConfigSection(BUILTIN_COLORS),
        cube=ConfigSection(BUILTIN_CUBE),
        mck_overrides=ConfigSection(BUILTIN_MCK_OVERRIDES),
        source_paths=source_paths,
    )


__all__ = [name for name in globals() if not name.startswith("_")]
