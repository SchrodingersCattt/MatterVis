from __future__ import annotations

import pytest

from mat_viewer.app import ViewerBackend, _camera_from_store, _camera_store_payload


CAMERA_A = {
    "eye": {"x": 1.1, "y": -1.2, "z": 1.3},
    "center": {"x": 0.2, "y": 0.0, "z": -0.1},
    "up": {"x": 0.0, "y": 0.0, "z": 1.0},
}
CAMERA_B = {
    "eye": {"x": -1.6, "y": 0.4, "z": 0.8},
    "center": {"x": -0.1, "y": 0.1, "z": 0.0},
    "up": {"x": 0.0, "y": 1.0, "z": 0.0},
}


def test_camera_store_is_scoped_to_scene_id():
    payload = _camera_store_payload("scene-a", CAMERA_A)

    assert _camera_from_store(payload, "scene-a") == CAMERA_A
    assert _camera_from_store(payload, "scene-b") is None


def test_backend_figures_use_each_scene_camera(tmp_path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    scene_a = backend.active_scene_id()
    scene_b = backend.create_scene(structure=backend.get_state()["structure"], label="Second")["id"]

    backend.patch_state({"camera": CAMERA_A}, scene_id=scene_a)
    backend.patch_state({"camera": CAMERA_B}, scene_id=scene_b)

    fig_a, _ = backend.figure_for_state(backend.get_state(scene_a))
    fig_b, _ = backend.figure_for_state(backend.get_state(scene_b))

    assert fig_a.layout.scene.camera.eye.x == CAMERA_A["eye"]["x"]
    assert fig_a.layout.scene.camera.center.x == CAMERA_A["center"]["x"]
    assert fig_b.layout.scene.camera.eye.x == CAMERA_B["eye"]["x"]
    assert fig_b.layout.scene.camera.center.x == CAMERA_B["center"]["x"]


def test_backend_figure_title_prefers_scene_label(tmp_path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    scene_id = backend.active_scene_id()
    backend.update_scene(scene_id, {"label": "1_HTP"})

    state = backend.get_state(scene_id)
    fig, _ = backend.figure_for_state(state)

    assert fig.layout.title.text == "1_HTP"


@pytest.mark.parametrize("action", ["reset", "align"])
def test_cached_figure_refreshes_camera_and_uirevision(tmp_path, monkeypatch, action):
    from mat_viewer.app import backend_camera

    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    try:
        backend.patch_state({"camera": CAMERA_A}, broadcast=False)
        before = backend.get_state()
        first, _ = backend.figure_for_state(before)
        cache_key = backend._figure_state_cache_key(before)
        cached_revision = backend._figure_cache[cache_key][0]["layout"]["scene"]["uirevision"]

        def unexpected_build(*args, **kwargs):
            pytest.fail("camera revision must not rebuild molecular geometry")

        monkeypatch.setattr(backend_camera, "build_figure", unexpected_build)
        backend.camera_action(action, axis="a", broadcast=False)
        state = backend.get_state()
        assert backend._figure_state_cache_key(state) == cache_key
        second, _ = backend.figure_for_state(state)
        expected = backend.style_for_state(state)["uirevision"]
        assert second.layout.uirevision == second.layout.scene.uirevision == expected
        assert second.layout.scene.uirevision != first.layout.scene.uirevision
        rendered_camera = second.layout.scene.camera.to_plotly_json()
        assert all(rendered_camera[key] == value for key, value in state["camera"].items())
        assert rendered_camera["projection"]["type"] == state["projection"]
        assert second.layout.meta["mattervis_render"]["camera_revision"] == state["camera_revision"]
        assert backend._figure_cache[cache_key][0]["layout"]["scene"]["uirevision"] == cached_revision
        assert not backend._figure_revision_matches_current(state["scene_id"], before)
    finally:
        backend.close()
