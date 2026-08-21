from __future__ import annotations

from mat_viewer.app import ViewerBackend
from mat_viewer.app.dash_impl import _display_options_can_fast_patch
from mat_viewer.app.camera_helpers import _structure_summary
from mat_viewer.loader import build_empty_bundle
from mat_viewer.presets import DEFAULT_STYLE
from mat_viewer.renderer import build_figure


def _label_scene():
    scene = build_empty_bundle().scene
    scene["draw_atoms"] = [
        {
            "label": "C1",
            "elem": "C",
            "cart": [0.0, 0.0, 0.0],
            "atom_radius": 0.18,
            "color": "#555555",
            "color_light": "#888888",
            "is_minor": False,
            "uiso": 0.04,
            "U": None,
        }
    ]
    scene["bonds"] = []
    scene["label_items"] = [
        {
            "text": "C1",
            "label_cart": [0.0, 0.0, 0.0],
            "is_minor": False,
        }
    ]
    return scene


def test_labels_checkbox_removes_text_traces():
    base_style = {
        **DEFAULT_STYLE,
        "show_axes": False,
        "topology_enabled": False,
    }
    shown = build_figure(
        _label_scene(),
        {
            **base_style,
            "show_labels": True,
        },
    )
    hidden = build_figure(
        _label_scene(),
        {
            **base_style,
            "show_labels": False,
        },
    )

    assert any(getattr(trace, "mode", None) == "text" for trace in shown.data)
    hidden_text = [trace for trace in hidden.data if getattr(trace, "mode", None) == "text"]
    assert hidden_text
    assert all(getattr(trace, "visible", True) is False for trace in hidden_text)


