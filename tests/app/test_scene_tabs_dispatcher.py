from __future__ import annotations

from pathlib import Path

from crystal_viewer.app import ViewerBackend, create_app


_VALID_CIF = b"""data_minimal
_cell_length_a 10.0
_cell_length_b 10.0
_cell_length_c 10.0
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_space_group_name_H-M_alt 'P 1'
loop_
_space_group_symop_operation_xyz
'x, y, z'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
C1 C 0.0 0.0 0.0 1.0
"""


def _walk(component):
    yield component
    children = getattr(component, "children", None)
    if children is None:
        return
    if isinstance(children, (list, tuple)):
        for child in children:
            yield from _walk(child)
    else:
        yield from _walk(children)


def _outputs(callback):
    out = callback.get("output")
    items = out if isinstance(out, list) else [out]
    pairs = set()
    for item in items:
        cid = getattr(item, "component_id", None)
        prop = getattr(item, "component_property", None)
        if isinstance(cid, str) and isinstance(prop, str):
            pairs.add((cid, prop))
    return pairs


def _inputs(callback):
    return {(str(item.get("id")), item.get("property")) for item in callback.get("inputs", [])}


def _callbacks_with_output(app, component_id: str, prop: str):
    return [
        callback
        for callback in app.callback_map.values()
        if (component_id, prop) in _outputs(callback)
    ]


def test_layout_contains_scene_event_store(tmp_path: Path):
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    ids = {
        component_id
        for component_id in (getattr(component, "id", None) for component in _walk(app.layout))
        if isinstance(component_id, str)
    }
    assert "scene-event-store" in ids


def test_scene_tabs_dom_has_single_writer(tmp_path: Path):
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))

    for prop in ("children", "value"):
        writers = _callbacks_with_output(app, "scene-tabs", prop)
        assert len(writers) == 1
        assert not any(
            getattr(output, "allow_duplicate", False)
            for callback in writers
            for output in (callback.get("output") if isinstance(callback.get("output"), list) else [callback.get("output")])
        )


def test_dispatcher_listens_to_upload_and_scene_events(tmp_path: Path):
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    writers = _callbacks_with_output(app, "scene-tabs", "children")
    assert len(writers) == 1
    inputs = _inputs(writers[0])
    assert ("scene-event-store", "data") in inputs
    assert ("native-upload-sync", "data") in inputs


def test_sync_agent_state_no_longer_writes_scene_tabs(tmp_path: Path):
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    sync_callbacks = [
        callback
        for callback in app.callback_map.values()
        if ("agent-state-store", "data") in _outputs(callback)
        and ("camera-state-store", "data") in _outputs(callback)
        and ("scene-tabs", "value") in _inputs(callback)
    ]
    assert len(sync_callbacks) == 1
    assert ("scene-tabs", "children") not in _outputs(sync_callbacks[0])
    assert ("scene-tabs", "value") not in _outputs(sync_callbacks[0])


def test_backend_upload_append_and_close_actions_drive_scene_options(tmp_path: Path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    first_scene = backend.active_scene_id()

    bundle = backend.add_uploaded_file_bytes(_VALID_CIF, "__mattervis_test_scene_tabs_upload__.cif")
    uploaded_scene = backend.active_scene_id()

    options = backend.scene_options()
    assert bundle.name == "__mattervis_test_scene_tabs_upload__"
    assert uploaded_scene != first_scene
    assert any(
        scene["id"] == uploaded_scene
        and scene["structure_name"] == "__mattervis_test_scene_tabs_upload__"
        for scene in options
    )

    duplicate = backend.duplicate_scene(uploaded_scene)
    backend.delete_other_scenes(duplicate["id"])
    assert [scene["id"] for scene in backend.scene_options()] == [duplicate["id"]]

    second = backend.duplicate_scene(duplicate["id"])
    backend.delete_scene(second["id"])
    assert [scene["id"] for scene in backend.scene_options()] == [duplicate["id"]]
