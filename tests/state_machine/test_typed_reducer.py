from __future__ import annotations

from mat_viewer.app.reducer import (
    Invalidation,
    Operation,
    OperationKind,
    SetCamera,
    SetDisplayMode,
    SetDisplayOptions,
    SetProjection,
    SetTransforms,
    operation_from_intent,
    operation_patch,
    reduce_state,
)


def _state(**changes):
    state = {
        "scene_id": "scene-a",
        "structure": "water",
        "display_mode": "formula_unit",
        "display_options": ["labels"],
        "topology_site_index": 4,
        "camera": {"eye": {"x": 1.0, "y": 1.0, "z": 1.0}},
        "camera_revision": 3,
        "transforms": [],
    }
    state.update(changes)
    return state


def test_reducer_is_pure_and_resets_site_explicitly():
    before = _state()
    after, invalidations = reduce_state(before, SetDisplayMode("unit_cell"))

    assert before["display_mode"] == "formula_unit"
    assert before["topology_site_index"] == 4
    assert after["display_mode"] == "unit_cell"
    assert after["topology_site_index"] is None
    assert invalidations == frozenset(
        {
            Invalidation.SCENE_GEOMETRY,
            Invalidation.TOPOLOGY_GEOMETRY,
            Invalidation.FIGURE_BODY,
            Invalidation.SIDE_PANEL,
            Invalidation.CAMERA_LAYOUT,
        }
    )


def test_display_option_invalidations_distinguish_geometry_tokens():
    before = _state(display_options=["labels"])
    labels, labels_invalidations = reduce_state(
        before, SetDisplayOptions(["labels", "axes"])
    )
    hydrogens, hydrogen_invalidations = reduce_state(
        before, SetDisplayOptions(["labels", "hydrogens"])
    )

    assert labels["display_options"] == ["labels", "axes"]
    assert labels_invalidations == frozenset({Invalidation.FIGURE_BODY})
    assert hydrogen_invalidations >= {
        Invalidation.SCENE_GEOMETRY,
        Invalidation.TOPOLOGY_GEOMETRY,
        Invalidation.CAMERA_LAYOUT,
    }


def test_camera_and_projection_operations_only_invalidate_camera_layout():
    before = _state()
    camera, camera_invalidations = reduce_state(
        before, SetCamera({"eye": {"x": 2, "y": 1, "z": 1}})
    )
    projected, projection_invalidations = reduce_state(
        before, SetProjection("orthographic")
    )

    assert camera["camera"]["eye"]["x"] == 2
    assert camera_invalidations == frozenset({Invalidation.CAMERA_LAYOUT})
    assert projected["projection"] == "orthographic"
    assert projected["camera"]["projection"] == {"type": "orthographic"}
    assert projection_invalidations == frozenset({Invalidation.CAMERA_LAYOUT})


def test_transform_operation_invalidates_topology_and_side_panel():
    before = _state()
    after, invalidations = reduce_state(
        before,
        SetTransforms([{"kind": "repeat", "params": {"a": 2, "b": 1, "c": 1}}]),
    )
    assert after["transforms"][0]["kind"] == "repeat"
    assert invalidations >= {
        Invalidation.TRANSFORM_GEOMETRY,
        Invalidation.TOPOLOGY_GEOMETRY,
        Invalidation.SIDE_PANEL,
    }


def test_legacy_intent_mapping_keeps_wire_payloads():
    style = operation_from_intent(
        {
            "type": "set_style",
            "scene_id": "scene-a",
            "payload": {"atom_scale": 1.2, "material": "flat"},
        }
    )
    camera = operation_from_intent(
        {
            "type": "set_camera",
            "payload": {"camera": {"eye": {"x": 1, "y": 2, "z": 3}}},
        }
    )
    patch = operation_from_intent(
        {"type": "patch_state", "payload": {"display_mode": "cluster"}}
    )

    assert style.kind is OperationKind.SET_RENDER_STYLE
    assert style.scene_id == "scene-a"
    assert operation_patch(style) == {"atom_scale": 1.2, "material": "flat"}
    assert camera.kind is OperationKind.SET_CAMERA
    assert operation_patch(camera)["camera"]["eye"]["z"] == 3
    assert patch.kind is OperationKind.PATCH_STATE


def test_mapping_operations_are_accepted_for_replay():
    state, invalidations = reduce_state(
        _state(),
        {"kind": "set_display_options", "payload": {"display_options": ["axes"]}},
    )
    assert isinstance(Operation("set_display_options", {"display_options": ["axes"]}), Operation)
    assert state["display_options"] == ["axes"]
    assert invalidations == frozenset({Invalidation.FIGURE_BODY})
