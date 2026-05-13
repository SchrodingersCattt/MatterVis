from __future__ import annotations

from pathlib import Path

from crystal_viewer.app import ViewerBackend
from crystal_viewer.loader import build_empty_bundle
from crystal_viewer.presets import default_preset_path
from crystal_viewer.renderer import build_figure


def _scene():
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
        },
        {
            "label": "O1",
            "elem": "O",
            "cart": [1.2, 0.0, 0.0],
            "atom_radius": 0.17,
            "color": "#B85060",
            "color_light": "#D48A88",
            "is_minor": True,
            "uiso": 0.04,
            "U": None,
        },
    ]
    scene["bonds"] = [
        {
            "i": 0,
            "j": 1,
            "start": [0.0, 0.0, 0.0],
            "end": [1.2, 0.0, 0.0],
            "color_i": "#555555",
            "color_j": "#B85060",
            "is_minor": True,
        }
    ]
    scene["label_items"] = [
        {"label_cart": [0.0, 0.0, 0.2], "text": "C1", "is_minor": False},
        {"label_cart": [1.2, 0.0, 0.2], "text": "O1", "is_minor": True},
    ]
    return scene


def _style(**overrides):
    style = {
        "material": "mesh",
        "style": "ball_stick",
        "disorder": "opacity",
        "atom_scale": 1.0,
        "bond_radius": 0.1,
        "axis_scale": 0.1,
        "minor_opacity": 0.35,
        "show_axes": False,
        "show_labels": False,
        "show_unit_cell": False,
        "show_minor_only": False,
        "topology_enabled": False,
    }
    style.update(overrides)
    return style


def test_camera_action_broadcast_false_does_not_arm_pending_state(tmp_path: Path):
    backend = ViewerBackend(preset_path=default_preset_path(), root_dir=str(tmp_path))
    backend.pop_pending_state()

    backend.camera_action("align", axis="a", broadcast=False)

    assert backend.pop_pending_state() is None


def test_style_toggle_does_not_rebuild_mesh_cache():
    scene = _scene()
    build_figure(scene, _style())
    cache = scene.get("_mesh_trace_cache") or {}
    keys_before = set(cache.keys())

    build_figure(
        scene,
        _style(
            show_axes=True,
            show_labels=True,
            show_unit_cell=True,
            show_minor_only=True,
            minor_opacity=0.65,
        ),
    )

    assert set(cache.keys()) == keys_before


def test_polyhedra_reorder_uses_same_topology_geometry_cache(tmp_path: Path):
    backend = ViewerBackend(preset_path=default_preset_path(), root_dir=str(tmp_path))
    calls = {"n": 0}

    def fake_resolve_topology_site(**_kwargs):
        return 0

    def fake_compute_topology_geometry(**_kwargs):
        calls["n"] += 1
        return {"spec_results": []}

    backend.resolve_topology_site = fake_resolve_topology_site  # type: ignore[method-assign]
    backend._compute_topology_geometry = fake_compute_topology_geometry  # type: ignore[method-assign]
    base_state = backend.get_state()
    base_state.update(
        {
            "topology_enabled": True,
            "polyhedron_specs": [
                {
                    "id": "spec_a",
                    "name": "A",
                    "center_species": "A",
                    "ligand_species": "X",
                    "color": "#7C5CBF",
                    "enabled": True,
                },
                {
                    "id": "spec_b",
                    "name": "B",
                    "center_species": "B",
                    "ligand_species": "X",
                    "color": "#E07C24",
                    "enabled": True,
                },
            ],
        }
    )

    backend.topology_for_state(base_state)
    reordered = dict(base_state)
    reordered["polyhedron_specs"] = list(reversed(base_state["polyhedron_specs"]))
    backend.topology_for_state(reordered)

    assert calls["n"] == 1
