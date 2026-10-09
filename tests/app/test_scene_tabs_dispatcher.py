from __future__ import annotations

from pathlib import Path

from mat_viewer.app import ViewerBackend, create_app


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


from _layout_helpers import (  # noqa: E402  shared helpers
    callback_inputs as _inputs,
    callback_outputs as _outputs,
    callbacks_with_output as _callbacks_with_output,
    walk_layout as _walk,
)


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
    assert ("agent-state-poll", "n_intervals") not in inputs


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


def _manage_scene_tabs_source(app):
    """Return the source code of ``manage_scene_tabs_dom`` from the live
    Dash app. Direct invocation of registered callbacks is fragile (the
    Dash wrapper requires an internal ``outputs_list`` kwarg), so the
    contract tests below assert on the callback source instead -- the
    same approach already used by ``test_camera_capture_no_poll_echo``.
    """
    import inspect

    writers = _callbacks_with_output(app, "scene-tabs", "children")
    assert len(writers) == 1
    return inspect.getsource(writers[0]["callback"])


def test_scene_tab_dom_is_not_wired_to_periodic_poll(tmp_path: Path):
    """A stale poll response must not recreate a scene after close.

    The control-state poll remains active elsewhere, but the scene-tab DOM is
    rebuilt only by explicit CRUD/upload events. This prevents an older poll
    response from arriving after a close and putting the removed tab back.
    """
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    source = _manage_scene_tabs_source(app)

    writers = _callbacks_with_output(app, "scene-tabs", "children")
    assert len(writers) == 1
    assert ("agent-state-poll", "n_intervals") not in _inputs(writers[0])
    assert "older poll response" in source


def test_explicit_event_path_writes_scene_tabs_value(tmp_path: Path):
    """The CRUD / upload event paths SHOULD write ``scene-tabs.value`` to
    the freshly created scene so the UI lands on the new tab. This is
    the explicit-event path that remains after removing the race-prone poll
    writer: without this, uploading a CIF would land the tab list on the new
    scene's label but never auto-switch the focused tab. We assert this at the
    source level for the same reason as above.
    """
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    source = _manage_scene_tabs_source(app)

    # The explicit-event return writes ``active_id`` into the third
    # (scene-tabs.value) output slot.
    assert source.rstrip().endswith("active_id"), (
        "the function must end with an explicit ``return ..., active_id`` "
        "on the CRUD/upload event path so tab uploads auto-switch."
    )


def test_scene_tabs_dom_rebuilds_only_after_explicit_events(tmp_path: Path):
    """The tab subtree has one explicit-event writer and no poll writer."""
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    callback = _callbacks_with_output(app, "scene-tabs", "children")[0]
    assert _inputs(callback) == {
        ("scene-event-store", "data"),
        ("native-upload-sync", "data"),
    }


def test_update_view_not_wired_to_graph_interaction_store(tmp_path: Path):
    """``graph-interaction-store`` must NOT appear as an Input on any
    callback that outputs ``crystal-graph.figure``.

    The store fires on every pointerdown / wheel / pointerup gesture
    and was previously wired to ``update_view``.  Each fire triggered a
    full ``normalize_state`` + ``topo_key_preview`` + ``figure_for_state``
    round-trip that tripped ``dcc.Loading``'s 300 ms spinner threshold,
    so every drag/zoom flash the loading overlay.

    Its only legitimate consumer is the WS figure fast lane in
    ``mattervis.js``, which gates deferred pushes purely in JS without
    any server round-trip.
    """
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    wired = [
        callback
        for callback in _callbacks_with_output(app, "crystal-graph", "figure")
        if ("graph-interaction-store", "data") in _inputs(callback)
    ]
    assert len(wired) == 0, (
        "graph-interaction-store must not be an Input to any "
        "crystal-graph.figure callback"
    )


def test_update_view_only_triggers_on_agent_state(tmp_path: Path):
    """``update_view`` must have exactly one Input: ``agent-state-store``.
    No other events (poll intervals, interaction stores, upload signals)
    should cascade through the expensive full-figure path."""
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    # Find callbacks that write to crystal-graph.figure AND take
    # agent-state-store as Input, but do NOT have allow_duplicate.
    candidates = []
    for cb in _callbacks_with_output(app, "crystal-graph", "figure"):
        if ("agent-state-store", "data") not in _inputs(cb):
            continue
        # Exclude allow_duplicate callbacks (view buttons, projection
        # toggle, compass patch, etc.).  The primary update_view is the
        # ONLY non-allow_duplicate writer.
        outputs = cb["output"] if isinstance(cb["output"], list) else [cb["output"]]
        if any(getattr(o, "allow_duplicate", False) for o in outputs):
            continue
        candidates.append(cb)
    assert len(candidates) == 1, f"expected exactly 1 primary figure writer; got {len(candidates)}"
    inputs = _inputs(candidates[0])
    assert ("graph-interaction-store", "data") not in inputs, (
        f"graph-interaction-store must NOT be an Input to the primary "
        f"figure callback; got inputs={inputs}"
    )


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
