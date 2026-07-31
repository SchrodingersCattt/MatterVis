"""Terminal-based crystal structure viewer.

This package provides:
- A non-interactive ASCII/structured output for LLM consumption.
- An interactive Textual TUI for human debugging.

Entry point: ``matvis tui <file>``
"""

from __future__ import annotations

from dataclasses import replace

from .controller import TerminalViewController
from .state import (
    OBSERVATION_SCHEMA,
    TerminalCameraState,
    TerminalDisplayState,
    TerminalEditState,
    TerminalFocusState,
    TerminalObservation,
    TerminalPickToken,
    TerminalViewportState,
    TerminalViewSnapshot,
    TerminalViewState,
)

__all__ = [
    "OBSERVATION_SCHEMA",
    "TerminalCameraState",
    "TerminalDisplayState",
    "TerminalEditState",
    "TerminalFocusState",
    "TerminalObservation",
    "TerminalPickToken",
    "TerminalViewController",
    "TerminalViewportState",
    "TerminalViewSnapshot",
    "TerminalViewState",
    "run_tui",
]


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
    display_mode: str = "auto",
    show_minor: bool = False,
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
    display_mode : str
        CIF display slice: "unit_cell", "formula_unit", or "asymmetric_unit".
    show_minor : bool
        Show minor disorder alternatives. Hidden by default.
    """
    from .loader_adapter import load_for_tui

    crystal = load_for_tui(path, display_mode=display_mode)

    from ..math.camera import Camera, ProjectionMode, project_points

    cam = Camera.from_view_name(view, crystal)
    cam = replace(cam, projection=ProjectionMode(projection))

    if not interactive:
        from .serializer import serialize_crystal
        from .compositor import compose_frame
        pts_2d, depth = project_points(cam, crystal.cart_coords)

        if format == "structured":
            print(serialize_crystal(
                crystal,
                cam,
                pts_2d,
                show_minor=show_minor,
            ))
        else:
            frame = compose_frame(
                crystal, cam, pts_2d, depth,
                width=width, height=height, mono=mono,
                show_minor=show_minor,
            )
            print(frame)
    else:
        from .app import CrystalTUI

        app = CrystalTUI(
            crystal=crystal,
            mono=mono,
            initial_view=view,
            camera=cam,
            show_minor=show_minor,
            initial_level=(
                "molecule"
                if display_mode == "auto"
                and crystal.species_map
                and crystal.n_atoms > 64
                else "atom"
            ),
        )
        app.run()
