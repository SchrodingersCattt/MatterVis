"""Saved paint follows semantic identity through render, reset and export."""
from copy import deepcopy

from mat_viewer.loader import build_empty_bundle
from mat_viewer.presets import (
    DEFAULT_STYLE,
    load_preset,
    save_preset,
    scene_to_preset_entry,
)
from mat_viewer.render.geometry_entities import geometry_entity_traces, mesh_entity
from mat_viewer.render.morphology import _morphology_traces
from mat_viewer.render.picking import (
    atom_instance_id,
    atom_parent_targets,
    facet_instance_id,
)
from mat_viewer.render.style.resolver import apply_scene_style_overrides
from mat_viewer.renderer import build_figure


def scene_fixture():
    scene = build_empty_bundle().scene
    scene.update(display_mode="cluster", atom_fragment_labels=["A", "A", "B"])
    scene["draw_atoms"] = [
        {"label": "C1", "elem": "C", "cart": [float(i) * 2, 0, 0],
         "atom_radius": .2, "color": "#555555", "color_light": "#888888",
         "_source_index": i, "_source_molecule_index": i // 2,
         "part": "A", "disorder_group": "A", "is_minor": False, "occ": 1}
        for i in range(3)
    ]
    scene["bonds"] = [{"i": 0, "j": 1, "start": [0, 0, 0], "end": [2, 0, 0]}]
    return scene


def test_exact_child_style_wins_after_group_decoration_and_static_export():
    scene = scene_fixture()
    original = deepcopy(scene)
    atom_id = atom_instance_id(scene["draw_atoms"][0])
    style = {**DEFAULT_STYLE, "atom_groups": [
        {"id": "all", "selector": {"all": True}, "color": "#112233"},
    ], "style_overrides": {atom_id: {"kind": "atom", "color": "#aa6688"}},
        "selection": {"targets": [{"kind": "atom", "id": atom_id}], "atom_labels": ["C1"]}}
    interactive = build_figure(scene, style).to_plotly_json()
    static = build_figure(scene, style, include_interaction_traces=False).to_plotly_json()
    colors = {t.get("color") for t in static["data"] if t.get("type") == "mesh3d"}
    assert {"#aa6688", "#112233"} <= colors
    assert any(t.get("meta", {}).get("mv_radii") for t in interactive["data"])
    assert any(t.get("meta", {}).get("mv_segments") for t in interactive["data"])
    for trace in static["data"]:
        assert trace.get("name") != "selection-outline"
        assert not {"mv_radii", "mv_segments", "face_ids", "vertex_face_ids"} & trace.get("meta", {}).keys()
    assert scene["draw_atoms"] == original["draw_atoms"]
    assert scene["bonds"] == original["bonds"]


def test_molecular_part_inheritance_and_exact_child_remain_fieldwise():
    scene = scene_fixture()
    parent = atom_parent_targets(scene["draw_atoms"][0], "A")[-1]
    child_id = atom_instance_id(scene["draw_atoms"][0])
    style = {"style_overrides": {
        parent["id"]: {"kind": "part", "color": "#aa6688", "opacity": .4},
        child_id: {"kind": "atom", "color": "#445566", "representation": "wireframe", "material": "flat"},
    }}
    atoms = apply_scene_style_overrides(scene, style)["draw_atoms"]
    assert atoms[0]["_render_color"] == "#445566"
    assert atoms[0]["_render_opacity_scale"] == .4
    assert atoms[0]["_render_style"] == "wireframe"
    assert atoms[0]["_render_material"] == "flat"
    assert atoms[1]["_render_color"] == "#aa6688"
    assert "_render_color" not in atoms[2]
    del style["style_overrides"][child_id]
    style["style_overrides"][parent["id"]]["color"] = "#778899"
    assert apply_scene_style_overrides(scene, style)["draw_atoms"][0]["_render_color"] == "#778899"


def test_part_ids_preserve_image_and_disorder_without_label_aliasing():
    atom = scene_fixture()["draw_atoms"][0]
    base = atom_parent_targets(atom)[-1]
    image = atom_parent_targets({**atom, "_image_shift": [1, 0, 0]})[-1]
    alternate = atom_parent_targets({**atom, "part": "B", "disorder_group": "B"})[-1]
    assert len({base["id"], image["id"], alternate["id"]}) == 3
    assert atom_parent_targets({"label": "unassigned", "elem": "C"}) == []


def test_two_meshes_keep_sibling_paint_and_reset_live():
    first = mesh_entity([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]], entity_id="one", color="#112233")
    second = {**first, "id": "two"}
    scene = {"geometry_entities": [first, second]}
    traces = geometry_entity_traces(scene, {"style_overrides": {"one": {"kind": "geometry", "color": "#aa6688", "opacity": .3, "edge_width": 4}}})
    surfaces = [t for t in traces if t.type == "mesh3d"]
    assert [t.color for t in surfaces] == ["#aa6688", "#112233"]
    assert [t.opacity for t in surfaces] == [.3, 1]
    assert len([t for t in traces if t.type == "scatter3d"]) == 1
    first["color"] = "#445566"
    assert geometry_entity_traces(scene)[0].color == "#445566"


