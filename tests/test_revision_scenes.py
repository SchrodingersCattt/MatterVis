"""Revision identity, atomic publication, failure and lifecycle contract."""
import stat
import time
from concurrent.futures import CancelledError
from dataclasses import FrozenInstanceError
from pathlib import Path
from threading import Event, RLock

import pytest

from mat_viewer.extensions import (
    RevisionId,
    RevisionScenes,
    RevisionUnavailable,
    SceneEvent,
    SceneHandle,
    StructureWriter,
)


class Host:
    def __init__(self):
        self.lock = RLock()
        self.sources = {}
        self.writer = StructureWriter(self)
        self.revisions = RevisionScenes(self.writer)

    def execute(self, action):
        with self.lock:
            return action()

    def prepare(self, path, name):
        if path.read_bytes() == b"invalid":
            raise ValueError("invalid source")
        return name

    def commit(self, source, handle, label):
        if handle is None:
            handle = SceneHandle(str(len(self.sources)), 1)
        elif self.handle(handle.scene_id) != handle:
            raise KeyError("stale")
        self.sources[handle.scene_id] = source
        return handle

    def handle(self, scene_id):
        if scene_id not in self.sources:
            raise KeyError(scene_id)
        return SceneHandle(scene_id, 1)

    def source_id(self, scene_id):
        return self.sources[scene_id]

    def focus(self, handle, target):
        assert self.handle(handle.scene_id) == handle
        self.writer.emit(SceneEvent("activated", handle, self.source_id(handle.scene_id)))


@pytest.fixture
def host():
    host = Host()
    yield host
    host.revisions.close()
    host.writer.close()


def revision(token, namespace="example.workflow"):
    return RevisionId(namespace, token)


def register(host, token):
    return host.revisions.register(revision(token), b"valid", format="cif").result(5)


def test_namespaced_opaque_identity_and_frozen_events(host):
    assert revision("A") != revision("a")
    assert revision("a") != revision("a", "other.workflow")
    for args in [("plain", "x"), ("example.workflow", ""), ("example.workflow", 3)]:
        with pytest.raises(ValueError):
            RevisionId(*args)
    events = []
    host.revisions.subscribe(events.append)
    binding = register(host, "a")
    with pytest.raises(FrozenInstanceError):
        binding.source_id = "changed"
    with pytest.raises(FrozenInstanceError):
        events[0].kind = "changed"
    assert events[0].bindings == (binding,)


def test_reload_drops_old_and_siblings_preserve_mapping(host):
    first, sibling = register(host, "first"), register(host, "sibling")
    events = []
    host.revisions.subscribe(events.append)
    replacement = host.revisions.reload(
        revision("replacement"), first.scene, b"new", format="cif"
    ).result(5)
    assert replacement.scene == first.scene
    assert replacement.source_id != first.source_id
    with pytest.raises(RevisionUnavailable):
        host.revisions.focus(first.revision)
    assert host.revisions.lookup(sibling.revision) == sibling
    assert [e.kind for e in events] == ["dropped", "bound"]
    assert events[0].reason == "reloaded"


def test_failed_writes_do_not_bind_or_destroy_previous(host):
    first = register(host, "first")
    with pytest.raises(ValueError):
        host.revisions.reload(revision("bad"), first.scene, b"invalid", format="cif").result(5)
    assert host.revisions.lookup(first.revision) == first
    with pytest.raises(RevisionUnavailable):
        host.revisions.lookup(revision("bad"))
    with pytest.raises(ValueError):
        host.revisions.register(revision("bad"), b"valid")
    assert register(host, "bad")


def test_bind_requires_current_receipt_and_external_reload_invalidates(host):
    receipts = []
    host.writer.subscribe(receipts.append)
    sid = host.writer.register(b"valid", format="cif").result(5)
    receipt = receipts[-1]
    binding = host.revisions.bind(revision("receipt"), receipt).result(5)
    assert binding.scene.scene_id == sid
    with pytest.raises(ValueError):
        host.revisions.bind(revision("bare"), sid)
    host.writer.reload(sid, b"new", format="cif").result(5)
    with pytest.raises(RevisionUnavailable):
        host.revisions.bind(revision("stale"), receipt).result(5)
    with pytest.raises(RevisionUnavailable):
        host.revisions.lookup(binding.revision)


def test_duplicate_pending_and_cancellation_do_not_record_mapping(host, monkeypatch):
    entered, release = Event(), Event()
    prepare = host.prepare

    def delayed(*args):
        entered.set()
        assert release.wait(5)
        return prepare(*args)

    monkeypatch.setattr(host, "prepare", delayed)
    first = host.revisions.register(revision("first"), b"valid", format="cif")
    assert entered.wait(5)
    queued = host.revisions.register(revision("queued"), b"valid", format="cif")
    with pytest.raises(ValueError, match="pending"):
        host.revisions.register(revision("queued"), b"valid", format="cif")
    assert queued.cancel()
    release.set()
    first.result(5)
    with pytest.raises(CancelledError):
        queued.result()
    with pytest.raises(RevisionUnavailable):
        host.revisions.lookup(revision("queued"))
    assert register(host, "queued")


