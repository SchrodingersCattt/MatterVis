"""Camera resets must survive scene-state reads and persistence."""

from mat_viewer.scenes.core import Scene


def test_explicit_camera_reset_overrides_creation_state_and_defaults():
    old = {"eye": {"x": 1, "y": 2, "z": 3}}
    scene = Scene.create(label="sample", structure_name="sample",
                         state_patch={"camera": old}, camera=old)
    scene.patch({"camera": None})
    assert scene.state({"camera": old})["camera"] is None
    restored = Scene.from_dict(scene.to_dict())
    assert restored.state({"camera": old})["camera"] is None


def test_camera_after_reset_is_detached_and_visible():
    scene = Scene.create(label="sample", structure_name="sample")
    scene.patch({"camera": None})
    current = {"eye": {"x": 0, "y": 0, "z": 4}}
    scene.patch({"camera": current})
    current["eye"]["z"] = 100
    state = scene.state({})
    assert state["camera"]["eye"]["z"] == 4
    state["camera"]["eye"]["z"] = 200
    assert scene.state({})["camera"]["eye"]["z"] == 4