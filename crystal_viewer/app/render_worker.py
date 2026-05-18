from __future__ import annotations

from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
import copy
import json
import os
import threading
from typing import Any

from .backend_topology import compute_topology_geometry_payload


class AsyncRenderWorker:
    """Background topology/figure pipeline.

    Flask request threads only enqueue work. The expensive MolCrysKit
    topology pass runs in a process pool when the payload is picklable,
    and final Plotly JSON assembly runs in a daemon thread before the
    result is pushed to WebSocket subscribers.
    """

    def __init__(self, backend):
        self.backend = backend
        max_workers = max(1, min(4, os.cpu_count() or 1))
        self._process_pool = ProcessPoolExecutor(
            max_workers=max_workers,
            mp_context=None,
        )
        self._finalize_pool = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="mattervis-render-finalize",
        )
        self._lock = threading.Lock()
        self._pending: set[tuple[Any, ...]] = set()
        self._pending_render: set[str] = set()

    def request_topology(self, state: dict[str, Any], context: dict[str, Any]) -> bool:
        cache_key = context["cache_key"]
        with self._lock:
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
            future = self._process_pool.submit(compute_topology_geometry_payload, payload)
        except Exception:
            future = Future()
            self._finalize_pool.submit(self._compute_fallback, future, payload)
        future.add_done_callback(
            lambda fut: self._finalize_pool.submit(
                self._finish_topology,
                cache_key,
                context["structure"],
                copy.deepcopy(state),
                payload,
                fut,
            )
        )
        return True

    @staticmethod
    def _compute_fallback(future: Future, payload: dict[str, Any]) -> None:
        try:
            future.set_result(compute_topology_geometry_payload(payload))
        except Exception as exc:
            future.set_exception(exc)

    def _finish_topology(
        self,
        cache_key: tuple[Any, ...],
        structure: str,
        state: dict[str, Any],
        payload: dict[str, Any],
        future: Future,
    ) -> None:
        try:
            try:
                geometry = future.result()
            except Exception:
                # Pickling a third-party object can fail in some MCK /
                # pymatgen versions. Retain the invariant that request
                # threads never block by recomputing in this background
                # finalizer thread instead of falling back synchronously.
                geometry = compute_topology_geometry_payload(payload)
            if geometry is None:
                return
            self.backend._store_topology_geometry(structure, cache_key, geometry)
            fig, topology_data = self.backend.figure_for_state(state, async_topology=False)
            self.backend.broadcast_figure(
                scene_id=state.get("scene_id"),
                figure=fig.to_plotly_json(),
                topology_data=topology_data,
                state=state,
                reason="topology-ready",
            )
        except Exception as exc:
            try:
                self.backend.broadcast_render_error(
                    scene_id=state.get("scene_id"),
                    error=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                pass
        finally:
            with self._lock:
                self._pending.discard(cache_key)

    def prewarm(self, state: dict[str, Any]) -> None:
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
        with self._lock:
            if render_key in self._pending_render:
                return
            self._pending_render.add(render_key)

        def _job() -> None:
            try:
                fig, topology_data = self.backend.figure_for_state(state, async_topology=False)
                self.backend.broadcast_figure(
                    scene_id=state.get("scene_id"),
                    figure=fig.to_plotly_json(),
                    topology_data=topology_data,
                    state=state,
                    reason="prewarm-ready",
                )
            finally:
                with self._lock:
                    self._pending_render.discard(render_key)

        self._finalize_pool.submit(_job)

    def shutdown(self) -> None:
        self._finalize_pool.shutdown(wait=False, cancel_futures=True)
        self._process_pool.shutdown(wait=False, cancel_futures=True)
