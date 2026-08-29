from __future__ import annotations

from pathlib import Path

from mat_viewer.app.backend import ViewerBackend


def _backend(tmp_path: Path) -> ViewerBackend:
    return ViewerBackend(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
    )


def test_close_stops_background_resources_and_is_idempotent(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    persist_thread = backend._persist_thread

    assert persist_thread.is_alive()
    backend.close()
    backend.close()

    assert not persist_thread.is_alive()
    assert backend._closed is True
    assert backend._render_worker._compute_pool._shutdown is True
    assert backend._render_worker._finalize_pool._shutdown is True


def test_context_manager_closes_backend(tmp_path: Path) -> None:
    with _backend(tmp_path) as backend:
        persist_thread = backend._persist_thread
        assert persist_thread.is_alive()

    assert not persist_thread.is_alive()
    assert backend._closed is True
