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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from .braille import BrailleCanvas
from .renderer import ELEMENT_COLORS, DEFAULT_COLOR, BOND_COLOR, CELL_COLOR

if TYPE_CHECKING:
    from .crystal_ir import CrystalIR
    from ..math.camera import Camera


# ── Label modes ─────────────────────────────────────────────────────────────

LABEL_MODES = ("element", "label", "molecule", "dot")

_SUPERSCRIPTS = "⁰¹²³⁴⁵⁶⁷⁸⁹"


def _superscript(n: int) -> str:
    if n < 0:
        return ""
    return "".join(_SUPERSCRIPTS[int(c)] for c in str(n))


def _atom_label_text(atom, label_mode: str) -> str:
    if label_mode == "dot":
        return "●"
    elif label_mode == "element":
        return atom.element
    elif label_mode == "label":
        return atom.display_label
    elif label_mode == "molecule":
        base = atom.display_label
        if atom.molecule_index >= 0:
            return base + _superscript(atom.molecule_index)
        return base
    return atom.element


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
) -> Viewport:
    """Compute aspect-correct viewport. zoom>1 crops to center region."""
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

    # Apply zoom (crop to center)
    if zoom > 1.0:
        cx = (x_min + x_max) / 2
        cy = (y_min + y_max) / 2
        x_range /= zoom
        y_range /= zoom
        x_min = cx - x_range / 2
        x_max = cx + x_range / 2
        y_min = cy - y_range / 2
        y_max = cy + y_range / 2

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

_RADIUS_PX = 4  # subpixel radius for atom circles (uniform for clarity)
_RADIUS_PX_H = 2  # smaller for H


