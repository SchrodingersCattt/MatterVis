from __future__ import annotations

from crystal_viewer.app import ViewerBackend
from crystal_viewer.app.dash_impl import _display_options_can_fast_patch
from crystal_viewer.loader import build_empty_bundle
from crystal_viewer.presets import DEFAULT_STYLE
from crystal_viewer.renderer import build_figure


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


def test_only_label_and_axis_options_use_fast_display_patch():
    assert _display_options_can_fast_patch(["labels"], ["labels", "axes"])
    assert not _display_options_can_fast_patch([], ["unit_cell_box"])
    assert not _display_options_can_fast_patch(["minor_only"], [])
    assert not _display_options_can_fast_patch([], ["minor_wireframe"])


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
