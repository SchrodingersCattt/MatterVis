"""Terminal ASCII renderer for crystal structures.

Renders projected 2D atom/bond/cell data onto a character grid,
supporting both element-symbol and compact-dot display modes,
with optional ANSI 256-color coding.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .crystal_ir import CrystalIR
    from ..math.camera import Camera


# ── Element color palette (CPK-inspired, ANSI 256) ─────────────────────────

# Map element → ANSI 256-color code (approximate CPK)
ELEMENT_COLORS: dict[str, int] = {
    "H": 255,   # white
    "He": 123,  # cyan
    "Li": 129,  # violet
    "Be": 118,  # dark green
    "B": 216,   # salmon
    "C": 245,   # grey
    "N": 33,    # blue
    "O": 196,   # red
    "F": 82,    # green
    "Ne": 123,  # cyan
    "Na": 129,  # violet
    "Mg": 34,   # dark green
    "Al": 249,  # light grey
    "Si": 214,  # dark orange
    "P": 208,   # orange
    "S": 226,   # yellow
    "Cl": 46,   # green
    "Ar": 123,  # cyan
    "K": 129,   # violet
    "Ca": 34,   # green
    "Ti": 249,  # grey
    "V": 249,   # grey
    "Cr": 33,   # blue
    "Mn": 129,  # violet
    "Fe": 208,  # orange
    "Co": 33,   # blue
    "Ni": 34,   # green
    "Cu": 208,  # orange
    "Zn": 249,  # grey
    "Ga": 249,  # grey
    "Ge": 249,  # grey
    "As": 129,  # violet
    "Se": 208,  # orange
    "Br": 124,  # dark red
    "Mo": 45,   # teal
    "Ru": 45,   # teal
    "Pd": 33,   # blue
    "Ag": 249,  # light grey
    "Cd": 214,  # orange
    "In": 249,  # grey
    "Sn": 249,  # grey
    "I": 90,    # dark violet
    "Ba": 34,   # green
    "W": 33,    # blue
    "Pt": 249,  # grey
    "Au": 220,  # gold
    "Pb": 242,  # dark grey
    "Bi": 129,  # violet
}

DEFAULT_COLOR = 252  # fallback light grey
BOND_COLOR = 240     # dim grey
CELL_COLOR = 238     # darker grey


# ── Glyph ──────────────────────────────────────────────────────────────────

@dataclass
class Glyph:
    """A single renderable unit on the terminal grid."""

    text: str
    display_width: int
    color: int = DEFAULT_COLOR  # ANSI 256 color code
    depth: float = 0.0         # For sorting (larger = closer)
    is_atom: bool = False


# ── Grid ────────────────────────────────────────────────────────────────────

@dataclass
class TerminalFrame:
    """A 2D character grid with ANSI color support."""

    width: int
    height: int
    cells: list[list[Glyph | None]] = field(default_factory=list)

    def __post_init__(self):
        if not self.cells:
            self.cells = [
                [None for _ in range(self.width)]
                for _ in range(self.height)
            ]

    def put(self, row: int, col: int, glyph: Glyph) -> None:
        """Place a glyph, respecting bounds and depth (painter's algo)."""
        if row < 0 or row >= self.height or col < 0 or col >= self.width:
            return
        existing = self.cells[row][col]
        if existing is not None and existing.depth > glyph.depth:
            return  # Existing is closer, don't overwrite
        self.cells[row][col] = glyph
        # For multi-char glyphs, mark adjacent cells
        if glyph.display_width > 1 and col + 1 < self.width:
            # Mark next cell as occupied (sentinel)
            self.cells[row][col + 1] = Glyph(
                text="", display_width=0, depth=glyph.depth
            )

    def to_string(self, mono: bool = False) -> str:
        """Serialize the grid to a printable string."""
        lines = []
        for row in self.cells:
            line_parts = []
            col = 0
            while col < self.width:
                cell = row[col]
                if cell is None or cell.text == "":
                    line_parts.append(" ")
                    col += 1
                else:
                    if mono:
                        line_parts.append(cell.text)
                    else:
                        line_parts.append(
                            f"\033[38;5;{cell.color}m{cell.text}\033[0m"
                        )
                    col += cell.display_width
            lines.append("".join(line_parts).rstrip())
        # Strip trailing empty lines
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)


# ── Main render function ────────────────────────────────────────────────────


