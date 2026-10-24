"""Catalog evidence survives presentation lifecycle and failures."""
from dataclasses import FrozenInstanceError
from threading import Event

import pytest

from mat_viewer.extensions import StructureCatalog, RevisionUnavailable
from test_revision_scenes import Host, revision


@pytest.fixture
def host():
    value = Host()
    yield value
    value.revisions.close()
    value.writer.close()


def add(catalog, token, source=b"valid", **kwargs):
    return catalog.add(revision(token), source, format="cif", label=token, **kwargs)


def test_owned_snapshot_and_deeply_detached_evidence(host, tmp_path, monkeypatch):
    catalog = StructureCatalog(host.revisions)
    path = tmp_path / "original.cif"
    path.write_bytes(b"original")
    provenance = {"parameters": {"steps": [1, 2]}}
    checks = {"warnings": ["inspect"]}
    entry = catalog.add(revision("a"), path, label="Input", provenance=provenance, checks=checks)
    assert path.read_bytes() == b"original"
    path.write_bytes(b"edited")
    path.unlink()
    provenance["parameters"]["steps"].append(3)
    checks["warnings"].clear()
    assert entry.source == b"original"
    assert entry.provenance["parameters"]["steps"] == (1, 2)
    assert entry.checks["warnings"] == ("inspect",)
    with pytest.raises(TypeError):
        entry.provenance["parameters"]["new"] = 1
    with pytest.raises(FrozenInstanceError):
        entry.label = "changed"
    captured = []
    prepare = host.prepare

    def capture(path, name):
        captured.append(path.read_bytes())
        return prepare(path, name)

    monkeypatch.setattr(host, "prepare", capture)
    assert catalog.open(entry.id).result(5).revision == entry.id
    assert captured == [b"original"]


def test_parent_validation_and_order(host):
    catalog = StructureCatalog(host.revisions)
    with pytest.raises(ValueError, match="parent"):
        add(catalog, "child", parent=revision("missing"))
    with pytest.raises(ValueError, match="parent"):
        add(catalog, "self", parent=revision("self"))
    parent = add(catalog, "input")
    child = add(catalog, "child", parent=parent.id, operation="relax")
    with pytest.raises(ValueError, match="already exists"):
        add(catalog, "input")
    assert catalog.entries() == (parent, child)
    assert not host.sources  # evidence capture is not Scene creation


@pytest.mark.parametrize("kwargs, error", [
    ({"checks": {"bad": object()}}, TypeError),
    ({"checks": {"bad": float("nan")}}, TypeError),
    ({"provenance": {1: "bad"}}, TypeError),
    ({"operation": ""}, ValueError),
    ({"parent": "not-an-id"}, TypeError),
])
def test_invalid_metadata_is_atomic(host, kwargs, error):
    catalog = StructureCatalog(host.revisions)
    with pytest.raises(error):
        add(catalog, "bad", **kwargs)
    assert catalog.entries() == ()


def test_repeat_open_focus_reload_and_reopen(host):
    catalog = StructureCatalog(host.revisions)
    first = add(catalog, "a")
    second = add(catalog, "b", parent=first.id)
    a = catalog.open(first.id).result(5)
    assert catalog.open(first.id).result(5) == a
    catalog.focus(first.id).result(5)
    assert len(host.sources) == 1
    catalog.reload(second.id, a.scene).result(5)
    with pytest.raises(RevisionUnavailable):
        catalog.focus(first.id)
    reopened = catalog.open(first.id).result(5)
    assert reopened.scene != a.scene
    assert catalog.entries() == (first, second)
    with pytest.raises(ValueError, match="bound"):
        catalog.reload(first.id, reopened.scene)


def test_stale_reserved_id_reports_conflict_without_retry(host):
    catalog = StructureCatalog(host.revisions)
    entry = add(catalog, "a")
    binding = catalog.open(entry.id).result(5)
    host.sources[binding.scene.scene_id] = "external-drift"
    with pytest.raises(RevisionUnavailable, match="reserved or pending"):
        catalog.open(entry.id)
    assert len(host.sources) == 1
    assert catalog.entries() == (entry,)
    # Public reload emits the drop; only then can the historical ID be reopened.
    host.writer.reload(binding.scene, b"new", format="cif").result(5)
    assert catalog.open(entry.id).result(5).revision == entry.id


def test_failures_preserve_evidence_and_prior_scene(host, monkeypatch):
    catalog = StructureCatalog(host.revisions)
    good, bad = add(catalog, "good"), add(catalog, "bad", b"invalid")
    binding = catalog.open(good.id).result(5)
    with pytest.raises(ValueError, match="invalid"):
        catalog.reload(bad.id, binding.scene).result(5)
    with pytest.raises(ValueError, match="invalid"):
        catalog.open(bad.id).result(5)
    assert catalog.open(good.id).result(5) == binding
    assert catalog.entries() == (good, bad)
    host.revisions.close()
    with pytest.raises(RuntimeError, match="closed"):
        catalog.open(bad.id)
    assert catalog.get(good.id).source == b"valid"


def test_pending_open_is_not_a_second_registration(host, monkeypatch):
    catalog = StructureCatalog(host.revisions)
    entry = add(catalog, "a")
    entered, release = Event(), Event()
    prepare = host.prepare

    def delayed(*args):
        entered.set()
        assert release.wait(5)
        return prepare(*args)

    monkeypatch.setattr(host, "prepare", delayed)
    pending = catalog.open(entry.id)
    assert entered.wait(5)
    try:
        with pytest.raises(RevisionUnavailable, match="pending"):
            catalog.open(entry.id)
    finally:
        release.set()
    binding = pending.result(5)
    assert catalog.open(entry.id).result(5) == binding
    assert len(host.sources) == 1


def test_hosts_isolated_and_unused_host_unaffected(host):
    other = Host()
    try:
        assert host.writer._storage is None
        assert other.writer._storage is None
        a, b = StructureCatalog(host.revisions), StructureCatalog(other.revisions)
        first, second = add(a, "same"), add(b, "same", b"different")
        assert first.id == second.id
        a.open(first.id).result(5)
        assert other.writer._storage is None
        assert b.entries() == (second,)
        host.revisions.close()
        assert b.open(second.id).result(5)
        assert a.entries() == (first,)
    finally:
        other.revisions.close()
        other.writer.close()


def test_commit_failure_keeps_catalog_and_live_binding(host, monkeypatch):
    catalog = StructureCatalog(host.revisions)
    first, second = add(catalog, "a"), add(catalog, "b")
    binding = catalog.open(first.id).result(5)

    def fail(*args):
        raise RuntimeError("commit rejected")

    monkeypatch.setattr(host, "commit", fail)
    with pytest.raises(RuntimeError, match="commit rejected"):
        catalog.reload(second.id, binding.scene).result(5)
    assert catalog.open(first.id).result(5) == binding
    assert catalog.entries() == (first, second)
