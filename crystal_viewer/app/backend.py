from __future__ import annotations

from .shared import ApiError, TopologyUnavailable
from .backend_camera import _CameraBackendMixin
from .backend_core import _CoreBackendMixin
from .backend_io import _IOBackendMixin
from .backend_overlays import _OverlaysBackendMixin
from .backend_topology import _TopologyBackendMixin


class ViewerBackend(
    _CoreBackendMixin,
    _OverlaysBackendMixin,
    _TopologyBackendMixin,
    _CameraBackendMixin,
    _IOBackendMixin,
):
    pass


__all__ = ["ApiError", "TopologyUnavailable", "ViewerBackend"]
