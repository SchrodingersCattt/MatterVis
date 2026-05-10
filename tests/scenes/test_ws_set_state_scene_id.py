"""WebSocket ``set_state`` envelope must accept a ``scene_id``.

Background
----------
Old shape: ``{"type": "set_state", "payload": {...}}`` -- always
applied to the currently active tab. Multi-tab automation tooling
that targets a specific scene then ended up silently writing to the
wrong tab whenever the user clicked something else first.

New shape: optional ``scene_id`` either at the envelope level OR on
the inner payload. Test the dispatch helper directly so we don't
need a real socket.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from crystal_viewer.api import handle_ws_message
from crystal_viewer.app import WORKSPACE_DIR, ViewerBackend


@pytest.fixture
def backend(tmp_path: Path):
    return ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=WORKSPACE_DIR)


@pytest.fixture
def two_scenes(backend: ViewerBackend):
    boot = backend.active_scene_id()
    second = backend.create_scene(label="probe", structure=backend.get_state(boot)["structure"])
    return boot, second["id"]


def test_envelope_scene_id_targets_specific_tab(backend, two_scenes):
    boot, second = two_scenes
    backend.set_active_scene(boot, broadcast=False)
    handle_ws_message(backend, {
        "type": "set_state",
        "scene_id": second,
        "payload": {"atom_scale": 1.45},
    })
    assert backend.get_state(second)["atom_scale"] == pytest.approx(1.45)
    assert backend.get_state(boot)["atom_scale"] != pytest.approx(1.45)
    # Active tab unchanged.
    assert backend.active_scene_id() == boot


def test_inner_scene_id_targets_specific_tab(backend, two_scenes):
    boot, second = two_scenes
    backend.set_active_scene(boot, broadcast=False)
    handle_ws_message(backend, {
        "type": "set_state",
        "payload": {"scene_id": second, "atom_scale": 1.55},
    })
    assert backend.get_state(second)["atom_scale"] == pytest.approx(1.55)
    assert backend.active_scene_id() == boot


def test_no_scene_id_falls_back_to_active(backend, two_scenes):
    boot, second = two_scenes
    backend.set_active_scene(second, broadcast=False)
    handle_ws_message(backend, {
        "type": "set_state",
        "payload": {"atom_scale": 1.65},
    })
    assert backend.get_state(second)["atom_scale"] == pytest.approx(1.65)


def test_unknown_envelope_type_is_a_no_op(backend, two_scenes):
    boot, _second = two_scenes
    before = backend.get_state(boot)["atom_scale"]
    result = handle_ws_message(backend, {"type": "raw", "message": "hi"})
    assert result is None
    assert backend.get_state(boot)["atom_scale"] == before
