"""Regression tests for the ``_figure_cache`` reuse behaviour.

Historically every state mutation called ``_bump_version()`` which
*also* wiped the entire figure cache. That made every tab switch and
every cancelled slider tweak cost a full ~400 ms ``build_figure``
rebuild. The cache key already uniquely identifies the figure body
content, so the broad invalidation is no longer needed.
"""

from __future__ import annotations

from crystal_viewer.app import ViewerBackend


def _make_backend(tmp_path):
    return ViewerBackend(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
    )


def test_bump_version_does_not_clear_figure_cache(tmp_path):
    backend = _make_backend(tmp_path)
    state = backend.get_state()
    backend.figure_for_state(state)
    cache_size_before = len(backend._figure_cache)
    assert cache_size_before >= 1

    backend._bump_version()
    assert len(backend._figure_cache) == cache_size_before


def test_figure_cache_hits_across_version_bumps(tmp_path):
    backend = _make_backend(tmp_path)
    state_a = backend.get_state()
    fig_first, _ = backend.figure_for_state(state_a)
    snapshot = len(backend._figure_cache)
    assert snapshot >= 1

    backend._bump_version()
    backend._bump_version()
    state_b = backend.get_state()
    fig_second, _ = backend.figure_for_state(state_b)
    assert len(backend._figure_cache) == snapshot
    assert fig_first.to_dict()["data"] == fig_second.to_dict()["data"]


def test_camera_change_does_not_invalidate_cache(tmp_path):
    backend = _make_backend(tmp_path)
    state_a = backend.get_state()
    backend.figure_for_state(state_a)
    snapshot = len(backend._figure_cache)

    state_b = dict(state_a)
    state_b["camera"] = {"eye": {"x": 2.0, "y": 1.0, "z": 0.5}}
    backend.figure_for_state(state_b)
    assert len(backend._figure_cache) == snapshot


def test_preset_load_invalidates_figure_cache(tmp_path):
    backend = _make_backend(tmp_path)
    state = backend.get_state()
    backend.figure_for_state(state)
    assert len(backend._figure_cache) >= 1

    preset_payload = backend.save_preset("regression.json")
    backend.load_preset_from_path(preset_payload["path"], allow_external=True)
    assert backend._figure_cache == {}
