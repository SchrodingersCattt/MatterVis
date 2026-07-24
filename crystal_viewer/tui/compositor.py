"""Hybrid compositor: braille background + text foreground.

Renders bonds/cell as smooth braille subpixel lines, then overlays
atom labels as colored text. The result is dramatically cleaner than
the old all-character approach.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from .braille import BrailleCanvas
from .renderer import ELEMENT_COLORS, DEFAULT_COLOR, BOND_COLOR, CELL_COLOR

if TYPE_CHECKING:
    from .crystal_ir import CrystalIR
    from ..math.camera import Camera


# ── Label modes ─────────────────────────────────────────────────────────────

LABEL_MODES = ("element", "label", "molecule", "dot")

# Superscript digits for molecule index display
_SUPERSCRIPTS = "⁰¹²³⁴⁵⁶⁷⁸⁹"


def _superscript(n: int) -> str:
    """Convert an integer to superscript digits."""
    if n < 0:
        return ""
    s = str(n)
    return "".join(_SUPERSCRIPTS[int(c)] for c in s)


def _atom_label_text(atom, label_mode: str) -> str:
    """Generate display text for an atom based on label mode."""
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


# ── Viewport computation ────────────────────────────────────────────────────


@dataclass
class Viewport:
    """Mapping from projected 2D coords to terminal grid/subpixel coords."""

    x_min: float
    x_max: float
    y_min: float
    y_max: float
    scale_x: float
    scale_y: float
    width: int       # terminal char width
    height: int      # terminal char height

    def to_grid(self, pt_2d: np.ndarray) -> tuple[int, int]:
        """Convert projected 2D point to (row, col) in character space."""
        x, y = pt_2d
        col = int((x - self.x_min) * self.scale_x)
        row = int((self.y_max - y) * self.scale_y)
        return row, col

    def to_subpixel(self, pt_2d: np.ndarray) -> tuple[int, int]:
        """Convert projected 2D point to braille subpixel (px_x, px_y)."""
        x, y = pt_2d
        px_x = int((x - self.x_min) * self.scale_x * 2)
        px_y = int((self.y_max - y) * self.scale_y * 4)
        return px_x, px_y


def _compute_viewport(
    pts_2d: np.ndarray,
    extra_pts: list[np.ndarray],
    width: int,
    height: int,
) -> Viewport:
    """Compute viewport from all projected points."""
    all_arrays = [pts_2d] if len(pts_2d) > 0 else []
    all_arrays.extend(p for p in extra_pts if len(p) > 0)

    if all_arrays:
        combined = np.vstack(all_arrays)
        x_min, y_min = combined.min(axis=0)
        x_max, y_max = combined.max(axis=0)
    else:
        x_min = y_min = -1.0
        x_max = y_max = 1.0

    # Padding
    x_range = max(x_max - x_min, 0.01)
    y_range = max(y_max - y_min, 0.01)
    pad = 0.08
    x_min -= x_range * pad
    x_max += x_range * pad
    y_min -= y_range * pad
    y_max += y_range * pad
    x_range = x_max - x_min
    y_range = y_max - y_min

    # Aspect-ratio-aware fitting
    # Terminal chars are ~2:1 (height:width), braille is 4:2 subpixels = same ratio
    char_aspect = 2.0
    data_aspect = y_range / x_range if x_range > 0 else 1.0
    grid_aspect = (height / width) * char_aspect

    if data_aspect > grid_aspect:
        scale_y = (height - 1) / y_range
        scale_x = scale_y / char_aspect
    else:
        scale_x = (width - 1) / x_range
        scale_y = scale_x * char_aspect

    return Viewport(
        x_min=x_min, x_max=x_max,
        y_min=y_min, y_max=y_max,
        scale_x=scale_x, scale_y=scale_y,
        width=width, height=height,
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
    show_minor: bool = False,
) -> str:
    """Render crystal structure using hybrid braille + text overlay.

    Parameters
    ----------
    crystal : CrystalIR
        Crystal structure data.
    camera : Camera
        Camera state.
    pts_2d : np.ndarray
        (N, 2) projected screen coordinates.
    depth : np.ndarray
        (N,) depth values (larger = closer to camera).
    width, height : int or None
        Terminal dimensions. Auto-detect if None.
    mono : bool
        Force monochrome (no ANSI escape codes).
    label_mode : str
        One of: "element", "label", "molecule", "dot".
    show_bonds : bool
        Draw bonds as braille lines.
    show_cell : bool
        Draw unit cell edges as braille lines.
    show_minor : bool
        Show minor disorder atoms (dimmed).

    Returns
    -------
    str
        The rendered frame as a printable string.
    """
    if width is None or height is None:
        try:
            term_size = os.get_terminal_size()
            width = width or term_size.columns - 2
            height = height or term_size.lines - 4
        except OSError:
            width = width or 80
            height = height or 40

    width = max(width, 20)
    height = max(height, 10)

    # ── Collect all points for viewport ─────────────────────────────────
    extra_pts: list[np.ndarray] = []

    # Project cell vertices
    cell_verts_2d = None
    cell_verts_depth = None
    if show_cell and crystal.lattice is not None:
        from ..math.camera import project_points as _proj
        verts = crystal.lattice.cell_vertices()
        cell_verts_2d, cell_verts_depth = _proj(camera, verts)
        extra_pts.append(cell_verts_2d)

    viewport = _compute_viewport(pts_2d, extra_pts, width, height)

    # ── Layer 1: Braille canvas (bonds + cell) ──────────────────────────
    canvas = BrailleCanvas(width, height)

    # Draw cell edges
    if show_cell and cell_verts_2d is not None:
        for i, j in crystal.lattice.cell_edges():
            sx, sy = viewport.to_subpixel(cell_verts_2d[i])
            ex, ey = viewport.to_subpixel(cell_verts_2d[j])
            canvas.draw_line(sx, sy, ex, ey)

    # Draw bonds
    if show_bonds and crystal.bonds:
        for bond in crystal.bonds:
            if bond.i >= len(pts_2d) or bond.j >= len(pts_2d):
                continue
            atom_i = crystal.atoms[bond.i]
            atom_j = crystal.atoms[bond.j]

            # Skip bonds to hidden minor atoms
            if not show_minor and (atom_i.is_minor or atom_j.is_minor):
                continue

            sx, sy = viewport.to_subpixel(pts_2d[bond.i])
            ex, ey = viewport.to_subpixel(pts_2d[bond.j])

            # Dashed line for bonds involving minor atoms
            if atom_i.is_minor or atom_j.is_minor:
                canvas.draw_dashed_line(sx, sy, ex, ey)
            else:
                canvas.draw_line(sx, sy, ex, ey)

    # ── Layer 2: Atom labels (text overlay) ─────────────────────────────
    # Prepare label data sorted by depth (back-to-front)
    @dataclass
    class LabelEntry:
        row: int
        col: int
        text: str
        text_width: int
        color: int
        depth: float
        is_minor: bool

    labels: list[LabelEntry] = []

    if len(pts_2d) > 0:
        for idx in range(len(crystal.atoms)):
            if idx >= len(pts_2d):
                break
            atom = crystal.atoms[idx]

            # Skip minor atoms if not showing
            if not show_minor and atom.is_minor:
                continue

            row, col = viewport.to_grid(pts_2d[idx])
            if row < 0 or row >= height or col < 0 or col >= width:
                continue

            text = _atom_label_text(atom, label_mode)
            if atom.is_minor and label_mode != "dot":
                text += "'"  # Minor disorder marker

            color = ELEMENT_COLORS.get(atom.element, DEFAULT_COLOR)
            if atom.is_minor:
                color = 240  # Dim for minor

            labels.append(LabelEntry(
                row=row, col=col,
                text=text,
                text_width=len(text),
                color=color,
                depth=float(depth[idx]),
                is_minor=atom.is_minor,
            ))

    # ── Layer 3: Composit — resolve collisions + merge ──────────────────
    # Grid of "occupied" cells: stores (depth, label_idx) for collision detection
    occupied: dict[tuple[int, int], tuple[float, int]] = {}

    # Sort labels by depth (FRONT-to-BACK: largest depth first = closest first)
    labels.sort(key=lambda e: -e.depth)

    # Assign labels to cells (winner = closest to camera)
    surviving_labels: list[LabelEntry] = []
    for label in labels:
        # Check if any cell in this label's span is already taken by a closer atom
        conflict = False
        for dc in range(label.text_width):
            cell = (label.row, label.col + dc)
            if cell in occupied:
                conflict = True
                break

        if conflict:
            continue  # Skip this label — something closer already here

        # Claim cells
        for dc in range(label.text_width):
            cell = (label.row, label.col + dc)
            occupied[cell] = (label.depth, len(surviving_labels))
        surviving_labels.append(label)

    # ── Build output string ─────────────────────────────────────────────
    # Start from braille canvas, then overlay labels
    braille_lines = canvas.render()

    # Pad braille lines to height
    while len(braille_lines) < height:
        braille_lines.append("")

    # Build label map: row → list of (col, text, color)
    label_map: dict[int, list[tuple[int, str, int]]] = {}
    for label in surviving_labels:
        label_map.setdefault(label.row, []).append(
            (label.col, label.text, label.color)
        )

    # Compose final output
    output_lines: list[str] = []
    for row_idx in range(height):
        braille_row = braille_lines[row_idx] if row_idx < len(braille_lines) else ""
        # Pad braille row to width
        braille_row = braille_row.ljust(width)

        row_labels = label_map.get(row_idx)
        if not row_labels:
            # No labels on this row — just output braille (with optional color)
            if mono:
                output_lines.append(braille_row.rstrip())
            else:
                # Color braille in dim grey
                stripped = braille_row.rstrip()
                if stripped:
                    output_lines.append(
                        f"\033[38;5;{CELL_COLOR}m{stripped}\033[0m"
                    )
                else:
                    output_lines.append("")
        else:
            # Merge labels into braille row
            # Sort labels by column for left-to-right processing
            row_labels.sort(key=lambda x: x[0])
            parts: list[str] = []
            col = 0
            label_idx = 0

            while col < width:
                # Check if a label starts at this column
                if label_idx < len(row_labels) and row_labels[label_idx][0] == col:
                    lcol, ltext, lcolor = row_labels[label_idx]
                    if mono:
                        parts.append(ltext)
                    else:
                        parts.append(f"\033[1;38;5;{lcolor}m{ltext}\033[0m")
                    col += len(ltext)
                    label_idx += 1
                else:
                    # Output braille character (possibly colored dim)
                    ch = braille_row[col] if col < len(braille_row) else " "
                    if mono:
                        parts.append(ch)
                    else:
                        if ch != " " and ord(ch) >= 0x2800:
                            parts.append(f"\033[38;5;{CELL_COLOR}m{ch}\033[0m")
                        else:
                            parts.append(ch)
                    col += 1

            output_lines.append("".join(parts).rstrip())

    # Strip trailing empty lines
    while output_lines and not output_lines[-1]:
        output_lines.pop()

    return "\n".join(output_lines)
