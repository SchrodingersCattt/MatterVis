from __future__ import annotations

import copy
from hashlib import sha256
import json
import sys
from pathlib import Path
import threading
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Expose ``tests/`` itself on sys.path so shared helper modules under
# ``tests/`` (e.g. ``tests/_layout_helpers.py``) resolve via the bare
# ``import _layout_helpers`` / ``from _layout_helpers import x``
# pattern. ``tests/`` is intentionally NOT a package -- adding
# ``__init__.py`` would change pytest's collection rootdir.
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

_INTEGRATION_DIRECTORIES = {"app", "scenes", "tui"}
_INTEGRATION_RENDER_FILES = {
    "test_animation_adapter.py",
    "test_atom_property_coloring.py",
    "test_cpu_backend.py",
    "test_export.py",
    "test_fast_animation.py",
    "test_plotly_plan_adapter.py",
    "test_static_publication.py",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Derive coarse test tiers from stable directory and file boundaries."""
    for item in items:
        relative = Path(item.path).resolve().relative_to(ROOT)
        parts = relative.parts
        if len(parts) >= 2 and parts[0] == "tests" and parts[1] == "perf":
            item.add_marker(pytest.mark.slow)
            continue
        if len(parts) < 2 or parts[0] != "tests":
            continue
        file_name = relative.name
        directory = parts[1] if len(parts) >= 3 else ""
        if (
            directory in _INTEGRATION_DIRECTORIES
            or file_name.startswith("test_cli_")
            or file_name.endswith(("_api.py", "_rest.py"))
            or (directory == "render" and file_name in _INTEGRATION_RENDER_FILES)
        ):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def catalog_bundle_factory():
    """Return fresh copies of content-addressed built-in catalog bundles."""
    from mat_viewer.app import backend_core

    real_loader = backend_core.build_loaded_crystal
    templates: dict[tuple[Any, ...], Any] = {}
    file_digests: dict[tuple[str, int, int], str] = {}
    lock = threading.Lock()

    def canonical_json(value: Any) -> str:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=repr,
        )

    def load(**kwargs):
        source_path = Path(kwargs["cif_path"]).resolve()
        stat = source_path.stat()
        file_identity = (str(source_path), stat.st_size, stat.st_mtime_ns)
        with lock:
            digest = file_digests.get(file_identity)
            if digest is None:
                digest = sha256(source_path.read_bytes()).hexdigest()
                file_digests[file_identity] = digest
        key = (
            str(source_path),
            digest,
            str(kwargs["name"]),
            kwargs.get("title"),
            str(kwargs.get("source", "catalog")),
            canonical_json(kwargs.get("preset")),
            canonical_json(kwargs.get("view_weights")),
        )
        with lock:
            template = templates.get(key)
            if template is None:
                template = real_loader(**kwargs)
                templates[key] = template
            return copy.deepcopy(template)

    return load


@pytest.fixture(scope="session")
def tui_crystal_factory():
    """Return fresh copies of repeatedly loaded terminal-view fixtures."""
    from mat_viewer.tui.loader_adapter import load_for_tui

    templates: dict[tuple[str, str, str], Any] = {}
    lock = threading.Lock()

    def load(path: str | Path, **kwargs):
        source_path = Path(path).resolve()
        key = (
            str(source_path),
            sha256(source_path.read_bytes()).hexdigest(),
            json.dumps(kwargs, sort_keys=True, separators=(",", ":"), default=repr),
        )
        with lock:
            template = templates.get(key)
            if template is None:
                template = load_for_tui(str(source_path), **kwargs)
                templates[key] = template
            return copy.deepcopy(template)

    return load


@pytest.fixture(autouse=True)
def _isolated_local_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    catalog_bundle_factory,
):
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
    from mat_viewer import presets as presets_pkg
    from mat_viewer.app import backend_core, backend_io, shared
    from mat_viewer.app.backend import ViewerBackend
    from mat_viewer.presets import core as presets_core
    from mat_viewer.scenes import SceneStore

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

    # Force synchronous upload mode in tests so bundle is ready before
    # the upload call returns. The async background path is tested
    # explicitly in dedicated integration tests.
    from mat_viewer.app import backend_io as bio_module
    monkeypatch.setattr(bio_module._IOBackendMixin, "_upload_sync_mode", True, raising=False)

    catalog_root = (ROOT / "scripts" / "data").resolve()
    real_catalog_loader = backend_core.build_loaded_crystal
    if request.node.get_closest_marker("real_catalog_load") is None:
        def cached_catalog_loader(**kwargs):
            source_path = Path(kwargs["cif_path"]).resolve()
            if (
                kwargs.get("source", "catalog") == "catalog"
                and source_path.is_relative_to(catalog_root)
            ):
                return catalog_bundle_factory(**kwargs)
            return real_catalog_loader(**kwargs)

        monkeypatch.setattr(
            backend_core,
            "build_loaded_crystal",
            cached_catalog_loader,
        )

    created_backends: list[ViewerBackend] = []
    real_backend_init = ViewerBackend.__init__

    def tracked_backend_init(self, *args, **kwargs):
        real_backend_init(self, *args, **kwargs)
        created_backends.append(self)

    monkeypatch.setattr(ViewerBackend, "__init__", tracked_backend_init)

    try:
        yield isolated_local
    finally:
        for backend in reversed(created_backends):
            backend.close(wait=False)
