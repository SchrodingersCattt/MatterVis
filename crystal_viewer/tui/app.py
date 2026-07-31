"""Textual TUI interactive crystal viewer.

Full-screen terminal app with keyboard controls for rotating,
panning, zooming, and toggling display options.
"""

from __future__ import annotations

import json
import shlex
from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input, Static
from textual.reactive import reactive
from rich.text import Text

if TYPE_CHECKING:
    from .crystal_ir import CrystalIR

from ..math.camera import Camera
from .compositor import (
    LABEL_MODES,
    DISPLAY_LEVELS,
)
from .controller import TerminalViewController
from .state import TerminalObservation


# ── Constants ───────────────────────────────────────────────────────────────

ROTATE_STEP = 10.0   # degrees per keypress
PAN_STEP = 0.1       # viewport units per keypress
ZOOM_FACTOR = 1.3    # multiplicative zoom per keypress


# ── Canvas Widget ───────────────────────────────────────────────────────────


class CrystalCanvas(Static):
    """Widget that displays the pre-rendered crystal frame.

    Uses Rich Text with no_wrap to prevent reflow of braille+ANSI content.
    Non-scrollable so it doesn't steal j/k/i/l keys.
    """

    can_focus = False

    frame_text: reactive[str] = reactive("")

    def render(self) -> Text:
        t = Text.from_ansi(self.frame_text, no_wrap=True, overflow="crop")
        return t


# ── Main App ────────────────────────────────────────────────────────────────


