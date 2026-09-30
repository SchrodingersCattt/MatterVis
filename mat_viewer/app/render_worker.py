from __future__ import annotations

from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
import copy
import json
import os
import threading
from typing import Any

from .backend_topology import compute_topology_geometry_payload
from .view_updates import UpdateKind


# Opt-in process-based topology worker via environment variable.
# Thread-first is the default because sending full ``LoadedCrystal`` /
# ``MolecularCrystal`` / pymatgen objects across process boundaries
# relies on third-party pickle support, which historically caused
# silent repeated fallbacks.  Set ``MATTERVIS_TOPOLOGY_WORKER=process``
# for CPU-isolated topology on hosts where MolCrysKit objects are
# reliably picklable.
_TOPOLOGY_WORKER_MODE = os.environ.get("MATTERVIS_TOPOLOGY_WORKER", "thread")


class AsyncRenderWorker:
    """Background topology/figure pipeline.

    Flask request threads only enqueue work.  The expensive MolCrysKit
    topology pass runs in a background thread pool by default; an
    optional process-pool mode is available via
    ``MATTERVIS_TOPOLOGY_WORKER=process``.

    Final Plotly JSON assembly always runs in a daemon thread before
    the result is pushed to WebSocket subscribers.
    """

    def __init__(self, backend):
        self.backend = backend
        max_workers = max(1, min(4, os.cpu_count() or 1))
        if _TOPOLOGY_WORKER_MODE == "process":
            self._compute_pool = ProcessPoolExecutor(
                max_workers=max_workers,
                mp_context=None,
            )
            self._use_process = True
        else:
            self._compute_pool = ThreadPoolExecutor(
                max_workers=max_workers,
                thread_name_prefix="mattervis-topology-compute",
            )
            self._use_process = False
        self._finalize_pool = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="mattervis-render-finalize",
        )
        self._lock = threading.RLock()
        self._closed = False
        self._pending: set[tuple[Any, ...]] = set()
        self._latest_topology_by_scene: dict[str, tuple[Any, ...]] = {}
        self._topology_futures: dict[tuple[Any, ...], Future] = {}
        self._pending_render: set[str] = set()
        self._latest_render_by_scene: dict[str, str] = {}
        self._render_futures: dict[str, Future] = {}
        self._task_status: dict[str, dict[str, Any]] = {}

    def task_status(self, scene_id: str | None = None) -> dict[str, dict[str, Any]]:
        with self._lock:
            values = self._task_status
            if scene_id is not None:
                values = {
                    key: value
                    for key, value in values.items()
                    if value.get("scene_id") == str(scene_id)
                }
            return copy.deepcopy(values)

    def _is_current(self, state: dict[str, Any]) -> bool:
        scene_id = state.get("scene_id")
        return (
            not self._closed
            and scene_id in self.backend.scene_store.scenes
            and self.backend._figure_state_matches_current(scene_id, state)
            and self.backend._figure_revision_matches_current(scene_id, state)
        )

    def _report_error(self, state: dict[str, Any], exc: Exception) -> None:
        with self._lock:
            if self._is_current(state):
                self.backend.broadcast_render_error(
                    scene_id=state.get("scene_id"),
                    error=f"{type(exc).__name__}: {exc}",
                )

    def request_topology(self, state: dict[str, Any], context: dict[str, Any]) -> bool:
        state = copy.deepcopy(state)
        cache_key = context["cache_key"]
        scene_key = str(state.get("scene_id") or "")
        with self._lock:
            if self._closed:
                return False
            previous_key = self._latest_topology_by_scene.get(scene_key)
            if previous_key and previous_key != cache_key:
                previous_future = self._topology_futures.get(previous_key)
                if previous_future is not None:
                    previous_future.cancel()
                self._pending.discard(previous_key)
            self._latest_topology_by_scene[scene_key] = cache_key
            if cache_key in self._pending:
                return True
            self._pending.add(cache_key)

        payload = {
            "bundle": context["bundle"],
            "scene": context["scene"],
            "effective_specs": copy.deepcopy(context["effective_specs"]),
            "site_index": int(context["site_index"]),
            "cutoff": float(context["cutoff"]),
        }
        try:
            future = self._compute_pool.submit(compute_topology_geometry_payload, payload)
            with self._lock:
                self._topology_futures[cache_key] = future
        except Exception:
            # Submission failure (e.g. process pool couldn't pickle).
            # Fall back to direct computation in the finalizer thread.
            try:
                future = self._finalize_pool.submit(compute_topology_geometry_payload, payload)
            except Exception as exc:
                with self._lock:
                    self._pending.discard(cache_key)
                    self._topology_futures.pop(cache_key, None)
                self._report_error(state, exc)
                return False

        def finish(fut: Future) -> None:
            with self._lock:
                if self._closed or fut.cancelled():
                    self._pending.discard(cache_key)
                    self._topology_futures.pop(cache_key, None)
                    return
            try:
                finalizer = self._finalize_pool.submit(
                    self._finish_topology, cache_key, context["structure"], state, payload, fut
                )
                finalizer.add_done_callback(cleanup)
            except Exception as exc:
                with self._lock:
                    self._pending.discard(cache_key)
                    self._topology_futures.pop(cache_key, None)
                self._report_error(state, exc)

        def cleanup(fut: Future) -> None:
            if fut.cancelled():
                with self._lock:
                    self._pending.discard(cache_key)
                    self._topology_futures.pop(cache_key, None)

        future.add_done_callback(finish)
        return True

    def _finish_topology(
        self,
        cache_key: tuple[Any, ...],
        structure: str,
        state: dict[str, Any],
        payload: dict[str, Any],
        future: Future,
    ) -> None:
        try:
            if future.cancelled() or not self._is_current(state):
                return
            try:
                geometry = future.result()
            except Exception:
                # Worker failure (pickle, OOM, or MolCrysKit crash).
                # Recompute in this finalizer thread so the invariant
                # "request thread never blocks" still holds.
                geometry = compute_topology_geometry_payload(payload)
            if geometry is None:
                return
            self.backend._store_topology_geometry(structure, cache_key, geometry)
            fig, topology_data = self.backend.figure_for_state(state, async_topology=False)
            with self._lock:
                if self._is_current(state):
                    self.backend.broadcast_figure(
                        scene_id=state.get("scene_id"),
                        figure=fig.to_plotly_json(),
                        topology_data=topology_data,
                        state=state,
                        reason="topology-ready",
                    )
        except Exception as exc:
            self._report_error(state, exc)
        finally:
            with self._lock:
                self._pending.discard(cache_key)
                self._topology_futures.pop(cache_key, None)

    def prewarm(self, state: dict[str, Any]) -> None:
        self._request_render(state, reason="prewarm-ready")

    def request_figure_build(self, state: dict[str, Any]) -> bool:
        """Submit a full figure build to the background pool.

        Returns True if the build was submitted (or already running).
        The completed figure is pushed to clients via WebSocket
        ``broadcast_figure(reason="figure-ready")``.
        """
        return self._request_render(state, reason="figure-ready")

    def _request_render(self, state: dict[str, Any], *, reason: str) -> bool:
        state = copy.deepcopy(state)
        kind = str(
            state.get("update_kind")
            or getattr(self.backend, "_last_update_kind_by_scene", {}).get(
                str(state.get("scene_id") or ""), ""
            )
        )
        if kind in {
            UpdateKind.CAMERA.value,
            UpdateKind.OVERLAY.value,
            UpdateKind.DISPLAY.value,
        }:
            return False
        try:
            render_key = json.dumps(
                {
                    key: value
                    for key, value in state.items()
                    if key not in {"version", "server_started_at", "camera"}
                },
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            )
        except Exception:
            render_key = repr(sorted(state.items()))
        scene_id = str(state.get("scene_id") or "")
        with self._lock:
            if self._closed:
                return False
            previous_key = self._latest_render_by_scene.get(scene_id)
            if previous_key and previous_key != render_key:
                # A queued build for an older state is never useful once the
                # confirmed state advanced.  Running jobs are also guarded by
                # ``_is_current`` and will discard their result.
                previous_future = self._render_futures.get(previous_key)
                if previous_future is not None:
                    previous_future.cancel()
                self._pending_render.discard(previous_key)
                if previous_key in self._task_status:
                    self._task_status[previous_key]["status"] = "cancelled"
            self._latest_render_by_scene[scene_id] = render_key
            if render_key in self._pending_render:
                return True
            self._pending_render.add(render_key)
            self._task_status[render_key] = {
                "scene_id": scene_id,
                "status": "queued",
                "reason": reason,
            }

        def _job() -> None:
            with self._lock:
                if render_key in self._task_status:
                    self._task_status[render_key]["status"] = "running"
            if not self._is_current(state):
                with self._lock:
                    if render_key in self._task_status:
                        self._task_status[render_key]["status"] = "cancelled"
                return
            fig, topology_data = self.backend.figure_for_state(state, async_topology=False)
            with self._lock:
                if self._is_current(state):
                    if render_key in self._task_status:
                        self._task_status[render_key]["status"] = "completed"
                    self.backend.broadcast_figure(
                        scene_id=state.get("scene_id"),
                        figure=fig.to_plotly_json(),
                        topology_data=topology_data,
                        state=state,
                        reason=reason,
                    )

        def completed(future: Future) -> None:
            with self._lock:
                self._pending_render.discard(render_key)
                self._render_futures.pop(render_key, None)
            if not future.cancelled():
                exc = future.exception()
                if exc is not None:
                    with self._lock:
                        if render_key in self._task_status:
                            self._task_status[render_key].update(
                                status="failed", error=str(exc)
                            )
                    self._report_error(state, exc)
            else:
                with self._lock:
                    if render_key in self._task_status:
                        self._task_status[render_key]["status"] = "cancelled"

        try:
            future = self._finalize_pool.submit(_job)
            with self._lock:
                self._render_futures[render_key] = future
        except Exception as exc:
            with self._lock:
                self._pending_render.discard(render_key)
                self._render_futures.pop(render_key, None)
                if self._latest_render_by_scene.get(scene_id) == render_key:
                    self._latest_render_by_scene.pop(scene_id, None)
                self._task_status[render_key] = {
                    "scene_id": scene_id,
                    "status": "failed",
                    "reason": reason,
                    "error": str(exc),
                }
            self._report_error(state, exc)
            return False
        future.add_done_callback(completed)
        return True

    def shutdown(self, *, wait: bool = False) -> None:
        with self._lock:
            self._closed = True
            self._pending.clear()
            self._latest_topology_by_scene.clear()
            self._topology_futures.clear()
            self._pending_render.clear()
            self._latest_render_by_scene.clear()
            self._render_futures.clear()
        if wait:
            self._compute_pool.shutdown(wait=True, cancel_futures=True)
            self._finalize_pool.shutdown(wait=True, cancel_futures=True)
            return
        self._finalize_pool.shutdown(wait=False, cancel_futures=True)
        self._compute_pool.shutdown(wait=False, cancel_futures=True)
