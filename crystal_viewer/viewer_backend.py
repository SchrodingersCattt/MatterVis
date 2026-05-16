from __future__ import annotations

from .app_shared import ApiError, TopologyUnavailable
from .viewer_backend_camera import _CameraBackendMixin
from .viewer_backend_core import _CoreBackendMixin
from .viewer_backend_io import _IOBackendMixin
from .viewer_backend_overlays import _OverlaysBackendMixin
from .viewer_backend_topology import _TopologyBackendMixin


class ViewerBackend(
    _CoreBackendMixin,
    _OverlaysBackendMixin,
    _TopologyBackendMixin,
    _CameraBackendMixin,
    _IOBackendMixin,
):
    pass


__all__ = ["ApiError", "TopologyUnavailable", "ViewerBackend"]
