from __future__ import annotations

from mat_viewer.app import ViewerBackend
from mat_viewer.loader import build_empty_bundle


def test_fresh_disordered_scene_defaults_to_opaque_mesh_with_occupancy_opacity(
    tmp_path, monkeypatch
):
    backend = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )
    try:
        name = backend.structure_names[0]
        bundle = build_empty_bundle(name=name)
        bundle.scene["has_minor"] = True
        monkeypatch.setattr(backend, "get_bundle", lambda _name: bundle)

        state = backend.default_state(name)

        assert state["material"] == "mesh"
        assert state["disorder"] == "opacity"
        assert "minor_wireframe" not in state["display_options"]

        style = backend.style_for_state(state, scene=bundle.scene)
        assert style["material"] == "mesh"
        assert style["disorder"] == "opacity"
        assert style["minor_wireframe"] is False
    finally:
        backend.close()
