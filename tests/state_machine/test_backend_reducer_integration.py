from __future__ import annotations

from pathlib import Path

from mat_viewer.app import ViewerBackend
from mat_viewer.app.reducer import (
    Invalidation,
    SetDisplayMode,
    SetRenderStyle,
    operation_from_intent,
    reduce_state,
)
from mat_viewer.presets import default_preset_path


def _backend(tmp_path: Path) -> ViewerBackend:
    return ViewerBackend(preset_path=default_preset_path(), root_dir=str(tmp_path))


def test_legacy_style_transition_persists_full_payload_and_facts(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        payload = {
            "type": "set_style",
            "scene_id": scene_id,
            "payload": {
                "show_axes": False,
                "show_axis_key": True,
                "show_labels": True,
                "show_unit_cell": True,
                "monochrome": True,
                "color": "#123456",
                "isosurface": {"enabled": True},
                "isosurface_enabled": False,
                "isosurface_mode": "wireframe",
            },
        }
        before = backend.get_state(scene_id)
        operation = operation_from_intent(payload)
        reduced, expected = reduce_state(before, operation)

        result = backend.apply_intent(payload)
        state = result["state"]

        for key in (
            "show_axes",
            "show_axis_key",
            "show_labels",
            "show_unit_cell",
            "monochrome",
            "color",
            "isosurface",
            "isosurface_enabled",
            "isosurface_mode",
        ):
            assert state[key] == reduced[key]
        assert set(result["invalidations"]) == {item.value for item in expected}
        assert backend.style_for_state(state)["show_axis_key"] is True
    finally:
        backend.close()


def test_typed_and_legacy_style_paths_report_the_same_invalidation(tmp_path: Path):
    legacy_backend = _backend(tmp_path / "legacy")
    typed_backend = _backend(tmp_path / "typed")
    try:
        legacy = legacy_backend.apply_intent(
            {
                "type": "set_style",
                "payload": {"style": "ortep", "show_axes": False},
            }
        )
        typed_state, typed_invalidations = typed_backend.apply_operation(
            SetRenderStyle({"style": "ortep", "show_axes": False})
        )

        assert set(legacy["invalidations"]) == {
            item.value for item in typed_invalidations
        }
        # ORTEP is a paint style. It must not switch the display scope, so
        # the committed transition repaints the figure and leaves the scene
        # geometry alone.
        assert Invalidation.FIGURE_BODY.value in legacy["invalidations"]
        assert Invalidation.SCENE_GEOMETRY.value not in legacy["invalidations"]
        assert typed_state["display_mode"] == legacy["state"]["display_mode"]
        assert legacy["state"]["style"] == "ortep"
    finally:
        legacy_backend.close()
        typed_backend.close()


def test_typed_display_mode_can_preserve_topology_site(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state({"topology_site_index": 7}, scene_id=scene_id)
        state, _ = backend.apply_operation(
            SetDisplayMode("unit_cell", reset_topology_site=False),
            scene_id=scene_id,
        )
        assert state["display_mode"] == "unit_cell"
        assert state["topology_site_index"] == 7
    finally:
        backend.close()
