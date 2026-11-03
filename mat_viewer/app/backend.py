from __future__ import annotations

import atexit
import threading

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
        is bounded. The flush and ``atexit`` unregister still run when
        shutdown raises, so a failed close does not leave a second flush
        registered or drop a scene mutation already marked dirty.
        """
        with self._close_lock:
            if self._closed:
                return
            self._closed = True

        try:
            self._persist_stop.set()
            self._persist_event.set()
            try:
                extension_host = getattr(self, "_extension_structures", None)
                if extension_host is not None:
                    extension_host.writer.close()
                self._render_worker.shutdown(wait=wait)
                load_executor = getattr(self, "_load_executor", None)
                if load_executor is not None:
                    shutdown_executor(load_executor, wait=wait)
            finally:
                self._join_persist_thread()
        finally:
            try:
                self.flush_scene_store()
                self._retry_dirty_scene_flush()
            finally:
                atexit.unregister(self._atexit_flush_callback)

    def _retry_dirty_scene_flush(self) -> None:
        """One more flush when the bounded wait could not take the backend lock.

        The thread is a daemon. It does not keep ``close`` running, and it
        does not clear a generation it failed to write.
        """
        store = getattr(self, "scene_store", None)
        if store is None or not store.is_dirty():
            return
        threading.Thread(
            target=self.flush_scene_store,
            name="mattervis-scene-store-flush",
            daemon=True,
        ).start()

    def _join_persist_thread(self) -> None:
        if threading.current_thread() is self._persist_thread:
            return
        self._persist_thread.join(timeout=_PERSIST_THREAD_JOIN_TIMEOUT_SECONDS)


__all__ = ["ApiError", "TopologyUnavailable", "ViewerBackend"]