def test_delayed_revision_focus_rejects_overwritten_content(host, monkeypatch):
    binding = register(host, "first")
    entered, release = Event(), Event()
    prepare = host.prepare

    def delayed(*args):
        entered.set()
        assert release.wait(5)
        return prepare(*args)

    monkeypatch.setattr(host, "prepare", delayed)
    write = host.writer.reload(binding.scene, b"new", format="cif")
    assert entered.wait(5)
    focus = host.revisions.focus(binding.revision)
    release.set()
    write.result(5)
    with pytest.raises(RevisionUnavailable):
        focus.result(5)


def test_hosts_are_independent_and_notifications_never_focus_back(host):
    other = Host()
    try:
        a, b = register(host, "same"), register(other, "same")
        events = []
        unsubscribe = host.revisions.subscribe(events.append)
        host.revisions.focus(a.revision).result(5)
        assert [e.kind for e in events] == ["activated"]
        assert events[0].bindings == (a,)
        host.revisions.close()
        assert other.revisions.lookup(b.revision) == b
        with pytest.raises(RevisionUnavailable):
            host.revisions.focus(a.revision)
        unsubscribe()
    finally:
        other.revisions.close()
        other.writer.close()


def test_shutdown_from_commit_listener_does_not_delete_committed_source(host):
    host.writer.subscribe(lambda event: host.revisions.close())
    pending = host.revisions.register(revision("closing"), b"valid", format="cif")
    with pytest.raises(RevisionUnavailable):
        pending.result(5)
    # The write committed, but shutdown prevented a binding. Its file still
    # belongs to the host; a post-commit failure cannot remove live content.
    assert len(host.sources) == 1
    assert len(list(Path(host.writer._storage.name).iterdir())) == 1
    with pytest.raises(RevisionUnavailable):
        host.revisions.lookup(revision("closing"))


def _reject_readonly_unlink(monkeypatch):
    """POSIX allows unlinking a read-only file. Windows does not."""
    real_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        if self.exists() and not (self.stat().st_mode & stat.S_IWRITE):
            raise PermissionError(13, "Access is denied")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)


def _wait_until(predicate, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def test_failed_candidate_is_removed_when_windows_rejects_readonly_unlink(
    host, monkeypatch
):
    _reject_readonly_unlink(monkeypatch)
    with pytest.raises(ValueError, match="invalid source"):
        host.writer.register(b"invalid", format="cif").result(5)
    storage = Path(host.writer._storage.name)
    assert list(storage.glob("*.cif")) == []


def test_cleanup_failure_does_not_mask_the_source_error(host, monkeypatch):
    def unlink(self, missing_ok=False):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(ValueError, match="invalid source"):
        host.writer.register(b"invalid", format="cif").result(5)


def test_shutdown_removes_readonly_candidates_when_windows_rejects_unlink(
    host, monkeypatch
):
    _reject_readonly_unlink(monkeypatch)
    host.writer.register(b"valid", format="cif", label="Kept").result(5)
    storage = Path(host.writer._storage.name)
    candidates = list(storage.glob("*.cif"))
    assert len(candidates) == 1
    assert not candidates[0].stat().st_mode & stat.S_IWRITE
    host.writer.close()

    def gone() -> bool:
        if not storage.exists():
            return True
        return list(storage.glob("*.cif")) == []

    assert _wait_until(gone)


def test_shutdown_still_removes_other_candidates_when_one_unlink_fails(
    host, monkeypatch
):
    host.writer.register(b"valid", format="cif", label="One").result(5)
    host.writer.register(b"valid", format="cif", label="Two").result(5)
    storage = Path(host.writer._storage.name)
    names = {path.name for path in storage.glob("*.cif")}
    assert len(names) == 2
    blocked = min(names)
    attempted: list[str] = []
    real_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        attempted.append(self.name)
        if self.name == blocked:
            raise PermissionError(13, "Access is denied")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    host.writer.close()
    assert _wait_until(lambda: set(attempted) >= names)
    assert set(attempted) >= names


def test_commit_failure_preserves_previous_binding(host, monkeypatch):
    binding = register(host, "before")

    def fail(*args):
        raise RuntimeError("commit rejected")

    monkeypatch.setattr(host, "commit", fail)
    with pytest.raises(RuntimeError, match="commit rejected"):
        host.revisions.reload(revision("after"), binding.scene, b"new", format="cif").result(5)
    assert host.revisions.lookup(binding.revision) == binding
    with pytest.raises(RevisionUnavailable):
        host.revisions.lookup(revision("after"))