class CrystalTUI(App):
    """Interactive terminal crystal viewer."""

    TITLE = "MatterVis TUI"
    CSS = """
    Screen {
        layout: vertical;
    }
    #canvas {
        width: 1fr;
        height: 1fr;
        overflow: hidden hidden;
    }
    #command-result {
        dock: bottom;
        height: 2;
        overflow: hidden hidden;
    }
    #command {
        dock: bottom;
        height: 1;
    }
    Header {
        dock: top;
        height: 1;
    }
    Footer {
        dock: bottom;
        height: 1;
    }
    """

    BINDINGS = [
        # Movement keys handled via on_key() for reliability.
        # Only toggles and quit use the binding system.
        Binding("p", "toggle_proj", "Projection", show=True),
        Binding("c", "toggle_cell", "Cell", show=True),
        Binding("b", "toggle_bonds", "Bonds", show=True),
        Binding("t", "toggle_label", "Label", show=True),
        Binding("m", "toggle_mono", "Mono", show=True),
        Binding("n", "toggle_minor", "Minor", show=True),
        Binding("L", "cycle_level", "Level", show=True),
        Binding("r", "reset_view", "Reset", show=True),
        Binding("u", "zoom_out", "Zoom out", show=True),
        Binding("o", "zoom_in", "Zoom in", show=True),
        Binding("shift+semicolon", "command", "Command", show=True),
            Binding("x", "quit", "Quit", show=True),
    ]

    def __init__(
        self,
        crystal: "CrystalIR",
        *,
        mono: bool = False,
        initial_view: str = "auto",
        camera: Camera | None = None,
        show_bonds: bool = True,
        show_cell: bool = True,
        label_mode: str = "auto",
        show_minor: bool = False,
        compact: bool = False,
        initial_level: str = "atom",
    ):
        super().__init__()
        self.crystal = crystal
        self._command_mode = False
        self.controller = TerminalViewController(
            crystal,
            camera=camera or Camera.from_view_name(initial_view, crystal),
            mono=mono,
            show_bonds=show_bonds,
            show_cell=show_cell,
            label_mode=label_mode if not compact else "dot",
            show_minor=show_minor,
            display_level=initial_level if initial_level in DISPLAY_LEVELS else "atom",
        )

    @property
    def camera(self) -> Camera:
        """Compatibility access to the semantic controller's camera."""
        return self.controller.camera

    @camera.setter
    def camera(self, value: Camera) -> None:
        self.controller.camera = value

    @property
    def _mono(self) -> bool:
        return self.controller.state.display.mono

    @property
    def _show_bonds(self) -> bool:
        return self.controller.state.display.show_bonds

    @property
    def _show_cell(self) -> bool:
        return self.controller.state.display.show_cell

    @property
    def _label_mode(self) -> str:
        return self.controller.state.display.label_mode

    @property
    def _show_minor(self) -> bool:
        return self.controller.state.display.show_minor

    @property
    def _display_level(self) -> str:
        return self.controller.state.display.display_level

    def compose(self) -> ComposeResult:
        yield Header()
        yield CrystalCanvas(id="canvas")
        yield Static("", id="command-result")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#command-result", Static).display = False
        self._apply_observation(self._resize_and_observe())

    def on_resize(self) -> None:
        self._apply_observation(self._resize_and_observe())

    def on_key(self, event) -> None:
        """Direct key handler — bypasses binding resolution for movement keys."""
        if self._command_mode:
            if event.key == "escape":
                self._close_command()
                event.prevent_default()
                event.stop()
            return
        char = event.character
        key = event.key
        if char == ":" or key in ("colon", "shift+semicolon"):
            if not self._command_mode:
                self._command_mode = True
                self.run_worker(self._mount_command())
            event.prevent_default()
            event.stop()
            return
        handled = True
        observation: TerminalObservation | None = None
        if char == "j" or key in ("j", "left"):
            observation = self.controller.pan(dx=PAN_STEP)
        elif char == "l" or key in ("l", "right"):
            observation = self.controller.pan(dx=-PAN_STEP)
        elif char == "i" or key in ("i", "up"):
            observation = self.controller.pan(dy=-PAN_STEP)
        elif char == "k" or key in ("k", "down"):
            observation = self.controller.pan(dy=PAN_STEP)
        elif char == "w" or key == "w":
            observation = self.controller.orbit(pitch_deg=ROTATE_STEP)
        elif char == "s" or key == "s":
            observation = self.controller.orbit(pitch_deg=-ROTATE_STEP)
        elif char == "q" or key == "q":
            observation = self.controller.orbit(yaw_deg=-ROTATE_STEP)
        elif char == "e" or key == "e":
            observation = self.controller.orbit(yaw_deg=ROTATE_STEP)
        elif char == "a" or key == "a":
            observation = self.controller.orbit(roll_deg=-ROTATE_STEP)
        elif char == "d" or key == "d":
            observation = self.controller.orbit(roll_deg=ROTATE_STEP)
        elif char in ("[", "-", "u") or key in ("left_square_bracket", "minus", "u"):
            observation = self.controller.zoom(factor=1.0 / ZOOM_FACTOR)
        elif char in ("]", "+", "=", "o") or key in (
            "right_square_bracket", "plus", "equals_sign"
        ) or key == "o":
            observation = self.controller.zoom(factor=ZOOM_FACTOR)
        else:
            handled = False

        if handled:
            event.prevent_default()
            event.stop()
            assert observation is not None
            self._apply_observation(observation)

    # ── Rendering ───────────────────────────────────────────────────────

    def _resize_and_observe(self) -> TerminalObservation:
        """Synchronize dimensions and render exactly one current observation."""
        canvas = self.query_one("#canvas", CrystalCanvas)
        size = canvas.size
        w = size.width if size.width > 1 else max(self.size.width, 1)
        h = size.height if size.height > 1 else max(self.size.height - 2, 1)
        if (w, h) != (self.controller.width, self.controller.height):
            return self.controller.resize_viewport(w, h)
        return self.controller.observe()

    def _apply_observation(self, observation: TerminalObservation) -> None:
        """Apply one already-rendered semantic observation to the UI."""
        canvas = self.query_one("#canvas", CrystalCanvas)
        canvas.frame_text = observation.frame
        self.sub_title = observation.title

    def _redraw(self) -> None:
        """Compatibility wrapper for callers that request a visual refresh."""
        self._apply_observation(self._resize_and_observe())

    def _update_title(self) -> None:
        self.sub_title = self.controller.observe().title

    # ── Command mode ──────────────────────────────────────────────────

    async def action_command(self) -> None:
        """Open the one-line measurement command prompt."""
        if self._command_mode:
            commands = self.query("#command").nodes
            if commands:
                commands[0].focus()
            return
        self._command_mode = True
        await self._mount_command()

    async def _mount_command(self) -> None:
        """Mount and focus the command input after mode is reserved."""
        command = Input(placeholder=":distance A B [direct|mic]", id="command")
        await self.mount(command, before=self.query_one(Footer))
        command.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "command":
            return
        try:
            result, observation = self.execute_command(event.value)
        except (TypeError, ValueError) as exc:
            result, observation = f"error: {exc}", None
        event.input.remove()
        self._command_mode = False
        output = self.query_one("#command-result", Static)
        output.update(result)
        output.display = True
        if observation is not None:
            self._apply_observation(observation)

    def execute_command(self, text: str) -> tuple[str, TerminalObservation | None]:
        """Execute one command-mode line; kept public for deterministic tests."""
        parts = shlex.split(text.strip().lstrip(":"))
        if not parts:
            raise ValueError("empty command; use :help")
        name, args = parts[0].lower(), parts[1:]
        if name == "help":
            return (
                "edit atom|molecule | pick/unpick/toggle TOKEN... | active TOKEN | exit | "
                "select A... | focus A [depth]|selection | distance A B [direct|mic] | "
                "angle A B C [direct|mic] | dihedral A B C D [direct|mic_chain] | clear",
                None,
            )
        if name == "edit":
            if len(args) != 1:
                raise ValueError("edit requires atom or molecule")
            observation = self.controller.enter_edit(args[0])
            return f"edit mode: {args[0]}", observation
        if name in {"pick", "unpick", "toggle"}:
            if not args:
                raise ValueError(f"{name} requires at least one current-frame token")
            method = {
                "pick": self.controller.pick,
                "unpick": self.controller.unpick,
                "toggle": self.controller.toggle_pick,
            }[name]
            observation = method(args)
            return f"selected: {' '.join(observation.state.edit.selected_ids)}", observation
        if name == "active":
            if len(args) != 1:
                raise ValueError("active requires one current-frame token")
            observation = self.controller.set_active(args[0])
            return f"active: {observation.state.edit.active_id}", observation
        if name == "exit":
            return "browse mode", self.controller.exit_edit()
        if name == "select":
            if not args:
                raise ValueError("select requires at least one atom label")
            observation = self.controller.select_atom_references(args)
            return f"selected: {' '.join(args)}", observation
        if name == "clear":
            return "selection cleared", self.controller.clear_selection()
        if name == "focus":
            if not args or args == ["selection"]:
                return "focused selection", self.controller.focus_edit_selection()
            depth = int(args[1]) if len(args) > 1 else 1
            if self.controller.state.edit.mode == "edit" and args[0].startswith("a") and args[0][1:].isdigit():
                return f"focused {args[0]} with bond depth {depth}", self.controller.focus_pick_token(args[0], bond_depth=depth)
            return f"focused {args[0]} with bond depth {depth}", self.controller.focus_local(args[0], bond_depth=depth)
        if name == "distance":
            references, mode = self._measurement_arguments(args, 2, "mic")
            return self._format_measurement(self.controller.measure_distance(references, mode=mode)), None
        if name == "angle":
            references, mode = self._measurement_arguments(args, 3, "mic")
            return self._format_measurement(self.controller.measure_angle(references, mode=mode)), None
        if name == "dihedral":
            references, mode = self._measurement_arguments(args, 4, "mic_chain")
            return self._format_measurement(self.controller.measure_dihedral(references, mode=mode)), None
        raise ValueError(f"unknown command: {name}; use :help")

    def _measurement_arguments(
        self,
        args: list[str],
        count: int,
        default_mode: str,
    ) -> tuple[list[str | int | dict[str, str]], str]:
        modes = {"direct", "mic", "mic_chain"}
        mode = args[-1] if args and args[-1] in modes else default_mode
        labels = args[:-1] if args and args[-1] in modes else args
        references: list[str | int | dict[str, str]] = list(labels) if labels else self.controller.atom_selection_references()
        if len(references) != count:
            raise ValueError(f"measurement requires {count} atoms or a {count}-atom selection")
        return references, mode

    @staticmethod
    def _format_measurement(result: dict) -> str:
        atoms = "-".join(result["atoms"])
        shifts = json.dumps(result["image_shifts"], separators=(",", ":"))
        return f"{result['kind']} {atoms}: {result['value']:.4f} {result['unit']} ({result['mode']}; shifts={shifts})"

    def _close_command(self) -> None:
        command = self.query_one("#command", Input)
        command.remove()
        self._command_mode = False

    # ── Actions (toggle bindings only; movement is in on_key) ─────────

    def action_toggle_proj(self) -> None:
        projection = "perspective" if self.camera.projection.value == "orthographic" else "orthographic"
        self._apply_observation(self.controller.set_camera(projection=projection))

    def action_toggle_cell(self) -> None:
        self._apply_observation(self.controller.set_display(show_cell=not self._show_cell))

    def action_toggle_bonds(self) -> None:
        self._apply_observation(self.controller.set_display(show_bonds=not self._show_bonds))

    def action_toggle_mono(self) -> None:
        self._apply_observation(self.controller.set_display(mono=not self._mono))

    def action_toggle_label(self) -> None:
        """Cycle through label modes: element → label → molecule → dot."""
        idx = LABEL_MODES.index(self._label_mode) if self._label_mode in LABEL_MODES else 0
        self._apply_observation(
            self.controller.set_display(label_mode=LABEL_MODES[(idx + 1) % len(LABEL_MODES)])
        )

    def action_toggle_minor(self) -> None:
        self._apply_observation(self.controller.set_display(show_minor=not self._show_minor))

    def action_cycle_level(self) -> None:
        """Cycle display level: atom → molecule."""
        idx = DISPLAY_LEVELS.index(self._display_level) if self._display_level in DISPLAY_LEVELS else 0
        self._apply_observation(
            self.controller.set_display(display_level=DISPLAY_LEVELS[(idx + 1) % len(DISPLAY_LEVELS)])
        )

    def action_reset_view(self) -> None:
        """Restore the camera supplied at startup."""
        self._apply_observation(self.controller.reset_view())

    def action_zoom_out(self) -> None:
        self._apply_observation(self.controller.zoom(factor=1.0 / ZOOM_FACTOR))

    def action_zoom_in(self) -> None:
        self._apply_observation(self.controller.zoom(factor=ZOOM_FACTOR))
