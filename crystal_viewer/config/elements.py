from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from types import MappingProxyType

from ase.data import chemical_symbols, covalent_radii
from ase.data.colors import cpk_colors, jmol_colors


def _frozen(data: Mapping) -> Mapping:
    return MappingProxyType(dict(data))


def _rgb_to_hex(rgb) -> str:
    channels = []
    for value in rgb:
        channel = int(round(float(value) * 255.0))
        channels.append(max(0, min(255, channel)))
    return "#{:02X}{:02X}{:02X}".format(*channels)


def _lighten_hex(hex_color: str, factor: float = 0.50) -> str:
    value = hex_color.lstrip("#")
    if len(value) != 6:
        return hex_color
    channels = []
    for idx in (0, 2, 4):
        channel = int(value[idx : idx + 2], 16)
        channels.append(max(0, min(255, int(round(channel + (255 - channel) * factor)))))
    return "#{:02X}{:02X}{:02X}".format(*channels)


def _iter_symbols(max_z: int | None = None):
    upper = len(chemical_symbols) if max_z is None else min(len(chemical_symbols), max_z + 1)
    for atomic_number in range(1, upper):
        symbol = chemical_symbols[atomic_number]
        if symbol:
            yield atomic_number, symbol


def _finite_positive(value: float) -> bool:
    return isfinite(value) and value > 0


def _display_radius_from_covalent_radius(radius: float) -> float:
    return round(max(0.15, min(0.28, radius * 0.22)), 3)


def _cube_display_radius_from_covalent_radius(radius: float) -> float:
    return round(max(0.30, min(0.95, radius * 0.72)), 3)


def _jmol_color_for_atomic_number(atomic_number: int) -> str:
    if atomic_number < len(jmol_colors):
        return _rgb_to_hex(jmol_colors[atomic_number])
    return "#808080"


def _cpk_color_for_atomic_number(atomic_number: int) -> str:
    if atomic_number < len(cpk_colors):
        return _rgb_to_hex(cpk_colors[atomic_number])
    if atomic_number < len(jmol_colors):
        return _rgb_to_hex(jmol_colors[atomic_number])
    return "#999999"


# Main MatterVis scene palette (muted / print-safe) overrides.
_SCENE_COLOR_OVERRIDES = {
    "C": "#5E5E5E",
    "H": "#DDDDDD",
    "N": "#2C61AF",
    "O": "#B85060",
    "Cl": "#218E6A",
    "Cu": "#B87333",
    "Fe": "#B7410E",
    "Ni": "#4C8C4A",
    "Co": "#3F5FBF",
    "Zn": "#7D80B8",
}

_SCENE_LIGHT_OVERRIDES = {
    "C": "#888888",
    "H": "#D8D8D8",
    "N": "#8FADD4",
    "O": "#D48A88",
    "Cl": "#7DB88A",
    "Cu": "#D19A66",
    "Fe": "#D07A55",
    "Ni": "#82B57F",
    "Co": "#7F93D1",
    "Zn": "#A6A8D0",
}

_SCENE_DISPLAY_RADIUS_OVERRIDES = {
    "C": 0.18,
    "N": 0.18,
    "O": 0.17,
    "Cl": 0.24,
    # Hydrogen needs a visual-size override rather than a literal covalent
    # scaling; otherwise H spheres are smaller than the bond cylinders in
    # ball-stick scenes and become nearly invisible.
    "H": 0.16,
    "Cu": 0.22,
    "Fe": 0.22,
    "Ni": 0.22,
    "Co": 0.22,
    "Zn": 0.22,
}

_SCENE_COVALENT_RADIUS_OVERRIDES = {
    "C": 0.77,
    "H": 0.31,
    "N": 0.75,
    "O": 0.73,
    "Cl": 0.99,
    "Cu": 1.32,
    "Fe": 1.24,
    "Ni": 1.21,
    "Co": 1.26,
    "Zn": 1.22,
}

# Gaussian cube / static orbital helper overrides. These defaults intentionally
# stay separate from the muted scene palette because orbital panels historically
# used a brighter CPK-like palette.
_CUBE_COLOR_OVERRIDES = {
    "H": "#DDDDDD",
    "C": "#909090",
    "N": "#3050F8",
    "O": "#FF0D0D",
    "F": "#90E050",
    "P": "#FF8000",
    "S": "#FFD43B",
    "Cl": "#1FF01F",
    "Cu": "#C77800",
    "Fe": "#B7410E",
    "Ni": "#4C8C4A",
    "Co": "#3F5FBF",
    "Zn": "#7D80B8",
    "Br": "#A52A2A",
    "I": "#940094",
}

_CUBE_COVALENT_RADIUS_OVERRIDES = {
    "H": 0.31,
    "C": 0.76,
    "N": 0.71,
    "O": 0.66,
    "F": 0.57,
    "P": 1.07,
    "S": 1.05,
    "Cl": 1.02,
    "Cu": 1.32,
    "Fe": 1.24,
    "Ni": 1.21,
    "Co": 1.26,
    "Zn": 1.22,
    "Br": 1.20,
    "I": 1.39,
}

