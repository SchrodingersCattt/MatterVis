"""Regression: ``backend.save_preset`` / ``load_preset_from_path``
must reject any client-controlled ``path`` that escapes ``<root>/.local``.

Pre-fix, the ``/api/v{1,2}/preset/save`` and ``/preset/load`` handlers
piped the JSON ``path`` field straight into ``open()``. Anyone able to
reach the API could overwrite arbitrary files (with a JSON-shaped
payload) and read back any JSON file the server process could.

DO NOT REMOVE -- this guards an arbitrary-file-read/write bug.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from crystal_viewer.app import ViewerBackend
from crystal_viewer.presets import default_preset_path


@pytest.fixture
def backend(tmp_path: Path) -> ViewerBackend:
    return ViewerBackend(preset_path=default_preset_path(), root_dir=str(tmp_path))


def _safe_local_root(tmp_path: Path) -> Path:
    """Resolve the directory that ``backend.save_preset(path="x.json")``
    will land relative paths into.

    The autouse ``_isolated_local_state`` fixture in ``tests/conftest.py``
    redirects ``LOCAL_STATE_DIRNAME`` from the literal ``".local"`` to a
    per-test absolute path so the developer's
    ``<repo>/.local/crystal_view_uploads.json`` no longer leaks into the
    suite. That redirect makes ``os.path.join(root_dir,
    LOCAL_STATE_DIRNAME, ...)`` discard ``root_dir`` (because the second
    arg is absolute), so these tests must compare against the redirected
    path rather than ``tmp_path / ".local"``. Read the live value from
    production code to stay in sync if the fixture path scheme changes.
    """
    from crystal_viewer.app import backend_io as _backend_io  # local import for monkeypatched value

    return Path(_backend_io.LOCAL_STATE_DIRNAME).resolve()


def test_save_preset_rejects_absolute_path_outside_local(backend: ViewerBackend, tmp_path: Path) -> None:
    target = tmp_path / "evil.json"
    with pytest.raises(ValueError, match="preset path must resolve inside"):
        backend.save_preset(path=str(target))
    assert not target.exists(), "absolute attacker path got written"


def test_save_preset_rejects_dotdot_relative(backend: ViewerBackend) -> None:
    with pytest.raises(ValueError, match="preset path must resolve inside"):
        backend.save_preset(path="../escape.json")


def test_load_preset_rejects_absolute_path_outside_local(backend: ViewerBackend) -> None:
    with pytest.raises(ValueError, match="preset path must resolve inside"):
        backend.load_preset_from_path("/etc/passwd.json")


def test_load_preset_rejects_dotdot_relative(backend: ViewerBackend) -> None:
    with pytest.raises(ValueError, match="preset path must resolve inside"):
        backend.load_preset_from_path("../../etc/preset.json")


def test_save_preset_with_none_uses_default(backend: ViewerBackend) -> None:
    """Backwards compatibility: ``path=None`` keeps using ``self.preset_path``."""
    result = backend.save_preset(path=None)
    assert "path" in result
    assert Path(result["path"]).exists()


def test_save_preset_with_relative_filename_lands_in_local(
    backend: ViewerBackend, tmp_path: Path
) -> None:
    """A bare filename without ``..`` is allowed and lands under the
    safe local root (``<root>/.local`` in production; redirected
    per-test by the autouse ``_isolated_local_state`` fixture)."""
    result = backend.save_preset(path="custom_preset.json")
    written = Path(result["path"]).resolve()
    safe_root = _safe_local_root(tmp_path)
    assert str(written).startswith(str(safe_root))
    assert written.name == "custom_preset.json"
    assert written.exists()


def test_load_preset_with_relative_filename_works(backend: ViewerBackend, tmp_path: Path) -> None:
    """Round-trip: save under a relative name, load it back via the same name."""
    backend.save_preset(path="roundtrip.json")
    result = backend.load_preset_from_path("roundtrip.json")
    written = Path(result["path"]).resolve()
    safe_root = _safe_local_root(tmp_path)
    assert str(written).startswith(str(safe_root))


def test_preset_v2_round_trips_multiple_scenes(backend: ViewerBackend) -> None:
    first_id = backend.active_scene_id()
    structure = backend.get_state()["structure"]
    second = backend.create_scene(structure=structure, label="second view")
    backend.patch_state({"display_mode": "unit_cell"}, scene_id=second["id"])

    saved = backend.save_preset(path="multi_scene.json")
    assert saved["scenes"] >= 2

    backend.delete_scene(second["id"])
    assert second["id"] not in {scene["id"] for scene in backend.scene_options()}

    loaded = backend.load_preset_from_path("multi_scene.json")
    scene_ids = {scene["id"] for scene in backend.scene_options()}
    assert first_id in scene_ids
    assert second["id"] in scene_ids
    assert loaded["state"]["scene_id"] in scene_ids
    assert backend.get_state(second["id"])["display_mode"] == "unit_cell"
