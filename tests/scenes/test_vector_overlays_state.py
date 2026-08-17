from __future__ import annotations

import copy

import pytest

from crystal_viewer.render.overlay.vectors import normalize_vector_overlays


def _groups():
    return [{
        "id": "p",
        "magnitude_mode": "normalized",
        "length": 2.0,
        "arrows": [{"id": "p0", "origin": [0, 0, 0], "vector": [1, 0, 0]}],
    }]


def test_vector_state_normalization_is_json_safe_and_copyable() -> None:
    original = _groups()
    normalized = normalize_vector_overlays(original)
    assert normalized[0]["viewport_policy"] == "include"
    copied = copy.deepcopy(normalized)
    copied[0]["arrows"][0]["origin"][0] = 9
    assert normalized[0]["arrows"][0]["origin"][0] == 0


def test_duplicate_ids_are_rejected() -> None:
    groups = _groups()
    groups.append(copy.deepcopy(groups[0]))
    with pytest.raises(ValueError):
        normalize_vector_overlays(groups)


def test_backend_normalizes_vectors_and_invalidates_camera() -> None:
    from crystal_viewer.app.backend import ViewerBackend

    backend = ViewerBackend.__new__(ViewerBackend)
    backend.current_state = {
        "structure": "dummy",
        "vector_overlays": [],
        "camera": {"eye": {"x": 1, "y": 1, "z": 1}},
        "camera_revision": 3,
        "display_mode": "cluster",
        "display_options": [],
        "topology_enabled": False,
        "atom_scale": 1.0,
        "bond_radius": 0.15,
        "minor_opacity": 0.35,
        "axis_scale": 1.0,
        "cutoff": 10.0,
        "material": "mesh",
        "style": "ball_stick",
        "disorder": "none",
        "ortep_mode": "ortep_axes",
        "label_mode": "unique_sites",
        "topology_species_keys": [],
        "topology_site_index": None,
        "topology_hull_color": "#7C5CBF",
        "polyhedron_specs": [],
        "atom_groups": [],
        "bond_groups": [],
        "transforms": [],
        "selection": {"atom_labels": [], "active_label": None, "order": []},
        "disorder_resolve": {"method": "enumerate", "count": 5, "seed": None},
        "disorder_replicas": [],
        "fast_rendering": False,
        "projection": "perspective",
    }
    backend.scene_store = type("Store", (), {"scenes": {}})()
    normalized = backend.normalize_state({"vector_overlays": _groups()})
    assert normalized["vector_overlays"][0]["id"] == "p"
    assert normalized["camera"] is None
    assert normalized["camera_revision"] == 4


def test_scene_for_state_attaches_tab_local_vector_copy(monkeypatch) -> None:
    from crystal_viewer.app import backend_core
    from crystal_viewer.app.backend import ViewerBackend

    shared_scene = {"name": "dummy", "fragment_table": []}
    bundle = type("Bundle", (), {"scene": shared_scene, "fragment_table": []})()
    backend = ViewerBackend.__new__(ViewerBackend)
    backend.current_state = {
        "structure": "dummy",
        "display_mode": "cluster",
        "display_options": [],
        "transforms": [],
        "vector_overlays": _groups(),
    }
    backend.preset = {}
    backend.get_bundle = lambda name: bundle
    monkeypatch.setattr(backend_core, "build_bundle_scene", lambda *args, **kwargs: shared_scene)
    first = backend.scene_for_state()
    first["vector_overlays"][0]["arrows"][0]["origin"][0] = 99
    second = backend.scene_for_state()
    assert second["vector_overlays"][0]["arrows"][0]["origin"][0] == 0
    assert "vector_overlays" not in shared_scene
