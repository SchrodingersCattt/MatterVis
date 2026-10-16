"""Figure-journal reads.

These helpers snapshot the journal before they compare it with scene state.
They must not run that comparison while holding the journal lock, because
state reads take the backend lock and ``patch_state`` acquires the locks in
the other order.
"""

from __future__ import annotations

import copy
from typing import Any, Optional


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

    def ensure_active_scene_figure(self) -> bool:
        """Queue a figure when a subscriber arrives before any journal frame."""
        state = self.get_state()
        if not isinstance(state, dict):
            return False
        scene_id = state.get("scene_id")
        if not scene_id or self.latest_figure_broadcast(scene_id) is not None:
            return False
        worker = getattr(self, "_render_worker", None)
        if worker is None:
            return False
        return bool(worker.request_figure_build(state))

    def peek_pending_state(self) -> Optional[dict[str, Any]]:
        """Return the armed poll payload without consuming it."""
        with self._lock:
            return copy.deepcopy(self.pending_state) if self.pending_state else None


__all__ = ["_JournalBackendMixin"]
