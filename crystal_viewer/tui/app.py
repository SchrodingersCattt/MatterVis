"""Textual TUI interactive crystal viewer.

Full-screen terminal app with keyboard controls for rotating,
panning, zooming, and toggling display options.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dataclasses import replace

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Static
from textual.reactive import reactive
from rich.text import Text

if TYPE_CHECKING:
    from .crystal_ir import CrystalIR

from ..math.camera import Camera, project_points
from .compositor import compose_frame, LABEL_MODES, DISPLAY_LEVELS


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
        label_mode: str = "label",
        show_minor: bool = False,
        compact: bool = False,
    ):
        super().__init__()
        self.crystal = crystal
        self.camera = camera or Camera.from_view_name(initial_view, crystal)
        self._initial_camera = replace(self.camera)
        self._mono = mono
        self._show_bonds = show_bonds
        self._show_cell = show_cell
        self._label_mode = label_mode if not compact else "dot"
        self._show_minor = show_minor
        self._display_level = "atom"

    def compose(self) -> ComposeResult:
        yield Header()
        yield CrystalCanvas(id="canvas")
        yield Footer()

    def on_mount(self) -> None:
        self._update_title()
        self._redraw()

    def on_resize(self) -> None:
        self._redraw()

    def on_key(self, event) -> None:
        """Direct key handler — bypasses binding resolution for movement keys."""
        char = event.character
        key = event.key
        handled = True
        if char == "j" or key in ("j", "left"):
            self.camera = self.camera.pan(dx=PAN_STEP)
        elif char == "l" or key in ("l", "right"):
            self.camera = self.camera.pan(dx=-PAN_STEP)
        elif char == "i" or key in ("i", "up"):
            self.camera = self.camera.pan(dy=-PAN_STEP)
        elif char == "k" or key in ("k", "down"):
            self.camera = self.camera.pan(dy=PAN_STEP)
        elif char == "w" or key == "w":
            self.camera = self.camera.rotate(d_elev=ROTATE_STEP)
            self._update_title()
        elif char == "s" or key == "s":
            self.camera = self.camera.rotate(d_elev=-ROTATE_STEP)
            self._update_title()
        elif char == "q" or key == "q":
            self.camera = self.camera.rotate(d_azim=-ROTATE_STEP)
            self._update_title()
        elif char == "e" or key == "e":
            self.camera = self.camera.rotate(d_azim=ROTATE_STEP)
            self._update_title()
        elif char == "a" or key == "a":
            self.camera = self.camera.rotate(d_roll=-ROTATE_STEP)
            self._update_title()
        elif char == "d" or key == "d":
            self.camera = self.camera.rotate(d_roll=ROTATE_STEP)
            self._update_title()
        elif char in ("[", "-") or key in ("left_square_bracket", "minus"):
            self.camera = self.camera.zoom(1.0 / ZOOM_FACTOR)
            self._update_title()
        elif char in ("]", "+", "=") or key in (
            "right_square_bracket", "plus", "equals_sign"
        ):
            self.camera = self.camera.zoom(ZOOM_FACTOR)
            self._update_title()
        else:
            handled = False

        if handled:
            event.prevent_default()
            event.stop()
            self._redraw()

    # ── Rendering ───────────────────────────────────────────────────────

    def _redraw(self) -> None:
        """Re-project and re-render the crystal."""
        canvas = self.query_one("#canvas", CrystalCanvas)
        size = canvas.size
        w = max(size.width, 1)
        h = max(size.height, 1)

        pts_2d, depth = project_points(self.camera, self.crystal.cart_coords)

        frame = compose_frame(
            self.crystal, self.camera, pts_2d, depth,
            width=w, height=h,
            mono=self._mono, label_mode=self._label_mode,
            show_bonds=self._show_bonds,
            show_cell=self._show_cell,
            show_minor=self._show_minor,
            zoom=self.camera.viewport_zoom,
            pan_x=self.camera.pan_x,
            pan_y=self.camera.pan_y,
            display_level=self._display_level,
        )
        canvas.frame_text = frame

    def _update_title(self) -> None:
        proj = self.camera.projection.value[:5]
        zoom_str = f" ×{self.camera.viewport_zoom:.1f}" if self.camera.viewport_zoom != 1.0 else ""
        roll_str = f" r={self.camera.roll:.0f}°" if abs(self.camera.roll) > 0.5 else ""
        level_str = f" [{self._display_level}]" if self._display_level != "atom" else ""
        self.sub_title = (
            f"{self.crystal.formula} | "
            f"az={self.camera.azimuth:.0f}° el={self.camera.elevation:.0f}°{roll_str} | "
            f"{proj} | {self._label_mode}{zoom_str}{level_str}"
        )

    # ── Actions (toggle bindings only; movement is in on_key) ─────────

    def action_toggle_proj(self) -> None:
        self.camera = self.camera.toggle_projection()
        self._update_title()
        self._redraw()

    def action_toggle_cell(self) -> None:
        self._show_cell = not self._show_cell
        self._redraw()

    def action_toggle_bonds(self) -> None:
        self._show_bonds = not self._show_bonds
        self._redraw()

    def action_toggle_mono(self) -> None:
        self._mono = not self._mono
        self._redraw()

    def action_toggle_label(self) -> None:
        """Cycle through label modes: element → label → molecule → dot."""
        idx = LABEL_MODES.index(self._label_mode) if self._label_mode in LABEL_MODES else 0
        self._label_mode = LABEL_MODES[(idx + 1) % len(LABEL_MODES)]
        self._update_title()
        self._redraw()

    def action_toggle_minor(self) -> None:
        self._show_minor = not self._show_minor
        self._redraw()

    def action_cycle_level(self) -> None:
        """Cycle display level: atom → molecule."""
        idx = DISPLAY_LEVELS.index(self._display_level) if self._display_level in DISPLAY_LEVELS else 0
        self._display_level = DISPLAY_LEVELS[(idx + 1) % len(DISPLAY_LEVELS)]
        self._update_title()
        self._redraw()

    def action_reset_view(self) -> None:
        """Restore the camera supplied at startup."""
        self.camera = replace(self._initial_camera)
        self._update_title()
        self._redraw()
