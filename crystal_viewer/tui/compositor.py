"""ORTEP-style terminal renderer with label relaxation and leader lines.

Design:
- Uniform scale preserving aspect ratio (cubic → visually square)
- Atom circles (braille) with centered labels
- Label relaxation: if labels collide, push apart with leader lines
- Bonds drawn circle-edge to circle-edge
- Partial-occupancy: dashed circle + '*' suffix
- Zoom = viewport crop (not camera distance)
- Clipping for atoms outside viewport
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from .braille import BrailleCanvas
from .renderer import ELEMENT_COLORS, DEFAULT_COLOR, BOND_COLOR, CELL_COLOR
from .text import terminal_text

if TYPE_CHECKING:
    from .crystal_ir import CrystalIR
    from ..math.camera import Camera
    from .state import TerminalEditState, TerminalPickToken


# ── Label modes ─────────────────────────────────────────────────────────────

LABEL_MODES = ("auto", "label", "element", "molecule", "dot")

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


def _atom_label_text(atom, label_mode: str) -> str:
    if label_mode == "dot":
        return "●"
    elif label_mode == "element":
        return atom.element
    elif label_mode == "label":
        return terminal_text(atom.display_label)
    elif label_mode == "molecule":
        base = terminal_text(atom.display_label)
        if atom.molecule_index >= 0:
            return base + _superscript(atom.molecule_index)
        return base
    return terminal_text(atom.element)


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


# ── Viewport (uniform scale, correct aspect) ───────────────────────────────

# Terminal char cell is ~2× taller than wide
CHAR_ASPECT = 2.0


@dataclass
class Viewport:
    """Uniform-scale viewport mapping data→terminal, aspect-correct."""

    x_min: float
    x_max: float
    y_min: float
    y_max: float
    scale: float   # cols per data unit (uniform for both axes)
    width: int
    height: int

    def to_grid(self, x: float, y: float) -> tuple[int, int]:
        """Data coords → (row, col). Row 0 = top."""
        col = int((x - self.x_min) * self.scale)
        row = int((self.y_max - y) * self.scale / CHAR_ASPECT)
        return row, col

    def to_px(self, x: float, y: float) -> tuple[int, int]:
        """Data coords → braille subpixel (px_x, px_y)."""
        px_x = int((x - self.x_min) * self.scale * 2)
        px_y = int((self.y_max - y) * self.scale / CHAR_ASPECT * 4)
        return px_x, px_y

    def in_bounds_grid(self, row: int, col: int) -> bool:
        return 0 <= row < self.height and 0 <= col < self.width


def _compute_viewport(
    pts_2d: np.ndarray,
    extra_pts: list[np.ndarray],
    width: int,
    height: int,
    zoom: float = 1.0,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
) -> Viewport:
    """Compute an aspect-correct viewport for any positive zoom."""
    if zoom <= 0:
        raise ValueError("zoom must be greater than zero")
    all_arrays = [pts_2d] if len(pts_2d) > 0 else []
    all_arrays.extend(p for p in extra_pts if len(p) > 0)

    if all_arrays:
        combined = np.vstack(all_arrays)
        x_min, y_min = combined.min(axis=0)
        x_max, y_max = combined.max(axis=0)
    else:
        x_min = y_min = -1.0
        x_max = y_max = 1.0

    x_range = max(x_max - x_min, 0.01)
    y_range = max(y_max - y_min, 0.01)

    # Padding
    pad = 0.12
    x_min -= x_range * pad
    x_max += x_range * pad
    y_min -= y_range * pad
    y_max += y_range * pad
    x_range = x_max - x_min
    y_range = y_max - y_min

    return viewport_from_bounds(
        x_min,
        x_max,
        y_min,
        y_max,
        width,
        height,
        zoom=zoom,
        pan_x=pan_x,
        pan_y=pan_y,
    )


def viewport_from_bounds(
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    width: int,
    height: int,
    *,
    zoom: float = 1.0,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
) -> Viewport:
    """Create an aspect-correct viewport from already fitted data bounds."""
    if zoom <= 0:
        raise ValueError("zoom must be greater than zero")
    width = max(int(width), 1)
    height = max(int(height), 1)
    x_range = max(float(x_max) - float(x_min), 0.01)
    y_range = max(float(y_max) - float(y_min), 0.01)

    # Apply zoom around the fitted center. Values below one zoom out.
    cx = (x_min + x_max) / 2
    cy = (y_min + y_max) / 2
    x_range /= zoom
    y_range /= zoom
    x_min = cx - x_range / 2
    x_max = cx + x_range / 2
    y_min = cy - y_range / 2
    y_max = cy + y_range / 2

    # Apply 2D pan offset (shift viewport window)
    if pan_x != 0.0 or pan_y != 0.0:
        x_min += pan_x
        x_max += pan_x
        y_min += pan_y
        y_max += pan_y

    # Uniform scale: fit both axes, preserving aspect.
    # X axis: cols = x_range * scale
    # Y axis: rows = y_range * scale / CHAR_ASPECT
    scale_x = (width - 1) / x_range if x_range > 0 else 1.0
    scale_y = (height - 1) * CHAR_ASPECT / y_range if y_range > 0 else 1.0
    scale = min(scale_x, scale_y)

    return Viewport(
        x_min=x_min, x_max=x_max,
        y_min=y_min, y_max=y_max,
        scale=scale, width=width, height=height,
    )


# ── Atom radius for circles ────────────────────────────────────────────────

# Base radius per depth tier: front / mid / back
_RADIUS_BASE = (5, 4, 3)
_RADIUS_H = (3, 2, 1)

# Depth tier constants
_TIER_FRONT = 0
_TIER_MID = 1
_TIER_BACK = 2

# Dimmed color for back-tier braille geometry
_BACK_TIER_COLOR = 236  # dark grey (ANSI 256)
_MID_TIER_COLOR = 244   # medium grey


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
        return _TIER_MID  # flat → all same tier
    # depth array: larger = closer to camera
    norm = (depth_val - depth_min) / d_range  # 0=far, 1=close
    if norm >= 0.67:
        return _TIER_FRONT
    elif norm >= 0.33:
        return _TIER_MID
    return _TIER_BACK


def _tier_color(element_color: int, tier: int) -> int:
    """Map an element color + depth tier to the rendered ANSI color."""
    if tier == _TIER_FRONT:
        return element_color
    elif tier == _TIER_MID:
        return element_color  # same hue, will be rendered without bold
    return _BACK_TIER_COLOR  # back tier: grey


# ── Display levels ──────────────────────────────────────────────────────────

DISPLAY_LEVELS = ("atom", "molecule")


# ── Label relaxation ────────────────────────────────────────────────────────

# Offsets to try when label doesn't fit at ideal pos (row_offset, col_offset)
_OFFSETS = [
    (0, 0),       # ideal: centered at atom
    (0, 1), (0, -1),
    (-1, 0), (1, 0),
    (-1, 1), (-1, -1),
    (1, 1), (1, -1),
    (0, 2), (0, -2),
    (-2, 0), (2, 0),
    (-1, 2), (-1, -2),
    (1, 2), (1, -2),
]

_EDIT_OFFSETS = [
    *_OFFSETS,
    (-2, 1), (-2, -1), (2, 1), (2, -1),
    (-2, 2), (-2, -2), (2, 2), (2, -2),
    (-3, 0), (3, 0), (0, 3), (0, -3),
    (-3, 2), (-3, -2), (3, 2), (3, -2),
]


@dataclass
class _AtomDraw:
    """Internal atom drawing state."""
    idx: int
    x2d: float       # projected x
    y2d: float       # projected y
    row: int         # grid row (ideal)
    col: int         # grid col (ideal, label center)
    px_x: int        # subpixel x
    px_y: int        # subpixel y
    radius: int      # subpixel circle radius
    text: str        # label text
    color: int       # ANSI 256 color
    depth: float
    is_partial: bool # occ < 1
    depth_tier: int = _TIER_MID  # 0=front, 1=mid, 2=back
    # Placed label position (after relaxation)
    placed_row: int = -1
    placed_col: int = -1
    needs_leader: bool = False


@dataclass(frozen=True)
class ComposedFrame:
    """Rendered terminal text plus tokens that were actually placed."""

    text: str
    pick_tokens: tuple["TerminalPickToken", ...] = ()


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
    zoom: float = 1.0,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
    display_level: str = "atom",
    viewport: Viewport | None = None,
    edit_state: "TerminalEditState | None" = None,
) -> str:
    """Compatibility wrapper returning only rendered terminal text."""
    return compose_frame_result(
        crystal, camera, pts_2d, depth,
        width=width, height=height, mono=mono, label_mode=label_mode,
        show_bonds=show_bonds, show_cell=show_cell, show_minor=show_minor,
        zoom=zoom, pan_x=pan_x, pan_y=pan_y, display_level=display_level,
        viewport=viewport, edit_state=edit_state,
    ).text


def compose_frame_result(
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
    zoom: float = 1.0,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
    display_level: str = "atom",
    viewport: Viewport | None = None,
    edit_state: "TerminalEditState | None" = None,
) -> ComposedFrame:
    """Render crystal in ORTEP style with label relaxation.

    Parameters
    ----------
    display_level : str
        Visualization level: "atom" (default) or "molecule".
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
    visible_atom_count = sum(show_minor or not atom.is_minor for atom in crystal.atoms)
    resolved_label_mode = resolve_label_mode(
        label_mode,
        atom_count=visible_atom_count,
        width=width,
        height=height,
        zoom=zoom,
    )
    detail_scale = {
        "dot": 0.45,
        "element": 0.7,
    }.get(resolved_label_mode, 1.0)

    # ── Viewport ────────────────────────────────────────────────────────
    extra_pts: list[np.ndarray] = []
    cell_verts_2d = None
    if show_cell and crystal.lattice is not None:
        from ..math.camera import project_points as _proj
        verts = crystal.lattice.cell_vertices()
        cell_verts_2d, _ = _proj(camera, verts)
        extra_pts.append(cell_verts_2d)

    if viewport is None:
        viewport = _compute_viewport(pts_2d, extra_pts, width, height, zoom, pan_x, pan_y)
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
    canvas = BrailleCanvas(width, height)

    # ── Layer 1: Cell edges (dashed braille) ────────────────────────────
    if show_cell and cell_verts_2d is not None:
        for i, j in crystal.lattice.cell_edges():
            sx, sy = viewport.to_px(cell_verts_2d[i][0], cell_verts_2d[i][1])
            ex, ey = viewport.to_px(cell_verts_2d[j][0], cell_verts_2d[j][1])
            canvas.draw_dashed_line(sx, sy, ex, ey, dash=5, gap=3, color=CELL_COLOR)

    # ── Dispatch to display-level-specific rendering ───────────────────
    if display_level == "molecule":
        return _compose_molecule_frame(
            crystal, camera, pts_2d, depth, viewport, canvas,
            width, height, mono, show_minor, edit_state,
        )
    # ── Depth tier computation ───────────────────────────────────────
    depth_min = float(depth.min()) if len(depth) > 0 else 0.0
    depth_max = float(depth.max()) if len(depth) > 0 else 1.0

    # ── Prepare atoms ───────────────────────────────────────────────────
    atoms_draw: list[_AtomDraw] = []
    if len(pts_2d) > 0:
        for idx in range(min(len(crystal.atoms), len(pts_2d))):
            atom = crystal.atoms[idx]
            if not show_minor and atom.is_minor:
                continue
            x2d, y2d = float(pts_2d[idx][0]), float(pts_2d[idx][1])
            row, col = viewport.to_grid(x2d, y2d)
            px_x, px_y = viewport.to_px(x2d, y2d)
            tier = _depth_tier(float(depth[idx]), depth_min, depth_max)
            radius = _atom_radius(atom.element, tier, detail_scale)

            text = _atom_label_text(atom, resolved_label_mode)
            if edit_state is not None and edit_state.mode == "edit" and edit_state.level == "atom":
                text = terminal_text(atom.display_label)
                radius = min(radius, 2 if atom.element != "H" else 1)
            is_partial = atom.occupancy < 0.99
            if is_partial and resolved_label_mode != "dot":
                text += "*"

            color = ELEMENT_COLORS.get(atom.element, DEFAULT_COLOR)

            atoms_draw.append(_AtomDraw(
                idx=idx, x2d=x2d, y2d=y2d,
                row=row, col=col,
                px_x=px_x, px_y=px_y,
                radius=radius, text=text,
                color=color, depth=float(depth[idx]),
                is_partial=is_partial,
                depth_tier=tier,
            ))

    # Sort back-to-front for drawing (far first)
    atoms_draw.sort(key=lambda a: a.depth)

    # ── Layer 1.5: Density blobs (behind bonds/atoms, stippled fill) ────
    _draw_density_blobs(canvas, viewport, camera, crystal)

    # ── Layer 2: Bonds (circle-edge to circle-edge) ─────────────────────
    if show_bonds and crystal.bonds:
        from ..math.camera import project_points as _proj

        pos_map = {a.idx: a for a in atoms_draw}
        for bond in crystal.bonds:
            a1 = pos_map.get(bond.i)
            a2 = pos_map.get(bond.j)
            if a1 is None or a2 is None:
                continue
            if bond.start is not None and bond.end is not None:
                endpoints_2d, _ = _proj(
                    camera,
                    np.asarray([bond.start, bond.end], dtype=float),
                )
                start_px = viewport.to_px(*endpoints_2d[0])
                end_px = viewport.to_px(*endpoints_2d[1])
            else:
                start_px = (a1.px_x, a1.px_y)
                end_px = (a2.px_x, a2.px_y)
            dx = end_px[0] - start_px[0]
            dy = end_px[1] - start_px[1]
            length = (dx * dx + dy * dy) ** 0.5
            if length < 2:
                continue
            ux, uy = dx / length, dy / length
            # Shorten to circle edges
            sx = int(start_px[0] + ux * (a1.radius + 1))
            sy = int(start_px[1] + uy * (a1.radius + 1))
            ex = int(end_px[0] - ux * (a2.radius + 1))
            ey = int(end_px[1] - uy * (a2.radius + 1))
            gap = ((ex - sx) ** 2 + (ey - sy) ** 2) ** 0.5
            if gap < 2:
                continue
            # Depth-aware bond styling
            bond_tier = max(a1.depth_tier, a2.depth_tier)
            bond_color = BOND_COLOR if bond_tier <= _TIER_MID else _BACK_TIER_COLOR
            if a1.is_partial or a2.is_partial:
                canvas.draw_dashed_line(sx, sy, ex, ey, dash=2, gap=2, color=bond_color)
            elif bond_tier == _TIER_BACK:
                canvas.draw_dashed_line(sx, sy, ex, ey, dash=3, gap=2, color=bond_color)
            else:
                canvas.draw_line(sx, sy, ex, ey, color=bond_color)

    # ── Layer 3: Atom circles (depth-colored) ───────────────────────────
    for a in atoms_draw:
        circle_color = _tier_color(a.color, a.depth_tier)
        if a.is_partial:
            _draw_dashed_circle(canvas, a.px_x, a.px_y, a.radius, color=circle_color)
        else:
            _draw_circle(canvas, a.px_x, a.px_y, a.radius, color=circle_color)

    # ── Layer 4: Label relaxation ───────────────────────────────────────
    # Sort front-to-back for label priority (closest gets first pick)
    label_order = sorted(atoms_draw, key=lambda a: -a.depth)

    edit_tokens: dict[int, str] = {}
    edit_legend_width = 0
    if edit_state is not None and edit_state.mode == "edit" and edit_state.level == "atom":
        candidates = sorted(
            (
                atom_draw for atom_draw in atoms_draw
                if viewport.in_bounds_grid(atom_draw.row, atom_draw.col)
            ),
            key=lambda atom_draw: (atom_draw.row, atom_draw.col, -atom_draw.depth, crystal.atoms[atom_draw.idx].display_copy_id),
        )
        edit_tokens = {atom_draw.idx: f"a{index}" for index, atom_draw in enumerate(candidates, start=1)}
        if candidates:
            edit_legend_width = min(22, max(14, width // 4))
        for atom_draw in candidates:
            atom = crystal.atoms[atom_draw.idx]
            marker = ">" if atom.display_copy_id == edit_state.active_id else "*" if atom.display_copy_id in edit_state.selected_ids else ""
            atom_draw.text = f"{marker}[{edit_tokens[atom_draw.idx]}]{terminal_text(atom.display_label)}"

    # Grid tracks occupied cells
    occupied: set[tuple[int, int]] = {
        (row, col)
        for row in range(height)
        for col in range(max(width - edit_legend_width, 0), width)
    } if edit_legend_width else set()

    for a in label_order:
        if len(a.text) > width:
            a.text = a.text[:width]
        lw = len(a.text)
        placed = False

        offsets = _EDIT_OFFSETS if edit_tokens else _OFFSETS
        for dr, dc in offsets:
            r = a.row + dr
            c = a.col - lw // 2 + dc
            # Clamp to bounds
            if r < 0 or r >= height:
                continue
            c = max(0, min(width - lw, c))

            # Check all cells
            conflict = False
            for j in range(lw):
                if (r, c + j) in occupied:
                    conflict = True
                    break
            if conflict:
                continue

            # Place it
            for j in range(lw):
                occupied.add((r, c + j))
            a.placed_row = r
            a.placed_col = c
            # Need leader if offset > 1 char from ideal
            if abs(r - a.row) > 1 or abs((c + lw // 2) - a.col) > 1:
                a.needs_leader = True
            placed = True
            break

        if not placed:
            # Could not place — try to at least mark with "dot" at atom pos
            a.placed_row = -1
            a.placed_col = -1

    # ── Layer 5: Leader lines (braille, from label edge → atom center) ──
    for a in atoms_draw:
        if not a.needs_leader or a.placed_row < 0:
            continue
        # Leader from center of placed label to atom subpixel position
        lw = len(a.text)
        label_center_col = a.placed_col + lw // 2
        label_center_row = a.placed_row
        # Convert label center to subpixel
        lx = label_center_col * 2 + 1
        ly = label_center_row * 4 + 2
        leader_color = _tier_color(a.color, a.depth_tier)
        canvas.draw_dashed_line(lx, ly, a.px_x, a.px_y, dash=1, gap=1, color=leader_color)

    # ── Build output (color-aware) ─────────────────────────────────────
    colored_rows = canvas.render_colored()

    # Index placed labels by row — include depth_tier for label styling
    label_map: dict[int, list[tuple[int, str, int, bool, int]]] = {}
    for a in atoms_draw:
        if a.placed_row >= 0:
            label_map.setdefault(a.placed_row, []).append(
                (a.placed_col, a.text, a.color, a.is_partial, a.depth_tier)
            )

    output_lines: list[str] = []
    for row_idx in range(height):
        row_data = colored_rows[row_idx] if row_idx < len(colored_rows) else []
        row_labels = label_map.get(row_idx)

        if not row_labels:
            # No labels — emit braille with per-cell color runs
            if mono:
                line = "".join(ch for ch, _ in row_data).rstrip()
                output_lines.append(line)
            else:
                output_lines.append(_build_colored_braille_line(row_data))
        else:
            row_labels.sort(key=lambda x: x[0])
            parts: list[str] = []
            col = 0
            li = 0
            braille_run: list[tuple[str, int]] = []

            def _flush_braille_colored():
                nonlocal braille_run
                if braille_run:
                    if mono:
                        parts.append("".join(ch for ch, _ in braille_run))
                    else:
                        parts.append(_color_run_to_ansi(braille_run))
                    braille_run = []

            while col < width:
                if li < len(row_labels) and row_labels[li][0] == col:
                    _flush_braille_colored()
                    lcol, ltext, lcolor, is_partial, ltier = row_labels[li]
                    if mono:
                        parts.append(ltext)
                    else:
                        # Depth-aware label styling
                        if is_partial:
                            parts.append(f"\033[2;38;5;{lcolor}m{ltext}\033[0m")
                        elif ltier == _TIER_FRONT:
                            parts.append(f"\033[1;38;5;{lcolor}m{ltext}\033[0m")
                        elif ltier == _TIER_MID:
                            parts.append(f"\033[38;5;{lcolor}m{ltext}\033[0m")
                        else:  # back
                            parts.append(f"\033[2;38;5;{lcolor}m{ltext}\033[0m")
                    col += len(ltext)
                    li += 1
                else:
                    if col < len(row_data):
                        braille_run.append(row_data[col])
                    else:
                        braille_run.append((" ", 0))
                    col += 1
            _flush_braille_colored()
            output_lines.append("".join(parts).rstrip())

    while output_lines and not output_lines[-1]:
        output_lines.pop()
    from .state import TerminalPickToken

    if edit_legend_width:
        legend_items = [
            (
                f">[{edit_tokens[atom_draw.idx]}]{terminal_text(crystal.atoms[atom_draw.idx].display_label)}"
                if crystal.atoms[atom_draw.idx].display_copy_id == edit_state.active_id
                else f"*[{edit_tokens[atom_draw.idx]}]{terminal_text(crystal.atoms[atom_draw.idx].display_label)}"
                if crystal.atoms[atom_draw.idx].display_copy_id in edit_state.selected_ids
                else f"[{edit_tokens[atom_draw.idx]}]{terminal_text(crystal.atoms[atom_draw.idx].display_label)}"
            )
            for atom_draw in sorted(
                (item for item in label_order if item.idx in edit_tokens),
                key=lambda item: int(edit_tokens[item.idx][1:]),
            )
        ]
        columns = 2 if len(legend_items) > height else 1
        rows_per_column = (len(legend_items) + columns - 1) // columns
        column_width = max(edit_legend_width // columns, 1)
        while len(output_lines) < min(height, rows_per_column):
            output_lines.append("")
        for index, item in enumerate(legend_items):
            row = index % rows_per_column
            if row >= height:
                break
            column = index // rows_per_column
            start = width - edit_legend_width + column * column_width
            item = item[:column_width]
            base = output_lines[row] if row < len(output_lines) else ""
            base = base[:start].ljust(start) + item
            if row < len(output_lines):
                output_lines[row] = base
            else:
                output_lines.append(base)

    tokens = tuple(
        TerminalPickToken(
            token=edit_tokens[atom_draw.idx],
            level="atom",
            target_id=crystal.atoms[atom_draw.idx].display_copy_id,
            label=terminal_text(crystal.atoms[atom_draw.idx].display_label),
            selected=crystal.atoms[atom_draw.idx].display_copy_id in edit_state.selected_ids,
            active=crystal.atoms[atom_draw.idx].display_copy_id == edit_state.active_id,
        )
        for atom_draw in label_order
        if atom_draw.idx in edit_tokens
    ) if edit_state is not None else ()
    return ComposedFrame("\n".join(output_lines), tokens)

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


# ── Density blob drawing ────────────────────────────────────────────────────

# ANSI 256 colors for orbital lobes
_BLOB_POS_COLOR = 208   # orange (positive lobe)
_BLOB_NEG_COLOR = 33    # blue (negative lobe)


def _draw_density_blobs(
    canvas: "BrailleCanvas",
    viewport: "Viewport",
    camera: "Camera",
    crystal: "CrystalIR",
) -> None:
    """Draw stippled density blobs for cube isosurface lobes.

    Uses a sparse dot pattern to simulate translucency — structural
    geometry drawn on top will overwrite the dots, keeping atoms/bonds
    legible through the orbital cloud.
    """
    blobs = crystal.metadata.get("density_blobs")
    if not blobs:
        return

    from ..math.camera import project_points as _proj

    centers = np.array([b["center"] for b in blobs], dtype=float)
    pts_2d, blob_depth = _proj(camera, centers)

    for i, blob in enumerate(blobs):
        x2d, y2d = float(pts_2d[i][0]), float(pts_2d[i][1])
        px_x, px_y = viewport.to_px(x2d, y2d)
        # Convert world-space radius to subpixel radius
        screen_r = max(2, int(blob["radius"] * viewport.scale * 2))
        # Cap radius to avoid flooding the canvas
        screen_r = min(screen_r, min(canvas.px_width, canvas.px_height) // 3)

        color = _BLOB_POS_COLOR if blob.get("sign", 1) > 0 else _BLOB_NEG_COLOR
        density = 0.25  # sparse fill for translucency effect

        _draw_stippled_disk(canvas, px_x, px_y, screen_r, color=color, density=density)


def _draw_stippled_disk(
    canvas: "BrailleCanvas",
    cx: int, cy: int, radius: int,
    *, color: int = 0, density: float = 0.3,
) -> None:
    """Draw a filled disk with deterministic stipple pattern.

    Uses a simple hash to decide which pixels to set, giving a repeatable
    translucent appearance without importing random.
    """
    r2 = radius * radius
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx * dx + dy * dy > r2:
                continue
            # Deterministic stipple: hash of position
            h = ((dx * 7919 + dy * 104729) & 0xFFFF) / 65535.0
            if h < density:
                canvas.set_pixel(cx + dx, cy + dy, color=color)


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


def _draw_dashed_circle(
    canvas: BrailleCanvas, cx: int, cy: int, r: int, *, color: int = 0
) -> None:
    """Dashed circle for partial-occupancy atoms."""
    x, y, d = 0, r, 1 - r
    count = 0
    while x <= y:
        if (count % 5) < 3:
            for px, py in _oct(cx, cy, x, y):
                canvas.set_pixel(px, py, color=color)
        count += 1
        x, y, d = _step(x, y, d)


def _oct(cx, cy, x, y):
    return (
        (cx + x, cy + y), (cx - x, cy + y),
        (cx + x, cy - y), (cx - x, cy - y),
        (cx + y, cy + x), (cx - y, cy + x),
        (cx + y, cy - x), (cx - y, cy - x),
    )


def _step(x, y, d):
    if d < 0:
        return x + 1, y, d + 2 * x + 3
    return x + 1, y - 1, d + 2 * (x - y) + 5


# ── Molecule-level rendering ────────────────────────────────────────────────

# Qualitative palette for molecule species (ANSI 256 high-contrast hues)
MOLECULE_COLORS = (
    39,   # deep sky blue
    208,  # orange
    120,  # bright green
    201,  # magenta/pink
    226,  # yellow
    87,   # cyan
    160,  # dark red
    99,   # purple
    48,   # sea green
    214,  # amber
    63,   # slate blue
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
    edit_state: "TerminalEditState | None" = None,
) -> ComposedFrame:
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
    molecule_candidates: list[tuple[int, float, int, int, int]] = []
    edit_molecule = edit_state is not None and edit_state.mode == "edit" and edit_state.level == "molecule"
    for mol_idx, molecule_depth in mol_depths:
        atom_indices = mol_groups[mol_idx]
        if len(atom_indices) < 2 and not edit_molecule:
            continue

        color = mol_color_map.get(mol_idx, MOLECULE_COLORS[mol_idx % len(MOLECULE_COLORS)])

        # Get 2D projected coordinates for this molecule's atoms
        mol_pts = [(float(pts_2d[i][0]), float(pts_2d[i][1])) for i in atom_indices]

        # Centroid for label placement
        cx = sum(x for x, y in mol_pts) / len(mol_pts)
        cy = sum(y for x, y in mol_pts) / len(mol_pts)
        if molecule_detail == "centroid" or len(mol_pts) == 1:
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
        molecule_candidates.append((mol_idx, molecule_depth, row, col, color))
        label_candidates.setdefault(formula, []).append(
            (molecule_depth, row, col, color)
        )

    # One label per species is enough to identify the full unit-cell packing;
    # choose the front-most instance so text does not cover every replica.
    labels_to_place: list[tuple[int, int, str, int, int | None]] = []
    if edit_molecule:
        visible_candidates = sorted(
            (candidate for candidate in molecule_candidates if 0 <= candidate[2] < height and 0 <= candidate[3] < width),
            key=lambda candidate: (candidate[2], candidate[3], -candidate[1], candidate[0]),
        )
        for token_index, (mol_idx, _, row, col, color) in enumerate(visible_candidates, start=1):
            member_index = mol_groups[mol_idx][0]
            target_id = crystal.atoms[member_index].display_fragment_id
            marker = ">" if target_id == edit_state.active_id else "*" if target_id in edit_state.selected_ids else ""
            labels_to_place.append((row, col, f"{marker}[m{token_index}]{_display_species_name(mol_formula.get(mol_idx, f'M{mol_idx}'))}", color, mol_idx))
    else:
        for formula, candidates in label_candidates.items():
            _, row, col, color = max(candidates, key=lambda item: item[0])
            labels_to_place.append((row, col, _display_species_name(formula), color, None))

    # Build output with labels
    colored_rows = canvas.render_colored()
    output_lines: list[str] = []

    # Index labels by row
    lbl_map: dict[int, list[tuple[int, str, int, int | None]]] = {}
    placed_molecules: list[tuple[int, str]] = []
    for row, col, text, color, mol_idx in labels_to_place:
        if 0 <= row < height:
            text = text[:width]
            start = max(0, min(width - len(text), col - len(text) // 2))
            lbl_map.setdefault(row, []).append((start, text, color, mol_idx))

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
                    lcol, ltext, lcolor, mol_idx = row_labels[li]
                    if mono:
                        parts.append(ltext)
                    else:
                        parts.append(f"\033[1;38;5;{lcolor}m{ltext}\033[0m")
                    col += len(ltext)
                    if mol_idx is not None:
                        placed_molecules.append((mol_idx, ltext))
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
    from .state import TerminalPickToken

    tokens = tuple(
        TerminalPickToken(
            token=text[text.find("[") + 1:text.find("]")],
            level="molecule",
            target_id=crystal.atoms[mol_groups[mol_idx][0]].display_fragment_id,
            label=_display_species_name(mol_formula.get(mol_idx, f"M{mol_idx}")),
            selected=crystal.atoms[mol_groups[mol_idx][0]].display_fragment_id in edit_state.selected_ids,
            active=crystal.atoms[mol_groups[mol_idx][0]].display_fragment_id == edit_state.active_id,
        )
        for mol_idx, text in placed_molecules
    ) if edit_molecule else ()
    return ComposedFrame("\n".join(output_lines), tokens)
