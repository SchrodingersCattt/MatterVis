"""Opt-in workflow evidence, independent of native Scene lifetime.

A catalog owns immutable CIF bytes in memory. It never writes caller files,
infers revisions from scenes, or persists history. Use external storage instead
when a workflow already owns durable revisions; RevisionScenes works directly.
"""
from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import Future
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from threading import RLock
from types import MappingProxyType

from .revisions import RevisionId, RevisionScenes, RevisionUnavailable


def _freeze(value):
    """Detach JSON-shaped evidence without retaining mutable caller objects."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and isfinite(value):
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("evidence keys must be strings")
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    raise TypeError("evidence must contain finite JSON-shaped values")


@dataclass(frozen=True)
class CatalogEntry:
    """Immutable evidence. Parent membership is validated by StructureCatalog."""

    id: RevisionId
    label: str
    source: bytes
    parent: RevisionId | None = None
    operation: str | None = None
    provenance: Mapping | None = None
    checks: Mapping | None = None

    def __post_init__(self):
        if not isinstance(self.id, RevisionId):
            raise TypeError("id must be a RevisionId")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("label must be nonempty text")
        if not isinstance(self.source, bytes) or not self.source:
            raise ValueError("source must be nonempty immutable CIF bytes")
        if self.parent is not None and not isinstance(self.parent, RevisionId):
            raise TypeError("parent must be a RevisionId")
        if self.operation is not None and (
            not isinstance(self.operation, str) or not self.operation.strip()
        ):
            raise ValueError("operation must be nonempty text or None")
        for name in ("provenance", "checks"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, Mapping):
                raise TypeError(f"{name} must be a mapping")
            object.__setattr__(self, name, _freeze(value if value is not None else {}))


class StructureCatalog:
    """Explicitly constructed, workflow-owned catalog for one revision host.

    Reserve these IDs for this workflow; do not bind them to unrelated sources
    through another caller. Methods never wait on host futures. Validation raises
    synchronously; parsing/commit/focus errors also propagate through futures.
    There is deliberately no delete, implicit persistence, UI, or worker here.
    """

    def __init__(self, revisions: RevisionScenes):
        if not isinstance(revisions, RevisionScenes):
            raise TypeError("revisions must be a RevisionScenes host")
        self._revisions = revisions
        self._entries = {}
        self._lock = RLock()

    def add(self, revision, source, *, label, format=None, parent=None,
            operation=None, provenance=None, checks=None) -> CatalogEntry:
        """Capture bytes now; adding evidence neither parses nor opens a Scene."""
        if not isinstance(source, (str, Path, bytes)):
            raise TypeError("source must be a path or bytes")
        inferred = None if isinstance(source, bytes) else Path(source).suffix[1:].lower()
        if (format or inferred) != "cif":
            raise ValueError('only CIF is supported; bytes require format="cif"')
        data = source if isinstance(source, bytes) else Path(source).read_bytes()
        entry = CatalogEntry(revision, label, data, parent, operation, provenance, checks)
        with self._lock:
            if revision in self._entries:
                raise ValueError("catalog revision already exists")
            if parent is not None and parent not in self._entries:
                raise ValueError("parent must already exist in this catalog")
            self._entries[revision] = entry
        return entry

    def entries(self) -> tuple[CatalogEntry, ...]:
        """Insertion-ordered immutable snapshot, including closed revisions."""
        with self._lock:
            return tuple(self._entries.values())

    def get(self, revision) -> CatalogEntry:
        """Return evidence, or raise KeyError for an unknown catalog ID."""
        with self._lock:
            return self._entries[revision]

    def open(self, revision):
        """Return a live binding or register the snapshot; does not change focus.

        An unavailable lookup is not proof an ID is free. The public register
        reservation is authoritative: stale/reserved or pending IDs raise a
        clear error, with no retry and no new scientific ID.
        """
        entry = self.get(revision)
        try:
            binding = self._revisions.lookup(revision)
        except RevisionUnavailable:
            try:
                return self._revisions.register(
                    revision, entry.source, format="cif", label=entry.label
                )
            except ValueError as exc:
                raise RevisionUnavailable(
                    "revision is reserved or pending; await pending work, or close/"
                    "reload the stale Scene through the public writer before reopening"
                ) from exc
        result = Future()
        result.set_result(binding)
        return result

    def focus(self, revision, *, target=None):
        """Focus an already open revision; Web requires its page routing token."""
        self.get(revision)
        return self._revisions.focus(revision, target=target)

    def reload(self, revision, scene):
        """Load an unbound entry into a Scene; old display bindings are dropped.

        Already bound/pending IDs are rejected by RevisionScenes. For an open
        entry use focus; reload is for replacing a view with another snapshot.
        """
        entry = self.get(revision)
        return self._revisions.reload(revision, scene, entry.source, format="cif")
