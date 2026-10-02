"""Multi-tenant service primitives for the hosted MatterVis surface.

The local Dash viewer remains backed by :class:`ViewerBackend`.  This package
contains the persistent, tenant-scoped services used by the stable ``/api``
surface.  Optional infrastructure adapters are imported lazily so the
browser-independent renderer stays usable without SaaS dependencies.
"""

from .config import SaaSConfig
from .auth import AuthContext, AuthError, ApiKeyManager
from .repository import SaaSRepository
from .storage import LocalObjectStore
from .service import SaaSService

__all__ = [
    "ApiKeyManager",
    "AuthContext",
    "AuthError",
    "LocalObjectStore",
    "SaaSConfig",
    "SaaSRepository",
    "SaaSService",
]
