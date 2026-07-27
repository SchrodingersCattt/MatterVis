"""Textual TUI interactive crystal viewer.

Full-screen terminal app with keyboard controls for rotating,
panning, zooming, and toggling display options.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Static
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
        Binding("Q", "quit", "Quit", show=True),
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
        yield Footer()

    def on_mount(self) -> None:
        self._redraw()
        self._update_title()

    def on_resize(self) -> None:
        self._redraw()
        self._update_title()

    def on_key(self, event) -> None:
        """Direct key handler — bypasses binding resolution for movement keys."""
        char = event.character
        key = event.key
        handled = True
        if char == "j" or key in ("j", "left"):
            self.controller.pan(dx=PAN_STEP)
        elif char == "l" or key in ("l", "right"):
            self.controller.pan(dx=-PAN_STEP)
        elif char == "i" or key in ("i", "up"):
            self.controller.pan(dy=-PAN_STEP)
        elif char == "k" or key in ("k", "down"):
            self.controller.pan(dy=PAN_STEP)
        elif char == "w" or key == "w":
            self.controller.orbit(pitch_deg=ROTATE_STEP)
        elif char == "s" or key == "s":
            self.controller.orbit(pitch_deg=-ROTATE_STEP)
        elif char == "q" or key == "q":
            self.controller.orbit(yaw_deg=-ROTATE_STEP)
        elif char == "e" or key == "e":
            self.controller.orbit(yaw_deg=ROTATE_STEP)
        elif char == "a" or key == "a":
            self.controller.orbit(roll_deg=-ROTATE_STEP)
        elif char == "d" or key == "d":
            self.controller.orbit(roll_deg=ROTATE_STEP)
        elif char in ("[", "-", "u") or key in ("left_square_bracket", "minus", "u"):
            self.controller.zoom(factor=1.0 / ZOOM_FACTOR)
        elif char in ("]", "+", "=", "o") or key in (
            "right_square_bracket", "plus", "equals_sign"
        ) or key == "o":
            self.controller.zoom(factor=ZOOM_FACTOR)
        else:
            handled = False

        if handled:
            event.prevent_default()
            event.stop()
            self._update_title()
            self._redraw()

    # ── Rendering ───────────────────────────────────────────────────────

    def _redraw(self) -> None:
        """Re-project and re-render the crystal."""
        canvas = self.query_one("#canvas", CrystalCanvas)
        size = canvas.size
        w = size.width if size.width > 1 else max(self.size.width, 1)
        h = size.height if size.height > 1 else max(self.size.height - 2, 1)
        if (w, h) != (self.controller.width, self.controller.height):
            self.controller.resize_viewport(w, h)
        canvas.frame_text = self.controller.observe().frame

    def _update_title(self) -> None:
        self.sub_title = self.controller.observe().title

    # ── Actions (toggle bindings only; movement is in on_key) ─────────

    def action_toggle_proj(self) -> None:
        projection = "perspective" if self.camera.projection.value == "orthographic" else "orthographic"
        self.controller.set_camera(projection=projection)
        self._update_title()
        self._redraw()

    def action_toggle_cell(self) -> None:
        self.controller.set_display(show_cell=not self._show_cell)
        self._update_title()
        self._redraw()

    def action_toggle_bonds(self) -> None:
        self.controller.set_display(show_bonds=not self._show_bonds)
        self._update_title()
        self._redraw()

    def action_toggle_mono(self) -> None:
        self.controller.set_display(mono=not self._mono)
        self._update_title()
        self._redraw()

    def action_toggle_label(self) -> None:
        """Cycle through label modes: element → label → molecule → dot."""
        idx = LABEL_MODES.index(self._label_mode) if self._label_mode in LABEL_MODES else 0
        self.controller.set_display(label_mode=LABEL_MODES[(idx + 1) % len(LABEL_MODES)])
        self._update_title()
        self._redraw()

    def action_toggle_minor(self) -> None:
        self.controller.set_display(show_minor=not self._show_minor)
        self._update_title()
        self._redraw()

    def action_cycle_level(self) -> None:
        """Cycle display level: atom → molecule."""
        idx = DISPLAY_LEVELS.index(self._display_level) if self._display_level in DISPLAY_LEVELS else 0
        self.controller.set_display(display_level=DISPLAY_LEVELS[(idx + 1) % len(DISPLAY_LEVELS)])
        self._update_title()
        self._redraw()

    def action_reset_view(self) -> None:
        """Restore the camera supplied at startup."""
        self.controller.reset_view()
        self._update_title()
        self._redraw()

    def action_zoom_out(self) -> None:
        self.controller.zoom(factor=1.0 / ZOOM_FACTOR)
        self._update_title()
        self._redraw()

    def action_zoom_in(self) -> None:
        self.controller.zoom(factor=ZOOM_FACTOR)
        self._update_title()
        self._redraw()