_CUBE_DISPLAY_RADIUS_OVERRIDES = {
    "H": 0.30,
    "C": 0.55,
    "N": 0.55,
    "O": 0.55,
    "F": 0.50,
    "P": 0.75,
    "S": 0.75,
    "Cl": 0.70,
    "Cu": 0.85,
    "Fe": 0.82,
    "Ni": 0.82,
    "Co": 0.82,
    "Zn": 0.82,
    "Br": 0.85,
    "I": 0.95,
}


def _build_element_colors() -> Mapping[str, str]:
    data: dict[str, str] = {}
    # ASE's Jmol table covers H through Mt. Use it as the full-scene base and
    # keep MatterVis's muted, print-safe palette as an explicit override layer.
    # Elements beyond ASE's Jmol table still receive explicit default entries so
    # the config covers the whole periodic table exposed by ASE.
    for atomic_number, symbol in _iter_symbols():
        data[symbol] = _jmol_color_for_atomic_number(atomic_number)
    data.update(_SCENE_COLOR_OVERRIDES)
    data["default"] = "#808080"
    return _frozen(data)


def _build_element_colors_light() -> Mapping[str, str]:
    base = _build_element_colors()
    data = {symbol: _lighten_hex(color) for symbol, color in base.items() if symbol != "default"}
    data.update(_SCENE_LIGHT_OVERRIDES)
    data["default"] = "#B0B0B0"
    return _frozen(data)


def _build_covalent_radii() -> Mapping[str, float]:
    data: dict[str, float] = {}
    for atomic_number, symbol in _iter_symbols(max_z=len(covalent_radii) - 1):
        radius = float(covalent_radii[atomic_number])
        if _finite_positive(radius):
            data[symbol] = radius
    data.update(_SCENE_COVALENT_RADIUS_OVERRIDES)
    return _frozen(data)


def _build_atom_radius() -> Mapping[str, float]:
    covalent = _build_covalent_radii()
    data = {
        symbol: _display_radius_from_covalent_radius(radius)
        for symbol, radius in covalent.items()
        if symbol != "default"
    }
    data.update(_SCENE_DISPLAY_RADIUS_OVERRIDES)
    data["default"] = 0.18
    return _frozen(data)


def _build_cube_element_symbols() -> Mapping[int, str]:
    return _frozen({atomic_number: symbol for atomic_number, symbol in _iter_symbols()})


def _build_cube_element_colors() -> Mapping[str, str]:
    data: dict[str, str] = {}
    for atomic_number, symbol in _iter_symbols():
        data[symbol] = _cpk_color_for_atomic_number(atomic_number)
    data.update(_CUBE_COLOR_OVERRIDES)
    data["default"] = "#999999"
    return _frozen(data)


def _build_cube_covalent_radii() -> Mapping[str, float]:
    data: dict[str, float] = {}
    for atomic_number, symbol in _iter_symbols(max_z=len(covalent_radii) - 1):
        radius = float(covalent_radii[atomic_number])
        if _finite_positive(radius):
            data[symbol] = radius
    data.update(_CUBE_COVALENT_RADIUS_OVERRIDES)
    return _frozen(data)


def _build_cube_display_radii() -> Mapping[str, float]:
    covalent = _build_cube_covalent_radii()
    data = {
        symbol: _cube_display_radius_from_covalent_radius(radius)
        for symbol, radius in covalent.items()
        if symbol != "default"
    }
    data.update(_CUBE_DISPLAY_RADIUS_OVERRIDES)
    data["default"] = 0.55
    return _frozen(data)


ELEMENT_COLORS = _build_element_colors()
ELEMENT_COLORS_LIGHT = _build_element_colors_light()
ATOM_RADIUS = _build_atom_radius()
COVALENT_RADIUS = _build_covalent_radii()

CUBE_ELEMENT_SYMBOLS = _build_cube_element_symbols()
CUBE_ELEMENT_COLORS = _build_cube_element_colors()
CUBE_COVALENT_RADII_ANG = _build_cube_covalent_radii()
CUBE_ATOM_DISPLAY_RADII_ANG = _build_cube_display_radii()

POLYHEDRON_AUTO_COLORS = (
    "#7C5CBF",
    "#E07C24",
    "#1F77B4",
    "#2CA02C",
    "#D62728",
    "#9467BD",
    "#17BECF",
    "#BCBD22",
)

SELECTION_HIGHLIGHT = "#FFD24A"

__all__ = [
    "ATOM_RADIUS",
    "COVALENT_RADIUS",
    "CUBE_ATOM_DISPLAY_RADII_ANG",
    "CUBE_COVALENT_RADII_ANG",
    "CUBE_ELEMENT_COLORS",
    "CUBE_ELEMENT_SYMBOLS",
    "ELEMENT_COLORS",
    "ELEMENT_COLORS_LIGHT",
    "POLYHEDRON_AUTO_COLORS",
    "SELECTION_HIGHLIGHT",
]
