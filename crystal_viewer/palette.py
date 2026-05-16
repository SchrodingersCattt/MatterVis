from __future__ import annotations

# ── Element colours — Nature-style muted palette ────────────────────────────
# Inspired by CCDC Mercury / Nature structural biology figures:
# low saturation, print-safe, distinguishable in greyscale
ELEM_COLOR = {
    'C':  "#5E5E5E",   # dark charcoal gray
    'H':  "#DDDDDD",   # light gray
    'N':  "#2C61AF",   # muted steel blue
    'O':  "#B85060",   # muted brick red
    'Cl': "#218E6A",   # muted sage green
    'Cu': "#B87333",
    'Fe': "#B7410E",
    'Ni': "#4C8C4A",
    'Co': "#3F5FBF",
    'Zn': "#7D80B8",
    'default': '#808080',
}
ELEM_COLOR_LIGHT = {
    'C':  '#888888',   # medium gray (minor disorder)
    'H':  '#D8D8D8',
    'N':  '#8FADD4',   # lighter steel blue
    'O':  '#D48A88',   # lighter brick red
    'Cl': '#7DB88A',   # lighter sage green
    'Cu': '#D19A66',
    'Fe': '#D07A55',
    'Ni': '#82B57F',
    'Co': '#7F93D1',
    'Zn': '#A6A8D0',
    'default': '#B0B0B0',
}
# Atom display radii (Å) — used when no ADP available
ATOM_RADIUS = {'C': 0.18, 'N': 0.18, 'O': 0.17, 'Cl': 0.24, 'H': 0.08, 'Cu': 0.22, 'Fe': 0.22, 'Ni': 0.22, 'Co': 0.22, 'Zn': 0.22, 'default': 0.18}
COV_RADIUS   = {'C': 0.77, 'H': 0.31, 'N': 0.75, 'O': 0.73, 'Cl': 0.99, 'Cu': 1.32, 'Fe': 1.24, 'Ni': 1.21, 'Co': 1.26, 'Zn': 1.22}

def elem_color(s):       return ELEM_COLOR.get(s, ELEM_COLOR['default'])
def elem_color_light(s): return ELEM_COLOR_LIGHT.get(s, ELEM_COLOR_LIGHT['default'])
def atom_r(s):           return ATOM_RADIUS.get(s, ATOM_RADIUS['default'])
def cov_r(s):            return COV_RADIUS.get(s, 0.80)

def hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16)/255.0 for i in (0, 2, 4))

def hex_to_rgba(h, alpha=1.0):
    r, g, b = hex_to_rgb(h)
    return (r, g, b, alpha)


__all__ = [name for name in globals() if not name.startswith("__")]
