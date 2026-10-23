"""Terminal frame assembly.

Atom level is the readable glyph picture in :mod:`.glyph_frame`: element
symbols, solid bonds, ``:`` cell edges, and a per-cell depth buffer.
Molecules use opaque silhouettes and short IDs on the same depth grid.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import numpy as np

# Label vocabulary lives in .annotations; LABEL_MODES etc. are re-exported for callers.
from .annotations import LABEL_MODE_ALIASES, LABEL_MODE_HELP, LABEL_MODES  # noqa: F401
from .annotations import axes_scene_height, bind_axes_footer, copy_aware_label, normalize_label_mode  # noqa: F401
from .braille import BrailleCanvas
from .projection import (
    Viewport,
    _compute_viewport,
    viewport_from_bounds,
)
from ..utils.colors import element_ansi_color  # noqa: F401  # framework compatibility
from .renderer import BOND_COLOR as BOND_COLOR  # noqa: F401  # framework overview
from .renderer import DEFAULT_COLOR, CELL_COLOR  # noqa: F401
from .text import ascii7_text, terminal_text
from .framework import (
    structure_is_framework_like,
)

if TYPE_CHECKING:
    from .crystal_ir import CrystalIR
    from ..math.camera import Camera


# ── Label modes ─────────────────────────────────────────────────────────────

_SUPERSCRIPTS = "⁰¹²³⁴⁵⁶⁷⁸⁹"


def _superscript(n: int) -> str:
    if n < 0:
        return ""
    return "".join(_SUPERSCRIPTS[int(c)] for c in str(n))


def _display_species_name(species_id: str) -> str:
    """Hide MCK's trailing graph-discriminator suffix in terminal labels."""
    safe_species_id = terminal_text(species_id)
    base, separator, suffix = safe_species_id.rpartition("_")
    if separator and base and suffix.isdigit():
        return base
    return safe_species_id


def _atom_label_text(atom, label_mode: str, charset: str = "unicode") -> str:
    formatter = ascii7_text if charset == "ascii7" else terminal_text
    if label_mode == "dot":
        return "*" if charset == "ascii7" else "●"
    elif label_mode == "element":
        return atom.element
    elif label_mode == "label":
        return formatter(atom.display_label)
    elif label_mode == "label_sym":
        return formatter(copy_aware_label(atom))
    elif label_mode == "molecule":
        base = formatter(atom.display_label)
        if atom.molecule_index >= 0:
            suffix = (
                str(atom.molecule_index)
                if charset == "ascii7"
                else _superscript(atom.molecule_index)
            )
            return base + suffix
        return base
    return formatter(atom.element)


def resolve_label_mode(
    label_mode: str,
    *,
    atom_count: int,
    width: int,
    height: int,
    zoom: float,
) -> str:
    """Resolve adaptive labels without changing the scientific atom set."""
    if label_mode != "auto":
        return label_mode
    _ = zoom  # Zoom currently changes framing, not automatic label policy.
    visible_cells = max(width * height, 1)
    density = atom_count / visible_cells
    if density >= 0.02 or atom_count > 32:
        return "element"
    return "label"


def atom_is_drawn(
    atom,
    *,
    show_minor: bool,
    show_hydrogen: bool,
    show_boundary: bool,
) -> bool:
    """Whether an atom participates in atom-level fit, draw, and auto labels."""
    if not show_minor and atom.is_minor:
        return False
    if not show_hydrogen and atom.element == "H":
        return False
    if not show_boundary and atom.is_boundary_replica:
        return False
    return True


def resolve_molecule_detail(
    *,
    molecule_count: int,
    width: int,
    height: int,
) -> str:
    """Choose hulls for sparse views and centroids for dense packing maps."""
    density = molecule_count / max(width * height, 1)
    if molecule_count > 32 or density >= 0.025:
        return "centroid"
    return "hull"


# Depth-tier helpers remain for the braille molecule / framework overviews.
_RADIUS_BASE = (5, 4, 3)
_RADIUS_H = (3, 2, 1)
_TIER_FRONT = 0
_TIER_MID = 1
_TIER_BACK = 2
_BACK_TIER_COLOR = 236  # dark grey (ANSI 256)


def _atom_radius(
    element: str,
    depth_tier: int = _TIER_MID,
    detail_scale: float = 1.0,
) -> int:
    """Depth-dependent atom circle radius (subpixels)."""
    if element == "H":
        base = _RADIUS_H[depth_tier]
    else:
        base = _RADIUS_BASE[depth_tier]
    return max(1, int(round(base * detail_scale)))


def _depth_tier(depth_val: float, depth_min: float, depth_max: float) -> int:
    """Classify depth into 3 tiers: front(0), mid(1), back(2)."""
    d_range = depth_max - depth_min
    if d_range < 1e-6:
        return _TIER_MID
    norm = (depth_val - depth_min) / d_range
    if norm >= 0.67:
        return _TIER_FRONT
    if norm >= 0.33:
        return _TIER_MID
    return _TIER_BACK


