"""Braille subpixel canvas for terminal rendering.

Each terminal character encodes a 2×4 dot matrix (Unicode Braille
U+2800–U+28FF), giving 8× effective resolution over character grids.
An 80×40 terminal becomes 160×160 subpixels.

MIT-compatible, zero external dependencies, pure Python + numpy.
"""

from __future__ import annotations


# Subpixel (row, col) → bit mask in the braille codepoint offset.
# Layout:
#   Col:  0    1
#   Row 0: dot1 dot4   (bit 0, bit 3)
#   Row 1: dot2 dot5   (bit 1, bit 4)
#   Row 2: dot3 dot6   (bit 2, bit 5)
#   Row 3: dot7 dot8   (bit 6, bit 7)
_BRAILLE_MAP = (
    (0x01, 0x08),  # row 0
    (0x02, 0x10),  # row 1
    (0x04, 0x20),  # row 2
    (0x40, 0x80),  # row 3
)

_BRAILLE_BASE = 0x2800


def _ascii7_cell(bits: int) -> str:
    """Approximate one 2x4 mask perceptually; this is not reversible Braille."""
    if bits == 0:
        return " "
    points = [
        (row, col)
        for row in range(4)
        for col in range(2)
        if bits & _BRAILLE_MAP[row][col]
    ]
    if len(points) == 1:
        return "."
    rows = {row for row, _ in points}
    cols = {col for _, col in points}
    if len(rows) == 1:
        return "-"
    if len(cols) == 1:
        return "|"
    covariance = sum((row - 1.5) * (col - 0.5) for row, col in points)
    if len(points) <= 4 and covariance:
        return "\\" if covariance > 0 else "/"
    if len(points) <= 5:
        return "+"
    return "#"


class BrailleCanvas:
    """Braille-based subpixel canvas for smooth line rendering.

    Parameters
    ----------
    width : int
        Width in terminal characters.
    height : int
        Height in terminal characters.
    """

    __slots__ = ("width", "height", "_buffer", "_color_buffer")

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self._buffer = [[0] * width for _ in range(height)]
        # Per-cell color attribution (ANSI 256 code). 0 = default.
        # Last-writer-wins: because atoms are drawn back-to-front,
        # the front atom's color naturally overwrites the back atom's.
        self._color_buffer = [[0] * width for _ in range(height)]

    @property
    def px_width(self) -> int:
        """Subpixel width (2× char width)."""
        return self.width * 2

    @property
    def px_height(self) -> int:
        """Subpixel height (4× char height)."""
        return self.height * 4

    def clear(self) -> None:
        """Reset all pixels."""
        for row in self._buffer:
            for i in range(len(row)):
                row[i] = 0
        for row in self._color_buffer:
            for i in range(len(row)):
                row[i] = 0

    def set_pixel(self, x: int, y: int, *, color: int = 0) -> None:
        """Set a subpixel at (x, y). Origin is top-left.

        Parameters
        ----------
        color : int
            ANSI 256 color code to attribute to this cell.
            Last-writer-wins per cell (painter's algorithm).
        """
        if x < 0 or x >= self.px_width or y < 0 or y >= self.px_height:
            return
        cell_col = x >> 1       # x // 2
        cell_row = y >> 2       # y // 4
        sub_col = x & 1        # x % 2
        sub_row = y & 3        # y % 4
        self._buffer[cell_row][cell_col] |= _BRAILLE_MAP[sub_row][sub_col]
        if color:
            self._color_buffer[cell_row][cell_col] = color

    def get_pixel(self, x: int, y: int) -> bool:
        """Check if a subpixel is set."""
        if x < 0 or x >= self.px_width or y < 0 or y >= self.px_height:
            return False
        cell_col = x >> 1
        cell_row = y >> 2
        sub_col = x & 1
        sub_row = y & 3
        return bool(self._buffer[cell_row][cell_col] & _BRAILLE_MAP[sub_row][sub_col])

    def draw_line(
        self, x0: int, y0: int, x1: int, y1: int, *, color: int = 0
    ) -> None:
        """Draw a line using Bresenham's algorithm in subpixel space."""
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy

        while True:
            self.set_pixel(x0, y0, color=color)
            if x0 == x1 and y0 == y1:
                break
            e2 = err << 1  # 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def draw_dashed_line(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        dash: int = 3,
        gap: int = 2,
        *,
        color: int = 0,
    ) -> None:
        """Draw a dashed line (for minor-disorder bonds).

        Parameters
        ----------
        dash : int
            Subpixels drawn per dash segment.
        gap : int
            Subpixels skipped per gap segment.
        color : int
            ANSI 256 color code for this line.
        """
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        count = 0
        cycle = dash + gap

        while True:
            if (count % cycle) < dash:
                self.set_pixel(x0, y0, color=color)
            count += 1
            if x0 == x1 and y0 == y1:
                break
            e2 = err << 1
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def get_char(self, row: int, col: int) -> str:
        """Get the braille character at a grid position."""
        if row < 0 or row >= self.height or col < 0 or col >= self.width:
            return " "
        bits = self._buffer[row][col]
        if bits == 0:
            return " "
        return chr(_BRAILLE_BASE + bits)

    def render(self) -> list[str]:
        """Render the canvas to a list of strings (one per row)."""
        lines: list[str] = []
        for row in self._buffer:
            chars = []
            for bits in row:
                if bits == 0:
                    chars.append(" ")
                else:
                    chars.append(chr(_BRAILLE_BASE + bits))
            lines.append("".join(chars).rstrip())
        return lines

    def render_to_string(self) -> str:
        """Render the full canvas as a single string."""
        lines = self.render()
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    def render_colored(self, *, charset: str = "unicode") -> list[list[tuple[str, int]]]:
        """Render canvas with per-cell color attribution.

        Returns
        -------
        list of rows, each row is a list of (char, color) tuples.
        Color 0 means "use default/CELL_COLOR".
        """
        result: list[list[tuple[str, int]]] = []
        for row_idx in range(self.height):
            row_data: list[tuple[str, int]] = []
            for col_idx in range(self.width):
                bits = self._buffer[row_idx][col_idx]
                color = self._color_buffer[row_idx][col_idx]
                if charset == "unicode":
                    character = " " if bits == 0 else chr(_BRAILLE_BASE + bits)
                elif charset == "ascii7":
                    character = _ascii7_cell(bits)
                else:
                    raise ValueError("charset must be 'unicode' or 'ascii7'")
                row_data.append((character, color if bits else 0))
            result.append(row_data)
        return result
