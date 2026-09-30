"""Extension lifecycle in the real Textual viewer (no second renderer)."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from textual.widgets import Input, Static

from mat_viewer.extensions import Extension
from mat_viewer.tui.app import CrystalTUI


class TuiProbe(Extension):
    name = "example.tui"

    def __init__(self):
        self.events = []
        self.context = None

    def build_tui_panel(self, context):
        self.context = context
        self.events.append("build")
        return Input(id="example-tui-input")

    def on_tui_mount(self, app, context):
        assert context.viewer is app
        assert context is self.context
        assert app.query_one("#example-tui-input", Input)
        self.events.append("mount")

    def on_tui_unmount(self, app, context):
        self.events.append("unmount")

    def close(self):
        self.events.append("close")


@pytest.fixture
def crystal(tui_crystal_factory):
    return tui_crystal_factory(Path(__file__).parent / "fixtures" / "dirty_geometry.vasp")


def test_no_extension_keeps_native_body(crystal):
    async def run():
        app = CrystalTUI(crystal)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert [child.id for child in app.query_one("#body").children] == [
                "canvas", "inspector"
            ]
            assert app.query_one("#canvas", Static)
    asyncio.run(run())


def test_tui_lifecycle_snapshot_and_input(crystal):
    plugin = TuiProbe()

    async def run():
        app = CrystalTUI(crystal, extensions=(plugin,))
        async with app.run_test(size=(140, 35)) as pilot:
            await pilot.pause()
            assert plugin.events == ["build", "mount"]
            assert plugin.context.viewer.crystal is crystal
            with ThreadPoolExecutor(max_workers=1) as pool:
                snapshot = pool.submit(plugin.context.snapshot).result()
            assert snapshot["source_path"] == crystal.source_path
            assert snapshot["view_revision"] == app.controller.state.revision
            assert snapshot["selection"] == app.controller.state.selection.as_dict()
            app.query_one("#example-tui-input", Input).focus()
            await pilot.press("j", "x")
            assert app.query_one("#example-tui-input", Input).value == "jx"
        app.close_extensions()
        assert plugin.events == ["build", "mount", "unmount", "close"]
    asyncio.run(run())


def test_duplicate_tui_names_rejected(crystal):
    with pytest.raises(ValueError, match="duplicate"):
        CrystalTUI(crystal, extensions=(TuiProbe(), TuiProbe()))


def test_build_failure_closes_resources(crystal):
    class Broken(TuiProbe):
        def build_tui_panel(self, context):
            raise RuntimeError("build failed")

    plugin = Broken()
    app = CrystalTUI(crystal, extensions=(plugin,))
    with pytest.raises(RuntimeError, match="build failed"):
        list(app.compose())
    app.close_extensions()
    assert plugin.events == ["close"]