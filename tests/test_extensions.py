"""Frontend-neutral extension ownership and detached snapshot contracts."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from mat_viewer.extensions import Extension, ExtensionContext, _ExtensionHost


class Probe(Extension):
    name = "example.probe"

    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


@pytest.mark.parametrize("name", ["", "chat", "Example.chat", "example..chat", "a/b"])
def test_names_must_be_namespaced(name):
    probe = Probe()
    probe.name = name
    with pytest.raises(ValueError, match="namespaced"):
        _ExtensionHost((probe,))


def test_duplicate_names_rejected_before_ownership():
    first, second = Probe(), Probe()
    with pytest.raises(ValueError, match="duplicate"):
        _ExtensionHost((first, second))
    assert first.closed == second.closed == 0


def test_cleanup_is_once_even_from_workers():
    probe = Probe()
    host = _ExtensionHost((probe,))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: host.close(), range(20)))
    assert probe.closed == 1


def test_cleanup_continues_after_failure():
    first, second = Probe(), Probe()
    second.name = "example.failing"

    def fail():
        raise RuntimeError("cleanup failed")

    second.close = fail
    host = _ExtensionHost((first, second))
    host.close()
    host.close()
    assert first.closed == 1


def test_snapshot_is_detached_and_worker_safe():
    viewer = object()
    context = ExtensionContext("tui", viewer)
    assert context.viewer is viewer
    assert context.snapshot()["view_revision"] is None
    context._publish(view_revision=3, selection={"atom_id": "C1"})
    with ThreadPoolExecutor(max_workers=1) as pool:
        snapshot = pool.submit(context.snapshot).result()
    snapshot["selection"]["atom_id"] = "modified"
    assert context.snapshot()["selection"] == {"atom_id": "C1"}


def test_base_hooks_are_noops():
    plugin = Extension()
    context = ExtensionContext("tui", object())
    assert plugin.build_web_panel(context) is None
    assert plugin.register_web(None, context) is None
    assert plugin.build_tui_panel(context) is None
    assert plugin.on_tui_mount(None, context) is None
    assert plugin.on_tui_unmount(None, context) is None
    assert plugin.close() is None