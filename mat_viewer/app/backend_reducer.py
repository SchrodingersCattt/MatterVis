"""Compatibility bridge from typed view actions to ``ViewerBackend``.

The pure transition logic lives in :mod:`mat_viewer.app.reducer`.  This mixin
contains the small amount of service wiring needed to read a scene, invoke the
reducer, and hand the resulting legacy payload to the existing persistence
methods.  Keeping this adapter out of ``backend_core.py`` avoids growing the
already-large core while the reducer migration is in progress.
"""

from __future__ import annotations

import copy
from typing import Any, Optional

from .reducer import (
    Invalidation,
    Operation,
    OperationKind,
    invalidations_for,
    operation_from_intent,
    operation_patch,
    reduce_state,
)


class _ReducerBackendMixin:
    def _prepare_operation(
        self,
        payload: dict[str, Any],
        *,
        scene_id: Optional[str] = None,
    ) -> tuple[Operation, frozenset[Invalidation]]:
        """Build an operation and calculate facts against the target scene."""
        operation = operation_from_intent(payload)
        target_scene = scene_id or operation.scene_id or self.active_scene_id()
        target_state = (
            self.get_state(str(target_scene))
            if target_scene and str(target_scene) in self.scene_store.scenes
            else self.get_state()
        )
        _reduced_state, invalidations = reduce_state(target_state, operation)
        return operation, invalidations

    def apply_operation(
        self,
        operation: Operation,
        *,
        scene_id: Optional[str] = None,
        broadcast: bool = True,
    ) -> tuple[dict[str, Any], frozenset[Invalidation]]:
        """Apply one typed operation through the compatibility backend.

        ``reduce_state`` remains the source of transition semantics and this
        method only adapts its payload to the existing persistence machinery.
        It is intentionally additive: old REST ``patch_state`` callers keep
        their wire shape while new callers can use a stable operation API.
        """
        if not isinstance(operation, Operation):
            raise TypeError("operation must be an Operation")
        target_scene = scene_id or operation.scene_id or self.active_scene_id()
        before = self.get_state(target_scene) if target_scene else self.get_state()
        reduced, invalidations = reduce_state(before, operation)
        kind = operation.name

        if kind == OperationKind.SWITCH_SCENE.value:
            target = operation.payload.get("scene_id") or target_scene
            if not target:
                raise ValueError("switch_scene requires scene_id")
            self.set_active_scene(str(target), broadcast=broadcast)
            return self.get_state(str(target)), invalidations
        if kind == OperationKind.RENAME_SCENE.value:
            if not target_scene:
                raise ValueError("rename_scene requires scene_id")
            self.update_scene(
                str(target_scene), {"label": operation.payload.get("label", "")}
            )
            return self.get_state(str(target_scene)), invalidations
        if kind == OperationKind.RESET_CAMERA.value:
            self.camera_action("reset", scene_id=target_scene, broadcast=broadcast)
            return self.get_state(target_scene), invalidations

        # Persist the reducer's committed transition, rather than replaying
        # the caller's intent payload.  This keeps typed operations and legacy
        # intents on one state machine and makes unknown operations read-only.
        patch = operation_patch(operation)
        known_kinds = {item.value for item in OperationKind} | {
            "upload_complete",
            "crud_scene",
        }
        if kind not in known_kinds:
            patch = {}
        else:
            for key, value in reduced.items():
                if before.get(key) != value and key not in {
                    "geometry_version",
                    "display_version",
                    "camera_version",
                    "render_revision",
                    "server_started_at",
                }:
                    patch[key] = copy.deepcopy(value)
        if kind == OperationKind.SET_PROJECTION.value and isinstance(
            reduced.get("camera"), dict
        ):
            # ``set_projection`` is mirrored onto both state and camera by the
            # old backend method; carry that invariant into the bridge.
            patch["camera"] = reduced["camera"]
        if kind == OperationKind.SET_DISPLAY_MODE.value and not operation.payload.get(
            "reset_topology_site", True
        ):
            # ``patch_state`` treats a display-mode write without an explicit
            # site as the legacy "reset site" request. Preserve the typed
            # operation's opt-out by carrying the reduced site through even
            # when its value did not change.
            patch["topology_site_index"] = copy.deepcopy(
                reduced.get("topology_site_index")
            )
        state = self.patch_state(patch, scene_id=target_scene, broadcast=broadcast)
        # Report facts for the state that was actually committed. The
        # compatibility normalizer can add transitions that the pure reducer
        # cannot know about (for example style aliases and display-mode
        # defaults), so returning the planned set would under-invalidate both
        # legacy and typed callers.
        return state, invalidations_for(operation, before, state)


__all__ = ["_ReducerBackendMixin"]