def test_material_persists_when_style_changes(tmp_path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    scene_id = backend.active_scene_id()

    backend.patch_state({"material": "flat", "style": "ball"}, scene_id=scene_id)
    backend.patch_state({"style": "ortep"}, scene_id=scene_id)

    state = backend.get_state(scene_id)
    assert state["material"] == "flat"
    assert state["style"] == "ortep"


def test_display_scope_persists_after_selection(tmp_path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    scene_id = backend.active_scene_id()

    backend.patch_state({"display_mode": "unit_cell"}, scene_id=scene_id)

    assert backend.get_state(scene_id)["display_mode"] == "unit_cell"


def test_cell_box_is_not_drawn_around_formula_unit_cluster(tmp_path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    scene_id = backend.active_scene_id()
    try:
        backend.patch_state(
            {
                "display_mode": "formula_unit",
                "display_options": ["unit_cell_box"],
            },
            scene_id=scene_id,
        )
        empty_scene = build_empty_bundle().scene
        formula_style = backend.style_for_state(
            backend.get_state(scene_id), scene=empty_scene
        )
        assert formula_style["show_unit_cell"] is False

        backend.patch_state({"display_mode": "unit_cell"}, scene_id=scene_id)
        cell_style = backend.style_for_state(
            backend.get_state(scene_id), scene=empty_scene
        )
        assert cell_style["show_unit_cell"] is True
    finally:
        backend._render_worker.shutdown()


def test_only_label_and_axis_options_use_fast_display_patch():
    assert _display_options_can_fast_patch(["labels"], ["labels", "axes"])
    assert not _display_options_can_fast_patch([], ["unit_cell_box"])
    assert not _display_options_can_fast_patch(["minor_only"], [])
    assert not _display_options_can_fast_patch([], ["minor_wireframe"])


def test_structure_summary_reports_unresolved_disorder():
    scene = {
        "draw_atoms": [
            {
                "is_minor": False,
                "is_disordered": True,
                "disorder_resolved": False,
            }
        ],
        "bonds": [],
    }

    summary = _structure_summary(scene)

    assert "Disorder detected: 1 site(s)" in summary
    assert "no resolved major/minor assignment" in summary


def test_ws_figure_broadcast_rejects_empty_2d_payload(tmp_path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    scene_id = backend.active_scene_id()
    try:
        ignored = backend.broadcast_figure(
            scene_id=scene_id,
            figure={"data": [], "layout": {"scene": {"camera": {}}}},
        )

        assert ignored["type"] == "figure_ignored"
        assert backend.latest_figure_broadcast() is None

        valid = {
            "data": [{"type": "scatter3d", "x": [0], "y": [0], "z": [0]}],
            "layout": {"scene": {"camera": {}}},
        }
        payload = backend.broadcast_figure(scene_id=scene_id, figure=valid)
        assert payload["type"] == "figure"
        assert backend.websocket_snapshot(include_figure=True)["figure"] == valid

        other_scene = "stale-scene"
        backend.broadcast_figure(scene_id=other_scene, figure=valid)
        assert "figure" not in backend.websocket_snapshot(include_figure=True)
    finally:
        backend._render_worker.shutdown()


def test_ws_figure_broadcast_rejects_stale_polyhedron_state(tmp_path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    scene_id = backend.active_scene_id()
    try:
        old_state = backend.get_state(scene_id)
        old_state.update(
            {
                "topology_enabled": True,
                "polyhedron_specs": [
                    {
                        "id": "spec_a",
                        "name": "Old",
                        "center_species": "N",
                        "ligand_species": "C5N6FeO",
                        "color": "#7C5CBF",
                        "enabled": True,
                    }
                ],
            }
        )
        backend.patch_state(
            {
                "topology_enabled": True,
                "polyhedron_specs": [
                    {
                        "id": "spec_a",
                        "name": "New",
                        "center_species": "C4NO",
                        "ligand_species": "C5N6FeO",
                        "color": "#7C5CBF",
                        "enabled": True,
                    }
                ],
            },
            scene_id=scene_id,
            broadcast=False,
        )
        valid = {
            "data": [{"type": "scatter3d", "x": [0], "y": [0], "z": [0]}],
            "layout": {"scene": {"camera": {}}},
        }

        ignored = backend.broadcast_figure(scene_id=scene_id, figure=valid, state=old_state)

        assert ignored["type"] == "figure_ignored"
        assert ignored["reason"] == "stale-state"
        assert backend.latest_figure_broadcast() is None
    finally:
        backend._render_worker.shutdown()


def test_ws_figure_broadcast_allows_camera_revision_drift(tmp_path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    scene_id = backend.active_scene_id()
    try:
        render_state = backend.get_state(scene_id)
        current_state = dict(render_state)
        current_state["camera_revision"] = int(current_state.get("camera_revision", 0) or 0) + 1
        backend.patch_state(
            {"camera_revision": current_state["camera_revision"]},
            scene_id=scene_id,
            broadcast=False,
        )
        valid = {
            "data": [{"type": "scatter3d", "x": [0], "y": [0], "z": [0]}],
            "layout": {"scene": {"camera": {}}},
        }

        payload = backend.broadcast_figure(scene_id=scene_id, figure=valid, state=render_state)

        assert payload["type"] == "figure"
        assert backend.latest_figure_broadcast()["figure"] == valid
    finally:
        backend._render_worker.shutdown()


def test_ws_figure_broadcast_rejects_old_generation_after_state_round_trip(tmp_path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    scene_id = backend.active_scene_id()
    try:
        state_a1 = backend.get_state(scene_id)
        backend.patch_state({"display_mode": "unit_cell"}, scene_id=scene_id, broadcast=False)
        backend.patch_state(
            {"display_mode": state_a1["display_mode"]},
            scene_id=scene_id,
            broadcast=False,
        )
        state_a2 = backend.get_state(scene_id)
        valid = {
            "data": [{"type": "scatter3d", "x": [0], "y": [0], "z": [0]}],
            "layout": {"scene": {"camera": {}}},
        }

        ignored = backend.broadcast_figure(scene_id=scene_id, figure=valid, state=state_a1)
        accepted = backend.broadcast_figure(scene_id=scene_id, figure=valid, state=state_a2)

        assert state_a2["render_revision"] == state_a1["render_revision"] + 2
        assert ignored["type"] == "figure_ignored"
        assert ignored["reason"] == "stale-render-revision"
        assert accepted["render_revision"] == state_a2["render_revision"]
    finally:
        backend._render_worker.shutdown()


def test_figure_history_filters_payload_that_became_stale(tmp_path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    scene_id = backend.active_scene_id()
    try:
        state = backend.get_state(scene_id)
        valid = {
            "data": [{"type": "scatter3d", "x": [0], "y": [0], "z": [0]}],
            "layout": {"scene": {"camera": {}}},
        }
        payload = backend.broadcast_figure(scene_id=scene_id, figure=valid, state=state)
        backend.patch_state({"display_mode": "unit_cell"}, scene_id=scene_id, broadcast=False)

        assert backend.figure_broadcasts_since(0) == []
        assert backend.latest_figure_broadcast() is None
        assert payload["figure_seq"] == backend.latest_figure_seq()
    finally:
        backend._render_worker.shutdown()


def test_pending_state_snapshot_includes_render_revision(tmp_path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    scene_id = backend.active_scene_id()
    try:
        state = backend.patch_state(
            {"display_mode": "unit_cell"},
            scene_id=scene_id,
            broadcast=True,
        )

        pending = backend.pop_pending_state()

        assert pending["scene_id"] == scene_id
        assert pending["render_revision"] == state["render_revision"]
    finally:
        backend._render_worker.shutdown()


def test_figure_metadata_identifies_scene_and_render_revision(tmp_path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    scene_id = backend.active_scene_id()
    try:
        state = backend.get_state(scene_id)

        fig, _ = backend.figure_for_state(state)
        meta = fig.to_plotly_json()["layout"]["meta"]["mattervis_render"]

        assert meta["scene_id"] == scene_id
        assert meta["render_revision"] == state["render_revision"]
        assert meta["server_started_at"] == state["server_started_at"]
    finally:
        backend._render_worker.shutdown()
