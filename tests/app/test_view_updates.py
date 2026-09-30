from mat_viewer.app.view_updates import (
    UpdateKind,
    classify_change,
    display_state_key,
    geometry_state_key,
    make_update,
    update_applies,
)


def _state(**changes):
    state = {
        "scene_id": "scene-a",
        "structure": "water",
        "display_mode": "formula_unit",
        "display_options": ["hydrogens", "axes"],
        "geometry_version": 3,
        "display_version": 4,
        "camera_version": 2,
        "camera_revision": 2,
        "camera": {"eye": {"x": 1, "y": 1, "z": 1}},
    }
    state.update(changes)
    return state


def test_axes_and_labels_share_geometry_but_are_different_view_updates():
    before = _state()
    axes = _state(display_options=["hydrogens"])
    labels = _state(display_options=["hydrogens", "axes", "labels"], display_version=5)
    assert geometry_state_key(before) == geometry_state_key(axes)
    assert classify_change(before, axes) is UpdateKind.OVERLAY
    assert classify_change(before, labels) is UpdateKind.DISPLAY
    assert display_state_key(before) != display_state_key(labels)


def test_camera_update_does_not_advance_geometry_clock():
    before = _state()
    after = _state(camera={"eye": {"x": 2, "y": 1, "z": 1}}, camera_version=3)
    update = make_update(after, before=before)
    assert update.kind is UpdateKind.CAMERA
    assert update.versions.geometry == before["geometry_version"]
    assert update_applies(update, after)


def test_display_patch_requires_matching_geometry_version():
    update = make_update(
        _state(display_version=5), before=_state(display_version=4)
    )
    assert update_applies(update, _state(display_version=5))
    assert not update_applies(update, _state(geometry_version=4, display_version=5))


def test_polyhedron_enabled_is_part_of_display_identity():
    before = _state(polyhedron_specs=[{"id": "p", "enabled": True}])
    after = _state(polyhedron_specs=[{"id": "p", "enabled": False}], display_version=5)
    assert display_state_key(before) != display_state_key(after)
    assert geometry_state_key(before) == geometry_state_key(after)
    assert classify_change(before, after) is UpdateKind.DISPLAY

