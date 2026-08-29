from __future__ import annotations

import atexit
import threading

from .shared import ApiError, TopologyUnavailable
from .backend_analysis import _AnalysisBackendMixin
from .backend_camera import _CameraBackendMixin
from .backend_core import _CoreBackendMixin
from .backend_scenes import _SceneBackendMixin
from .backend_io import _IOBackendMixin
from .backend_operations import _OperationsBackendMixin
from .backend_overlays import _OverlaysBackendMixin
from .backend_selection import _SelectionBackendMixin
from .backend_topology import _TopologyBackendMixin
from .backend_vectors import _VectorBackendMixin


_PERSIST_THREAD_JOIN_TIMEOUT_SECONDS = 2.0


class ViewerBackend(
    _VectorBackendMixin,
    _SceneBackendMixin,
    _CoreBackendMixin,
    _OperationsBackendMixin,
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
        """Release background workers and flush pending scene state once."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True

        self._persist_stop.set()
        self._persist_event.set()
        if threading.current_thread() is not self._persist_thread:
            self._persist_thread.join(timeout=_PERSIST_THREAD_JOIN_TIMEOUT_SECONDS)
        self._render_worker.shutdown(wait=wait)
        self.flush_scene_store()
        atexit.unregister(self._atexit_flush_callback)


__all__ = ["ApiError", "TopologyUnavailable", "ViewerBackend"]
