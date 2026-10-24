"""Revision mappings use native Textual controllers and user scene events."""
import asyncio
from pathlib import Path

import pytest
from textual.widgets import Select

from mat_viewer.extensions import RevisionId, RevisionUnavailable
from mat_viewer.tui.app import CrystalTUI

CIF = Path(__file__).parents[1] / "fixtures" / "cod_1000043.cif"


def test_revision_native_switch_reload_close(tui_crystal_factory):
    async def run():
        app = CrystalTUI(tui_crystal_factory(CIF))
        async with app.run_test() as pilot:
            await pilot.pause()
            api = app.extension_context.revisions
            first = await asyncio.wrap_future(api.register(RevisionId("test.flow", "a"), CIF))
            second = await asyncio.wrap_future(api.register(RevisionId("test.flow", "b"), CIF))
            events = []
            api.subscribe(events.append)
            app.query_one("#native-scenes", Select).value = first.scene.scene_id
            await pilot.pause()
            assert events[-1].kind == "activated"
            assert events[-1].bindings == (first,)
            assert app.controller is app.scenes.controllers[first.scene.scene_id]
            replacement = await asyncio.wrap_future(api.reload(
                RevisionId("test.flow", "c"), first.scene, CIF
            ))
            with pytest.raises(RevisionUnavailable):
                api.focus(first.revision)
            await asyncio.wrap_future(api.focus(second.revision))
            assert events[-1].bindings == (second,)
            app.scenes.close_scene(replacement.scene.scene_id)
            with pytest.raises(RevisionUnavailable):
                api.lookup(replacement.revision)
            assert api.lookup(second.revision) == second
    asyncio.run(run())