def render_ascii_frame(
    crystal: "CrystalIR",
    camera: "Camera",
    pts_2d: np.ndarray,
    depth: np.ndarray,
    *,
    width: int | None = None,
    height: int | None = None,
    mono: bool = False,
    compact: bool = False,
    show_bonds: bool = True,
    show_cell: bool = True,
) -> str:
    """Render a crystal structure to an ASCII string.

    Parameters
    ----------
    crystal : CrystalIR
        The crystal structure data.
    camera : Camera
        Camera state used for projection.
    pts_2d : np.ndarray
        (N, 2) projected screen coordinates.
    depth : np.ndarray
        (N,) depth values (larger = closer to camera).
    width, height : int or None
        Grid dimensions. Auto-detect from terminal if None.
    mono : bool
        If True, suppress ANSI color codes.
    compact : bool
        If True, use single-char dot mode instead of element symbols.
    show_bonds : bool
        Whether to draw bonds.
    show_cell : bool
        Whether to draw unit cell edges.

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

    # Ensure minimum size
    width = max(width, 20)
    height = max(height, 10)

    frame = TerminalFrame(width=width, height=height)

    # ── Compute viewport mapping ────────────────────────────────────────
    # Collect all 2D points to determine bounding box
    all_pts = [pts_2d] if len(pts_2d) > 0 else []

    # Project cell edges if needed
    cell_segments_2d = []
    if show_cell and crystal.lattice is not None:
        from ..math.camera import project_points as _proj
        verts = crystal.lattice.cell_vertices()
        verts_2d, verts_depth = _proj(camera, verts)
        all_pts.append(verts_2d)

        for i, j in crystal.lattice.cell_edges():
            cell_segments_2d.append((
                verts_2d[i], verts_2d[j],
                (verts_depth[i] + verts_depth[j]) / 2.0,
            ))

    # Project bond endpoints
    bond_segments_2d = []
    if show_bonds and crystal.bonds:
        for bond in crystal.bonds:
            if bond.i < len(pts_2d) and bond.j < len(pts_2d):
                avg_d = (depth[bond.i] + depth[bond.j]) / 2.0
                bond_segments_2d.append((
                    pts_2d[bond.i], pts_2d[bond.j], avg_d
                ))

    # Determine bounding box
    if all_pts:
        combined = np.vstack(all_pts) if len(all_pts) > 1 else all_pts[0]
        if len(combined) > 0:
            x_min, y_min = combined.min(axis=0)
            x_max, y_max = combined.max(axis=0)
        else:
            x_min = y_min = -1.0
            x_max = y_max = 1.0
    else:
        x_min = y_min = -1.0
        x_max = y_max = 1.0

    # Add padding
    x_range = max(x_max - x_min, 0.01)
    y_range = max(y_max - y_min, 0.01)
    pad = 0.05
    x_min -= x_range * pad
    x_max += x_range * pad
    y_min -= y_range * pad
    y_max += y_range * pad
    x_range = x_max - x_min
    y_range = y_max - y_min

    # Aspect-ratio-aware fitting (terminal chars are ~2:1 aspect)
    char_aspect = 2.0  # Terminal char height / width ratio
    data_aspect = y_range / x_range if x_range > 0 else 1.0
    grid_aspect = (height / width) * char_aspect

    if data_aspect > grid_aspect:
        # Data is taller → fit to height, narrow x
        scale_y = (height - 1) / y_range
        scale_x = scale_y / char_aspect
    else:
        # Data is wider → fit to width, shorten y
        scale_x = (width - 1) / x_range
        scale_y = scale_x * char_aspect

    def to_grid(pt_2d: np.ndarray) -> tuple[int, int]:
        """Convert 2D projected point to (row, col) grid coords."""
        x, y = pt_2d
        col = int((x - x_min) * scale_x)
        row = int((y_max - y) * scale_y)  # Flip Y (screen Y is top-down)
        return row, col

    # ── Draw layers (back-to-front) ─────────────────────────────────────

    # 1. Cell edges (lowest priority)
    if cell_segments_2d:
        for start_2d, end_2d, seg_depth in cell_segments_2d:
            _draw_segment(frame, to_grid, start_2d, end_2d, seg_depth,
                          color=CELL_COLOR, chars="+-|/\\")

    # 2. Bonds
    if bond_segments_2d:
        for start_2d, end_2d, seg_depth in bond_segments_2d:
            _draw_segment(frame, to_grid, start_2d, end_2d, seg_depth,
                          color=BOND_COLOR, chars="--||//")

    # 3. Atoms (highest priority, drawn last = on top)
    if len(pts_2d) > 0:
        # Sort by depth (back-to-front: smallest depth first)
        order = np.argsort(depth)
        for idx in order:
            row, col = to_grid(pts_2d[idx])
            elem = crystal.atoms[idx].element
            d = float(depth[idx])
            color = ELEMENT_COLORS.get(elem, DEFAULT_COLOR)

            if compact:
                glyph = Glyph(
                    text="*" if mono else "●",
                    display_width=1,
                    color=color,
                    depth=d,
                    is_atom=True,
                )
            else:
                # 2-char element symbol, pad to 2
                text = elem[:2].ljust(2) if len(elem) < 2 else elem[:2]
                glyph = Glyph(
                    text=text,
                    display_width=2,
                    color=color,
                    depth=d,
                    is_atom=True,
                )
            frame.put(row, col, glyph)

    return frame.to_string(mono=mono)


# ── Segment rasterization ───────────────────────────────────────────────────


def _draw_segment(
    frame: TerminalFrame,
    to_grid,
    start_2d: np.ndarray,
    end_2d: np.ndarray,
    seg_depth: float,
    *,
    color: int,
    chars: str = "--||//",
) -> None:
    """Rasterize a line segment on the grid using Bresenham-like stepping."""
    r0, c0 = to_grid(start_2d)
    r1, c1 = to_grid(end_2d)

    dr = r1 - r0
    dc = c1 - c0
    steps = max(abs(dr), abs(dc), 1)

    # Pick character based on angle
    if abs(dc) < 1:
        ch = "|"
    elif abs(dr) < 1:
        ch = "-"
    elif (dr > 0 and dc > 0) or (dr < 0 and dc < 0):
        ch = "\\"
    else:
        ch = "/"

    for step in range(steps + 1):
        t = step / steps if steps > 0 else 0
        r = int(r0 + dr * t)
        c = int(c0 + dc * t)
        glyph = Glyph(
            text=ch,
            display_width=1,
            color=color,
            depth=seg_depth,
            is_atom=False,
        )
        frame.put(r, c, glyph)
