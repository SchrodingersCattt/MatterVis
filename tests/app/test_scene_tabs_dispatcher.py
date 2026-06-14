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


def test_poll_path_does_not_overwrite_scene_tabs_value(tmp_path: Path):
    """Regression: every 5 s ``agent-state-poll`` tick must NOT rewrite
    ``scene-tabs.value``. The browser is the authority for which tab is
    currently focused; the poll path used to echo
    ``backend.active_scene_id()`` back into ``scene-tabs.value`` and
    that overwrote in-flight tab clicks (the user-visible "switching
    tabs has no effect after 2+ tabs" bug). On scene-CRUD / upload
    events the callback still owns the active-id write.

    This contract is asserted at the source level so the protection
    cannot silently regress to "always write active_id". If you change
    the implementation, also update this test, but keep the no-poll-
    write semantics intact.
    """
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    source = _manage_scene_tabs_source(app)

    assert "agent-state-poll" in source, (
        "manage_scene_tabs_dom must explicitly branch on the agent-state-"
        "poll trigger so it can short-circuit the poll path."
    )
    # The poll branch must end with a no_update on the value slot.
    poll_branch_idx = source.index("agent-state-poll")
    poll_branch = source[poll_branch_idx:]
    assert "no_update" in poll_branch, (
        "the poll branch must short-circuit to no_update so the active "
        "scene id is never rewritten on a periodic tick"
    )
    # Defence-in-depth: the only ``Output(scene-tabs, value)`` write that
    # still survives must be on the explicit-event path. Look for the
    # explicit-event return that carries ``active_id``.
    assert "active_id" in source
    # And the poll branch must NOT carry ``active_id`` into its return.
    poll_return = poll_branch.split("return", 1)[1] if "return" in poll_branch else ""
    assert "active_id" not in poll_return.split("\n")[0], (
        "poll branch must not return ``active_id`` for scene-tabs.value"
    )


def test_explicit_event_path_writes_scene_tabs_value(tmp_path: Path):
    """The CRUD / upload event paths SHOULD write ``scene-tabs.value`` to
    the freshly created scene so the UI lands on the new tab. This is
    the symmetric counterpart of the poll-path guard above: without
    this, uploading a CIF would land the tab list on the new scene's
    label but never auto-switch the focused tab. We assert this at the
    source level for the same reason as above.
    """
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    source = _manage_scene_tabs_source(app)

    # The explicit-event branch (CRUD / upload) is everything AFTER the
    # poll-trigger branch -- the poll branch is the early-return special
    # case and the explicit-event return is the function's tail. Verify
    # that tail ends by writing ``active_id`` into the third (scene-tabs.
    # value) output slot.
    assert source.rstrip().endswith("active_id"), (
        "the function must end with an explicit ``return ..., active_id`` "
        "on the CRUD/upload event path so tab uploads auto-switch."
    )


def test_scene_tabs_dom_caches_fingerprint_so_poll_does_not_tear_down_react_tree(
    tmp_path: Path,
):
    """The poll path used to call ``backend.scene_tabs()`` and
    ``scene_close_buttons()`` every 5 s, returning fresh Dash component
    trees. React would then tear down and rebuild the tab subtree,
    cancelling any in-flight click event on a tab. With many tabs open
    the user saw "clicks on tabs are dropped randomly" -- this test
    pins the fingerprint short-circuit that fixes it.
    """
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    source = _manage_scene_tabs_source(app)

    assert "fingerprint" in source.lower(), (
        "the poll branch must compute a fingerprint of the scene-list "
        "(id + label) and short-circuit to no_update when nothing has "
        "changed; otherwise the React subtree gets rebuilt every 5 s "
        "and tab clicks get dropped."
    )


def test_update_view_allows_scene_switch_during_graph_interaction(tmp_path: Path):
    """A drag / wheel frame may set ``graph-interaction-store.active``.
    That should defer redundant redraws for the same scene, but it must not
    suppress a tab switch; otherwise the tab label changes while the graph
    stays on the previous material.
    """
    import inspect

    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    callbacks = [
        callback
        for callback in _callbacks_with_output(app, "crystal-graph", "figure")
        if ("agent-state-store", "data") in _inputs(callback)
        and ("graph-interaction-store", "data") in _inputs(callback)
    ]
    assert len(callbacks) == 1
    source = inspect.getsource(callbacks[0]["callback"])

    assert "last_rendered_scene_id" in source
    assert "interaction_active and last_rendered_scene_id == scene_id" in source
    assert "_last_rendered_scene_id = state.get(\"scene_id\")" in source


def test_compass_afterplot_prefers_live_camera():
    """A click/right-click on a point can trigger Plotly afterplot without
    committing ``layout.scene.camera``. The SVG compass must read the live
    WebGL camera in that path, or it snaps back until the next drag frame.
    """
    script = Path("frontend/assets/compass_overlay.js").read_text(encoding="utf-8")

    assert 'gd.on("plotly_afterplot", function () { redrawCompass(gd, null, true); });' in script


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
