from __future__ import annotations

from mat_viewer.app.selectors import (
    partition_state,
    scene_geometry_cache_key,
    show_axes,
    show_hydrogen,
    show_labels,
    show_unit_cell,
    transform_scene_cache_key,
    viewport_signature,
)
from mat_viewer.app.view_updates import geometry_state_key


def _state(**changes):
    state = {
        "scene_id": "scene-a",
        "structure": "water",
        "display_mode": "formula_unit",
        "display_options": ["hydrogens", "axes", "labels"],
        "label_mode": "unique_sites",
        "topology_enabled": False,
        "transforms": [],
        "camera": {"eye": {"x": 1, "y": 1, "z": 1}},
        "version": 7,
        "server_started_at": "2026-10-10T00:00:00Z",
    }
    state.update(changes)
    return state


def test_display_flags_are_derived_from_option_tokens():
    state = _state()

    assert show_hydrogen(state)
    assert show_axes(state)
    assert show_labels(state)
    assert not show_unit_cell(state)

    state["display_options"].append("unit_cell_box")
    assert show_unit_cell(state)


def test_cosmetic_options_do_not_change_geometry_identity():
    before = _state()
    labels_and_axes = _state(display_options=["hydrogens"])
    no_hydrogens = _state(display_options=["axes", "labels"])

    assert geometry_state_key(before) == geometry_state_key(labels_and_axes)
    assert scene_geometry_cache_key(before) == scene_geometry_cache_key(labels_and_axes)
    assert geometry_state_key(before) != geometry_state_key(no_hydrogens)
    assert scene_geometry_cache_key(before) != scene_geometry_cache_key(no_hydrogens)
    assert viewport_signature(before) == viewport_signature(labels_and_axes)


def test_viewport_selector_tracks_viewport_options_but_ignores_labels():
    before = _state(display_options=["hydrogens", "labels"])
    boxed = _state(display_options=["hydrogens", "labels", "unit_cell_box"])
    relabelled = _state(display_options=["hydrogens", "axes"])

    assert viewport_signature(before) != viewport_signature(boxed)
    assert viewport_signature(before) == viewport_signature(relabelled)


def test_partition_is_non_mutating_and_keeps_transport_fields_ephemeral():
    state = _state(
        transforms=[{"id": "t1", "kind": "repeat", "params": {"a": 2}}],
        topology_fragment_type="anion",
        unknown_extension={"enabled": True},
    )
    slices = partition_state(state)

    assert "structure" in slices.stored
    assert "camera" in slices.stored
    assert "show_hydrogen" in slices.derived
    assert "fast_rendering" not in slices.stored
    assert slices.derived["fast_rendering"] is False
    assert "version" in slices.ephemeral
    assert "topology_fragment_type" in slices.ephemeral
    assert slices.unknown == {"unknown_extension": {"enabled": True}}

    slices.stored["transforms"][0]["params"]["a"] = 99
    assert state["transforms"][0]["params"]["a"] == 2


def test_partition_round_trips_durable_render_layers():
    state = _state(
        fast_rendering=True,
        overlay_overrides=[{"id": "compass", "kind": "compass", "anchor": "paper"}],
        vector_overlays=[
            {
                "id": "mode-1",
                "magnitude_mode": "absolute",
                "arrows": [{"origin": [0, 0, 0], "vector": [1, 0, 0]}],
            }
        ],
    )

    slices = partition_state(state)
    restored = dict(slices.stored)

    assert restored["fast_rendering"] is True
    assert restored["overlay_overrides"] == state["overlay_overrides"]
    assert restored["vector_overlays"] == state["vector_overlays"]
    restored["vector_overlays"][0]["arrows"][0]["vector"][0] = 9
    assert state["vector_overlays"][0]["arrows"][0]["vector"][0] == 1


def test_monochrome_selector_honours_later_group_overrides():
    legacy = _state(display_options=["monochrome"])
    assert partition_state(legacy).derived["monochrome"] is True

    mixed = _state(
        display_options=["monochrome"],
        atom_groups=[
            {"selector": {"all": True}, "color": "#000000"},
            {"selector": {"elements": ["O"]}, "color": "#FF0000"},
        ],
    )
    assert partition_state(mixed).derived["monochrome"] is False

    replaced = _state(
        atom_groups=[
            {"selector": {"all": True}, "color": "#000000"},
            {"selector": {"all": True}, "color": "#FFFFFF"},
        ],
    )
    assert partition_state(replaced).derived["monochrome"] is False

    constrained = _state(
        atom_groups=[
            {"selector": {"all": True, "elements": ["C"]}, "color": "#000000"}
        ]
    )
    assert partition_state(constrained).derived["monochrome"] is False


def test_viewport_signature_tracks_geometry_and_topology_inputs():
    before = _state()
    assert viewport_signature(before) != viewport_signature(_state(atom_scale=1.2))
    assert viewport_signature(before) != viewport_signature(_state(bond_radius=0.2))
    assert viewport_signature(before) != viewport_signature(
        _state(
            topology_enabled=True,
            topology_site_index=2,
            topology_species_keys=["Pb"],
            polyhedron_specs=[{"id": "pb", "center_species": "Pb"}],
        )
    )


def test_viewport_signature_can_use_resolved_axis_ranges():
    resolved = {"x": [-1.0, 2.0], "y": [-2.0, 3.0], "z": [-3.0, 4.0]}
    assert viewport_signature(
        _state(atom_scale=1.0), resolved_viewport=resolved
    ) == viewport_signature(_state(atom_scale=9.0), resolved_viewport=resolved)
    changed = {**resolved, "z": [-4.0, 4.0]}
    assert viewport_signature(
        _state(), resolved_viewport=resolved
    ) != viewport_signature(_state(), resolved_viewport=changed)


def test_transform_scene_cache_ignores_row_identity_and_name():
    first = _state(
        transforms=[
            {"id": "one", "name": "Repeat", "kind": "repeat", "params": {"a": 2}}
        ]
    )
    renamed = _state(
        transforms=[
            {
                "id": "renamed",
                "name": "User label",
                "kind": "repeat",
                "params": {"a": 2},
            }
        ]
    )

    assert transform_scene_cache_key(first) == transform_scene_cache_key(renamed)
