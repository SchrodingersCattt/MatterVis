"""Terminal-based crystal structure viewer.

This package provides:
- A non-interactive ASCII/structured output for LLM consumption.
- An interactive Textual TUI for human debugging.

Entry point: ``matvis tui <file>``
"""

from __future__ import annotations

__all__ = ["run_tui"]


def run_tui(
    path: str,
    *,
    interactive: bool = True,
    mono: bool = False,
    format: str = "ascii",
    projection: str = "orthographic",
    width: int | None = None,
    height: int | None = None,
    view: str = "auto",
) -> None:
    """Launch the terminal crystal viewer.

    Parameters
    ----------
    path : str
        Path to a CIF, POSCAR, or VASP file.
    interactive : bool
        If True, launch the Textual TUI. If False, print to stdout.
    mono : bool
        Force monochrome output (no ANSI color codes).
    format : str
        Non-interactive output format: "ascii" or "structured".
    projection : str
        Initial projection mode: "orthographic" or "perspective".
    width, height : int or None
        Override terminal grid dimensions (auto-detect if None).
    view : str
        Initial view direction: "auto", "a", "b", "c", or "diagonal".
    """
    from .loader_adapter import load_for_tui

    crystal = load_for_tui(path)

    if not interactive:
        from .serializer import serialize_crystal
        from .renderer import render_ascii_frame
        from ..math.camera import Camera, project_points

        cam = Camera.from_view_name(view, crystal)
        pts_2d, depth = project_points(cam, crystal.cart_coords)

        if format == "structured":
            print(serialize_crystal(crystal, cam, pts_2d))
        else:
            frame = render_ascii_frame(
                crystal, cam, pts_2d, depth,
                width=width, height=height, mono=mono,
            )
            print(frame)
    else:
        from .app import CrystalTUI

        app = CrystalTUI(crystal=crystal, mono=mono, initial_view=view)
        app.run()
