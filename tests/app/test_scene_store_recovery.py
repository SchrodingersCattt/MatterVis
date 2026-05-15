"""Regression: a persisted ``crystal_view_scenes.json`` referencing a
structure that is no longer in the catalog must not crash boot.

Scenario:
- Last session uploaded a CIF; the upload landed in
  ``tempfile.gettempdir()/crystal_viewer_uploads/`` and was GC'd by the
  OS, but its ``Scene`` entry stayed on disk.
- This session starts with only the default catalog (e.g. DAP-4).

Before the fix, ``ViewerBackend.__init__`` resolved the active scene,
called ``default_state(structure_name)``, hit ``KeyError`` in
``get_bundle``, and the entire Dash app failed to boot with no UI and
no useful error in the browser.

DO NOT REMOVE -- this test guards a hard-to-diagnose blank-page crash.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from crystal_viewer.app import create_app
from crystal_viewer.scenes import SceneStore


@pytest.fixture
def stale_scenes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write a scenes.json that references a structure not in any catalog."""
    target = tmp_path / "stale-scenes.json"
    payload = {
        "version": 1,
        "active_id": "scene_aaaaaaaaaaaa",
        "order": ["scene_aaaaaaaaaaaa", "scene_bbbbbbbbbbbb"],
        "scenes": [
            {
                "id": "scene_aaaaaaaaaaaa",
                "label": "Vanished upload",
                "structure_name": "DOES_NOT_EXIST_ANY_MORE",
                "state_patch": {},
                "camera": None,
                "created_at": 0.0,
                "updated_at": 0.0,
            },
            {
                "id": "scene_bbbbbbbbbbbb",
                "label": "Another stale entry",
                "structure_name": "ALSO_MISSING",
                "state_patch": {},
                "camera": None,
                "created_at": 0.0,
                "updated_at": 0.0,
            },
        ],
    }
    target.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        SceneStore,
        "default_path",
        classmethod(lambda cls, root_dir: str(target)),
    )
    return target


def test_create_app_recovers_when_scene_store_references_missing_structures(
    stale_scenes_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    app = create_app()
    backend = app.server.extensions["crystal_viewer_backend"] if "crystal_viewer_backend" in getattr(app.server, "extensions", {}) else None
    # ``create_app`` doesn't expose the backend via app.server.extensions;
    # the only reliable check is that ``create_app`` returns without raising
    # and that the resulting app has at least one registered callback.
    assert app is not None
    assert app.callback_map, "app should have callbacks wired"
    captured = capsys.readouterr()
    assert "dropped" in captured.err.lower() or "dropped" in captured.out.lower()


def test_pruned_store_persists_to_disk(stale_scenes_file: Path) -> None:
    """After boot, the on-disk file should no longer contain the bad refs."""
    create_app()
    payload = json.loads(stale_scenes_file.read_text(encoding="utf-8"))
    structure_names = [scene["structure_name"] for scene in payload["scenes"]]
    assert "DOES_NOT_EXIST_ANY_MORE" not in structure_names
    assert "ALSO_MISSING" not in structure_names
    # ``ensure`` should have re-populated with at least one scene from
    # the live catalog so the user sees something.
    assert payload["scenes"], "scene store should not end up empty"


def test_prune_handles_active_id_gone() -> None:
    store = SceneStore("/tmp/unused.json")
    store.scenes = {
        "a": __import__("crystal_viewer.scenes", fromlist=["Scene"]).Scene(
            id="a", label="A", structure_name="GHOST"
        ),
        "b": __import__("crystal_viewer.scenes", fromlist=["Scene"]).Scene(
            id="b", label="B", structure_name="GHOST"
        ),
    }
    store.order = ["a", "b"]
    store.active_id = "a"

    removed = store.prune(["DAP-4"])

    assert sorted(removed) == ["a", "b"]
    assert store.scenes == {}
    assert store.order == []
    assert store.active_id is None


def test_prune_keeps_valid_and_demotes_active_to_first_survivor() -> None:
    Scene = __import__("crystal_viewer.scenes", fromlist=["Scene"]).Scene
    store = SceneStore("/tmp/unused.json")
    store.scenes = {
        "ghost": Scene(id="ghost", label="X", structure_name="GHOST"),
        "live": Scene(id="live", label="Y", structure_name="DAP-4"),
    }
    store.order = ["ghost", "live"]
    store.active_id = "ghost"

    removed = store.prune(["DAP-4"])

    assert removed == ["ghost"]
    assert store.order == ["live"]
    assert store.active_id == "live"


def test_prune_repairs_state_patch_structure_mismatch() -> None:
    Scene = __import__("crystal_viewer.scenes", fromlist=["Scene"]).Scene
    store = SceneStore("/tmp/unused.json")
    store.scenes = {
        "mixed": Scene(
            id="mixed",
            label="DP",
            structure_name="SY",
            state_patch={"structure": "DP", "display_mode": "formula_unit"},
        )
    }
    store.order = ["mixed"]
    store.active_id = "mixed"

    removed = store.prune(["SY", "DP"])

    assert removed == []
    assert store.scenes["mixed"].structure_name == "DP"
    assert store.scenes["mixed"].state_patch["structure"] == "DP"