def _tier_color(element_color: int, tier: int) -> int:
    """Map an element color + depth tier to the rendered ANSI color."""
    if tier in {_TIER_FRONT, _TIER_MID}:
        return element_color
    return _BACK_TIER_COLOR


# ── Display levels ──────────────────────────────────────────────────────────

DISPLAY_LEVELS = ("atom", "molecule")
AUTO_DISPLAY_LEVEL = "auto"
# A whole-cell atom view stops being legible once each terminal cell is asked
# to carry several projected atoms.  The overview keeps the same camera and
# cell geometry while reducing discrete molecules or dense frameworks to a
# bounded set of readable markers and bonds.
AUTO_MOLECULE_ATOM_LIMIT = 64

def resolve_display_level(
    display_level: str,
    *,
    atom_count: int,
    molecule_count: int,
    framework_like: bool = False,
) -> str:
    """Resolve the default terminal scale without changing the source scope.

    ``auto`` is intentionally a construction-time choice.  The public state
    only contains the concrete level (``atom`` or ``molecule``), so a session
    can be replayed deterministically after it has been created.
    """
    if display_level == AUTO_DISPLAY_LEVEL:
        if atom_count > AUTO_MOLECULE_ATOM_LIMIT and (
            molecule_count > 1 or framework_like
        ):
            return "molecule"
        return "atom"
    if display_level not in DISPLAY_LEVELS:
        raise ValueError(f"display_level must be one of {DISPLAY_LEVELS} or 'auto'")
    return display_level


def resolve_display_level_for_crystal(
    display_level: str, crystal: "CrystalIR", *, show_minor: bool = False
) -> str:
    """Resolve a level using the crystal's manifested topology."""
    return resolve_display_level(
        display_level,
        atom_count=len(crystal.atoms),
        molecule_count=sum(len(indices) for indices in crystal.species_map.values()),
        framework_like=structure_is_framework_like(crystal, show_minor=show_minor),
    )


# ── Main compositor ─────────────────────────────────────────────────────────


def compose_frame(
    crystal: "CrystalIR",
    camera: "Camera",
    pts_2d: np.ndarray,
    depth: np.ndarray,
    *,
    width: int | None = None,
    height: int | None = None,
    mono: bool = False,
    label_mode: str = "label",
    show_bonds: bool = True,
    show_cell: bool = True,
    show_minor: bool = True,
    show_hydrogen: bool = False,
    show_boundary: bool = False,
    zoom: float = 1.0,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
    display_level: str = "atom",
    viewport: Viewport | None = None,
    selected_display_index: int | None = None,
    charset: str = "unicode",
    show_axes: bool = True,
    show_depth: bool = True,
    slice_axis: str = "off",
    slice_index: int = 0,
    slice_width: float = 1.0,
    ghost_points: np.ndarray | None = None,
) -> str:
    """Render crystal in ORTEP style with label relaxation.

    Parameters
    ----------
    display_level : str
        Visualization level: "atom" (default) or "molecule".
    show_axes : bool
        Reserve the bottom row for a lattice-axis triad and a scale bar.
    show_hydrogen, show_boundary : bool
        Atom-level drawing omits hydrogen and periodic boundary replicas
        unless these are set. Molecule overviews keep the braille picture.
    """
    if width is None or height is None:
        try:
            term_size = os.get_terminal_size()
            width = width or term_size.columns - 2
            height = height or term_size.lines - 4
        except OSError:
            width = width or 80
            height = height or 40
    width = max(width, 1)
    height = max(height, 1)
    full_height = height
    height = axes_scene_height(height, show_axes=show_axes, has_lattice=crystal.lattice is not None)
    finish_axes = bind_axes_footer(crystal, camera, width, full_height, height, show_depth=show_depth)
    from .depth import slice_distances, validate_slice

    validate_slice(crystal, slice_axis, slice_index, slice_width)
    distances = slice_distances(crystal, camera, slice_axis, slice_index, slice_width) if slice_axis != "off" else None
    if charset not in {"unicode", "ascii7"}:
        raise ValueError("charset must be 'unicode' or 'ascii7'")
    display_level = resolve_display_level(
        display_level,
        atom_count=len(crystal.atoms),
        molecule_count=sum(len(indices) for indices in crystal.species_map.values()),
        framework_like=structure_is_framework_like(crystal, show_minor=show_minor),
    )
    drawn_kwargs = {
        "show_minor": show_minor,
        "show_hydrogen": show_hydrogen,
        "show_boundary": show_boundary,
    }
    visible_atom_count = sum(
        atom_is_drawn(atom, **drawn_kwargs) for atom in crystal.atoms
    )
    resolved_label_mode = resolve_label_mode(
        label_mode,
        atom_count=visible_atom_count,
        width=width,
        height=height,
        zoom=zoom,
    )

    # ── Viewport ────────────────────────────────────────────────────────
    extra_pts: list[np.ndarray] = []
    fit_pts = pts_2d
    if viewport is None and len(pts_2d) > 0:
        # Static / one-shot fit must match the atom-level visibility rule.
        fit_mask = [
            atom_is_drawn(atom, **{**drawn_kwargs, "show_hydrogen": True if display_level == "molecule" else show_hydrogen})
            for atom in crystal.atoms[: len(pts_2d)]
        ]
        if any(fit_mask):
            fit_pts = pts_2d[np.asarray(fit_mask, dtype=bool)]
        else:
            fit_pts = np.empty((0, 2), dtype=float)

    if viewport is None:
        viewport = _compute_viewport(
            fit_pts, extra_pts, width, height, zoom, pan_x, pan_y
        )
    else:
        viewport = viewport_from_bounds(
            viewport.x_min,
            viewport.x_max,
            viewport.y_min,
            viewport.y_max,
            width,
            height,
            zoom=zoom,
            pan_x=pan_x,
            pan_y=pan_y,
        )
    if display_level != "molecule":
        from .glyph_frame import render_glyph_frame

        return finish_axes(
            render_glyph_frame(
                crystal,
                camera,
                pts_2d,
                depth,
                viewport,
                width=width,
                height=height,
                mono=mono,
                charset=charset,
                label_mode=resolved_label_mode,
                show_bonds=show_bonds,
                show_cell=show_cell,
                show_minor=show_minor,
                show_hydrogen=show_hydrogen,
                show_boundary=show_boundary,
                selected_display_index=selected_display_index,
                show_depth=show_depth,
                slice_distance=distances,
                ghost_points=ghost_points,
            ),
            viewport,
        )
    from .molecule_frame import render_molecule_frame, render_network_frame

    draw = render_network_frame if structure_is_framework_like(crystal, show_minor=show_minor) else render_molecule_frame
    return finish_axes(
        draw(crystal, camera, pts_2d, depth, viewport, width=width, height=height,
             mono=mono, show_cell=show_cell, show_minor=show_minor,
             show_boundary=show_boundary, selected_display_index=selected_display_index,
             charset=charset, show_depth=show_depth, slice_distance=distances,
             ghost_points=ghost_points, show_bonds=show_bonds),
        viewport,
    )

