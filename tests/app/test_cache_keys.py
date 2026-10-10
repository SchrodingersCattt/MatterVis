from __future__ import annotations

from mat_viewer.cache_keys import (
    figure_state_cache_key,
    fragment_options_cache_key,
    fragment_table_cache_key,
    scene_cache_key,
    topology_geometry_cache_key,
    topology_side_panel_cache_key,
    topology_spec_geometry_key,
    transformed_scene_cache_key,
)


def test_scene_and_fragment_keys_are_named_and_stable():
    thresholds_a = {("O", "Zn"): 2.2, ("C", "C"): 1.7}
    thresholds_b = {("C", "C"): 1.7, ("O", "Zn"): 2.2}
    assert scene_cache_key(
        display_mode="unit_cell",
        show_hydrogen=False,
        bond_scale=1.1,
        bond_thresholds=thresholds_a,
    ) == scene_cache_key(
        display_mode="unit_cell",
        show_hydrogen=False,
        bond_scale=1.1,
        bond_thresholds=thresholds_b,
    )
    assert scene_cache_key(
        display_mode="unit_cell", show_hydrogen=True
    ) != scene_cache_key(display_mode="unit_cell", show_hydrogen=False)
    assert fragment_table_cache_key(
        display_mode="formula_unit", show_hydrogen=False
    ) != fragment_table_cache_key(
        display_mode="formula_unit", show_hydrogen=True
    )


def test_transformed_and_fragment_option_keys_include_pipeline_and_structure():
    transforms = [{"id": "rename", "kind": "repeat", "params": {"a": 2}}]
    renamed = [{**transforms[0], "id": "new-label"}]
    assert transformed_scene_cache_key(
        display_mode="formula_unit", show_hydrogen=False, transforms=transforms
    ) == transformed_scene_cache_key(
        display_mode="formula_unit", show_hydrogen=False, transforms=renamed
    )
    state = {
        "scene_id": "scene-a",
        "structure": "MOF",
        "display_mode": "formula_unit",
        "display_options": [],
        "transforms": transforms,
    }
    assert fragment_options_cache_key(state) != fragment_options_cache_key(
        {**state, "structure": "salt"}
    )
    assert fragment_options_cache_key(state) != fragment_options_cache_key(
        {**state, "transforms": []}
    )


def test_topology_geometry_key_ignores_paint_but_tracks_geometry_knobs():
    base = {
        "id": "zn",
        "center_species": "Zn",
        "ligand_species": ["O"],
        "color": "#ff0000",
        "enabled": True,
        "enforce_enclosure": True,
        "centroid_offset_frac": 0.25,
        "level": "molecule",
        "center_kind": "centroid",
        "hard_cutoff": None,
        "fallback_max": 12,
    }
    painted = {**base, "color": "#00ff00", "enabled": False}
    assert topology_spec_geometry_key([base]) == topology_spec_geometry_key([painted])
    assert topology_geometry_cache_key(
        structure="MOF",
        display_mode="formula_unit",
        show_hydrogen=False,
        site_index=2,
        cutoff=10.0,
        specs=[base],
        transforms=[],
    ) == topology_geometry_cache_key(
        structure="MOF",
        display_mode="formula_unit",
        show_hydrogen=False,
        site_index=2,
        cutoff=10.0,
        specs=[painted],
        transforms=[],
    )
    assert topology_spec_geometry_key([{**base, "hard_cutoff": 8.0}]) != topology_spec_geometry_key([base])


def test_topology_side_panel_key_uses_geometry_fields_and_ignores_paint():
    state = {
        "scene_id": "scene-a",
        "structure": "MOF",
        "display_mode": "formula_unit",
        "display_options": [],
        "topology_species_keys": ["Zn"],
        "topology_site_index": 2,
        "topology_enabled": True,
        "cutoff": 10.0,
        "transforms": [],
        "polyhedron_specs": [
            {
                "id": "zn",
                "center_species": "Zn",
                "ligand_species": ["O"],
                "color": "#ff0000",
                "enabled": True,
                "level": "molecule",
                "hard_cutoff": None,
            }
        ],
    }
    painted = {
        **state,
        "polyhedron_specs": [
            {**state["polyhedron_specs"][0], "color": "#00ff00", "enabled": False}
        ],
    }
    assert topology_side_panel_cache_key(state) == topology_side_panel_cache_key(painted)
    changed = {
        **state,
        "polyhedron_specs": [{**state["polyhedron_specs"][0], "hard_cutoff": 8.0}],
    }
    assert topology_side_panel_cache_key(state) != topology_side_panel_cache_key(changed)


def test_figure_key_keeps_flat_ortep_camera_policy():
    base = {
        "structure": "MOF",
        "display_mode": "formula_unit",
        "display_options": [],
        "polyhedron_specs": [{"id": "zn", "enabled": True, "center_species": "Zn"}],
        "material": "mesh",
        "style": "ball_stick",
        "camera": {"eye": {"x": 1}},
    }
    assert figure_state_cache_key(base) == figure_state_cache_key(
        {**base, "camera": {"eye": {"x": 9}}}
    )
    assert figure_state_cache_key(base) == figure_state_cache_key(
        {
            **base,
            "polyhedron_specs": [{"id": "zn", "enabled": False, "center_species": "Zn"}],
        }
    )
    flat = {**base, "material": "flat", "style": "ortep"}
    assert figure_state_cache_key(flat) != figure_state_cache_key(
        {**flat, "camera": {"eye": {"x": 9}}}
    )


def test_figure_key_excludes_delivery_counters():
    state = {
        "structure": "MOF",
        "display_mode": "formula_unit",
        "display_options": [],
        "material": "mesh",
        "style": "ball_stick",
        "version": 4,
        "render_revision": 7,
        "geometry_version": 3,
        "display_version": 5,
        "camera_version": 9,
        "camera_revision": 11,
        "projection": "perspective",
    }
    advanced = {
        **state,
        "version": 104,
        "render_revision": 107,
        "geometry_version": 103,
        "display_version": 105,
        "camera_version": 109,
        "camera_revision": 111,
        "projection": "orthographic",
    }
    assert figure_state_cache_key(state) == figure_state_cache_key(advanced)
