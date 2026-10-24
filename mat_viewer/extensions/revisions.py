"""Host-local presentation bindings, not a scientific revision store.

All writes use StructureWriter's owned-input/commit path. A binding pins both a
scene generation and a source identity: reloading a scene is destructive to its
old bindings. Callbacks are observations, never instructions to focus again.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import Future
from dataclasses import dataclass
from threading import RLock

from .structures import SceneEvent, SceneHandle, Source


@dataclass(frozen=True)
class RevisionId:
    """Caller-owned namespace plus opaque, case-sensitive revision token."""

    namespace: str
    token: str

    def __post_init__(self):
        if not isinstance(self.namespace, str) or not re.fullmatch(
            r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+", self.namespace
        ):
            raise ValueError("revision namespace must be namespaced, e.g. example.workflow")
        if not isinstance(self.token, str) or not self.token.strip():
            raise ValueError("revision token must be nonempty opaque text")


@dataclass(frozen=True)
class RevisionBinding:
    revision: RevisionId
    scene: SceneHandle
    source_id: str


@dataclass(frozen=True)
class RevisionEvent:
    kind: str
    bindings: tuple[RevisionBinding, ...]
    scene: SceneHandle | None = None
    target: str | None = None
    reason: str | None = None
    schema: str = "mattervis.revision-event/v1"


class RevisionUnavailable(KeyError):
    """No live binding; callers may explicitly load a historical source again."""


class RevisionScenes:
    """One instance per extension host, exposed as ``context.revisions``.

    Register opens a sibling. Reload keeps the scene id and drops prior bindings.
    Futures resolve to frozen bindings only after successful host publication.
    ``bind`` accepts the successful registered/reloaded SceneEvent as a receipt;
    a bare scene id is deliberately insufficient proof of source identity.
    """

    schema = "mattervis.revisions/v1"

    def __init__(self, writer):
        self._writer = writer
        self._lock = RLock()
        self._bindings = {}
        self._pending = set()
        self._active = {}
        self._listeners = []
        self._closed = False
        self._unsubscribe = writer.subscribe(self._changed)

    def subscribe(self, listener):
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe():
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)
        return unsubscribe

    def _emit(self, event):
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                logging.getLogger(__name__).exception("Revision event listener failed")

    def _reserve(self, revision):
        if not isinstance(revision, RevisionId):
            raise TypeError("revision must be a RevisionId")
        with self._lock:
            if self._closed:
                raise RuntimeError("revision host is closed")
            if revision in self._pending or revision in self._bindings:
                raise ValueError("revision already bound or pending")
            self._pending.add(revision)

    def _submit(self, revision, submit):
        self._reserve(revision)
        try:
            future = submit()
        except BaseException:
            self._release(revision)
            raise
        future.add_done_callback(lambda _: self._release(revision))
        return future

    def _release(self, revision):
        with self._lock:
            self._pending.discard(revision)

    def _check(self, binding):
        host = self._writer.host
        if self._closed or self._writer._closed:
            raise RevisionUnavailable("revision host is closed")
        try:
            valid = (
                host.handle(binding.scene.scene_id) == binding.scene
                and host.source_id(binding.scene.scene_id) == binding.source_id
            )
        except KeyError:
            valid = False
        if not valid:
            raise RevisionUnavailable("revision mapping was dropped or its scene was replaced")
        return binding

    def _bind(self, revision, event):
        binding = RevisionBinding(revision, event.scene, event.source_id)
        self._check(binding)
        with self._lock:
            if self._closed:
                raise RevisionUnavailable("revision host is closed")
            self._bindings[revision] = binding
        self._emit(RevisionEvent("bound", (binding,), binding.scene))
        with self._lock:
            targets = tuple(t for t, scene in self._active.items() if scene == binding.scene)
        for target in targets:
            self._changed(SceneEvent("activated", binding.scene, binding.source_id, target=target))
        return binding

    def register(
        self, revision: RevisionId, source: Source, *, format=None, label=None
    ) -> Future[RevisionBinding]:
        return self._submit(revision, lambda: self._writer._write(
            None, source, format,
            label if label is not None else "Structure",
            committed=lambda event: self._bind(revision, event),
        ))

    def reload(
        self, revision: RevisionId, scene: str | SceneHandle, source: Source, *, format=None
    ) -> Future[RevisionBinding]:
        handle = self._writer._handle(scene)
        return self._submit(revision, lambda: self._writer._write(
            handle, source, format, None,
            committed=lambda event: self._bind(revision, event),
        ))

    def bind(self, revision: RevisionId, receipt: SceneEvent) -> Future[RevisionBinding]:
        if not isinstance(receipt, SceneEvent) or receipt.kind not in (
            "registered", "reloaded"
        ) or not receipt.source_id:
            raise ValueError("bind requires a successful write SceneEvent receipt")
        return self._submit(revision, lambda: self._writer._pool.submit(
            lambda: self._writer.host.execute(lambda: self._bind(revision, receipt))
        ))

    def lookup(self, revision) -> RevisionBinding:
        with self._lock:
            binding = self._bindings.get(revision)
        if binding is None:
            raise RevisionUnavailable("revision mapping is absent or was dropped")
        return self._check(binding)

    def focus(self, revision: RevisionId, *, target=None) -> Future[None]:
        binding = self.lookup(revision)

        def guard():
            if self.lookup(revision) != binding:
                raise RevisionUnavailable("revision binding changed before focus")
        return self._writer._focus(binding.scene, target=target, guard=guard)

    select = focus

    def _changed(self, event):
        with self._lock:
            matches = tuple(b for b in self._bindings.values() if b.scene == event.scene)
            if event.kind in ("closed", "reloaded"):
                for binding in matches:
                    del self._bindings[binding.revision]
        if event.kind in ("closed", "reloaded") and matches:
            self._emit(RevisionEvent("dropped", matches, event.scene, reason=event.kind))
        elif event.kind == "activated":
            with self._lock:
                self._active[event.target] = event.scene
            # Empty bindings explicitly clear a workflow's previous highlight.
            live = tuple(b for b in matches if b.source_id == event.source_id)
            self._emit(RevisionEvent("activated", live, event.scene, event.target))

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            bindings = tuple(self._bindings.values())
            self._bindings.clear()
            self._active.clear()
        self._unsubscribe()
        self._emit(RevisionEvent("dropped", bindings, reason="shutdown"))
        with self._lock:
            self._listeners.clear()
