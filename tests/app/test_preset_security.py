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
    """A bare filename without ``..`` is allowed and lands under ``<root>/.local``."""
    result = backend.save_preset(path="custom_preset.json")
    written = Path(result["path"]).resolve()
    safe_root = (tmp_path / ".local").resolve()
    assert str(written).startswith(str(safe_root))
    assert written.name == "custom_preset.json"
    assert written.exists()


def test_load_preset_with_relative_filename_works(backend: ViewerBackend, tmp_path: Path) -> None:
    """Round-trip: save under a relative name, load it back via the same name."""
    backend.save_preset(path="roundtrip.json")
    result = backend.load_preset_from_path("roundtrip.json")
    written = Path(result["path"]).resolve()
    safe_root = (tmp_path / ".local").resolve()
    assert str(written).startswith(str(safe_root))
