"""Stable state and observation values for the semantic terminal viewer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


OBSERVATION_SCHEMA = "mattervis.tui.observation/v1"


@dataclass(frozen=True)
class TerminalCameraState:
    """JSON-safe public camera state.

    ``target`` is camera-control state, not an atom query. It is included so a
    serialized observation can reproduce the exact projection without exposing
    structure coordinates.
    """

    azimuth: float
    elevation: float
    roll: float
    target: tuple[float, float, float]
    projection: str
    zoom: float
    pan_x: float
    pan_y: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "azimuth": self.azimuth,
            "elevation": self.elevation,
            "roll": self.roll,
            "target": list(self.target),
            "projection": self.projection,
            "zoom": self.zoom,
            "pan_x": self.pan_x,
            "pan_y": self.pan_y,
        }


@dataclass(frozen=True)
class TerminalDisplayState:
    """Render choices independent from source display slicing."""

    display_level: str
    label_mode: str
    show_bonds: bool
    show_cell: bool
    show_minor: bool
    mono: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "display_level": self.display_level,
            "label_mode": self.label_mode,
            "show_bonds": self.show_bonds,
            "show_cell": self.show_cell,
            "show_minor": self.show_minor,
            "mono": self.mono,
        }


@dataclass(frozen=True)
class TerminalFocusState:
    """Resolved display-copy targets held by the current active view."""

    kind: str | None = None
    matched_copy_ids: tuple[str, ...] = ()
    framed_copy_ids: tuple[str, ...] = ()
    hidden_copy_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "matched_copy_ids": list(self.matched_copy_ids),
            "framed_copy_ids": list(self.framed_copy_ids),
            "hidden_copy_ids": list(self.hidden_copy_ids),
        }


@dataclass(frozen=True)
class TerminalEditState:
    """Selection state independent from source structure and camera state."""

    mode: str = "browse"
    level: str = "atom"
    selected_ids: tuple[str, ...] = ()
    active_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "level": self.level,
            "selected_ids": list(self.selected_ids),
            "active_id": self.active_id,
        }


@dataclass(frozen=True)
class TerminalPickToken:
    """One frame-local token mapped to a stable displayed object identity."""

    token: str
    level: str
    target_id: str
    label: str
    selected: bool = False
    active: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "level": self.level,
            "target_id": self.target_id,
            "label": self.label,
            "selected": self.selected,
            "active": self.active,
        }


@dataclass(frozen=True)
class TerminalViewportState:
    """Terminal viewport dimensions and stable fit scale."""

    width: int
    height: int
    scale: float
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "scale": self.scale,
            "x_min": self.x_min,
            "x_max": self.x_max,
            "y_min": self.y_min,
            "y_max": self.y_max,
        }


@dataclass(frozen=True)
class TerminalViewState:
    """Detached semantic TUI state at one monotonic revision."""

    revision: int
    camera: TerminalCameraState
    display: TerminalDisplayState
    focus: TerminalFocusState
    edit: TerminalEditState
    viewport: TerminalViewportState

    def as_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "camera": self.camera.as_dict(),
            "display": self.display.as_dict(),
            "focus": self.focus.as_dict(),
            "edit": self.edit.as_dict(),
            "viewport": self.viewport.as_dict(),
        }


@dataclass(frozen=True)
class TerminalViewSnapshot:
    """Metadata for a named saved terminal view."""

    name: str
    state: TerminalViewState

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "state": self.state.as_dict()}


@dataclass(frozen=True)
class TerminalObservation:
    """A complete structured, visual observation of the current TUI state."""

    revision: int
    state: TerminalViewState
    title: str
    frame: str
    scope: Mapping[str, Any]
    capabilities: tuple[str, ...]
    pick_tokens: tuple[TerminalPickToken, ...] = ()
    warnings: tuple[str, ...] = ()
    schema: str = OBSERVATION_SCHEMA

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "revision": self.revision,
            "title": self.title,
            "frame": {
                "width": self.state.viewport.width,
                "height": self.state.viewport.height,
                "text": self.frame,
            },
            "camera": self.state.camera.as_dict(),
            "display": self.state.display.as_dict(),
            "focus": self.state.focus.as_dict(),
            "edit": self.state.edit.as_dict(),
            "viewport": self.state.viewport.as_dict(),
            "scope": dict(self.scope),
            "capabilities": list(self.capabilities),
            "pick_tokens": [token.as_dict() for token in self.pick_tokens],
            "warnings": list(self.warnings),
        }


__all__ = [
    "OBSERVATION_SCHEMA",
    "TerminalCameraState",
    "TerminalDisplayState",
    "TerminalEditState",
    "TerminalFocusState",
    "TerminalObservation",
    "TerminalPickToken",
    "TerminalViewportState",
    "TerminalViewSnapshot",
    "TerminalViewState",
]