def test_one_bfdh_facet_inherits_parent_fields_without_changing_sibling():
    scene = {"bfdh_morphology": {"enabled": True, "facets": [
        {"miller": [1, 0, 0], "centroid": [.3, .3, 0], "triangles": [[[0, 0, 0], [1, 0, 0], [0, 1, 0]]]},
        {"miller": [0, 1, 0], "centroid": [.3, 0, .3], "triangles": [[[0, 0, 0], [1, 0, 0], [0, 0, 1]]]},
    ]}}
    facet_id = facet_instance_id([1, 0, 0], 0, morphology_id="bfdh-morphology")
    style = {"style_overrides": {
        "morphology:bfdh-morphology": {"kind": "morphology", "color": "#778899", "opacity": .4},
        facet_id: {"kind": "facet", "color": "#aa6688", "edge_width": 3},
    }}
    surfaces = [t for t in _morphology_traces(scene, style) if t.type == "mesh3d"]
    assert {t.color for t in surfaces} == {"#aa6688", "#778899"}
    assert all(t.opacity == .4 for t in surfaces)
    assert {tuple(t.meta["facet_millers"][0]) for t in surfaces} == {(1, 0, 0), (0, 1, 0)}


def test_saved_sparse_style_round_trips_through_public_preset_api(tmp_path):
    scene = scene_fixture()
    atom_id = atom_instance_id(scene["draw_atoms"][0])
    style = {**DEFAULT_STYLE, "style_overrides": {atom_id: {"kind": "atom", "color": "#aa6688"}}}
    entry = scene_to_preset_entry(scene, style=style)
    path = tmp_path / "appearance.json"
    save_preset(str(path), {"scenes": {"example": entry}})
    assert load_preset(str(path))["scenes"]["example"]["style"]["style_overrides"] == style["style_overrides"]


def test_two_polyhedra_of_one_spec_keep_independent_paint_and_geometry():
    from mat_viewer.render.picking import ensure_polyhedron_instance_ids
    from mat_viewer.render.topology import (
        apply_topology_style_overrides,
        topology_background_traces,
    )
    topology = {"spec_results": [{"spec_id": "shared", "color": "#778899", "overlays": [
        {"center_coords": [shift, 0, 0], "center_source_index": index,
         "shell_coords": [[shift, 0, 1], [shift+1, 0, 0], [shift, 1, 0], [shift-1, -1, -1]],
         "center_label": f"M{index}", "visible": True}
        for index, shift in enumerate([0, 4])
    ]}]}
    ensure_polyhedron_instance_ids(topology)
    first, second = topology["spec_results"][0]["overlays"]
    style = {"style_overrides": {first["instance_id"]: {"kind": "polyhedron", "color": "#aa6688", "opacity": .25}}}
    painted = apply_topology_style_overrides(topology, style)
    one, two = painted["spec_results"][0]["overlays"]
    assert one["color"] == "#aa6688" and one["opacity"] == .25
    assert two["color"] == "#778899"
    assert one["shell_coords"] is first["shell_coords"]
    assert two["shell_coords"] is second["shell_coords"]
    assert apply_topology_style_overrides(topology, style) is painted
    traces = topology_background_traces(painted)
    assert {t.get("color") for t in traces if t.get("type") == "mesh3d"} >= {"#aa6688", "#778899"}


def test_kind_defaults_inherit_per_field_under_color_only_groups_and_hidden_bonds():
    from mat_viewer.style.atom_groups import tag_atoms_with_groups
    scene = scene_fixture()
    scene["draw_atoms"] = tag_atoms_with_groups(scene["draw_atoms"], [
        {"id": "red", "selector": {"all": True}, "color": "#aa6688"},
    ])
    painted = apply_scene_style_overrides(scene, {"entity_defaults": {"atom": {"opacity": .4}}})
    assert all(a["_render_opacity_scale"] == .4 for a in painted["draw_atoms"])
    style = {**DEFAULT_STYLE, "bond_groups": [
        {"id": "hide", "selector": {"all": True}, "visible": False},
    ]}
    figure = build_figure(scene_fixture(), style).to_plotly_json()
    assert all(t.get("meta", {}).get("mv_role") != "bond_selection" for t in figure["data"])


def test_live_element_palette_and_exact_override_reset_are_pure():
    scene = scene_fixture()
    original = deepcopy(scene["draw_atoms"])
    atom_id = atom_instance_id(scene["draw_atoms"][0])
    style = {**DEFAULT_STYLE, "element_colors": {"C": "#8299ac"},
             "style_overrides": {atom_id: {"kind": "atom", "color": "#aa6688"}}}
    painted = apply_scene_style_overrides(scene, style)
    assert painted["draw_atoms"][0]["_render_color"] == "#aa6688"
    assert painted["draw_atoms"][1]["color"] == "#8299ac"
    style["element_colors"]["C"] = "#667788"
    style["style_overrides"] = {}
    reset = apply_scene_style_overrides(scene, style)
    assert all(a["color"] == "#667788" for a in reset["draw_atoms"])
    assert scene["draw_atoms"] == original
    assert "#667788" in {t.get("color") for t in build_figure(scene, style).to_plotly_json()["data"]}


def test_part_only_atom_identity_is_not_aliased():
    atom = {"_source_index": 0, "label": "C1", "elem": "C"}
    assert atom_instance_id({**atom, "part": "A"}) != atom_instance_id({**atom, "part": "B"})
    assert atom_instance_id({**atom, "part": "0"}) == atom_instance_id(atom)
