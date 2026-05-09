from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolated_scene_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect ``SceneStore.default_path`` to a per-test ``tmp_path``.

    Without this, every test that calls ``create_app()`` /
    ``ViewerBackend(...)`` reads ``<repo>/.local/crystal_view_scenes.json``
    -- whichever scenes a developer has built up over their last GUI
    session. That makes the suite environment-dependent: the same
    commit passes on a clean CI checkout and fails on a developer
    machine, with KeyErrors pointing at long-vanished structure names.

    Tests that genuinely need to control the scene store path (e.g.
    ``tests/app/test_scene_store_recovery.py``) re-monkeypatch
    ``default_path`` to their own tmp file inside their own fixture;
    the second monkeypatch wins and is reverted at test teardown.
    """
    from crystal_viewer.scenes import SceneStore

    target = tmp_path / "isolated-scenes.json"
    monkeypatch.setattr(
        SceneStore,
        "default_path",
        classmethod(lambda cls, root_dir: str(target)),
    )
    yield target
