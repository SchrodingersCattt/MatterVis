"""Regression: ``app.layout`` must be callable so a browser refresh
re-reads the live backend state instead of serving the startup
snapshot.

Scenario before the fix:
- User uploads ``EP.cif`` after the server starts; the scene store and
  bundles are updated, but ``app.layout`` is the static ``html.Div(...)``
  that was built at ``create_app()`` time, so a browser refresh sets
  ``scene-tabs.value`` and the visible slider defaults back to the
  *original* startup scene. The visible UI snapped back to the default
  structure and the user had to re-upload to recover the view.

DO NOT REMOVE -- this test guards "refresh shows the default screen"
that the project AGENTS doc enumerates as a known race.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from crystal_viewer.app import create_app


@pytest.fixture
def app_and_backend(tmp_path: Path):
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    backend = app.crystal_backend
    return app, backend


def test_app_layout_is_callable(app_and_backend):
    app, _ = app_and_backend
    assert callable(app.layout), (
        "app.layout must be a callable so Dash re-evaluates it on every "
        "initial-load request -- refresh used to echo the startup state"
    )


def test_layout_reflects_added_scene(app_and_backend):
    """A new scene created on the live backend appears in the next layout."""
    app, backend = app_and_backend
    initial_layout = app.layout()
    initial_ids = sorted(scene["id"] for scene in backend.scene_options())

    structure_name = backend.structure_names[0]
    new_scene = backend.create_scene(structure=structure_name, label="refresh-probe")
    new_scene_id = new_scene["id"] if isinstance(new_scene, dict) else new_scene.id

    refreshed_layout = app.layout()
    refreshed_ids = sorted(scene["id"] for scene in backend.scene_options())

    assert new_scene_id in refreshed_ids
    assert new_scene_id not in initial_ids
    # The two layout objects must not be identical -- if they were, Dash
    # would have served the cached snapshot.
    assert refreshed_layout is not initial_layout
