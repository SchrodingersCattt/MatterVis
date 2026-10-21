"""Typed view update envelopes shared by Dash, WebSocket and the browser.

The viewer has three independent clocks: the structure/geometry clock, the
display clock and the camera clock.  This module is deliberately free of Dash
and Plotly imports so callbacks, workers and protocol tests can use the same
classification rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import copy
import json
from typing import Any, Mapping

from .selectors import geometry_state_fields
from ..cache_keys import display_state_key as _canonical_display_state_key


class UpdateKind(str, Enum):
    CAMERA = "camera"
    OVERLAY = "overlay"
    DISPLAY = "display"
    GEOMETRY = "geometry"
    ANALYSIS = "analysis"
    FULL = "full"
    ERROR = "error"


@dataclass(frozen=True)
class ViewVersions:
    geometry: int = 0
    display: int = 0
    camera: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "geometry_version": int(self.geometry),
            "display_version": int(self.display),
            "camera_version": int(self.camera),
        }


@dataclass(frozen=True)
class ViewUpdate:
    """A delivery-safe update.  ``geometry_version`` scopes all patches."""

    kind: UpdateKind | str
    scene_id: str | None
    versions: ViewVersions
    payload: dict[str, Any] = field(default_factory=dict)
    reason: str = "state-confirmed"
    scene_generation: int | str | None = None

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value if isinstance(self.kind, UpdateKind) else str(self.kind)
        data = {
            "type": "view_update",
            "update_kind": kind,
            "kind": kind,
            "scene_id": self.scene_id,
            "scene_generation": self.scene_generation,
            "reason": self.reason,
            "versions": self.versions.to_dict(),
            "geometry_version": self.versions.geometry,
            "display_version": self.versions.display,
            "camera_version": self.versions.camera,
            "payload": copy.deepcopy(self.payload),
        }
        # Keeping the top-level payload fields makes the envelope convenient
        # for the small browser patches and preserves forward compatibility
        # with early WS clients.
        data.update(copy.deepcopy(self.payload))
        return data


def _stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def state_versions(state: Mapping[str, Any] | None) -> ViewVersions:
    state = state or {}
    # ``render_revision`` and ``camera_revision`` are the historic public
    # names.  New fields are aliases so persisted scenes and old clients keep
    # working while the clocks are separated.
    geometry = state.get("geometry_version", state.get("render_revision", 0))
    display = state.get("display_version", state.get("render_revision", 0))
    camera = state.get("camera_version", state.get("camera_revision", 0))
    try:
        return ViewVersions(int(geometry or 0), int(display or 0), int(camera or 0))
    except (TypeError, ValueError):
        return ViewVersions()


def geometry_state_key(state: Mapping[str, Any] | None) -> str:
    # Keep the async worker's broader update identity in one selector module.
    # The strict base-scene cache key lives beside it as
    # ``scene_geometry_cache_key`` for the later cache-centralisation phase.
    return _stable(geometry_state_fields(state))


def display_state_key(state: Mapping[str, Any] | None) -> str:
    """Compatibility wrapper for the shared canonical display key."""

    return _canonical_display_state_key(state)


# Fields the browser cannot patch by restyling an existing trace. A change
# to any of them needs a complete frame. Multi-field edits take the
# strongest of these requirements.
_COMPLETE_FRAME_FIELDS = (
    "atom_scale",
    "bond_radius",
    "material",
    "style",
    "ortep_mode",
    "label_mode",
    "atom_property_color",
    "minor_bond_scale",
    "scatter_atom_scale",
    "scatter_bond_scale",
    "fast_rendering",
    "vector_overlays",
    "selection",
    "cell_overlays",
    "overlay_overrides",
)


def _polyhedron_specs_need_rebuild(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    """Visibility-only polyhedron edits stay on the local display path."""

    def stripped(value: Any) -> list[Any]:
        if not isinstance(value, list):
            return []
        rows = []
        for spec in value:
            if isinstance(spec, dict):
                rows.append({key: item for key, item in spec.items() if key != "enabled"})
            else:
                rows.append(spec)
        return rows

    return stripped(before.get("polyhedron_specs")) != stripped(after.get("polyhedron_specs"))


def _needs_complete_frame(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    if any(before.get(key) != after.get(key) for key in _COMPLETE_FRAME_FIELDS):
        return True
    return _polyhedron_specs_need_rebuild(before, after)


def classify_change(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    *,
    operation: str | None = None,
) -> UpdateKind:
    """Classify a confirmed state change, with explicit operations winning."""

    op = str(operation or "").lower()
    if op in {"camera", "set_camera", "reset", "align", "projection"}:
        return UpdateKind.CAMERA
    if op in {"analysis", "topology", "bfdh"}:
        return UpdateKind.ANALYSIS
    if op in {"geometry", "hydrogens", "transform", "structure"}:
        return UpdateKind.GEOMETRY
    if op in {"overlay", "axes", "compass"}:
        return UpdateKind.OVERLAY
    if op in {"display", "labels", "opacity", "polyhedron"}:
        return UpdateKind.DISPLAY
    if before is None:
        return UpdateKind.FULL
    # A tab change can share geometry with the scene it replaces (duplicate
    # structure, same scope). Classifying that as DISPLAY leaves the previous
    # figure on the canvas while the checkbox already shows the new scene.
    if str(before.get("scene_id") or "") != str(after.get("scene_id") or ""):
        if geometry_state_key(before) != geometry_state_key(after):
            return UpdateKind.GEOMETRY
        return UpdateKind.FULL
    if state_versions(before).camera != state_versions(after).camera or before.get("camera") != after.get("camera"):
        # A camera-only change is cheap; a camera changed alongside geometry
        # still belongs to the geometry transaction.
        if geometry_state_key(before) == geometry_state_key(after) and display_state_key(before) == display_state_key(after):
            return UpdateKind.CAMERA
    # The scene label is rendered into the Plotly figure title. It cannot be
    # applied by the local display patch path (which only restyles existing
    # traces), so a rename must enqueue a complete frame for the browser.
    if (
        before.get("scene_id") == after.get("scene_id")
        and before.get("scene_label") != after.get("scene_label")
    ):
        return UpdateKind.FULL
    if geometry_state_key(before) != geometry_state_key(after):
        return UpdateKind.GEOMETRY
    if _needs_complete_frame(before, after):
        return UpdateKind.GEOMETRY
    before_opts = set(before.get("display_options") or [])
    after_opts = set(after.get("display_options") or [])
    option_delta = before_opts ^ after_opts
    if option_delta and option_delta <= {"axes"}:
        return UpdateKind.OVERLAY
    if not option_delta and before.get("axis_scale") != after.get("axis_scale"):
        return UpdateKind.OVERLAY
    if before.get("bfdh_morphology") != after.get("bfdh_morphology"):
        return UpdateKind.ANALYSIS
    if display_state_key(before) != display_state_key(after):
        return UpdateKind.DISPLAY
    return UpdateKind.FULL


def make_update(
    state: Mapping[str, Any],
    *,
    before: Mapping[str, Any] | None = None,
    operation: str | None = None,
    payload: Mapping[str, Any] | None = None,
    reason: str = "state-confirmed",
) -> ViewUpdate:
    return ViewUpdate(
        kind=classify_change(before, state, operation=operation),
        scene_id=str(state.get("scene_id")) if state.get("scene_id") is not None else None,
        versions=state_versions(state),
        payload=dict(payload or {}),
        reason=reason,
        scene_generation=state.get("scene_generation"),
    )


def update_applies(update: Mapping[str, Any] | ViewUpdate, state: Mapping[str, Any]) -> bool:
    """Return whether a patch can safely be applied to ``state``."""
    if isinstance(update, ViewUpdate):
        data = update.to_dict()
    else:
        data = update
    scene_id = data.get("scene_id")
    if scene_id is not None and str(scene_id) != str(state.get("scene_id")):
        return False
    current = state_versions(state)
    versions = data.get("versions") or data
    try:
        geometry = int(versions.get("geometry_version", 0) or 0)
        display = int(versions.get("display_version", 0) or 0)
        camera = int(versions.get("camera_version", 0) or 0)
    except (TypeError, ValueError):
        return False
    kind = str(data.get("update_kind", data.get("kind", "full")))
    if geometry != current.geometry:
        return False
    if kind in {UpdateKind.DISPLAY.value, UpdateKind.OVERLAY.value, UpdateKind.ANALYSIS.value}:
        return display == current.display
    if kind == UpdateKind.CAMERA.value:
        return camera == current.camera
    return geometry == current.geometry and display == current.display


def merge_pending_updates(updates: list[ViewUpdate]) -> list[ViewUpdate]:
    """Coalesce queued updates while retaining the newest per scene/kind."""
    latest: dict[tuple[str | None, str], ViewUpdate] = {}
    for update in updates:
        key = (update.scene_id, str(update.kind))
        latest[key] = update
    return sorted(latest.values(), key=lambda item: (item.versions.geometry, item.versions.display, item.versions.camera))


# Small compatibility aliases for host integrations that used the protocol
# vocabulary before this module was split out.
UpdateType = UpdateKind
classify_update = classify_change
versions_for_state = state_versions
is_update_applicable = update_applies

