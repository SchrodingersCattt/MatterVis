"""Deterministic stateful terminal sessions for local agents and subprocesses.

The session executes caller-selected semantic actions. It deliberately owns no
agent policy, action budget, answer protocol, or chemistry oracle restrictions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import sys
from typing import Any, IO, Mapping

from ..math.camera import project_points
from .compositor import compose_frame
from .controller import TerminalViewController
from .projection import viewport_from_bounds
from .state import TerminalObservation

SESSION_SCHEMA = "mattervis.tui.session/v1"
ACTION_SCHEMA = "mattervis.tui.action/v1"


@dataclass(frozen=True)
class TerminalAction:
    """One semantic terminal-view action selected by a caller."""

    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TerminalAction":
        if value.get("schema", ACTION_SCHEMA) != ACTION_SCHEMA:
            raise ValueError(f"action schema must be {ACTION_SCHEMA!r}")
        name = value.get("action")
        if not isinstance(name, str) or not name:
            raise ValueError("action must be non-empty text")
        arguments = value.get("arguments", {})
        if not isinstance(arguments, Mapping):
            raise ValueError("arguments must be an object")
        return cls(name=name, arguments=dict(arguments))


@dataclass(frozen=True)
class SessionObservation:
    """A fixed-size rendered observation plus deterministic fingerprints."""

    observation: TerminalObservation
    screen_lines: tuple[str, ...]
    screen_hash: str
    state_fingerprint: str
    schema: str = SESSION_SCHEMA

    def as_dict(self) -> dict[str, Any]:
        payload = self.observation.as_dict()
        payload.update(
            {
                "schema": self.schema,
                "frame": {
                    "width": self.observation.state.viewport.width,
                    "height": self.observation.state.viewport.height,
                    "lines": list(self.screen_lines),
                    "text": "\n".join(self.screen_lines),
                    "sha256": self.screen_hash,
                },
                "state_fingerprint": self.state_fingerprint,
            }
        )
        return payload


class TerminalSession:
    """Execute semantic actions against one persistent terminal view."""

    ACTIONS = (
        "observe",
        "reset",
        "orbit",
        "align",
        "pan",
        "zoom",
        "fit",
        "set_display",
        "select",
        "focus",
        "clear_selection",
        "clear_focus",
        "close",
    )

    def __init__(self, crystal, **controller_options: Any) -> None:
        self.crystal = crystal
        self._controller_options = dict(controller_options)
        self.charset = self._controller_options.pop("charset", "unicode")
        if self.charset not in {"unicode", "ascii7"}:
            raise ValueError("charset must be 'unicode' or 'ascii7'")
        self._controller_options.setdefault("mono", True)
        self._closed = False
        self.controller = TerminalViewController(crystal, **self._controller_options)

    @classmethod
    def from_file(
        cls,
        path: str,
        *,
        display_mode: str = "auto",
        input_format: str | None = None,
        type_map: list[str] | None = None,
        frame: int = 0,
        **controller_options: Any,
    ) -> "TerminalSession":
        from .loader_adapter import load_for_tui

        crystal = load_for_tui(
            path,
            display_mode=display_mode,
            input_format=input_format,
            type_map=type_map,
            frame=frame,
        )
        return cls(crystal, **controller_options)

    def observe(self) -> SessionObservation:
        self._require_open()
        return self._wrap(self.controller.observe())

    def reset(self) -> SessionObservation:
        self._require_open()
        self.controller = TerminalViewController(
            self.crystal, **self._controller_options
        )
        return self.observe()

    def execute(self, action: TerminalAction) -> SessionObservation | None:
        self._require_open()
        if not isinstance(action, TerminalAction):
            raise TypeError("action must be a TerminalAction")
        name = action.name
        arguments = dict(action.arguments)
        if name == "observe":
            self._require_arguments(name, arguments, set())
            return self.observe()
        if name == "reset":
            self._require_arguments(name, arguments, set())
            return self.reset()
        if name == "close":
            self._require_arguments(name, arguments, set())
            self.close()
            return None

        dispatch = {
            "orbit": (self.controller.orbit, {"yaw_deg", "pitch_deg", "roll_deg"}),
            "align": (self.controller.align, {"axis"}),
            "pan": (self.controller.pan, {"dx", "dy"}),
            "zoom": (self.controller.zoom, {"factor"}),
            "fit": (self.controller.fit, {"target"}),
            "set_display": (
                self.controller.set_display,
                {
                    "display_level",
                    "label_mode",
                    "show_bonds",
                    "show_cell",
                    "show_minor",
                    "mono",
                },
            ),
            "select": (self._select, {"atom_id", "pinned"}),
            "focus": (self._focus, {"atom_id"}),
            "clear_selection": (self.controller.clear_selection, {"exit_mode"}),
            "clear_focus": (self.controller.clear_focus, set()),
        }
        try:
            method, allowed = dispatch[name]
        except KeyError as exc:
            raise ValueError(
                f"unsupported action {name!r}; expected one of {self.ACTIONS}"
            ) from exc
        self._require_arguments(name, arguments, allowed)
        return self._wrap(method(**arguments))

    def close(self) -> None:
        self._closed = True

    def _select(self, *, atom_id: str, pinned: bool = False):
        return self.controller.select_atom({"atom_id": atom_id}, pinned=pinned)

    def _focus(self, *, atom_id: str):
        return self.controller.focus_atom({"atom_id": atom_id})

    def _wrap(self, observation: TerminalObservation) -> SessionObservation:
        width = observation.state.viewport.width
        height = observation.state.viewport.height
        frame = observation.frame
        if self.charset != "unicode":
            points, depth = project_points(
                self.controller.camera, self.crystal.cart_coords
            )
            display = observation.state.display
            viewport_state = observation.state.viewport
            viewport = viewport_from_bounds(
                viewport_state.x_min,
                viewport_state.x_max,
                viewport_state.y_min,
                viewport_state.y_max,
                width,
                height,
            )
            frame = compose_frame(
                self.crystal,
                self.controller.camera,
                points,
                depth,
                width=width,
                height=height,
                mono=True,
                label_mode=display.label_mode,
                show_bonds=display.show_bonds,
                show_cell=display.show_cell,
                show_minor=display.show_minor,
                zoom=1.0,
                pan_x=0.0,
                pan_y=0.0,
                display_level=display.display_level,
                viewport=viewport,
                selected_display_index=observation.state.selection.display_index,
                charset=self.charset,
            )
        raw_lines = frame.splitlines()
        if len(raw_lines) > height or any(len(line) > width for line in raw_lines):
            raise ValueError("rendered frame exceeds the configured viewport")
        lines = tuple(
            (raw_lines[index] if index < len(raw_lines) else "").ljust(width)
            for index in range(height)
        )
        encoding = "ascii" if self.charset == "ascii7" else "utf-8"
        screen = "\n".join(lines).encode(encoding)
        state = observation.state.as_dict()
        state.pop("revision", None)
        state["charset"] = self.charset
        state["structure"] = {
            "atoms": [
                {
                    "atom_id": atom.atom_id,
                    "element": atom.element,
                    "cart": [float(value) for value in atom.cart],
                    "occupancy": float(atom.occupancy),
                    "disorder": atom.disorder,
                    "image_shift": list(atom.image_shift),
                }
                for atom in self.crystal.atoms
            ],
            "bonds": [
                {
                    "i": bond.i,
                    "j": bond.j,
                    "image_relation": list(bond.image_relation),
                }
                for bond in self.crystal.bonds
            ],
        }
        state_material = json.dumps(
            state,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        return SessionObservation(
            observation=observation,
            screen_lines=lines,
            screen_hash=sha256(screen).hexdigest(),
            state_fingerprint=sha256(state_material).hexdigest(),
        )

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("terminal session is closed")

    @staticmethod
    def _require_arguments(name: str, arguments: Mapping[str, Any], allowed: set[str]) -> None:
        unknown = sorted(set(arguments) - allowed)
        if unknown:
            raise ValueError(f"{name} received unknown arguments: {', '.join(unknown)}")


def run_jsonl_session(
    session: TerminalSession,
    *,
    input_stream: IO[str] = sys.stdin,
    output_stream: IO[str] = sys.stdout,
) -> None:
    """Serve one session over newline-delimited JSON until close or EOF."""

    for line in input_stream:
        try:
            request = json.loads(line)
            if not isinstance(request, Mapping):
                raise ValueError("request must be a JSON object")
            action = TerminalAction.from_dict(request)
            observation = session.execute(action)
            payload: dict[str, Any] = {
                "schema": SESSION_SCHEMA,
                "ok": True,
                "closed": observation is None,
            }
            if observation is not None:
                payload["observation"] = observation.as_dict()
        except Exception as exc:
            payload = {
                "schema": SESSION_SCHEMA,
                "ok": False,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        output_stream.write(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n"
        )
        output_stream.flush()
        if payload.get("closed"):
            return


__all__ = [
    "ACTION_SCHEMA",
    "SESSION_SCHEMA",
    "SessionObservation",
    "TerminalAction",
    "TerminalSession",
    "run_jsonl_session",
]
