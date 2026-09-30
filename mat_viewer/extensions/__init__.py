"""Startup-only, in-process frontend extensions (no frontend imports).

UI hooks run on the host frontend thread. Workers may read ``snapshot()``;
they must not access UI methods or live source objects through ``viewer``.
"""

from copy import deepcopy
import logging
import re
from threading import RLock


class Extension:
    """Subclass with a unique namespaced ID, e.g. ``example.chat``."""

    name: str = ""

    def build_web_panel(self, context):
        return None

    def register_web(self, app, context) -> None:
        pass

    def build_tui_panel(self, context):
        return None

    def on_tui_mount(self, app, context) -> None:
        pass

    def on_tui_unmount(self, app, context) -> None:
        pass

    def close(self) -> None:
        pass


class ExtensionContext:
    """Detached metadata snapshots over the existing frontend-owned viewer."""

    def __init__(self, frontend, viewer, *, snapshot_reader=None):
        if frontend not in ("web", "tui"):
            raise ValueError("frontend must be 'web' or 'tui'")
        self.frontend = frontend
        self.viewer = viewer
        self._reader = snapshot_reader
        self._lock = RLock()
        self._snapshot = {
            "frontend": frontend, "structure_id": None, "source_path": None,
            "scene_id": None, "view_revision": None, "selection": None,
        }

    def snapshot(self) -> dict:
        """Return detached metadata; safe to call from a worker thread."""
        with self._lock:
            data = self._reader() if self._reader is not None else self._snapshot
            return deepcopy(data)

    def _publish(self, **metadata) -> None:
        with self._lock:
            self._snapshot.update(deepcopy(metadata))


class _ExtensionHost:
    """Per-app ownership and idempotent best-effort cleanup of all plugins."""

    def __init__(self, extensions):
        self.extensions = tuple(extensions)
        names = set()
        for extension in self.extensions:
            name = extension.name
            if not isinstance(name, str) or not re.fullmatch(
                r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+", name
            ):
                raise ValueError("extension name must be a namespaced ID, e.g. example.chat")
            if name in names:
                raise ValueError(f"duplicate extension name: {name}")
            names.add(name)
        self.context = None
        self._closed = False
        self._lock = RLock()

    def close(self) -> None:
        """Close once, in reverse order; one failure cannot skip other plugins."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        for extension in reversed(self.extensions):
            try:
                extension.close()
            except Exception:
                logging.getLogger(__name__).exception(
                    "Extension cleanup failed: %s", extension.name
                )