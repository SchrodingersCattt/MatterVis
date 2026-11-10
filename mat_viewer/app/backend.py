from __future__ import annotations

import atexit
import threading

from ..scenes.core import SceneStorePersistError
from .render_worker import shutdown_executor
from .shared import ApiError, TopologyUnavailable
from .backend_analysis import _AnalysisBackendMixin
from .backend_camera import _CameraBackendMixin
from .backend_core import _CoreBackendMixin
from .backend_state import _StateBackendMixin
from .backend_reducer import _ReducerBackendMixin
from .backend_scenes import _SceneBackendMixin
from .backend_io import _IOBackendMixin
from .backend_journal import _JournalBackendMixin
from .backend_display import _DisplayBackendMixin
from .backend_operate import _OperateBackendMixin
from .backend_overlays import _OverlaysBackendMixin
from .backend_selection import _SelectionBackendMixin
from .backend_topology import _TopologyBackendMixin
from .backend_vectors import _VectorBackendMixin


_PERSIST_THREAD_JOIN_TIMEOUT_SECONDS = 2.0


class ViewerBackend(
    _VectorBackendMixin,
    _SceneBackendMixin,
    _JournalBackendMixin,
    _StateBackendMixin,
    _CoreBackendMixin,
    _ReducerBackendMixin,
    _DisplayBackendMixin,
    _OperateBackendMixin,
    _OverlaysBackendMixin,
    _SelectionBackendMixin,
    _AnalysisBackendMixin,
    _TopologyBackendMixin,
    _CameraBackendMixin,
    _IOBackendMixin,
):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self, *, wait: bool = True) -> None:
        """Release background workers and flush pending scene state once.

        Render pools are stopped before the final flush, and each pool join
        is bounded. ``workers_quiescent`` is False when a worker was still
        running after that bound; ``close`` returning does not mean those
        workers have stopped. The flush and ``atexit`` unregister still run
        when shutdown raises. A final flush that leaves the store dirty
        raises ``SceneStorePersistError`` instead of scheduling another
        attempt that may not finish before the process exits.
        """
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self.workers_quiescent = False
        shutdown_error: BaseException | None = None
        try:
            self._persist_stop.set()
            self._persist_event.set()
            try:
                extension_host = getattr(self, "_extension_structures", None)
                if extension_host is not None:
                    extension_host.writer.close()
                self.workers_quiescent = bool(self._render_worker.shutdown(wait=wait))
                load_executor = getattr(self, "_load_executor", None)
                if load_executor is not None:
                    load_done = shutdown_executor(load_executor, wait=wait)
                    self.workers_quiescent = self.workers_quiescent and load_done
            finally:
                self._join_persist_thread()
        except Exception as exc:  # noqa: BLE001 - flush still runs, then this is re-raised
            shutdown_error = exc
        finally:
            try:
                self.flush_scene_store()
            finally:
                atexit.unregister(self._atexit_flush_callback)
        store = getattr(self, "scene_store", None)
        if store is not None and store.is_dirty():
            persist_error = SceneStorePersistError(
                "scene store flush did not persist the current generation"
            )
            if shutdown_error is not None:
                raise persist_error from shutdown_error
            raise persist_error
        if shutdown_error is not None:
            raise shutdown_error

    def _join_persist_thread(self) -> None:
        if threading.current_thread() is self._persist_thread:
            return
        self._persist_thread.join(timeout=_PERSIST_THREAD_JOIN_TIMEOUT_SECONDS)


__all__ = ["ApiError", "TopologyUnavailable", "ViewerBackend"]
