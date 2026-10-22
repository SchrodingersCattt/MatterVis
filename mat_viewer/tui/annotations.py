"""Terminal annotations: label-mode vocabulary, copy-aware labels, axis footer.

Kept apart from :mod:`.compositor` so the label/overlay policy can grow
without touching the ORTEP drawing pipeline. Everything here is derived from
the rendered camera and the ``AtomIR`` copy identity (symmetry operation and
lattice image shift); nothing reads chemistry records.
"""

from __future__ import annotations

import math
import re

import numpy as np

from .projection import Viewport

LABEL_MODES = ("auto", "label", "element", "molecule", "dot", "label_sym")

# One-line meaning of every mode; reused by validation errors and docs.
LABEL_MODE_HELP = {
    "auto": "label, element or dot chosen from atom density",
    "label": "source site label (symmetry copies share it)",
    "element": "element symbol",
    "molecule": "site label plus displayed molecule index",
    "dot": "marker only, no text",
    "label_sym": "site label plus copy suffix: #k = symmetry operation k "
    "(1-based input order; the first listed operation, index 0, is omitted "
    "even when it is not x,y,z), [i,j,k] = lattice shift from the home-cell copy",
}

# Common caller spellings mapped onto a canonical mode before validation.
LABEL_MODE_ALIASES = {"none": "dot", "off": "dot", "labels": "label", "copy": "label_sym"}

_IMAGE_SUFFIX = re.compile(r"\[-?\d+,-?\d+,-?\d+\]$")


def normalize_label_mode(label_mode) -> str:
    """Return the canonical label mode or raise a self-describing error."""
    if isinstance(label_mode, str):
        key = label_mode.strip().lower()
        mode = LABEL_MODE_ALIASES.get(key, key)
        if mode in LABEL_MODES:
            return mode
    modes = "; ".join(f"{name}: {text}" for name, text in LABEL_MODE_HELP.items())
    raise ValueError(
        f"label_mode must be one of {LABEL_MODES} (got {label_mode!r}). {modes}"
    )


def copy_aware_label(atom) -> str:
    """Site label that distinguishes symmetry and lattice copies of one site."""
    base = _IMAGE_SUFFIX.sub("", atom.display_label) or atom.element
    operation = int(atom.symmetry_operation_index)
    text = base if operation <= 0 else f"{base}#{operation + 1}"
    shift = tuple(int(value) for value in atom.image_shift)
    if shift != (0, 0, 0):
        text += f"[{shift[0]},{shift[1]},{shift[2]}]"
    return text


_COMPASS = ("E", "NE", "N", "NW", "W", "SW", "S", "SE")
_SCALE_BAR_LENGTHS = (1.0, 2.0, 5.0, 10.0, 20.0, 50.0)


def axes_footer_text(crystal, camera, viewport: Viewport, width: int) -> str:
    """Describe screen directions of a, b, c and a length scale in one line.

    Each lattice vector is reported as a compass direction on screen
    (N = up, E = right) or ``out`` / ``in`` when it points mostly toward or
    away from the viewer. The scale bar is exact for orthographic views and
    measured at the camera target for perspective views.
    """
    from ..math.camera import project_points as _proj

    rotation = np.asarray(camera.rotation_matrix, dtype=float)
    parts = []
    for name, vector in zip("abc", np.asarray(crystal.lattice.matrix, dtype=float)):
        x, y, z = rotation @ (vector / max(float(np.linalg.norm(vector)), 1e-12))
        if math.hypot(x, y) < abs(z):
            direction = "out" if z > 0 else "in"
        else:
            sector = round(math.degrees(math.atan2(y, x)) / 45.0) % 8
            direction = _COMPASS[sector]
        parts.append(f"{name}:{direction}")
    target = np.asarray(camera.target, dtype=float)
    ends, _ = _proj(camera, np.vstack([target, target + rotation[0]]))
    columns_per_angstrom = abs(float(ends[1][0] - ends[0][0])) * viewport.scale
    text = "axes " + " ".join(parts)
    fitting = [
        length
        for length in _SCALE_BAR_LENGTHS
        if 4 <= length * columns_per_angstrom <= max(width // 3, 4)
    ]
    if fitting:
        length = fitting[-1]
        bar = "|" + "-" * max(round(length * columns_per_angstrom) - 2, 0) + "|"
        text += f"  {bar} {length:g} A"
    return text[:width]


def overlay_axes_footer(frame, crystal, camera, viewport, width, height) -> str:
    """Keep the scene in the rows above a reserved axis/scale footer.

    The scene is drawn into ``height - 1`` rows. This appends the footer
    instead of painting over the last content row.
    """
    footer = axes_footer_text(crystal, camera, viewport, width)
    if height <= 1:
        return footer
    body = frame.split("\n")[: height - 1]
    body += [""] * max(height - 1 - len(body), 0)
    return "\n".join([*body, footer])


__all__ = [
    "LABEL_MODES",
    "LABEL_MODE_ALIASES",
    "LABEL_MODE_HELP",
    "axes_footer_text",
    "copy_aware_label",
    "normalize_label_mode",
    "overlay_axes_footer",
]
