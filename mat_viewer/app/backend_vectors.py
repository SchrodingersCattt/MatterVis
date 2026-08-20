"""Per-tab scientific vector-overlay state helpers."""
from __future__ import annotations

import copy


class _VectorBackendMixin:
    def default_state(self, structure: str) -> dict:
        state = super().default_state(structure)
        state["vector_overlays"] = []
        return state

    def scene_for_state(self, state=None) -> dict:
        """Return a tab-local scene copy carrying scientific vector content."""
        state = self.current_state if state is None else state
        scene = super().scene_for_state(state)
        local = dict(scene)
        local["vector_overlays"] = copy.deepcopy(state.get("vector_overlays") or [])
        return local

    def normalize_state(self, patch, scene_id=None) -> dict:
        """Normalize vector state in place and invalidate incompatible cameras."""
        state = super().normalize_state(patch, scene_id=scene_id)
        patch = patch or {}
        if "vector_overlays" not in patch:
            return state
        from ..render.overlay.vectors import normalize_vector_overlays

        previous = state.get("vector_overlays") or []
        state["vector_overlays"] = normalize_vector_overlays(
            patch.get("vector_overlays") or []
        )
        if state["vector_overlays"] == previous or "camera_revision" in patch:
            return state
        state["camera"] = None
        try:
            state["camera_revision"] = int(state.get("camera_revision", 0) or 0) + 1
        except (TypeError, ValueError):
            state["camera_revision"] = 1
        return state


__all__ = ["_VectorBackendMixin"]