from __future__ import annotations

from mat_viewer.render.style.resolver import (
    apply_scene_style_overrides,
    merge_style_overrides_for_targets,
    merge_style_override,
    normalize_style_overrides,
    resolve_entity_style,
    style_fields_for_targets,
    validate_style_override_patch,
)


def test_style_resolution_is_fieldwise_and_later_rules_win():
    entity = {"kind": "geometry", "id": "mesh-1", "group": "solvent"}
    resolved = resolve_entity_style(
        entity,
        theme={"color": "#111111", "opacity": 0.3},
        kind_defaults={"color": "#222222", "edge_width": 2},
        rules=[{"selector": {"group": "solvent"}, "color": "#333333"}],
        overrides={"mesh-1": {"kind": "geometry", "opacity": 0.8}},
    )
    assert resolved["color"] == "#333333"
    assert resolved["opacity"] == 0.8
    assert resolved["edge_width"] == 2


def test_style_override_normalization_drops_unsupported_fields_and_noops():
    raw = {"mesh-1": {"kind": "geometry", "color": "#abc", "unsupported": 1}}
    normalized = normalize_style_overrides(raw)
    assert normalized["mesh-1"] == {"kind": "geometry", "color": "#abc"}
    same = merge_style_override(normalized, "mesh-1", "geometry", {"color": "#abc"})
    assert same == normalized


def test_style_override_normalization_bounds_opacity_and_drops_bad_numbers():
    normalized = normalize_style_overrides(
        {
            "mesh-1": {
                "kind": "geometry",
                "opacity": 7,
                "edge_opacity": -2,
                "edge_width": "not-a-number",
                "visible": "false",
            },
            "mesh-2": {"kind": "geometry", "opacity": float("nan")},
        }
    )
    assert normalized["mesh-1"] == {
        "kind": "geometry",
        "opacity": 1.0,
        "edge_opacity": 0.0,
        "visible": False,
    }
    assert "mesh-2" not in normalized


def test_style_override_normalization_drops_illegal_colors_and_style_values():
    normalized = normalize_style_overrides(
        {
            "mesh-1": {
                "kind": "geometry",
                "color": "not-a-color",
                "edge_color": "#abc",
                "material": "mesh",
                "flatshading": "false",
            },
            "mesh-2": {
                "kind": "geometry",
                "color": "abcdef",
                "material": "invalid-material",
                "flatshading": "maybe",
            },
            "atom-1": {
                "kind": "atom",
                "representation": "ball-stick",
            },
        }
    )
    assert normalized["mesh-1"] == {
        "kind": "geometry",
        "edge_color": "#abc",
        "material": "mesh",
        "flatshading": False,
    }
    # Bare hexadecimal strings and arbitrary CSS values are not accepted
    # consistently by all MatterVis renderers.
    assert "mesh-2" not in normalized
    assert normalized["atom-1"] == {
        "kind": "atom",
        "representation": "ball_stick",
    }


def test_style_override_patch_rejects_unknown_and_invalid_values():
    import pytest

    with pytest.raises(ValueError, match="unsupported style field"):
        validate_style_override_patch("geometry", {"bogus": 1})
    with pytest.raises(ValueError, match="opacity"):
        validate_style_override_patch("geometry", {"opacity": 2})
    with pytest.raises(ValueError, match="renderer-safe color"):
        validate_style_override_patch("geometry", {"color": "not-a-color"})
    with pytest.raises(ValueError, match="at least one"):
        validate_style_override_patch("geometry", {})


def test_geometry_render_ignores_invalid_override_opacity():
    from mat_viewer.render.geometry_entities import geometry_entity_traces, mesh_entity

    entity = mesh_entity(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        [[0, 1, 2]],
        entity_id="mesh-1",
        opacity=0.4,
    )
    traces = geometry_entity_traces(
        {"geometry_entities": [entity]},
        {"style_overrides": {"mesh-1": {"kind": "geometry", "opacity": "bad"}}},
    )
    assert traces[0].opacity == 0.4


