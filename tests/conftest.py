from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolated_local_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect every per-developer-machine local-state path to ``tmp_path``.

    Two pieces of state used to leak from the developer's repo into
    every test:

    1. **Scene store** at ``<root_dir>/.local/crystal_view_scenes.json`` --
       whichever scenes the developer last had open in the GUI. Tests
       passed on a clean CI checkout and failed on a developer machine
       with ``KeyError`` on long-vanished structure names.
    2. **Upload manifest** at ``<root_dir>/.local/crystal_view_uploads.json``
       -- the developer's accumulated CIF uploads. ``ViewerBackend.__init__``
       calls ``_restore_uploaded_bundles()`` which re-parses **every** CIF
       in the manifest through ``build_loaded_crystal`` + MolCrysKit
       ``generate_ordered_replicas_from_disordered_sites``. On a typical
       dev machine that's 17 bundles / ~19-20 s **per test** that goes
       through ``create_app(..., root_dir=WORKSPACE_DIR)`` or
       ``ViewerBackend(..., root_dir=WORKSPACE_DIR)``. ~25 tests
       sit in that band; the suite was paying ~7-8 minutes of pure
       upload-restore latency on every full run.

    Mechanism: ``os.path.join("/repo", "/abs/redirect", "file.json")``
    discards the first argument when the second is absolute, so swapping
    ``LOCAL_STATE_DIRNAME`` from the relative ``".local"`` to an
    absolute per-test directory transparently relocates **every**
    consumer (``backend_core`` upload manifest, ``backend_io``
    upload safe-root, ``presets`` default preset path) without
    touching production code.

    Tests that genuinely need to control these paths (e.g.
    ``tests/app/test_scene_store_recovery.py``) re-monkeypatch the
    same names inside their own fixture; the inner monkeypatch wins
    and is reverted at teardown.
    """
    from crystal_viewer import presets as presets_pkg
    from crystal_viewer.app import backend_core, backend_io, shared
    from crystal_viewer.presets import core as presets_core
    from crystal_viewer.scenes import SceneStore

    isolated_local = tmp_path / "isolated-local"
    isolated_local.mkdir(parents=True, exist_ok=True)
    abs_local = str(isolated_local)

    # Redirect SceneStore.default_path so persisted scene state does not
    # cross test boundaries (legacy behaviour kept for the
    # ``_isolated_scene_store`` name expected by some test fixtures).
    target_scene_store = tmp_path / "isolated-scenes.json"
    monkeypatch.setattr(
        SceneStore,
        "default_path",
        classmethod(lambda cls, root_dir: str(target_scene_store)),
    )

    # Redirect every module-local copy of ``LOCAL_STATE_DIRNAME``.
    # Each module ran ``from .presets import LOCAL_STATE_DIRNAME`` (or
    # ``from .shared import *``) at import time, binding its own
    # reference; monkeypatching only the source ``presets.core`` leaves
    # the others stale. Hit each consumer explicitly.
    for module in (presets_core, presets_pkg, shared, backend_core, backend_io):
        if hasattr(module, "LOCAL_STATE_DIRNAME"):
            monkeypatch.setattr(module, "LOCAL_STATE_DIRNAME", abs_local)

    yield isolated_local
