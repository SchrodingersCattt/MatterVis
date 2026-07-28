"""Pure assembly helpers for terminal-controller observations."""

from __future__ import annotations

from typing import Any

from .compositor import resolve_label_mode, resolve_molecule_detail
from .state import TerminalCameraState, TerminalDisplayState
from .summary import build_scope_summary
from .text import terminal_text


def build_terminal_title(
    crystal,
    camera: TerminalCameraState,
    display: TerminalDisplayState,
    *,
    width: int,
    height: int,
) -> str:
    """Build the compact human-readable title from canonical state."""
    resolved_label = (
        resolve_label_mode(
            display.label_mode,
            atom_count=sum(display.show_minor or not atom.is_minor for atom in crystal.atoms),
            width=width,
            height=height,
            zoom=camera.zoom,
        )
        if display.label_mode == "auto"
        else display.label_mode
    )
    summary = build_scope_summary(
        crystal,
        show_minor=display.show_minor,
        display_level=display.display_level,
    )
    count_parts: list[str] = []
    if summary["expanded_atom_count"] is not None:
        count_parts.append(f"{summary['expanded_atom_count']} expanded")
    count_parts.append(f"{summary['display_atom_count']} displayed")
    if summary["visible_atom_count"] != summary["display_atom_count"]:
        count_parts.append(f"{summary['visible_atom_count']} visible")
    zoom = f" ×{camera.zoom:.1f}" if camera.zoom != 1.0 else ""
    roll = f" r={camera.roll:.0f}°" if abs(camera.roll) > 0.5 else ""
    level = f" [{display.display_level}]" if display.display_level != "atom" else ""
    if display.display_level == "molecule":
        molecule_count = sum(len(indices) for indices in crystal.species_map.values())
        level = f" [molecule:{resolve_molecule_detail(molecule_count=molecule_count, width=width, height=height)}]"
    return (
        f"{terminal_text(summary['canonical_formula'])} {'/'.join(count_parts)} "
        f"[{terminal_text(summary['display_mode'])}] | "
        f"az={camera.azimuth:.0f}° el={camera.elevation:.0f}°{roll} | "
        f"{camera.projection[:5]} | {resolved_label}{zoom}{level}"
    )


def build_observation_scope(crystal, display: TerminalDisplayState) -> dict[str, Any]:
    """Return a detached scope summary without exposing analytical answers."""
    return dict(build_scope_summary(
        crystal,
        show_minor=display.show_minor,
        display_level=display.display_level,
    ))


__all__ = ["build_observation_scope", "build_terminal_title"]