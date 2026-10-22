"""Terminal frame assembly.

Atom level is the readable glyph picture in :mod:`.glyph_frame`: element
symbols, solid bonds, ``:`` cell edges, and a per-cell depth buffer.
Molecule and framework overviews still use the braille canvas.
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
from ..utils.colors import element_ansi_color
from .renderer import DEFAULT_COLOR, CELL_COLOR
from .text import ascii7_text, terminal_text
from .framework import (
    compose_framework_frame,
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
    if density >= 0.08 or atom_count > 160:
        return "dot"
    if density >= 0.02 or atom_count > 32:
        return "element"
    return "label"


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


# Dimmed color for molecule-overview braille hints behind hull edges.
_BACK_TIER_COLOR = 236  # dark grey (ANSI 256)


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
    show_axes: bool = False,
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
    finish_axes = bind_axes_footer(crystal, camera, width, full_height, height)
    if charset not in {"unicode", "ascii7"}:
        raise ValueError("charset must be 'unicode' or 'ascii7'")
    display_level = resolve_display_level(
        display_level,
        atom_count=len(crystal.atoms),
        molecule_count=sum(len(indices) for indices in crystal.species_map.values()),
        framework_like=structure_is_framework_like(crystal, show_minor=show_minor),
    )
    visible_atom_count = sum(show_minor or not atom.is_minor for atom in crystal.atoms)
    resolved_label_mode = resolve_label_mode(
        label_mode,
        atom_count=visible_atom_count,
        width=width,
        height=height,
        zoom=zoom,
    )

    # ── Viewport ────────────────────────────────────────────────────────
    extra_pts: list[np.ndarray] = []
    cell_verts_2d = None
    if show_cell and crystal.lattice is not None:
        from ..math.camera import project_points as _proj

        verts = crystal.lattice.cell_vertices()
        cell_verts_2d, _ = _proj(camera, verts)
        # The cell is drawn, but it does not expand the fit. Visible atoms
        # are what should fill the canvas.

    fit_pts = pts_2d
    if viewport is None and display_level != "molecule" and len(pts_2d) > 0:
        # Static / one-shot fit must match the atom-level visibility rule.
        fit_mask = [
            (show_minor or not atom.is_minor)
            and (show_hydrogen or atom.element != "H")
            and (show_boundary or not atom.is_boundary_replica)
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
            ),
            viewport,
        )
    canvas = BrailleCanvas(width, height)

    # ── Layer 1: Cell edges (dashed braille) ────────────────────────────
    if show_cell and cell_verts_2d is not None:
        for i, j in crystal.lattice.cell_edges():
            sx, sy = viewport.to_px(cell_verts_2d[i][0], cell_verts_2d[i][1])
            ex, ey = viewport.to_px(cell_verts_2d[j][0], cell_verts_2d[j][1])
            canvas.draw_dashed_line(sx, sy, ex, ey, dash=5, gap=3, color=CELL_COLOR)

    # ── Dispatch to display-level-specific rendering ───────────────────
    if structure_is_framework_like(crystal, show_minor=show_minor):
        return finish_axes(
            compose_framework_frame(
                crystal,
                camera,
                pts_2d,
                depth,
                viewport,
                canvas,
                width,
                height,
                mono,
                show_bonds,
                show_minor,
                selected_display_index,
                charset,
            ),
            viewport,
        )
    return finish_axes(
        _compose_molecule_frame(
            crystal,
            camera,
            pts_2d,
            depth,
            viewport,
            canvas,
            width,
            height,
            mono,
            show_minor,
            selected_display_index,
            charset,
        ),
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


# ── Molecule-level rendering ────────────────────────────────────────────────

# Qualitative palette for molecule species (ANSI 256 high-contrast hues)
MOLECULE_COLORS = (
    39,  # deep sky blue
    208,  # orange
    120,  # bright green
    201,  # magenta/pink
    226,  # yellow
    87,  # cyan
    160,  # dark red
    99,  # purple
    48,  # sea green
    214,  # amber
    63,  # slate blue
    190,  # lime
)


def _compose_molecule_frame(
    crystal: "CrystalIR",
    camera: "Camera",
    pts_2d: np.ndarray,
    depth: np.ndarray,
    viewport: "Viewport",
    canvas: "BrailleCanvas",
    width: int,
    height: int,
    mono: bool,
    show_minor: bool,
    selected_display_index: int | None,
    charset: str,
) -> str:
    """Render molecule-level view: convex hull outlines + formula labels."""
    from ._hull2d import convex_hull_2d

    # Build molecule → color mapping via species_map
    mol_color_map: dict[int, int] = {}
    color_idx = 0
    for species_key, mol_indices in crystal.species_map.items():
        c = MOLECULE_COLORS[color_idx % len(MOLECULE_COLORS)]
        for mi in mol_indices:
            mol_color_map[mi] = c
        color_idx += 1

    # Group atoms by molecule_index
    mol_groups: dict[int, list[int]] = {}  # mol_idx → [atom_indices]
    for idx, atom in enumerate(crystal.atoms):
        if atom.molecule_index >= 0:
            if not show_minor and atom.is_minor:
                continue
            mol_groups.setdefault(atom.molecule_index, []).append(idx)
    molecule_detail = resolve_molecule_detail(
        molecule_count=len(mol_groups),
        width=width,
        height=height,
    )

    # Reverse species_map for labeling: mol_idx → species formula
    mol_formula: dict[int, str] = {}
    for species_key, mol_indices in crystal.species_map.items():
        for mi in mol_indices:
            mol_formula[mi] = species_key

    # Sort molecules by average depth (back-to-front)
    mol_depths: list[tuple[int, float]] = []
    for mol_idx, atom_indices in mol_groups.items():
        avg_d = sum(float(depth[i]) for i in atom_indices) / len(atom_indices)
        mol_depths.append((mol_idx, avg_d))
    mol_depths.sort(key=lambda x: x[1])  # far first

    # Draw each molecule outline
    label_candidates: dict[str, list[tuple[float, int, int, int]]] = {}
    for mol_idx, molecule_depth in mol_depths:
        atom_indices = mol_groups[mol_idx]
        if len(atom_indices) < 2:
            continue

        color = mol_color_map.get(
            mol_idx, MOLECULE_COLORS[mol_idx % len(MOLECULE_COLORS)]
        )

        # Get 2D projected coordinates for this molecule's atoms
        mol_pts = [(float(pts_2d[i][0]), float(pts_2d[i][1])) for i in atom_indices]

        # Centroid for label placement
        cx = sum(x for x, y in mol_pts) / len(mol_pts)
        cy = sum(y for x, y in mol_pts) / len(mol_pts)
        if molecule_detail == "centroid":
            px_x, px_y = viewport.to_px(cx, cy)
            _draw_circle(canvas, px_x, px_y, 2, color=color)
        else:
            # Draw individual atom hints and a projected molecular hull.
            for x, y in mol_pts:
                px_x, px_y = viewport.to_px(x, y)
                canvas.set_pixel(px_x, px_y, color=_BACK_TIER_COLOR)
            if len(mol_pts) < 3:
                p0 = viewport.to_px(*mol_pts[0])
                p1 = viewport.to_px(*mol_pts[1])
                canvas.draw_line(p0[0], p0[1], p1[0], p1[1], color=color)
            else:
                hull_indices = convex_hull_2d(mol_pts)
                if len(hull_indices) >= 3:
                    for k in range(len(hull_indices)):
                        i0 = hull_indices[k]
                        i1 = hull_indices[(k + 1) % len(hull_indices)]
                        p0 = viewport.to_px(*mol_pts[i0])
                        p1 = viewport.to_px(*mol_pts[i1])
                        canvas.draw_line(p0[0], p0[1], p1[0], p1[1], color=color)

        row, col = viewport.to_grid(cx, cy)
        formula = mol_formula.get(mol_idx, f"M{mol_idx}")
        if charset == "ascii7":
            formula = ascii7_text(formula)
        label_candidates.setdefault(formula, []).append(
            (molecule_depth, row, col, color)
        )

    # One label per species is enough to identify the full unit-cell packing;
    # choose the front-most instance so text does not cover every replica.
    labels_to_place: list[tuple[int, int, str, int]] = []
    for formula, candidates in label_candidates.items():
        _, row, col, color = max(candidates, key=lambda item: item[0])
        labels_to_place.append((row, col, _display_species_name(formula), color))

    # Molecule-level views still expose the exact selected atom.  The marker
    # is intentionally textual so it survives monochrome terminals.
    if selected_display_index is not None and 0 <= selected_display_index < len(
        crystal.atoms
    ):
        atom = crystal.atoms[selected_display_index]
        if show_minor or not atom.is_minor:
            row, col = viewport.to_grid(*pts_2d[selected_display_index])
            labels_to_place.append(
                (
                    row,
                    col,
                    f"[{(ascii7_text if charset == 'ascii7' else terminal_text)(atom.display_label)}]",
                    element_ansi_color(atom.element, default=DEFAULT_COLOR),
                )
            )

    # Build output with labels
    colored_rows = canvas.render_colored(charset=charset)
    output_lines: list[str] = []

    # Index labels by row
    lbl_map: dict[int, list[tuple[int, str, int]]] = {}
    for row, col, text, color in labels_to_place:
        if 0 <= row < height:
            text = text[:width]
            start = max(0, min(width - len(text), col - len(text) // 2))
            lbl_map.setdefault(row, []).append((start, text, color))

    for row_idx in range(height):
        row_data = colored_rows[row_idx] if row_idx < len(colored_rows) else []
        row_labels = lbl_map.get(row_idx)

        if not row_labels:
            if mono:
                output_lines.append("".join(ch for ch, _ in row_data).rstrip())
            else:
                output_lines.append(_build_colored_braille_line(row_data))
        else:
            row_labels.sort(key=lambda x: x[0])
            parts: list[str] = []
            col = 0
            li = 0
            braille_run: list[tuple[str, int]] = []

            def _flush():
                nonlocal braille_run
                if braille_run:
                    if mono:
                        parts.append("".join(ch for ch, _ in braille_run))
                    else:
                        parts.append(_color_run_to_ansi(braille_run))
                    braille_run = []

            while col < width:
                if li < len(row_labels) and row_labels[li][0] == col:
                    _flush()
                    lcol, ltext, lcolor = row_labels[li]
                    if mono:
                        parts.append(ltext)
                    else:
                        parts.append(f"\033[1;38;5;{lcolor}m{ltext}\033[0m")
                    col += len(ltext)
                    li += 1
                else:
                    if col < len(row_data):
                        braille_run.append(row_data[col])
                    else:
                        braille_run.append((" ", 0))
                    col += 1
            _flush()
            output_lines.append("".join(parts).rstrip())

    while output_lines and not output_lines[-1]:
        output_lines.pop()
    return "\n".join(output_lines)
