from __future__ import annotations

from types import MappingProxyType
from typing import Mapping


def _frozen(data: dict) -> Mapping:
    return MappingProxyType(dict(data))


# Main MatterVis scene palette (muted / print-safe).
ELEMENT_COLORS = _frozen(
    {
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
        "default": "#808080",
    }
)

ELEMENT_COLORS_LIGHT = _frozen(
    {
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
        "default": "#B0B0B0",
    }
)

ATOM_RADIUS = _frozen(
    {
        "C": 0.18,
        "N": 0.18,
        "O": 0.17,
        "Cl": 0.24,
        "H": 0.08,
        "Cu": 0.22,
        "Fe": 0.22,
        "Ni": 0.22,
        "Co": 0.22,
        "Zn": 0.22,
        "default": 0.18,
    }
)

COVALENT_RADIUS = _frozen(
    {
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
)


# Gaussian cube / static orbital helper tables. These defaults intentionally
# stay separate from the muted scene palette because orbital panels historically
# used a brighter CPK-like palette.
CUBE_ELEMENT_SYMBOLS = _frozen(
    {
        1: "H",
        6: "C",
        7: "N",
        8: "O",
        9: "F",
        15: "P",
        16: "S",
        17: "Cl",
        26: "Fe",
        27: "Co",
        28: "Ni",
        29: "Cu",
        30: "Zn",
        35: "Br",
        53: "I",
    }
)

CUBE_ELEMENT_COLORS = _frozen(
    {
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
)

CUBE_COVALENT_RADII_ANG = _frozen(
    {
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
)

CUBE_ATOM_DISPLAY_RADII_ANG = _frozen(
    {
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
)

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


__all__ = [name for name in globals() if not name.startswith("_")]
