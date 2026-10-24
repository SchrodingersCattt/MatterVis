"""Display-only depth controls shared by the terminal controller and sessions.

This mixin operates on controller-owned state. It never changes the supplied IR.
Kept separate from atom selection and chemistry inspection to bound module size.
"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np

from ..math.camera import project_points
from .compositor import compose_frame
from .controller_validate import validate_finite
from .depth import MAX_PARALLAX_FRAMES, layer_indices, validate_slice
from .state import TerminalObservation


class DepthControls:
    """Depth rendering, layer stepping, and bounded rotation sequences."""

    def render_frame(self, *, charset: str = "unicode") -> str:
        """Render the current picture, including transient rotation ghosts."""
        pts_2d, depth = project_points(self.camera, self.crystal.cart_coords)
        return compose_frame(
            self.crystal,
            self.camera,
            pts_2d,
            depth,
            width=self._width,
            height=self._height,
            mono=self._mono,
            label_mode=self._label_mode,
            show_bonds=self._show_bonds,
            show_cell=self._show_cell,
            show_minor=self._show_minor,
            show_hydrogen=self._show_hydrogen,
            show_boundary=self._show_boundary,
            zoom=self.camera.viewport_zoom,
            pan_x=self.camera.pan_x,
            pan_y=self.camera.pan_y,
            display_level=self._display_level,
            viewport=self._fit_viewport,
            selected_display_index=self._selection.display_index,
            show_axes=self._show_axes,
            charset=charset,
            show_depth=self._show_depth,
            slice_axis=self._slice_axis,
            slice_index=self._slice_index,
            slice_width=self._slice_width,
            ghost_points=(
                project_points(self._ghost_camera, self.crystal.cart_coords)[0]
                if self._parallax and self._ghost_camera is not None
                else None
            ),
        )

    def rotation_sequence(
        self, *, frames: int = 3, yaw_deg: float = 10.0
    ) -> tuple[TerminalObservation, ...]:
        """Return a bounded sequence of successive orbits for parallax comparison."""
        return tuple(self.rotation_sequence_iter(frames=frames, yaw_deg=yaw_deg))

    def rotation_sequence_iter(
        self, *, frames: int = 3, yaw_deg: float = 10.0
    ) -> Iterator[TerminalObservation]:
        """Yield successive orbit observations while each state is still current."""
        if (
            isinstance(frames, bool)
            or not isinstance(frames, int)
            or not 1 <= frames <= MAX_PARALLAX_FRAMES
        ):
            raise ValueError(
                f"frames must be an integer from 1 to {MAX_PARALLAX_FRAMES}"
            )
        validate_finite("orbit", yaw_deg)
        for _ in range(frames):
            yield self.orbit(yaw_deg=yaw_deg)

    def set_slice(
        self, *, axis: str = "off", index: int = 0, width: float = 1.0
    ) -> TerminalObservation:
        """Highlight one depth/lattice layer, dim neighbours, and hide the rest."""
        validate_slice(self.crystal, axis, index, width)
        self._slice_axis, self._slice_index, self._slice_width = (
            axis,
            index,
            float(width),
        )
        self._ghost_camera = None
        return self._changed()

    def step_slice(self, *, step: int = 1) -> TerminalObservation:
        """Step one layer; start depth slicing when it is currently disabled."""
        if isinstance(step, bool) or not isinstance(step, int):
            raise TypeError("slice step must be an integer")
        index = self._slice_index
        if self._slice_axis == "off":
            layers = layer_indices(
                self.crystal, self.camera, "depth", self._slice_width
            )
            index = int(np.median(layers)) if len(layers) else 0
        return self.set_slice(
            axis="depth" if self._slice_axis == "off" else self._slice_axis,
            index=index + step,
            width=self._slice_width,
        )