# ── Color-run helpers ───────────────────────────────────────────────────────


def _build_colored_braille_line(row_data: list[tuple[str, int]]) -> str:
    """Build an ANSI-colored line from per-cell (char, color) data."""
    # Strip trailing spaces
    last_non_space = -1
    for i in range(len(row_data) - 1, -1, -1):
        if row_data[i][0] != " ":
            last_non_space = i
            break
    if last_non_space < 0:
        return ""
    trimmed = row_data[: last_non_space + 1]
    return _color_run_to_ansi(trimmed)


def _color_run_to_ansi(cells: list[tuple[str, int]]) -> str:
    """Convert a list of (char, color) into ANSI escape sequences grouped by color runs."""
    if not cells:
        return ""
    parts: list[str] = []
    cur_color = cells[0][1] or CELL_COLOR
    cur_chars: list[str] = [cells[0][0]]

    for ch, color in cells[1:]:
        effective = color or CELL_COLOR
        if effective == cur_color:
            cur_chars.append(ch)
        else:
            text = "".join(cur_chars)
            parts.append(f"\033[38;5;{cur_color}m{text}\033[0m")
            cur_color = effective
            cur_chars = [ch]
    # Flush last run
    if cur_chars:
        text = "".join(cur_chars)
        parts.append(f"\033[38;5;{cur_color}m{text}\033[0m")
    return "".join(parts)


# Cube density lobes are not drawn on the glyph atom-level picture. Metadata
# may still carry density_blobs for a later braille or glyph overlay.


# ── Circle drawing ──────────────────────────────────────────────────────────


def _draw_circle(
    canvas: BrailleCanvas, cx: int, cy: int, r: int, *, color: int = 0
) -> None:
    """Solid circle via midpoint algorithm."""
    x, y, d = 0, r, 1 - r
    while x <= y:
        for px, py in _oct(cx, cy, x, y):
            canvas.set_pixel(px, py, color=color)
        x, y, d = _step(x, y, d)


def _oct(cx, cy, x, y):
    return (
        (cx + x, cy + y),
        (cx - x, cy + y),
        (cx + x, cy - y),
        (cx - x, cy - y),
        (cx + y, cy + x),
        (cx - y, cy + x),
        (cx + y, cy - x),
        (cx - y, cy - x),
    )


def _step(x, y, d):
    if d < 0:
        return x + 1, y, d + 2 * x + 3
    return x + 1, y - 1, d + 2 * (x - y) + 5
