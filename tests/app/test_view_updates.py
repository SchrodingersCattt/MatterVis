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


def _with(state, **changes):
    merged = dict(state)
    merged.update(changes)
    return merged


def test_atom_scale_and_style_require_a_complete_frame():
    before = _state(atom_scale=1.0, bond_radius=0.1, material="standard", style="ball_and_stick", axis_scale=1.0)
    assert classify_change(before, _with(before, atom_scale=1.4)) is UpdateKind.GEOMETRY
    assert classify_change(before, _with(before, bond_radius=0.2)) is UpdateKind.GEOMETRY
    assert classify_change(before, _with(before, material="flat", style="ortep")) is UpdateKind.GEOMETRY
    assert classify_change(before, _with(before, atom_scale=1.4, axis_scale=2.0)) is UpdateKind.GEOMETRY
    assert classify_change(before, _with(before, axis_scale=2.0)) is UpdateKind.OVERLAY


def test_polyhedron_enabled_is_part_of_display_identity():
    before = _state(polyhedron_specs=[{"id": "p", "enabled": True}])
    after = _state(polyhedron_specs=[{"id": "p", "enabled": False}], display_version=5)
    assert display_state_key(before) != display_state_key(after)
    assert geometry_state_key(before) == geometry_state_key(after)
    assert classify_change(before, after) is UpdateKind.DISPLAY


def test_same_geometry_scene_switch_requests_a_complete_frame():
    before = _state()
    after = _state(scene_id="scene-b", display_version=5)
    assert geometry_state_key(before) == geometry_state_key(after)
    assert classify_change(before, after) is UpdateKind.FULL


def test_scene_label_change_requires_a_complete_frame_for_canvas_title():
    before = _state(scene_label="Original scene")
    after = _state(scene_label="Renamed scene", display_version=5)
    assert classify_change(before, after) is UpdateKind.FULL


def test_cross_scene_label_change_keeps_geometry_classification():
    before = _state(scene_label="Original scene")
    after = _state(
        scene_id="scene-b",
        scene_label="Other scene",
        structure="nacl",
        geometry_version=4,
        display_version=5,
    )
    assert classify_change(before, after) is UpdateKind.GEOMETRY


def test_render_style_and_group_edits_advance_geometry_identity():
    before = _state(atom_scale=1.0, bond_radius=0.15, bond_groups=[])
    after = _state(
        atom_scale=1.3,
        bond_radius=0.22,
        bond_groups=[{"id": "custom", "selector": {"all": True}}],
        geometry_version=4,
        display_version=5,
    )
    assert geometry_state_key(before) != geometry_state_key(after)
    assert classify_change(before, after) is UpdateKind.GEOMETRY


def test_update_serialization_copies_nested_payload_and_scene_generation():
    payload = {"camera": {"eye": {"x": 1.0}}, "items": [{"id": "a"}]}
    state = _state(scene_generation=7)
    update = make_update(state, payload=payload)
    serialized = update.to_dict()
    payload["camera"]["eye"]["x"] = 99.0
    payload["items"][0]["id"] = "changed"
    assert serialized["scene_generation"] == 7
    assert serialized["payload"]["camera"]["eye"]["x"] == 1.0
    assert serialized["payload"]["items"][0]["id"] == "a"

