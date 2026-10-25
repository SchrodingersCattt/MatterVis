"""Independently installed MatterVis frame renderer.

The policy is deliberately unlike ``mattervis.native-v1``: visible sites
become a single ASCII depth mark, bonds become a midpoint dot, and element
symbols are never drawn. Install this package and select
``example.marks-v1``. Do not copy it into ``mat_viewer``.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from mat_viewer.tui.render_contract import RENDER_API_VERSION, REQUEST_SCHEMA

BACKEND_ID = "example.marks-v1"
MARKER_OPTION = "example.marks-v1.marker"
_DEPTH_FRONT = 0.67
_DEPTH_MID = 0.33
_DECLARED_VERSION = "0.1.0"


class MarkRenderer:
    """Depth-digit / marker picture over host-projected coordinates."""

    backend_id = BACKEND_ID
    api_version = RENDER_API_VERSION
    deterministic = True

    @property
    def package_version(self) -> str:
        try:
            return version("mattervis-mark-renderer")
        except PackageNotFoundError:
            return _DECLARED_VERSION

    @property
    def implementation_digest(self) -> str:
        return sha256(Path(__file__).read_bytes()).hexdigest()

    def validate_options(self, options: Mapping[str, object]) -> dict[str, str]:
        unknown = sorted(set(options) - {MARKER_OPTION})
        if unknown:
            names = ", ".join(unknown)
            raise ValueError(f"unsupported renderer options: {names}")
        marker = options.get(MARKER_OPTION, "#")
        if (
            not isinstance(marker, str)
            or len(marker) != 1
            or not 0x21 <= ord(marker) <= 0x7E
        ):
            raise ValueError(
                f"{MARKER_OPTION} must be one printable ASCII character"
            )
        return {MARKER_OPTION: marker}

    def render(self, request) -> str:
        if request.request_schema_version != REQUEST_SCHEMA:
            raise ValueError(
                f"unsupported render request schema {request.request_schema_version!r}"
            )
        options = request.render_options
        marker = str(options.backend_options.get(MARKER_OPTION, "#"))
        width = int(options.width)
        height = int(options.height)
        grid = [[" "] * width for _ in range(height)]
        placed, bounds = _place_sites(request, marker)
        for row, col, glyph, depth in sorted(placed, key=lambda item: item[3]):
            if 0 <= row < height and 0 <= col < width:
                grid[row][col] = glyph
        if options.show_bonds and bounds is not None:
            _place_bond_dots(request, grid, bounds)
        return "\n".join("".join(row) for row in grid)


def create_renderer() -> MarkRenderer:
    """Zero-argument entry-point factory."""
    return MarkRenderer()


def _place_sites(request, marker: str):
    options = request.render_options
    visible = [site for site in request.display_ir.sites if site.visible]
    if not visible:
        return [], None
    points = [request.view.projected_xy[site.index] for site in visible]
    depths = [request.view.projected_depth[site.index] for site in visible]
    low_x = min(point[0] for point in points)
    high_x = max(point[0] for point in points)
    low_y = min(point[1] for point in points)
    high_y = max(point[1] for point in points)
    low_depth = min(depths)
    high_depth = max(depths)
    placed = []
    for point, depth in zip(points, depths):
        placed.append(
            (
                _cell(point[1], low_y, high_y, options.height, invert=True),
                _cell(point[0], low_x, high_x, options.width, invert=False),
                _glyph(depth, low_depth, high_depth, marker, show_depth=options.show_depth),
                depth,
            )
        )
    return placed, (low_x, high_x, low_y, high_y)


def _glyph(depth: float, low: float, high: float, marker: str, *, show_depth: bool) -> str:
    if not show_depth:
        return marker
    span = high - low
    if span <= 1e-12:
        return "2"
    norm = (depth - low) / span
    if norm >= _DEPTH_FRONT:
        return "1"
    if norm >= _DEPTH_MID:
        return "2"
    return "3"


def _cell(value: float, low: float, high: float, size: int, *, invert: bool) -> int:
    if size <= 1:
        return 0
    span = high - low
    if span <= 1e-12:
        return 0
    fraction = (low + span - value) / span if invert else (value - low) / span
    index = round(fraction * (size - 1))
    return min(max(index, 0), size - 1)


def _place_bond_dots(request, grid: list[list[str]], bounds: tuple[float, float, float, float]) -> None:
    sites = request.display_ir.sites
    visible = {site.index for site in sites if site.visible}
    height = len(grid)
    width = len(grid[0]) if grid else 0
    low_x, high_x, low_y, high_y = bounds
    for bond in request.display_ir.bonds:
        if not bond.enabled or bond.i not in visible or bond.j not in visible:
            continue
        start = request.view.projected_xy[bond.i]
        end = request.view.projected_xy[bond.j]
        row = _cell((start[1] + end[1]) / 2.0, low_y, high_y, height, invert=True)
        col = _cell((start[0] + end[0]) / 2.0, low_x, high_x, width, invert=False)
        if grid[row][col] == " ":
            grid[row][col] = "."


__all__ = ["BACKEND_ID", "MARKER_OPTION", "MarkRenderer", "create_renderer"]
