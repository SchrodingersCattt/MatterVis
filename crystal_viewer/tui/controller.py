"""Semantic state owner shared by the Textual terminal UI and agent adapters."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np

from ..math.camera import Camera, ProjectionMode, project_points
from .compositor import (
    DISPLAY_LEVELS,
    LABEL_MODES,
    Viewport,
    _compute_viewport,
    compose_frame_result,
    viewport_from_bounds,
)
from .inspection import (
    inspect_atoms,
    inspect_local_geometries,
    inspect_local_geometry,
    inspect_molecules,
    measure_angle,
    measure_dihedral,
    measure_distance,
    resolve_atom_references,
    resolve_molecule_reference,
)
from .observation import build_observation_scope, build_terminal_title
from .state import (
    TerminalCameraState,
    TerminalDisplayState,
    TerminalEditState,
    TerminalFocusState,
    TerminalObservation,
    TerminalViewportState,
    TerminalViewSnapshot,
    TerminalViewState,
)


class TerminalViewController:
    """Control one manifested :class:`CrystalIR` without changing its chemistry.

    The controller owns only terminal view state. It never rebuilds bonds,
    molecular partitions, disorder assignments, or periodic copies; those are
    already represented in the supplied ``CrystalIR``.
    """

    CAPABILITIES = (
        "observe",
        "set_camera",
        "orbit",
        "align",
        "fit",
        "pan",
        "zoom",
        "set_display",
        "focus_atom",
        "focus_molecule",
        "focus_selection",
        "focus_local",
        "enter_edit",
        "exit_edit",
        "pick",
        "unpick",
        "toggle_pick",
        "clear_selection",
        "set_active",
        "clear_focus",
        "save_view",
        "restore_view",
        "list_views",
        "reset_view",
    )

    def __init__(
        self,
        crystal,
        *,
        camera: Camera | None = None,
        width: int = 80,
        height: int = 24,
        mono: bool = False,
        show_bonds: bool = True,
        show_cell: bool = True,
        label_mode: str = "auto",
        show_minor: bool = False,
        display_level: str = "atom",
    ) -> None:
        self.crystal = crystal
        self._width, self._height = self._validate_dimensions(width, height)
        self._mono = bool(mono)
        self._show_bonds = bool(show_bonds)
        self._show_cell = bool(show_cell)
        self._label_mode = self._validate_label_mode(label_mode)
        self._show_minor = bool(show_minor)
        self._display_level = self._validate_display_level(display_level)
        default_camera = camera is None
        self.camera = replace(
            self._copy_camera(camera or Camera.from_view_name("auto", crystal)),
            perspective_near_is_larger=True,
        )
        if default_camera:
            self.camera = replace(self.camera, target=self._all_view_center())
        self._initial_camera = self._copy_camera(self.camera)
        self._focus = TerminalFocusState()
        self._edit = TerminalEditState()
        self._pick_token_map: dict[str, str] = {}
        self._pick_token_revision = -1
        self._snapshots: dict[str, tuple[Camera, TerminalDisplayState, TerminalFocusState, TerminalEditState, Viewport]] = {}
        self._revision = 0
        self._fit_viewport = self._fit_all_viewport()

    @classmethod
    def from_file(cls, path: str, *, display_mode: str = "auto", **kwargs) -> "TerminalViewController":
        """Load a structure through the canonical TUI loader."""
        from .loader_adapter import load_for_tui

        return cls(load_for_tui(path, display_mode=display_mode), **kwargs)

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def state(self) -> TerminalViewState:
        """Return a detached immutable snapshot of the semantic view state."""
        viewport = self._effective_viewport()
        camera = TerminalCameraState(
            azimuth=float(self.camera.azimuth),
            elevation=float(self.camera.elevation),
            roll=float(self.camera.roll),
            target=tuple(float(value) for value in self.camera.target),
            projection=self.camera.projection.value,
            zoom=float(self.camera.viewport_zoom),
            pan_x=float(self.camera.pan_x),
            pan_y=float(self.camera.pan_y),
        )
        display = TerminalDisplayState(
            display_level=self._display_level,
            label_mode=self._label_mode,
            show_bonds=self._show_bonds,
            show_cell=self._show_cell,
            show_minor=self._show_minor,
            mono=self._mono,
        )
        viewport_state = TerminalViewportState(
            width=self._width,
            height=self._height,
            scale=float(viewport.scale),
            x_min=float(viewport.x_min),
            x_max=float(viewport.x_max),
            y_min=float(viewport.y_min),
            y_max=float(viewport.y_max),
        )
        return TerminalViewState(
            revision=self._revision,
            camera=camera,
            display=display,
            focus=self._focus,
            edit=self._edit,
            viewport=viewport_state,
        )

    def observe(self) -> TerminalObservation:
        """Render and describe the exact current terminal view without mutation."""
        state = self.state
        pts_2d, depth = project_points(self.camera, self.crystal.cart_coords)
        composed = compose_frame_result(
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
            zoom=self.camera.viewport_zoom,
            pan_x=self.camera.pan_x,
            pan_y=self.camera.pan_y,
            display_level=self._display_level,
            viewport=self._fit_viewport,
            edit_state=self._edit,
        )
        self._pick_token_map = {token.token: token.target_id for token in composed.pick_tokens}
        self._pick_token_revision = self._revision
        title = build_terminal_title(
            self.crystal,
            state.camera,
            state.display,
            width=self._width,
            height=self._height,
            edit=self._edit,
        )
        return TerminalObservation(
            revision=self._revision,
            state=state,
            title=title,
            frame=composed.text,
            scope=build_observation_scope(self.crystal, state.display),
            capabilities=self.CAPABILITIES,
            pick_tokens=composed.pick_tokens,
        )

    def enter_edit(self, level: str = "atom") -> TerminalObservation:
        """Enter edit mode at atom or molecule level."""
        if level not in {"atom", "molecule"}:
            raise ValueError("edit level must be 'atom' or 'molecule'")
        if level == "molecule" and not any(atom.display_fragment_id for atom in self.crystal.atoms):
            raise ValueError("molecule edit requires displayed molecule identities")
        if self._edit.mode == "edit" and self._edit.level == level:
            return self.observe()
        self._display_level = level
        self._edit = TerminalEditState(mode="edit", level=level)
        return self._changed()

    def exit_edit(self) -> TerminalObservation:
        """Leave edit mode while retaining stable selected object identities."""
        if self._edit.mode == "browse":
            return self.observe()
        self._edit = replace(self._edit, mode="browse")
        return self._changed()

    def pick(self, tokens: list[str]) -> TerminalObservation:
        """Add current-frame tokens to the ordered selection atomically."""
        target_ids = self._resolve_pick_tokens(tokens)
        selected = list(self._edit.selected_ids)
        for target_id in target_ids:
            if target_id not in selected:
                selected.append(target_id)
        self._edit = replace(self._edit, selected_ids=tuple(selected), active_id=target_ids[-1])
        return self._changed()

    def unpick(self, tokens: list[str]) -> TerminalObservation:
        """Remove current-frame tokens from selection atomically."""
        target_ids = set(self._resolve_pick_tokens(tokens))
        selected = tuple(value for value in self._edit.selected_ids if value not in target_ids)
        active = self._edit.active_id if self._edit.active_id in selected else (selected[-1] if selected else None)
        self._edit = replace(self._edit, selected_ids=selected, active_id=active)
        return self._changed()

    def toggle_pick(self, tokens: list[str]) -> TerminalObservation:
        """Toggle current-frame tokens in selection atomically."""
        target_ids = self._resolve_pick_tokens(tokens)
        selected = list(self._edit.selected_ids)
        for target_id in target_ids:
            if target_id in selected:
                selected.remove(target_id)
            else:
                selected.append(target_id)
        active = target_ids[-1] if target_ids[-1] in selected else (selected[-1] if selected else None)
        self._edit = replace(self._edit, selected_ids=tuple(selected), active_id=active)
        return self._changed()

    def clear_selection(self) -> TerminalObservation:
        """Clear edit selection without changing camera focus."""
        if not self._edit.selected_ids and self._edit.active_id is None:
            return self.observe()
        self._edit = replace(self._edit, selected_ids=(), active_id=None)
        return self._changed()

    def select_atom_references(
        self,
        references: list[str | int | dict[str, Any]],
    ) -> TerminalObservation:
        """Replace atom edit selection from explicit stable atom references."""
        indices = resolve_atom_references(self.crystal, references)
        target_ids = tuple(self.crystal.atoms[index].display_copy_id for index in indices)
        self._display_level = "atom"
        self._edit = TerminalEditState(
            mode="edit",
            level="atom",
            selected_ids=target_ids,
            active_id=target_ids[-1] if target_ids else None,
        )
        return self._changed()

    def atom_selection_references(self) -> list[dict[str, str]]:
        """Return current atom edit selection as exact measurement references."""
        if self._edit.level != "atom":
            raise ValueError("measurements require atom edit selection")
        return [{"display_copy_id": target_id} for target_id in self._edit.selected_ids]

    def set_active(self, token: str) -> TerminalObservation:
        """Set active object to one already selected current-frame token."""
        target_id = self._resolve_pick_tokens([token])[0]
        if target_id not in self._edit.selected_ids:
            raise ValueError("active object must already be selected")
        self._edit = replace(self._edit, active_id=target_id)
        return self._changed()

    def focus_edit_selection(self) -> TerminalObservation:
        """Fit the current atom-level edit selection."""
        if self._edit.level != "atom" or not self._edit.selected_ids:
            raise ValueError("atom edit selection is empty")
        indices = tuple(
            index for index, atom in enumerate(self.crystal.atoms)
            if atom.display_copy_id in self._edit.selected_ids
        )
        return self._set_focus("selection", indices)

    def focus_pick_token(self, token: str, *, bond_depth: int = 1) -> TerminalObservation:
        """Focus a current-frame atom token and its bond neighborhood."""
        if self._edit.level != "atom":
            raise ValueError("token-local focus requires atom edit level")
        target_id = self._resolve_pick_tokens([token])[0]
        return self.focus_local({"display_copy_id": target_id}, bond_depth=bond_depth)

    def _resolve_pick_tokens(self, tokens: list[str]) -> tuple[str, ...]:
        if self._edit.mode != "edit":
            raise ValueError("pick commands require edit mode")
        if not tokens:
            raise ValueError("at least one pick token is required")
        if self._pick_token_revision != self._revision:
            self.observe()
        missing = [token for token in tokens if token not in self._pick_token_map]
        if missing:
            raise ValueError(f"unknown pick token(s): {', '.join(missing)}; read the current frame again")
        return tuple(dict.fromkeys(self._pick_token_map[token] for token in tokens))

    def set_camera(
        self,
        *,
        azimuth: float | None = None,
        elevation: float | None = None,
        roll: float | None = None,
        projection: str | ProjectionMode | None = None,
        zoom: float | None = None,
        pan_x: float | None = None,
        pan_y: float | None = None,
        target: tuple[float, float, float] | list[float] | np.ndarray | None = None,
    ) -> TerminalObservation:
        """Apply absolute partial camera state and return the new observation."""
        candidate = self.camera
        if any(value is not None for value in (azimuth, elevation, roll)):
            candidate = candidate.set_orientation(
                azimuth=azimuth,
                elevation=elevation,
                roll=roll,
            )
        if projection is not None:
            candidate = replace(candidate, projection=self._validate_projection(projection))
        if zoom is not None:
            candidate = self._with_zoom(candidate, zoom)
        if pan_x is not None or pan_y is not None:
            values = (pan_x if pan_x is not None else candidate.pan_x, pan_y if pan_y is not None else candidate.pan_y)
            self._validate_finite("pan", *values)
            candidate = replace(candidate, pan_x=float(values[0]), pan_y=float(values[1]))
        target_changed = target is not None
        if target_changed:
            target_array = np.asarray(target, dtype=float)
            if target_array.shape != (3,) or not np.all(np.isfinite(target_array)):
                raise ValueError("target must be a finite three-dimensional vector")
            candidate = replace(candidate, target=target_array)
        self.camera = candidate
        if target_changed:
            self._fit_viewport = self._fit_all_viewport()
        return self._changed()

    def orbit(self, *, yaw_deg: float = 0.0, pitch_deg: float = 0.0, roll_deg: float = 0.0) -> TerminalObservation:
        """Orbit around a fixed world +Z up-axis and return the rendered view."""
        self.camera = self.camera.orbit_turntable(
            yaw_deg=float(yaw_deg),
            pitch_deg=float(pitch_deg),
            roll_deg=float(roll_deg),
        )
        return self._changed()

    def align(self, axis: str) -> TerminalObservation:
        """Align to real or reciprocal lattice axis ``a/b/c/a*/b*/c*``."""
        if self.crystal.lattice is None:
            raise ValueError("align requires a crystal lattice")
        self.camera = self.camera.align_lattice_axis(self.crystal.lattice.matrix, axis)
        return self._changed()

    def pan(self, *, dx: float = 0.0, dy: float = 0.0) -> TerminalObservation:
        """Pan the stable terminal viewport in screen-space data units."""
        self._validate_finite("pan", dx, dy)
        self.camera = self.camera.pan(dx=float(dx), dy=float(dy))
        return self._changed()

    def zoom(self, *, factor: float) -> TerminalObservation:
        """Multiply viewport zoom within the existing interactive bounds."""
        self._validate_finite("zoom factor", factor)
        if factor <= 0:
            raise ValueError("zoom factor must be greater than zero")
        self.camera = self.camera.zoom(float(factor))
        return self._changed()

    def set_display(
        self,
        *,
        display_level: str | None = None,
        label_mode: str | None = None,
        show_bonds: bool | None = None,
        show_cell: bool | None = None,
        show_minor: bool | None = None,
        mono: bool | None = None,
    ) -> TerminalObservation:
        """Apply absolute render choices without changing source structure data."""
        # Validate every field before changing any controller state.
        candidate_display_level = (
            self._display_level
            if display_level is None
            else self._validate_display_level(display_level)
        )
        candidate_label_mode = (
            self._label_mode
            if label_mode is None
            else self._validate_label_mode(label_mode)
        )
        candidate_show_bonds = self._show_bonds if show_bonds is None else bool(show_bonds)
        candidate_show_cell = self._show_cell if show_cell is None else bool(show_cell)
        candidate_show_minor = self._show_minor if show_minor is None else bool(show_minor)
        candidate_mono = self._mono if mono is None else bool(mono)
        candidate_edit = self._edit
        if self._edit.mode == "edit" and self._edit.level != candidate_display_level:
            if candidate_display_level == "molecule" and not any(atom.display_fragment_id for atom in self.crystal.atoms):
                raise ValueError("molecule edit requires displayed molecule identities")
            candidate_edit = TerminalEditState(mode="edit", level=candidate_display_level)

        self._display_level = candidate_display_level
        self._label_mode = candidate_label_mode
        self._show_bonds = candidate_show_bonds
        self._show_cell = candidate_show_cell
        self._show_minor = candidate_show_minor
        self._mono = candidate_mono
        self._edit = candidate_edit
        return self._changed()

    def fit(self, *, target: str = "all") -> TerminalObservation:
        """Explicitly fit either all visible structure or the current focus."""
        if target == "all":
            self.camera = replace(self.camera, target=self._all_view_center())
            self._fit_viewport = self._fit_all_viewport()
        elif target == "focus":
            frame_indices = self._focused_frame_indices()
            if not frame_indices:
                raise ValueError("focus has no visible displayed atoms to fit")
            self._fit_viewport = self._fit_indices_viewport(frame_indices)
        else:
            raise ValueError("fit target must be 'all' or 'focus'")
        self.camera = replace(self.camera, pan_x=0.0, pan_y=0.0)
        return self._changed()

    def focus_atom(self, references: str | int | dict[str, Any] | list[str | int | dict[str, Any]]) -> TerminalObservation:
        """Fit exact displayed atom references while preserving all visual context."""
        values = references if isinstance(references, list) else [references]
        indices = resolve_atom_references(self.crystal, values)
        return self._set_focus("atom", indices)

    def focus_molecule(self, reference: str | int | dict[str, Any]) -> TerminalObservation:
        """Fit a source or displayed molecule while preserving all visual context."""
        return self._set_focus("molecule", resolve_molecule_reference(self.crystal, reference))

    def focus_selection(self, references: list[str | int | dict[str, Any]]) -> TerminalObservation:
        """Focus the supplied external selection without mutating browser selection state."""
        if not references:
            raise ValueError("focus_selection requires at least one atom reference")
        return self._set_focus("selection", resolve_atom_references(self.crystal, references))

    def focus_local(
        self,
        reference: str | int | dict[str, Any],
        *,
        bond_depth: int = 1,
    ) -> TerminalObservation:
        """Fit one atom and its manifested bond neighborhood."""
        if isinstance(bond_depth, bool) or not isinstance(bond_depth, int) or bond_depth < 0:
            raise ValueError("bond_depth must be a non-negative integer")
        centers = resolve_atom_references(self.crystal, [reference])
        if len(centers) != 1:
            raise ValueError("local focus requires exactly one displayed atom; use display_copy_id to disambiguate")
        if not self._show_minor and self.crystal.atoms[centers[0]].is_minor:
            raise ValueError("focus has no visible displayed atoms to fit")
        selected = set(centers)
        frontier = set(centers)
        for _ in range(bond_depth):
            neighbors: set[int] = set()
            for bond in self.crystal.bonds:
                if bond.i in frontier:
                    neighbors.add(bond.j)
                if bond.j in frontier:
                    neighbors.add(bond.i)
            frontier = neighbors - selected
            selected.update(neighbors)
        return self._set_focus("local", tuple(index for index in range(self.crystal.n_atoms) if index in selected))

    def clear_focus(self) -> TerminalObservation:
        """Clear active focus and refit all current visible structure."""
        self._focus = TerminalFocusState()
        self._fit_viewport = self._fit_all_viewport()
        self.camera = replace(self.camera, pan_x=0.0, pan_y=0.0)
        return self._changed()

    def save_view(self, name: str, *, overwrite: bool = False) -> TerminalViewSnapshot:
        """Save detached camera, display, focus, and fit state under ``name``."""
        name = self._validate_snapshot_name(name)
        if name in self._snapshots and not overwrite:
            raise ValueError(f"snapshot already exists: {name}")
        display = self.state.display
        viewport = self._copy_viewport(self._fit_viewport)
        self._snapshots[name] = (
            self._copy_camera(self.camera),
            display,
            self._focus,
            self._edit,
            viewport,
        )
        return TerminalViewSnapshot(name=name, state=self.state)

    def restore_view(self, name: str) -> TerminalObservation:
        """Restore a previously saved view atomically."""
        try:
            camera, display, focus, edit, viewport = self._snapshots[name]
        except KeyError as exc:
            raise ValueError(f"unknown snapshot: {name}") from exc
        self.camera = self._copy_camera(camera)
        self._mono = display.mono
        self._show_bonds = display.show_bonds
        self._show_cell = display.show_cell
        self._label_mode = display.label_mode
        self._show_minor = display.show_minor
        self._display_level = display.display_level
        self._focus = focus
        self._edit = edit
        self._fit_viewport = self._copy_viewport(viewport)
        return self._changed()

    def list_views(self) -> tuple[TerminalViewSnapshot, ...]:
        """List detached metadata for saved named views in name order."""
        snapshots: list[TerminalViewSnapshot] = []
        for name in sorted(self._snapshots):
            camera, display, focus, edit, viewport = self._snapshots[name]
            state = TerminalViewState(
                revision=self._revision,
                camera=TerminalCameraState(
                    azimuth=float(camera.azimuth), elevation=float(camera.elevation),
                    roll=float(camera.roll), target=tuple(float(value) for value in camera.target),
                    projection=camera.projection.value,
                    zoom=float(camera.viewport_zoom), pan_x=float(camera.pan_x), pan_y=float(camera.pan_y),
                ),
                display=display,
                focus=focus,
                edit=edit,
                viewport=self._viewport_state_for(camera, viewport),
            )
            snapshots.append(TerminalViewSnapshot(name=name, state=state))
        return tuple(snapshots)

    def inspect_atom(self, references: list[str | int | dict[str, Any]] | None = None) -> dict[str, Any]:
        """Read existing atom provenance; this does not change view state."""
        return inspect_atoms(self.crystal, references, show_minor=self._show_minor)

    def inspect_molecule(self, reference: str | int | dict[str, Any] | None = None) -> dict[str, Any]:
        """Read existing MolCrysKit molecule provenance without re-grouping."""
        return inspect_molecules(self.crystal, reference, show_minor=self._show_minor)

    def inspect_local_geometry(
        self,
        reference: str | int | dict[str, Any],
        *,
        include_angles: bool = True,
    ) -> dict[str, Any]:
        """Read one displayed atom's current bond neighborhood and geometry."""
        return inspect_local_geometry(
            self.crystal,
            reference,
            include_angles=include_angles,
        )

    def inspect_local_geometries(
        self,
        references: list[str | int | dict[str, Any]] | None = None,
        *,
        include_angles: bool = True,
    ) -> dict[str, Any]:
        """Batch-read local geometry for selected or all displayed atoms."""
        return inspect_local_geometries(
            self.crystal,
            references,
            include_angles=include_angles,
        )

    def measure_distance(
        self,
        references: list[str | int | dict[str, Any]],
        *,
        mode: str = "mic",
    ) -> dict[str, Any]:
        """Measure a displayed atom pair without mutating view state."""
        return measure_distance(self.crystal, references, mode=mode)

    def measure_angle(
        self,
        references: list[str | int | dict[str, Any]],
        *,
        mode: str = "mic",
    ) -> dict[str, Any]:
        """Measure A-B-C without mutating view state."""
        return measure_angle(self.crystal, references, mode=mode)

    def measure_dihedral(
        self,
        references: list[str | int | dict[str, Any]],
        *,
        mode: str = "mic_chain",
    ) -> dict[str, Any]:
        """Measure signed A-B-C-D torsion without mutating view state."""
        return measure_dihedral(self.crystal, references, mode=mode)

    def resize(self, width: int, height: int) -> TerminalObservation:
        """Set terminal dimensions and explicitly refit for the new aspect ratio."""
        self._width, self._height = self._validate_dimensions(width, height)
        self._fit_viewport = self._fit_all_viewport()
        return self._changed()

    def resize_viewport(self, width: int, height: int) -> TerminalObservation:
        """Resize the existing stable frame without changing its fitted bounds."""
        self._width, self._height = self._validate_dimensions(width, height)
        self._fit_viewport = viewport_from_bounds(
            self._fit_viewport.x_min,
            self._fit_viewport.x_max,
            self._fit_viewport.y_min,
            self._fit_viewport.y_max,
            self._width,
            self._height,
        )
        return self._changed()

    def reset_view(self) -> TerminalObservation:
        """Restore the supplied startup camera and all-view framing only."""
        self.camera = self._copy_camera(self._initial_camera)
        self._focus = TerminalFocusState()
        self._fit_viewport = self._fit_all_viewport()
        return self._changed()

    def _fit_all_viewport(self) -> Viewport:
        coords = self._all_view_coords()
        if len(coords) == 0:
            return _compute_viewport(np.empty((0, 2)), [], self._width, self._height)

        # A 3D sphere centered on the camera target projects to a bounded
        # square for every orbit orientation. Unlike a frozen current 2D
        # projection, it cannot crop a long structure after rotating side-on.
        radius = float(np.linalg.norm(coords - self.camera.target, axis=1).max())
        extent = self._projected_sphere_extent(radius)
        points = np.array([[-extent, -extent], [extent, extent]], dtype=float)
        return _compute_viewport(points, [], self._width, self._height)

    def _effective_viewport(self) -> Viewport:
        """Return the same zoomed/panned viewport passed to the compositor."""
        return viewport_from_bounds(
            self._fit_viewport.x_min,
            self._fit_viewport.x_max,
            self._fit_viewport.y_min,
            self._fit_viewport.y_max,
            self._width,
            self._height,
            zoom=self.camera.viewport_zoom,
            pan_x=self.camera.pan_x,
            pan_y=self.camera.pan_y,
        )

    @staticmethod
    def _viewport_state_for(camera: Camera, viewport: Viewport) -> TerminalViewportState:
        effective = viewport_from_bounds(
            viewport.x_min,
            viewport.x_max,
            viewport.y_min,
            viewport.y_max,
            viewport.width,
            viewport.height,
            zoom=camera.viewport_zoom,
            pan_x=camera.pan_x,
            pan_y=camera.pan_y,
        )
        return TerminalViewportState(
            width=effective.width,
            height=effective.height,
            scale=float(effective.scale),
            x_min=float(effective.x_min),
            x_max=float(effective.x_max),
            y_min=float(effective.y_min),
            y_max=float(effective.y_max),
        )

    def _all_view_coords(self) -> np.ndarray:
        coords = [
            atom.cart
            for atom in self.crystal.atoms
            if self._show_minor or not atom.is_minor
        ]
        if self._show_cell and self.crystal.lattice is not None:
            coords.extend(self.crystal.lattice.cell_vertices())
        return np.asarray(coords, dtype=float) if coords else np.empty((0, 3))

    def _all_view_center(self) -> np.ndarray:
        coords = self._all_view_coords()
        return coords.mean(axis=0) if len(coords) else np.zeros(3)

    def _projected_sphere_extent(self, radius: float) -> float:
        radius = max(float(radius), 0.005)
        if self.camera.projection == ProjectionMode.ORTHOGRAPHIC:
            return radius / max(float(self.camera.distance), 1e-9)
        near_distance = max(float(self.camera.distance) - radius, 0.01)
        fov_scale = np.tan(np.radians(self.camera.fov_deg / 2.0))
        return radius / max(near_distance * fov_scale, 1e-9)

    def _fit_indices_viewport(self, indices: tuple[int, ...]) -> Viewport:
        coords = np.asarray([self.crystal.atoms[index].cart for index in indices], dtype=float)
        points, _ = project_points(self.camera, coords)
        return _compute_viewport(points, [], self._width, self._height)

    def _set_focus(self, kind: str, indices: tuple[int, ...]) -> TerminalObservation:
        matched_ids = tuple(self.crystal.atoms[index].display_copy_id for index in indices)
        visible = tuple(index for index in indices if self._show_minor or not self.crystal.atoms[index].is_minor)
        hidden_ids = tuple(
            self.crystal.atoms[index].display_copy_id
            for index in indices
            if index not in visible
        )
        frame_indices = self._expand_frame_indices(visible)
        focus = TerminalFocusState(
            kind=kind,
            matched_copy_ids=matched_ids,
            framed_copy_ids=tuple(self.crystal.atoms[index].display_copy_id for index in frame_indices),
            hidden_copy_ids=hidden_ids,
        )
        if not frame_indices:
            raise ValueError("focus has no visible displayed atoms to fit")
        viewport = self._fit_indices_viewport(frame_indices)
        self._focus = focus
        self._fit_viewport = viewport
        self.camera = replace(self.camera, pan_x=0.0, pan_y=0.0)
        return self._changed()

    def _focused_frame_indices(self) -> tuple[int, ...]:
        ids = set(self._focus.framed_copy_ids)
        return tuple(index for index, atom in enumerate(self.crystal.atoms) if atom.display_copy_id in ids)

    def _expand_frame_indices(self, indices: tuple[int, ...]) -> tuple[int, ...]:
        if self._display_level != "molecule":
            return indices
        molecule_indices = {
            self.crystal.atoms[index].molecule_index
            for index in indices
            if self.crystal.atoms[index].molecule_index >= 0
        }
        if not molecule_indices:
            return indices
        return tuple(
            index for index, atom in enumerate(self.crystal.atoms)
            if atom.molecule_index in molecule_indices and (self._show_minor or not atom.is_minor)
        )

    def _changed(self) -> TerminalObservation:
        self._revision += 1
        return self.observe()

    @staticmethod
    def _copy_camera(camera: Camera) -> Camera:
        basis = None if camera.basis is None else camera.basis.copy()
        return replace(camera, target=camera.target.copy(), basis=basis)

    @staticmethod
    def _copy_viewport(viewport: Viewport) -> Viewport:
        return Viewport(
            x_min=float(viewport.x_min), x_max=float(viewport.x_max),
            y_min=float(viewport.y_min), y_max=float(viewport.y_max),
            scale=float(viewport.scale), width=int(viewport.width), height=int(viewport.height),
        )

    @staticmethod
    def _validate_snapshot_name(name: str) -> str:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("snapshot name must be a non-empty string")
        return name.strip()

    @staticmethod
    def _validate_dimensions(width: int, height: int) -> tuple[int, int]:
        if isinstance(width, bool) or isinstance(height, bool):
            raise TypeError("viewport dimensions must be integers")
        width, height = int(width), int(height)
        if width <= 0 or height <= 0:
            raise ValueError("viewport dimensions must be greater than zero")
        return width, height

    @staticmethod
    def _validate_finite(name: str, *values: float) -> None:
        if not all(np.isfinite(value) for value in values):
            raise ValueError(f"{name} values must be finite")

    @staticmethod
    def _validate_label_mode(label_mode: str) -> str:
        if label_mode not in LABEL_MODES:
            raise ValueError(f"label_mode must be one of {LABEL_MODES}")
        return label_mode

    @staticmethod
    def _validate_display_level(display_level: str) -> str:
        if display_level not in DISPLAY_LEVELS:
            raise ValueError(f"display_level must be one of {DISPLAY_LEVELS}")
        return display_level

    @staticmethod
    def _validate_projection(projection: str | ProjectionMode) -> ProjectionMode:
        if isinstance(projection, ProjectionMode):
            return projection
        try:
            return ProjectionMode(str(projection))
        except ValueError as exc:
            raise ValueError("projection must be 'orthographic' or 'perspective'") from exc

    @staticmethod
    def _with_zoom(camera: Camera, zoom: float) -> Camera:
        if not np.isfinite(zoom) or zoom <= 0:
            raise ValueError("zoom must be greater than zero")
        return replace(camera, viewport_zoom=float(np.clip(zoom, 0.5, 20.0)))


__all__ = ["TerminalViewController"]