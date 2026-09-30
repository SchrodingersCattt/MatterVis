"""Pure assembly helpers for terminal-controller observations."""

from __future__ import annotations

from typing import Any

from .compositor import (
    resolve_display_level,
    resolve_label_mode,
    resolve_molecule_detail,
)
from .framework import structure_is_framework_like
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
        if structure_is_framework_like(crystal, show_minor=display.show_minor):
            level = " [framework skeleton]"
        else:
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


def build_static_header(
    crystal,
    camera,
    *,
    display_level: str,
    label_mode: str,
    show_bonds: bool,
    show_cell: bool,
    show_minor: bool,
    mono: bool,
    width: int,
    height: int,
) -> str:
    """Build the short human header shared by static and helper output."""
    resolved_level = resolve_display_level(
        display_level,
        atom_count=len(crystal.atoms),
        molecule_count=sum(len(indices) for indices in crystal.species_map.values()),
        framework_like=structure_is_framework_like(crystal, show_minor=show_minor),
    )
    camera_state = TerminalCameraState(
        azimuth=float(camera.azimuth),
        elevation=float(camera.elevation),
        roll=float(camera.roll),
        target=tuple(float(value) for value in camera.target),
        projection=camera.projection.value,
        zoom=float(camera.viewport_zoom),
        pan_x=float(camera.pan_x),
        pan_y=float(camera.pan_y),
    )
    display_state = TerminalDisplayState(
        display_level=resolved_level,
        label_mode=label_mode,
        show_bonds=show_bonds,
        show_cell=show_cell,
        show_minor=show_minor,
        mono=mono,
    )
    title = build_terminal_title(
        crystal,
        camera_state,
        display_state,
        width=width,
        height=height,
    )
    if resolved_level == "molecule" and structure_is_framework_like(
        crystal, show_minor=show_minor
    ):
        legend = (
            "legend: Braille nodes/lines show the framework; dashed marks partial/disorder; "
            "select or focus for labels"
        )
        return legend + "\n" + title
    if resolved_level == "molecule":
        return "legend: each marker is one displayed molecule; labels identify species\n" + title
    return title


__all__ = ["build_observation_scope", "build_static_header", "build_terminal_title"]
