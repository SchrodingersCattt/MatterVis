"""Versioned native-host writes, with owned CIF input and asynchronous completion.

No frontend imports. Futures complete after host commit (focus on Web means
queued for the named browser). Do not block a UI callback on a future.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from typing import Literal, Protocol
from uuid import uuid4

Source = str | Path | bytes


@dataclass(frozen=True)
class SceneHandle:
    scene_id: str
    generation: int


@dataclass(frozen=True)
class SceneEvent:
    kind: Literal["registered", "reloaded", "focused", "closed", "activated"]
    scene: SceneHandle
    source_id: str | None = None
    schema: str = "mattervis.scene-event/v1"
    target: str | None = None


class StructureHost(Protocol):
    def handle(self, scene_id: str) -> SceneHandle: ...
    def prepare(self, path: Path, name: str): ...
    def commit(
        self, prepared, handle: SceneHandle | None, label: str | None
    ) -> SceneHandle: ...
    def focus(self, handle: SceneHandle, target: str | None) -> None: ...
    def execute(self, action: Callable): ...


class StructureWriter:
    """CIF-only v1. Path suffix or explicit format must be ``cif``.

    A path is copied at submission; later caller edits cannot affect this write.
    Successful sources are retained until host shutdown (including close/undo).
    Failed candidates are removed immediately. Register does not imply focus.
    ``SceneHandle`` pins a generation; strings resolve the live generation when
    submitted. Use handles when retaining references across close/undo.
    """

    schema = "mattervis.structures/v1"

    def __init__(self, host: StructureHost):
        self.host = host
        self._lock = RLock()
        self._closed = False
        self._storage = None
        self._pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mv-structures"
        )
        self._listeners: list[Callable[[SceneEvent], None]] = []

    def subscribe(self, listener: Callable[[SceneEvent], None]) -> Callable[[], None]:
        """Observe commits/close; callbacks must be short and never block on writes."""
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe():
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def emit(self, event: SceneEvent) -> None:
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                logging.getLogger(__name__).exception("Scene event listener failed")

    def handle(self, scene_id: str) -> SceneHandle:
        return self.host.handle(scene_id)

    def _handle(self, scene: str | SceneHandle) -> SceneHandle:
        if isinstance(scene, SceneHandle):
            return scene
        if not isinstance(scene, str) or not scene:
            raise ValueError("scene_id must be nonempty text or a SceneHandle")
        return self.handle(scene)

    def register(
        self, source: Source, *, format: str | None = None, label: str | None = None
    ) -> Future[str]:
        if label is None:
            label = (
                Path(source).stem if isinstance(source, (str, Path)) else "Structure"
            )
        return self._write(None, source, format, label)

    def reload(
        self, scene_id: str | SceneHandle, source: Source, *, format: str | None = None
    ) -> Future[str]:
        return self._write(self._handle(scene_id), source, format, None)

    def _write(self, handle, source, format, label, *, committed=None):
        if label is not None and (not isinstance(label, str) or not label.strip()):
            raise ValueError("label must be nonempty text")
        if not isinstance(source, (str, Path, bytes)):
            raise TypeError("source must be a path or bytes")
        inferred = (
            Path(source).suffix.lstrip(".").lower()
            if not isinstance(source, bytes)
            else None
        )
        if (format or inferred) != "cif":
            raise ValueError('v1 supports CIF only; bytes require format="cif"')
        data = source if isinstance(source, bytes) else Path(source).read_bytes()
        if not data:
            raise ValueError("source is empty")
        with self._lock:
            if self._closed:
                raise RuntimeError("structure host is closed")
            if self._storage is None:
                self._storage = TemporaryDirectory(prefix="mattervis-extension-")
            name = "extension_" + uuid4().hex
            path = Path(self._storage.name) / (name + ".cif")
            path.write_bytes(data)
            path.chmod(0o400)

            def work():
                published = False
                try:
                    prepared = self.host.prepare(path, name)

                    def commit():
                        nonlocal published
                        with self._lock:
                            if self._closed:
                                raise RuntimeError("structure host is closed")
                            result = self.host.commit(prepared, handle, label)
                            published = True
                            event = SceneEvent(
                                "reloaded" if handle else "registered", result, name
                            )
                            self.emit(event)
                            if committed is not None:
                                return committed(event)
                            return result.scene_id

                    return self.host.execute(commit)
                except BaseException:
                    # A post-commit binding failure must not delete a source
                    # already owned by a live scene (or its native Undo entry).
                    if not published:
                        path.unlink(missing_ok=True)
                    raise

            future = self._pool.submit(work)

            # A queued Future can be cancelled before its worker starts, in
            # which case ``work`` never gets a chance to remove its owned
            # candidate.  Treat cancellation like any other failed
            # candidate and eagerly remove the temporary source.  Running
            # futures cannot be cancelled, so successful writes remain
            # retained until host shutdown as documented.
            def cleanup_cancelled(done: Future[str]) -> None:
                if done.cancelled():
                    path.unlink(missing_ok=True)

            future.add_done_callback(cleanup_cancelled)
            return future

    def focus(
        self, scene_id: str | SceneHandle, *, target: str | None = None
    ) -> Future[None]:
        return self._focus(self._handle(scene_id), target=target)

    def _focus(self, handle, *, target=None, guard=None):
        with self._lock:
            if self._closed:
                raise RuntimeError("structure host is closed")

            def work():
                def commit():
                    with self._lock:
                        if self._closed:
                            raise RuntimeError("structure host is closed")
                        if guard is not None:
                            guard()
                        self.host.focus(handle, target)
                        if guard is not None and hasattr(self.host, "guard_focus"):
                            self.host.guard_focus(target, guard)
                        self.emit(SceneEvent("focused", handle, target=target))

                return self.host.execute(commit)

            return self._pool.submit(work)

    select = focus

    def close(self) -> None:
        # Never join on Textual's event thread: a worker may be awaiting it.
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._pool.shutdown(wait=False)

        # Queue cleanup after in-flight work; pending commits reject _closed.
        def cleanup():
            self._pool.shutdown(wait=True)
            if self._storage is not None:
                self._storage.cleanup()

        from threading import Thread

        Thread(target=cleanup, daemon=True, name="mv-source-cleanup").start()