def _atom_radius(element: str) -> int:
    return _RADIUS_PX_H if element == "H" else _RADIUS_PX


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
    # Placed label position (after relaxation)
    placed_row: int = -1
    placed_col: int = -1
    needs_leader: bool = False


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
) -> str:
    """Render crystal in ORTEP style with label relaxation."""
    if width is None or height is None:
        try:
            term_size = os.get_terminal_size()
            width = width or term_size.columns - 2
            height = height or term_size.lines - 4
        except OSError:
            width = width or 80
            height = height or 40
    width = max(width, 30)
    height = max(height, 15)

    # ── Viewport ────────────────────────────────────────────────────────
    extra_pts: list[np.ndarray] = []
    cell_verts_2d = None
    if show_cell and crystal.lattice is not None:
        from ..math.camera import project_points as _proj
        verts = crystal.lattice.cell_vertices()
        cell_verts_2d, _ = _proj(camera, verts)
        extra_pts.append(cell_verts_2d)

    viewport = _compute_viewport(pts_2d, extra_pts, width, height, zoom)
    canvas = BrailleCanvas(width, height)

    # ── Layer 1: Cell edges (dashed braille) ────────────────────────────
    if show_cell and cell_verts_2d is not None:
        for i, j in crystal.lattice.cell_edges():
            sx, sy = viewport.to_px(cell_verts_2d[i][0], cell_verts_2d[i][1])
            ex, ey = viewport.to_px(cell_verts_2d[j][0], cell_verts_2d[j][1])
            canvas.draw_dashed_line(sx, sy, ex, ey, dash=5, gap=3)

    # ── Prepare atoms ───────────────────────────────────────────────────
    atoms_draw: list[_AtomDraw] = []
    if len(pts_2d) > 0:
        for idx in range(min(len(crystal.atoms), len(pts_2d))):
            atom = crystal.atoms[idx]
            x2d, y2d = float(pts_2d[idx][0]), float(pts_2d[idx][1])
            row, col = viewport.to_grid(x2d, y2d)
            px_x, px_y = viewport.to_px(x2d, y2d)
            radius = _atom_radius(atom.element)

            text = _atom_label_text(atom, label_mode)
            is_partial = atom.occupancy < 0.99
            if is_partial and label_mode != "dot":
                text += "*"

            color = ELEMENT_COLORS.get(atom.element, DEFAULT_COLOR)

            atoms_draw.append(_AtomDraw(
                idx=idx, x2d=x2d, y2d=y2d,
                row=row, col=col,
                px_x=px_x, px_y=px_y,
                radius=radius, text=text,
                color=color, depth=float(depth[idx]),
                is_partial=is_partial,
            ))

    # Sort back-to-front for drawing (far first)
    atoms_draw.sort(key=lambda a: a.depth)

    # ── Layer 2: Bonds (circle-edge to circle-edge) ─────────────────────
    if show_bonds and crystal.bonds:
        pos_map = {a.idx: a for a in atoms_draw}
        for bond in crystal.bonds:
            a1 = pos_map.get(bond.i)
            a2 = pos_map.get(bond.j)
            if a1 is None or a2 is None:
                continue
            dx = a2.px_x - a1.px_x
            dy = a2.px_y - a1.px_y
            length = (dx * dx + dy * dy) ** 0.5
            if length < 2:
                continue
            ux, uy = dx / length, dy / length
            # Shorten to circle edges
            sx = int(a1.px_x + ux * (a1.radius + 1))
            sy = int(a1.px_y + uy * (a1.radius + 1))
            ex = int(a2.px_x - ux * (a2.radius + 1))
            ey = int(a2.px_y - uy * (a2.radius + 1))
            gap = ((ex - sx) ** 2 + (ey - sy) ** 2) ** 0.5
            if gap < 2:
                continue
            if a1.is_partial or a2.is_partial:
                canvas.draw_dashed_line(sx, sy, ex, ey, dash=2, gap=2)
            else:
                canvas.draw_line(sx, sy, ex, ey)

    # ── Layer 3: Atom circles ───────────────────────────────────────────
    for a in atoms_draw:
        if a.is_partial:
            _draw_dashed_circle(canvas, a.px_x, a.px_y, a.radius)
        else:
            _draw_circle(canvas, a.px_x, a.px_y, a.radius)

    # ── Layer 4: Label relaxation ───────────────────────────────────────
    # Sort front-to-back for label priority (closest gets first pick)
    label_order = sorted(atoms_draw, key=lambda a: -a.depth)

    # Grid tracks occupied cells
    occupied: set[tuple[int, int]] = set()

    for a in label_order:
        lw = len(a.text)
        placed = False

        for dr, dc in _OFFSETS:
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
        canvas.draw_dashed_line(lx, ly, a.px_x, a.px_y, dash=1, gap=1)

    # ── Build output ────────────────────────────────────────────────────
    braille_lines = canvas.render()
    while len(braille_lines) < height:
        braille_lines.append("")

    # Index placed labels by row
    label_map: dict[int, list[tuple[int, str, int, bool]]] = {}
    for a in atoms_draw:
        if a.placed_row >= 0:
            label_map.setdefault(a.placed_row, []).append(
                (a.placed_col, a.text, a.color, a.is_partial)
            )

    # Compose final string
    output_lines: list[str] = []
    for row_idx in range(height):
        braille_row = braille_lines[row_idx] if row_idx < len(braille_lines) else ""
        braille_row = braille_row.ljust(width)

        row_labels = label_map.get(row_idx)
        if not row_labels:
            if mono:
                output_lines.append(braille_row.rstrip())
            else:
                stripped = braille_row.rstrip()
                if stripped:
                    output_lines.append(f"\033[38;5;{CELL_COLOR}m{stripped}\033[0m")
                else:
                    output_lines.append("")
        else:
            row_labels.sort(key=lambda x: x[0])
            parts: list[str] = []
            col = 0
            li = 0
            while col < width:
                if li < len(row_labels) and row_labels[li][0] == col:
                    lcol, ltext, lcolor, is_partial = row_labels[li]
                    if mono:
                        parts.append(ltext)
                    else:
                        if is_partial:
                            parts.append(f"\033[2;38;5;{lcolor}m{ltext}\033[0m")
                        else:
                            parts.append(f"\033[1;38;5;{lcolor}m{ltext}\033[0m")
                    col += len(ltext)
                    li += 1
                else:
                    ch = braille_row[col] if col < len(braille_row) else " "
                    if mono:
                        parts.append(ch)
                    else:
                        if ch.strip() and ord(ch) >= 0x2800:
                            parts.append(f"\033[38;5;{CELL_COLOR}m{ch}\033[0m")
                        else:
                            parts.append(ch)
                    col += 1
            output_lines.append("".join(parts).rstrip())

    while output_lines and not output_lines[-1]:
        output_lines.pop()
    return "\n".join(output_lines)


# ── Circle drawing ──────────────────────────────────────────────────────────


def _draw_circle(canvas: BrailleCanvas, cx: int, cy: int, r: int) -> None:
    """Solid circle via midpoint algorithm."""
    x, y, d = 0, r, 1 - r
    while x <= y:
        for px, py in _oct(cx, cy, x, y):
            canvas.set_pixel(px, py)
        x, y, d = _step(x, y, d)


def _draw_dashed_circle(canvas: BrailleCanvas, cx: int, cy: int, r: int) -> None:
    """Dashed circle for partial-occupancy atoms."""
    x, y, d = 0, r, 1 - r
    count = 0
    while x <= y:
        if (count % 5) < 3:
            for px, py in _oct(cx, cy, x, y):
                canvas.set_pixel(px, py)
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
