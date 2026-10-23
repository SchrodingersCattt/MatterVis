"""Readable terminal picture: element symbols, solid bonds, depth occlusion.

Atom and molecule views share this grid so a person or an agent can recover which element is
where. Each cell keeps the closest depth written into it; a farther atom
or bond is not drawn there.

Hydrogen and periodic boundary replicas are omitted unless the caller asks
for them. Labels longer than ``MAX_CANVAS_LABEL_COLUMNS`` are not stamped
on the picture; the selection inspector already carries the full label.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import numpy as np

from ..config import element_color
from ..utils.colors import ansi256_from_hex
from .annotations import copy_aware_label
from .depth import (
    BOND_DASH_LENGTH,
    BOND_DASH_PERIOD,
    CROSSING_GAP,
    DEPTH_EPSILON,
    depth_band,
    depth_marker,
)
from .text import ascii7_text, terminal_text

if TYPE_CHECKING:
    from .crystal_ir import CrystalIR
    from .projection import Viewport

MAX_CANVAS_LABEL_COLUMNS = 8
# Copy-aware labels carry a symmetry index and optional lattice shift; they
# need more columns than a site label alone or the #210 modes become silent.
MAX_COPY_LABEL_COLUMNS = 16
_BOND_RGB = (148, 148, 148)
_CELL_RGB = (90, 140, 190)
_LABEL_RGB = (230, 230, 230)
_LABEL_OFFSETS = (
    (0, 1),
    (0, -1),
    (1, 0),
    (-1, 0),
    (1, 1),
    (-1, 1),
    (0, 2),
    (1, -1),
    (-1, -1),
    (2, 0),
)


def render_glyph_frame(
    crystal: CrystalIR,
    camera,
    pts_2d: np.ndarray,
    depth: np.ndarray,
    viewport: Viewport,
    *,
    width: int,
    height: int,
    mono: bool,
    charset: str,
    label_mode: str,
    show_bonds: bool,
    show_cell: bool,
    show_minor: bool,
    show_hydrogen: bool,
    show_boundary: bool,
    selected_display_index: int | None,
    show_depth: bool = True,
    slice_distance: np.ndarray | None = None,
    visible_indices: list[int] | None = None,
    ghost_points: np.ndarray | None = None,
) -> str:
    """Draw one atom-level frame into ``width`` by ``height`` character cells."""
    grid = _Grid(width, height, mono=mono)
    visible = _visible_indices(
        crystal,
        pts_2d,
        depth,
        show_minor=show_minor,
        show_hydrogen=show_hydrogen,
        show_boundary=show_boundary,
    )
    if visible_indices is not None:
        allowed = set(visible_indices)
        visible = [index for index in visible if index in allowed]
    if slice_distance is not None:
        visible = [index for index in visible if slice_distance[index] <= 1]
    grid.depth_range = (
        (min(float(depth[i]) for i in visible), max(float(depth[i]) for i in visible))
        if visible
        else (0.0, 0.0)
    )
    grid.show_depth = show_depth
    grid.slice_distance = slice_distance
    if show_cell and crystal.lattice is not None:
        _draw_cell(grid, crystal, camera, viewport)
    if show_bonds:
        _draw_bonds(grid, crystal, camera, pts_2d, depth, viewport, visible)
    grid.resolve_strokes()
    _draw_atoms(
        grid,
        crystal,
        pts_2d,
        depth,
        viewport,
        visible,
        label_mode=label_mode,
        charset=charset,
        selected_display_index=selected_display_index,
    )
    if ghost_points is not None:
        _draw_ghosts(grid, ghost_points[visible], viewport, charset)
    return grid.to_string()


def _draw_ghosts(grid, points, viewport, charset) -> None:
    for point in points:
        row, col = viewport.to_grid(*point)
        if grid.in_bounds(row, col) and not grid.kind[row][col]:
            grid.place(
                row,
                col,
                "." if charset == "ascii7" else "·",
                -1e30,
                rgb=_BOND_RGB,
                kind="ghost",
                style="dim",
            )


def _visible_indices(
    crystal,
    pts_2d,
    depth,
    *,
    show_minor: bool,
    show_hydrogen: bool,
    show_boundary: bool,
) -> list[int]:
    limit = min(len(crystal.atoms), len(pts_2d), len(depth))
    chosen: list[int] = []
    for index in range(limit):
        atom = crystal.atoms[index]
        if not show_minor and atom.is_minor:
            continue
        if not show_hydrogen and atom.element == "H":
            continue
        if not show_boundary and atom.is_boundary_replica:
            continue
        chosen.append(index)
    return chosen


def _draw_cell(grid: _Grid, crystal, camera, viewport) -> None:
    from ..math.camera import project_points as project

    vertices = crystal.lattice.cell_vertices()
    points, depths = project(camera, vertices)
    for start, end in crystal.lattice.cell_edges():
        row0, col0 = viewport.to_grid(float(points[start][0]), float(points[start][1]))
        row1, col1 = viewport.to_grid(float(points[end][0]), float(points[end][1]))
        grid.stroke(
            row0,
            col0,
            row1,
            col1,
            float(depths[start]),
            float(depths[end]),
            text=":",
            rgb=_CELL_RGB,
            kind="cell",
        )


def _draw_bonds(grid, crystal, camera, pts_2d, depth, viewport, visible) -> None:
    from ..math.camera import project_points as project

    shown = set(visible)
    for bond in crystal.bonds:
        if bond.i not in shown or bond.j not in shown:
            continue
        if bond.start is not None and bond.end is not None:
            points, depths = project(
                camera, np.asarray([bond.start, bond.end], dtype=float)
            )
            start, end = points[0], points[1]
            depth0, depth1 = float(depths[0]), float(depths[1])
        elif bond.i < len(pts_2d) and bond.j < len(pts_2d):
            start, end = pts_2d[bond.i], pts_2d[bond.j]
            depth0, depth1 = float(depth[bond.i]), float(depth[bond.j])
        else:
            continue
        row0, col0 = viewport.to_grid(float(start[0]), float(start[1]))
        row1, col1 = viewport.to_grid(float(end[0]), float(end[1]))
        grid.stroke(
            row0,
            col0,
            row1,
            col1,
            depth0,
            depth1,
            text=None,
            rgb=_BOND_RGB,
            kind="bond",
            owners=(bond.i, bond.j),
            style="dim"
            if grid.slice_distance is not None
            and (grid.slice_distance[bond.i] or grid.slice_distance[bond.j])
            else "normal",
        )


def _draw_atoms(
    grid,
    crystal,
    pts_2d,
    depth,
    viewport,
    visible,
    *,
    label_mode: str,
    charset: str,
    selected_display_index: int | None,
) -> None:
    # Whole tokens are accepted front-to-back: an occluded Cl must not leave
    # a stray 'l', and a hidden atom must not receive a detached label.
    order = sorted(visible, key=lambda index: (-float(depth[index]), -index))
    for index in order:
        atom = crystal.atoms[index]
        row, col = viewport.to_grid(float(pts_2d[index][0]), float(pts_2d[index][1]))
        text = _marker(
            atom, label_mode, charset, selected=index == selected_display_index
        )
        band = depth_band(float(depth[index]), *grid.depth_range)
        if grid.show_depth:
            text += depth_marker(band, charset)
        else:
            band = 1
        adjacent = grid.slice_distance is not None and grid.slice_distance[index] == 1
        if adjacent:
            text = "(" + text + ")"
        if not grid.in_bounds(row, col):
            continue
        row, col, text = grid.fit_text(row, col, text)
        grid.place(
            row,
            col,
            text,
            float(depth[index]),
            rgb=_element_rgb(atom.element),
            kind="atom",
            anchor=index,
            style="dim"
            if adjacent
            else "bold"
            if grid.slice_distance is not None or band == 0
            else "dim"
            if band == 2
            else "normal",
        )


def _marker(atom, label_mode: str, charset: str, *, selected: bool) -> str:
    if label_mode == "dot":
        text = "*" if charset == "ascii7" else "●"
    else:
        text = atom.element
        label = _label_text(atom, label_mode, charset, selected=False)
        limit = (
            MAX_COPY_LABEL_COLUMNS
            if label_mode == "label_sym"
            else MAX_CANVAS_LABEL_COLUMNS
        )
        if label and len(label) <= limit:
            text = label
        if atom.occupancy < 0.99 and not text.endswith("*"):
            text += "*"
    if selected:
        if label_mode not in {"element", "dot"}:
            label = _label_text(atom, label_mode, charset, selected=False)
            if label and len(label) <= MAX_COPY_LABEL_COLUMNS:
                text = label
        text = f"[{text}]"
    return text


def _label_text(atom, label_mode: str, charset: str, *, selected: bool) -> str | None:
    formatter = ascii7_text if charset == "ascii7" else terminal_text
    if label_mode == "label":
        text = formatter(atom.display_label)
    elif label_mode == "label_sym":
        text = formatter(copy_aware_label(atom))
    elif label_mode == "molecule":
        text = formatter(atom.display_label)
        if atom.molecule_index >= 0:
            text += str(atom.molecule_index)
    else:
        return None
    if atom.occupancy < 0.99 and not text.endswith("*"):
        text += "*"
    if selected:
        text = f"[{text}]"
    if text == atom.element or text == f"[{atom.element}]":
        return None
    return text


def _bond_char(d_row: int, d_col: int) -> str:
    if abs(d_col) < 1:
        return "|"
    if abs(d_row) < 1:
        return "-"
    if (d_row > 0 and d_col > 0) or (d_row < 0 and d_col < 0):
        return "\\"
    return "/"


def _element_rgb(element: str) -> tuple[int, int, int]:
    text = str(element_color(element) or "#808080").strip().lstrip("#")
    if len(text) != 6:
        return (128, 128, 128)
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return (128, 128, 128)


def _color_mode(mono: bool) -> str:
    if mono:
        return "mono"
    advertised = os.environ.get("COLORTERM", "").lower()
    if advertised in {"truecolor", "24bit"}:
        return "truecolor"
    return "256"


class _Grid:
    """Character grid with one depth sample per cell. Larger depth is closer."""

    def __init__(self, width: int, height: int, *, mono: bool) -> None:
        self.width = width
        self.height = height
        self.color_mode = _color_mode(mono)
        self.chars = [[" "] * width for _ in range(height)]
        self.depth = [[-1.0e30] * width for _ in range(height)]
        self.rgb = [[(0, 0, 0)] * width for _ in range(height)]
        self.kind = [[""] * width for _ in range(height)]
        self.anchor = [[None] * width for _ in range(height)]
        self.bond_owners = [[None] * width for _ in range(height)]
        self._owned_anchors: set[int] = set()
        self.styles = [["normal"] * width for _ in range(height)]
        self.depth_range = (0.0, 0.0)
        self.show_depth = True
        self.slice_distance = None
        self._strokes: list[
            tuple[
                list[tuple[int, int, float]],
                str | None,
                tuple[int, int, int],
                tuple[int, int] | None,
                str,
            ]
        ] = []

    def in_bounds(self, row: int, col: int) -> bool:
        return 0 <= row < self.height and 0 <= col < self.width

    def place(
        self,
        row: int,
        col: int,
        text: str,
        depth: float,
        *,
        rgb: tuple[int, int, int],
        kind: str,
        anchor: int | None = None,
        style: str = "normal",
    ) -> bool:
        """Place a whole token; atom tokens are submitted front-to-back."""
        cells = self._span(row, col, text)
        if cells is None:
            return False
        for r, c in cells:
            existing = self.kind[r][c]
            if existing == "surface" and self.depth[r][c] > depth + DEPTH_EPSILON:
                return False
            if existing == "atom" and self.anchor[r][c] != anchor:
                return False
            # Cell edges are a guide. Atoms cover them, and they also cover a
            # bond that only ties the same depth at the atom centre.
            if existing in {"atom", "label"} and self.depth[r][c] > depth:
                return False
            if existing == "bond":
                owners = self.bond_owners[r][c]
                # An atom may always reclaim cells from its own bonds; otherwise
                # only a clearly closer bond blocks the symbol.
                if owners is not None and anchor in owners:
                    continue
                if self.depth[r][c] > depth + 1.0e-9:
                    return False
        for offset, (r, c) in enumerate(cells):
            self.chars[r][c] = text[offset]
            self.depth[r][c] = depth
            self.rgb[r][c] = rgb
            self.kind[r][c] = kind
            self.anchor[r][c] = anchor
            self.bond_owners[r][c] = None
            self.styles[r][c] = style
        if anchor is not None:
            self._owned_anchors.add(anchor)
        return True

    def fit_text(self, row: int, col: int, text: str) -> tuple[int, int, str]:
        """Shift a glyph into the grid instead of dropping an edge atom."""
        if len(text) > self.width:
            text = text[: self.width]
        row = min(max(int(row), 0), self.height - 1)
        start = int(col) - (len(text) - 1) // 2
        start = min(max(start, 0), self.width - len(text))
        return row, start, text

    def owns(self, anchor: int) -> bool:
        return anchor in self._owned_anchors

    def place_in_gaps(self, row: int, col: int, text: str, *, anchor: int) -> bool:
        """Place a label in a gap, with a column separating adjacent tokens."""
        cells = self._span(row, col, text)
        if cells is None:
            return False
        for r, c in cells:
            if self.kind[r][c] in {"atom", "bond", "label"}:
                return False
        for c in (col - 1, col + len(text)):
            if self.in_bounds(row, c) and self.kind[row][c] in {
                "atom",
                "bond",
                "label",
            }:
                return False
        for offset, (r, c) in enumerate(cells):
            self.chars[r][c] = text[offset]
            self.kind[r][c] = "label"
            self.rgb[r][c] = _LABEL_RGB
            self.anchor[r][c] = anchor
        return True

    def stroke(
        self,
        row0: int,
        col0: int,
        row1: int,
        col1: int,
        depth0: float,
        depth1: float,
        *,
        text: str | None,
        rgb: tuple[int, int, int],
        kind: str,
        owners: tuple[int, int] | None = None,
        style: str = "normal",
    ) -> None:
        clipped = _clip_to_grid(row0, col0, row1, col1, self.height, self.width)
        if clipped is None:
            return
        points = list(_line_cells(*clipped))
        # Interpolate on the original segment, not its clipped endpoints.
        dr, dc = row1 - row0, col1 - col0
        denominator = max(dr * dr + dc * dc, 1)
        samples = [
            (
                row,
                col,
                depth0
                + (depth1 - depth0)
                * min(
                    1.0, max(0.0, ((row - row0) * dr + (col - col0) * dc) / denominator)
                ),
            )
            for row, col in points
        ]
        if kind == "bond":
            self._strokes.append((samples, text, rgb, owners, style))
            return
        prev_row, prev_col = points[0]
        for step, (row, col, sampled) in enumerate(samples):
            if not (0 <= row < self.height and 0 <= col < self.width):
                continue
            if self.depth[row][col] >= sampled:
                prev_row, prev_col = row, col
                continue
            glyph = (
                text if text is not None else _bond_char(row - prev_row, col - prev_col)
            )
            if text is None and step == 0 and len(points) > 1:
                # First cell has no previous step; aim at the next cell.
                next_row, next_col = points[1]
                glyph = _bond_char(next_row - row, next_col - col)
            self.chars[row][col] = glyph
            self.depth[row][col] = sampled
            self.rgb[row][col] = rgb
            self.kind[row][col] = kind
            self.anchor[row][col] = None
            self.bond_owners[row][col] = owners if kind == "bond" else None
            prev_row, prev_col = row, col

    def resolve_strokes(self) -> None:
        """Break the farther line on both sides of a crossing, in any order."""
        by_cell: dict[tuple[int, int], list[tuple[int, int, float]]] = {}
        for line, (samples, *_rest) in enumerate(self._strokes):
            for step, (row, col, depth) in enumerate(samples):
                by_cell.setdefault((2 * row, 2 * col), []).append((line, step, depth))
                if step:
                    r0, c0, d0 = samples[step - 1]
                    # Opposite diagonals may cross between four cells without
                    # sharing a raster cell. Index those half-cell crossings too.
                    if abs(row - r0) == 1 and abs(col - c0) == 1:
                        by_cell.setdefault((row + r0, col + c0), []).append(
                            (line, step - 1, (depth + d0) / 2)
                        )
        gaps: set[tuple[int, int]] = set()
        for candidates in by_cell.values():
            if len(candidates) < 2:
                continue
            for line, step, depth in candidates:
                owners = self._strokes[line][3]
                for other, _, nearer in candidates:
                    other_owners = self._strokes[other][3]
                    if other == line or nearer <= depth + DEPTH_EPSILON:
                        continue
                    if owners and other_owners and set(owners) & set(other_owners):
                        continue
                    a, b = self._strokes[line][0], self._strokes[other][0]
                    if len(a) < 2 or len(b) < 2:
                        continue
                    # Collinear overlapping edges use ordinary depth testing.
                    ar, ac = a[-1][0] - a[0][0], a[-1][1] - a[0][1]
                    br, bc = b[-1][0] - b[0][0], b[-1][1] - b[0][1]
                    if ar * bc == ac * br:
                        continue
                    gaps.update(
                        (line, k)
                        for k in range(step - CROSSING_GAP, step + CROSSING_GAP + 1)
                    )
        for line, (samples, text, rgb, owners, style) in enumerate(self._strokes):
            for step, (row, col, depth) in enumerate(samples):
                if (line, step) in gaps:
                    continue
                band = depth_band(depth, *self.depth_range)
                if (
                    self.show_depth
                    and band == 2
                    and step % BOND_DASH_PERIOD >= BOND_DASH_LENGTH
                ):
                    continue
                if not self.show_depth:
                    band = 1
                own_surface = (
                    self.kind[row][col] == "surface"
                    and owners
                    and self.anchor[row][col] in owners
                )
                if (
                    not own_surface
                    and self.kind[row][col] != "cell"
                    and self.depth[row][col] >= depth
                ):
                    continue
                previous = samples[max(0, step - 1)]
                following = samples[min(len(samples) - 1, step + 1)]
                glyph = text or _bond_char(
                    following[0] - previous[0], following[1] - previous[1]
                )
                self.chars[row][col] = glyph
                self.depth[row][col] = depth
                self.rgb[row][col] = rgb
                self.kind[row][col] = "bond"
                self.bond_owners[row][col] = owners
                self.styles[row][col] = (
                    "dim" if style == "dim" or band == 2 else "normal"
                )
        self._strokes.clear()

    def _span(self, row: int, col: int, text: str) -> list[tuple[int, int]] | None:
        if not text or row < 0 or row >= self.height:
            return None
        cells = [(row, col + offset) for offset in range(len(text))]
        if any(column < 0 or column >= self.width for _, column in cells):
            return None
        return cells

    def to_string(self) -> str:
        lines = [self._paint_row(row).rstrip() for row in range(self.height)]
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    def _paint_row(self, row: int) -> str:
        parts: list[str] = []
        column = 0
        while column < self.width:
            if self.color_mode == "mono" or not self.kind[row][column]:
                parts.append(self.chars[row][column])
                column += 1
                continue
            rgb = self.rgb[row][column]
            style = self.styles[row][column]
            run = [self.chars[row][column]]
            column += 1
            while (
                column < self.width
                and self.kind[row][column]
                and self.rgb[row][column] == rgb
                and self.styles[row][column] == style
            ):
                run.append(self.chars[row][column])
                column += 1
            parts.append(_paint("".join(run), rgb, self.color_mode, style))
        return "".join(parts)


def _paint(
    text: str, rgb: tuple[int, int, int], color_mode: str, style: str = "normal"
) -> str:
    emphasis = "1;" if style == "bold" else "2;" if style == "dim" else ""
    if color_mode == "truecolor":
        red, green, blue = rgb
        return f"\033[{emphasis}38;2;{red};{green};{blue}m{text}\033[0m"
    hex_color = "#{:02x}{:02x}{:02x}".format(*rgb)
    return f"\033[{emphasis}38;5;{ansi256_from_hex(hex_color)}m{text}\033[0m"


def _clip_to_grid(row0, col0, row1, col1, height: int, width: int):
    """Cohen–Sutherland clip. Returns integer endpoints inside the grid."""
    left, right, bottom, top = 1, 2, 4, 8

    def code(row: float, col: float) -> int:
        value = 0
        if col < 0:
            value |= left
        elif col >= width:
            value |= right
        if row < 0:
            value |= top
        elif row >= height:
            value |= bottom
        return value

    r0, c0, r1, c1 = float(row0), float(col0), float(row1), float(col1)
    code0, code1 = code(r0, c0), code(r1, c1)
    for _ in range(12):
        if code0 == 0 and code1 == 0:
            return round(r0), round(c0), round(r1), round(c1)
        if code0 & code1:
            return None
        outside = code0 or code1
        if outside & bottom:
            col = c0 + (c1 - c0) * (height - 1 - r0) / (r1 - r0 or 1e-12)
            row = float(height - 1)
        elif outside & top:
            col = c0 + (c1 - c0) * (0.0 - r0) / (r1 - r0 or 1e-12)
            row = 0.0
        elif outside & right:
            row = r0 + (r1 - r0) * (width - 1 - c0) / (c1 - c0 or 1e-12)
            col = float(width - 1)
        else:
            row = r0 + (r1 - r0) * (0.0 - c0) / (c1 - c0 or 1e-12)
            col = 0.0
        if outside == code0:
            r0, c0, code0 = row, col, code(row, col)
        else:
            r1, c1, code1 = row, col, code(row, col)
    return None


def _line_cells(row0: int, col0: int, row1: int, col1: int):
    """Bresenham cells. The span is already clipped to the grid."""
    d_row = abs(row1 - row0)
    d_col = abs(col1 - col0)
    step_row = 1 if row0 < row1 else -1
    step_col = 1 if col0 < col1 else -1
    row, col = row0, col0
    if d_col >= d_row:
        error = d_col / 2
        while True:
            yield row, col
            if col == col1:
                return
            error -= d_row
            if error < 0:
                row += step_row
                error += d_col
            col += step_col
    else:
        error = d_row / 2
        while True:
            yield row, col
            if row == row1:
                return
            error -= d_col
            if error < 0:
                col += step_col
                error += d_row
            row += step_row


__all__ = ["MAX_CANVAS_LABEL_COLUMNS", "render_glyph_frame"]
