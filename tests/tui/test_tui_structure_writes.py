"""Real Textual event-loop execution and stable native controller ownership."""

import asyncio
from pathlib import Path
from threading import Event, get_ident

import pytest
from textual.widgets import Select

from mat_viewer.tui.app import CrystalTUI

CIF = Path(__file__).parents[1] / "fixtures" / "cod_1000043.cif"


def test_native_scenes_register_focus_reload_and_close(
    tui_crystal_factory, monkeypatch
):
    async def run():
        app = CrystalTUI(tui_crystal_factory(CIF))
        async with app.run_test() as pilot:
            await pilot.pause()
            api = app.extension_context.structures
            with pytest.raises(ValueError, match="bytes require"):
                api.register(b"invalid")
            with pytest.raises(Exception):
                await asyncio.wrap_future(api.register(b"invalid CIF", format="cif"))
            initial = app.scenes.active_id
            initial_controller = app.controller
            threads = []
            commit = app.scenes.commit

            def tracked(*args):
                threads.append(get_ident())
                return commit(*args)

            monkeypatch.setattr(app.scenes, "commit", tracked)
            scene_id = await asyncio.wrap_future(api.register(CIF, label="New"))
            assert scene_id != initial
            assert app.controller is initial_controller
            assert app.query_one("#native-scenes", Select).display
            await asyncio.wrap_future(api.focus(scene_id))
            assert app.controller is app.scenes.controllers[scene_id]
            assert app.crystal.title == "New"
            assert app.extension_context.snapshot()["scene_id"] == scene_id
            app.controller.select_atom(0)
            await asyncio.wrap_future(
                api.reload(scene_id, CIF.read_bytes(), format="cif")
            )
            assert app.scenes.active_id == scene_id
            assert app.controller.state.selection.atom_id is None
            assert app.scenes.controllers[initial] is initial_controller
            assert threads == [get_ident(), get_ident()]
            app.scenes.close_scene(scene_id)
            assert app.controller is initial_controller
            with pytest.raises(KeyError):
                api.focus(scene_id)

    asyncio.run(run())


def test_close_during_worker_parse_rejects_commit(tui_crystal_factory, monkeypatch):
    async def run():
        app = CrystalTUI(tui_crystal_factory(CIF))
        async with app.run_test() as pilot:
            await pilot.pause()
            api = app.extension_context.structures
            scene_id = await asyncio.wrap_future(api.register(CIF))
            entered, release = Event(), Event()
            prepare = app.scenes.prepare

            def delayed(*args):
                entered.set()
                assert release.wait(20)
                return prepare(*args)

            monkeypatch.setattr(app.scenes, "prepare", delayed)
            future = api.reload(scene_id, CIF)
            assert await asyncio.to_thread(entered.wait, 5)
            app.scenes.close_scene(scene_id)
            release.set()
            with pytest.raises(KeyError):
                await asyncio.wrap_future(future)
            assert scene_id not in app.scenes.controllers

    asyncio.run(run())


def test_unmount_during_parse_rejects_commit(tui_crystal_factory, monkeypatch):
    async def run():
        app = CrystalTUI(tui_crystal_factory(CIF))
        entered, release = Event(), Event()
        async with app.run_test() as pilot:
            await pilot.pause()
            api = app.extension_context.structures
            prepare = app.scenes.prepare

            def delayed(*args):
                entered.set()
                assert release.wait(20)
                return prepare(*args)

            monkeypatch.setattr(app.scenes, "prepare", delayed)
            pending = api.register(CIF)
            assert await asyncio.to_thread(entered.wait, 5)
        release.set()
        with pytest.raises(RuntimeError):
            await asyncio.wrap_future(pending)
        assert len(app.scenes.controllers) == 1

    asyncio.run(run())
