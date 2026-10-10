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

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value if isinstance(self.kind, UpdateKind) else str(self.kind)
        data = {
            "type": "view_update",
            "update_kind": kind,
            "kind": kind,
            "scene_id": self.scene_id,
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


_VOLATILE = {
    "version",
    "server_started_at",
    "render_revision",
    "geometry_version",
    "display_version",
    "camera_version",
    "camera_revision",
    "projection",
}


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
    state = state or {}
    # These fields can change atom/bond/mesh identity and therefore require a
    # background geometry build.  Labels, axes, opacity and visibility are
    # display patches and intentionally do not participate.
    options = set(state.get("display_options") or [])
    fields = {
        key: state.get(key)
        for key in (
            "structure",
            "display_mode",
            # Only geometry-affecting display options belong here.  Labels,
            # axes and visibility toggles are trace/SVG patches.
            "display_options",
            "transforms",
            "disorder",
            "disorder_resolve",
            "disorder_replicas",
            "cutoff",
        )
    }
    fields["display_options"] = sorted(options - {"labels", "axes"})
    fields["hydrogens"] = "hydrogens" in options
    fields["structure"] = state.get("structure")
    return _stable(fields)


def display_state_key(state: Mapping[str, Any] | None) -> str:
    """Compatibility wrapper for the shared canonical display key."""

    return _canonical_display_state_key(state)


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
    if state_versions(before).camera != state_versions(after).camera or before.get("camera") != after.get("camera"):
        # A camera-only change is cheap; a camera changed alongside geometry
        # still belongs to the geometry transaction.
        if geometry_state_key(before) == geometry_state_key(after) and display_state_key(before) == display_state_key(after):
            return UpdateKind.CAMERA
    if geometry_state_key(before) != geometry_state_key(after):
        return UpdateKind.GEOMETRY
    before_opts = set(before.get("display_options") or [])
    after_opts = set(after.get("display_options") or [])
    option_delta = before_opts ^ after_opts
    if (option_delta and option_delta <= {"axes"}) or before.get("axis_scale") != after.get("axis_scale"):
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