def test_scene_style_overrides_materialize_exact_atom_and_bond_targets():
    from mat_viewer.render.picking import atom_instance_id, bond_instance_id

    atoms = [
        {"label": "C1", "elem": "C", "cart": [0.0, 0.0, 0.0], "atom_radius": 0.3, "_source_index": 0},
        {"label": "O1", "elem": "O", "cart": [1.0, 0.0, 0.0], "atom_radius": 0.3, "_source_index": 1},
    ]
    bonds = [{"i": 0, "j": 1, "start": [0.0, 0.0, 0.0], "end": [1.0, 0.0, 0.0]}]
    atom_id = atom_instance_id(atoms[0], 0)
    bond_id = bond_instance_id(bonds[0], atoms, 0)
    scene = apply_scene_style_overrides(
        {"draw_atoms": atoms, "bonds": bonds},
        {
            "bond_radius": 0.1,
            "style_overrides": {
                atom_id: {"kind": "atom", "color": "#ff0000", "opacity": 0.4, "radius": 0.5},
                bond_id: {"kind": "bond", "color": "#00ff00", "opacity": 0.2, "visible": False},
            },
        },
    )
    assert scene["draw_atoms"][0]["_render_color"] == "#ff0000"
    assert scene["draw_atoms"][0]["_render_opacity_scale"] == 0.4
    assert scene["draw_atoms"][0]["atom_radius"] == 0.5
    assert scene["bonds"][0]["_render_color"] == "#00ff00"
    assert scene["bonds"][0]["_render_opacity_scale"] == 0.2
    assert scene["bonds"][0]["_render_visible"] is False


def test_style_defaults_cover_geometry_polyhedron_and_bfdh_facet():
    assert resolve_entity_style({"kind": "mesh"})["color"] == "#7C5CBF"
    assert resolve_entity_style({"kind": "polyhedron"})["opacity"] == 0.55
    assert resolve_entity_style({"kind": "bfdh"})["color"] == "#4f7cff"


def test_named_theme_changes_defaults_but_exact_override_wins():
    light = resolve_entity_style({"kind": "geometry"}, theme="light")
    dark = resolve_entity_style({"kind": "geometry"}, theme="dark")
    assert light["color"] != dark["color"]
    assert resolve_entity_style(
        {"kind": "geometry", "id": "mesh"},
        theme="dark",
        overrides={"mesh": {"kind": "geometry", "color": "#00ff00"}},
    )["color"] == "#00ff00"


def test_parent_and_child_overrides_are_fieldwise_and_siblings_are_isolated():
    child = resolve_entity_style(
        {"kind": "facet", "id": "facet-a", "parent_id": "poly-a"},
        overrides={
            "poly-a": {"kind": "polyhedron", "color": "#111111", "opacity": 0.2},
            "facet-a": {"kind": "facet", "edge_color": "#222222"},
            "facet-b": {"kind": "facet", "color": "#333333"},
        },
    )
    assert child["color"] == "#111111"
    assert child["opacity"] == 0.2
    assert child["edge_color"] == "#222222"
    sibling = resolve_entity_style({"kind": "facet", "id": "facet-b"}, overrides={
        "poly-a": {"kind": "polyhedron", "color": "#111111"},
        "facet-a": {"kind": "facet", "edge_color": "#222222"},
        "facet-b": {"kind": "facet", "color": "#333333"},
    })
    assert sibling["color"] == "#333333"
    assert sibling["edge_color"] == "#4f7cff"


def test_multi_selection_uses_supported_property_intersection_atomically():
    targets = [
        {"kind": "atom", "id": "a1"},
        {"kind": "polyhedron", "id": "p1"},
    ]
    fields = style_fields_for_targets(targets)
    assert {"color", "opacity", "visible"} <= fields
    assert "radius" not in fields
    merged = merge_style_overrides_for_targets(None, targets, {"color": "#abcdef"})
    assert merged["a1"]["color"] == "#abcdef"
    assert merged["p1"]["color"] == "#abcdef"

    import pytest

    with pytest.raises(ValueError, match="selection"):
        merge_style_overrides_for_targets(None, targets, {"radius": 0.2})


def test_property_color_override_is_independent_from_solid_color():
    from mat_viewer.render.picking import atom_instance_id

    atoms = [{"label": "C1", "elem": "C", "cart": [0, 0, 0], "_source_index": 0}]
    atom_id = atom_instance_id(atoms[0], 0)
    scene = apply_scene_style_overrides(
        {"draw_atoms": atoms, "bonds": []},
        {"style_overrides": {atom_id: {"kind": "atom", "property_color": "#abcdef"}}},
    )
    assert scene["draw_atoms"][0]["_property_color"] == "#abcdef"
    assert "_render_color" not in scene["draw_atoms"][0]
