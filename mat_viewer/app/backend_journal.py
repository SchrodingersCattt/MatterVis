"""Figure-journal reads.

These helpers snapshot the journal before they compare it with scene state.
They must not run that comparison while holding the journal lock, because
state reads take the backend lock and ``patch_state`` acquires the locks in
the other order.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Optional

from ..render.retention import (
    FIGURE_JOURNAL_MAX_BYTES,
    FIGURE_JOURNAL_MAX_ENTRIES,
    clear_bundle_render_caches,
    payload_nbytes,
)


def _figure_cache_scene_id(key: str) -> str | None:
    try:
        payload = json.loads(key)
    except (TypeError, ValueError):
        return None
    if isinstance(payload, dict) and payload.get("scene_id") is not None:
        return str(payload["scene_id"])
    return None


class _JournalBackendMixin:
    def figure_journal_seq(self) -> int:
        with self._figure_broadcast_lock:
            return int(self._figure_broadcast_seq)

    def _refs_since(self, floor: int) -> tuple[list[dict[str, Any]], int]:
        """Return journal references newer than ``floor`` plus the counter.

        The lock covers the filter only. Callers deepcopy after releasing
        it so a slow copy does not block ``patch_state``.
        """
        with self._figure_broadcast_lock:
            head = int(self._figure_broadcast_seq)
            refs = [
                payload
                for payload in self._figure_broadcasts
                if int(payload.get("figure_seq", 0) or 0) > floor
            ]
            return refs, head

    def _copy_current(self, refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        snapshot = [copy.deepcopy(payload) for payload in refs]
        states = self._journal_current_states(snapshot)
        return [
            payload
            for payload in snapshot
            if self._journal_payload_is_current(payload, states)
        ]

    def _journal_current_states(
        self, payloads: list[dict[str, Any]]
    ) -> dict[str, Optional[dict[str, Any]]]:
        """Read each scene once, outside the journal lock."""
        states: dict[str, Optional[dict[str, Any]]] = {}
        for payload in payloads:
            scene_id = payload.get("scene_id")
            key = str(scene_id) if scene_id else ""
            if not key or key in states or not isinstance(payload.get("state"), dict):
                continue
            try:
                states[key] = self.get_state(scene_id)
            except Exception:
                states[key] = None
        return states

    def _journal_payload_is_current(
        self,
        payload: dict[str, Any],
        states: dict[str, Optional[dict[str, Any]]],
    ) -> bool:
        scene_id = payload.get("scene_id")
        state = payload.get("state")
        if not isinstance(state, dict) or not scene_id:
            return True
        current = states.get(str(scene_id))
        if current is None:
            return False
        try:
            if "scene_generation" in state and int(state.get("scene_generation") or 0) != int(
                current.get("scene_generation") or 0
            ):
                return False
            if int(state.get("render_revision", -1)) != int(current.get("render_revision", -1)):
                return False
            payload_camera = int(state.get("camera_revision", 0) or 0)
            current_camera = int(current.get("camera_revision", 0) or 0)
            if payload_camera != current_camera:
                return False
            return self._figure_state_cache_key(state) == self._figure_state_cache_key(current)
        except Exception:
            return False

    def figure_broadcasts_since_with_head(
        self, seq: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        """Return current events and the journal counter from one snapshot.

        ``head`` is the counter observed with the references, not a later
        append. A frame that arrives while the copies are filtered stays
        pending because its sequence is greater than ``head``.
        """
        refs, head = self._refs_since(int(seq))
        return self._copy_current(refs), head

    def figure_broadcasts_since(self, seq: int = 0) -> list[dict[str, Any]]:
        events, _head = self.figure_broadcasts_since_with_head(seq)
        return events

    def latest_figure_broadcast(
        self, scene_id: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        with self._figure_broadcast_lock:
            refs = [
                payload
                for payload in reversed(self._figure_broadcasts)
                if payload.get("type") == "figure"
                and (scene_id is None or payload.get("scene_id") == scene_id)
            ]
        current = self._copy_current(refs)
        return current[0] if current else None

    def ensure_scene_figure(self, scene_id: Optional[str] = None) -> bool:
        """Queue a complete frame for a scene missing from the journal."""
        state = self.get_state(scene_id)
        if not isinstance(state, dict):
            return False
        target = state.get("scene_id")
        if not target or self.latest_figure_broadcast(target) is not None:
            return False
        worker = getattr(self, "_render_worker", None)
        if worker is None:
            return False
        return bool(worker.request_figure_build(state))

    def ensure_active_scene_figure(self) -> bool:
        """Queue a figure when a subscriber arrives before any journal frame."""
        return self.ensure_scene_figure()

    def peek_pending_state(self) -> Optional[dict[str, Any]]:
        """Return the armed poll payload without consuming it."""
        with self._lock:
            return copy.deepcopy(self.pending_state) if self.pending_state else None

    def _queue_scene_render_release(
        self, scene_id: str, structure_name: str | None = None
    ) -> None:
        """Drop one scene's figure bodies, and its meshes when nothing else uses them.

        Figure-cache eviction takes only the figure lock. Journal entries
        are removed on the next journal critical section, because a
        broadcast holds that lock and then takes the backend lock.
        Scene lifecycle methods already hold the backend lock.
        """

        self._drop_figure_cache_for_scene(str(scene_id))
        if structure_name and not self._structure_has_open_scene(str(structure_name)):
            bundle = self.bundles.get(str(structure_name))
            if bundle is not None:
                clear_bundle_render_caches(bundle)
        self._journal_drop_scene_ids.add(str(scene_id))

    def _structure_has_open_scene(self, structure_name: str) -> bool:
        scenes = getattr(getattr(self, "scene_store", None), "scenes", {}) or {}
        return any(
            getattr(scene, "structure_name", None) == structure_name
            for scene in scenes.values()
        )

    def _drop_figure_cache_for_scene(self, scene_id: str) -> None:
        scene_id = str(scene_id)
        with self._figure_cache_lock:
            stale = [
                key
                for key in list(self._figure_cache.keys())
                if _figure_cache_scene_id(key) == scene_id
            ]
            for key in stale:
                self._figure_cache.pop(key, None)

    def _note_journal_locked(self, payload: dict[str, Any]) -> None:
        """Append one journal envelope and enforce the count and byte caps.

        Caller holds ``_figure_broadcast_lock``.
        """

        self._apply_journal_scene_drops_locked()
        figure = payload.get("figure") if isinstance(payload, dict) else None
        size = payload_nbytes(figure) if isinstance(figure, (dict, list)) else 0
        self._figure_broadcasts.append(payload)
        sizes = getattr(self, "_figure_broadcast_bytes", None)
        if not isinstance(sizes, dict):
            sizes = self._figure_broadcast_bytes = {}
        sizes[id(payload)] = size
        self._trim_journal_locked()

    def _apply_journal_scene_drops_locked(self) -> None:
        pending = getattr(self, "_journal_drop_scene_ids", None)
        if not pending:
            return
        scene_ids = set(pending)
        pending.clear()
        sizes = getattr(self, "_figure_broadcast_bytes", {}) or {}
        kept: list[dict[str, Any]] = []
        for payload in self._figure_broadcasts:
            scene_id = ""
            if isinstance(payload, dict):
                scene_id = str(payload.get("scene_id") or "")
            if (
                scene_id in scene_ids
                and isinstance(payload, dict)
                and payload.get("type") != "scene_catalog"
            ):
                sizes.pop(id(payload), None)
                continue
            kept.append(payload)
        self._figure_broadcasts[:] = kept

    def _trim_journal_locked(self) -> None:
        broadcasts = self._figure_broadcasts
        sizes = getattr(self, "_figure_broadcast_bytes", {}) or {}
        while len(broadcasts) > FIGURE_JOURNAL_MAX_ENTRIES:
            old = broadcasts.pop(0)
            sizes.pop(id(old), None)

        def total() -> int:
            return sum(sizes.get(id(item), 0) for item in broadcasts)

        def figure_indexes() -> list[int]:
            return [
                index
                for index, item in enumerate(broadcasts)
                if isinstance(item, dict)
                and item.get("type") == "figure"
                and item.get("figure") is not None
            ]

        while total() > FIGURE_JOURNAL_MAX_BYTES:
            indexes = figure_indexes()
            # Keep the newest figure so a reconnect still has a body.
            if len(indexes) <= 1:
                break
            old = broadcasts.pop(indexes[0])
            sizes.pop(id(old), None)


__all__ = ["_JournalBackendMixin"]
