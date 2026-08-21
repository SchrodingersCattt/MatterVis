from __future__ import annotations

from ..config import atom_radius, covalent_radius, element_color
from ..config import colors as _colors

ELEM_COLOR = _colors.ELEMENT_COLORS
ELEM_COLOR_LIGHT = _colors.ELEMENT_COLORS_LIGHT
ATOM_RADIUS = _colors.ATOM_RADIUS
COV_RADIUS = _colors.COVALENT_RADIUS

def elem_color(s):       return element_color(s)
def elem_color_light(s): return element_color(s, light=True)
def atom_r(s):           return atom_radius(s)
def cov_r(s):            return covalent_radius(s)

def hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16)/255.0 for i in (0, 2, 4))

def hex_to_rgba(h, alpha=1.0):
    r, g, b = hex_to_rgb(h)
    return (r, g, b, alpha)


__all__ = [name for name in globals() if not name.startswith("__")]
