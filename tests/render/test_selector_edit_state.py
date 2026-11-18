"""Empty by-element and between-elements edits must stay editable.

Normalization used to delete ``{"elements": []}`` and the bond editor
used to rewrite an empty endpoint list to ``{"all": True}``. A populated
rule must keep its previous meaning, and a mixed C/O fixture must show
that the chosen symbols are what change colour.
"""

from __future__ import annotations

import json
from pathlib import Path

from mat_viewer.app.callbacks_editors import _build_atom_groups, _build_bond_groups
from mat_viewer.app.editor_tables import (
    _BOND_GROUP_KIND_BETWEEN,
    _atom_groups_table_rows,
    _bond_groups_table_rows,
    _bond_selector_kind,
    _selector_kind,
)
from mat_viewer.app.normalizers import (
    _normalize_atom_group,
    _normalize_atom_groups,
    _normalize_bond_group,
    _normalize_bond_groups,
)
from mat_viewer.style.atom_groups import atom_matches_selector, tag_atoms_with_groups
from mat_viewer.style.bond_groups import bond_matches_selector, tag_bonds_with_groups

_MIXED_CIF = """data_mixed_cco
_cell_length_a 20
_cell_length_b 20
_cell_length_c 20
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_space_group_name_H-M_alt 'P 1'
loop_
_space_group_symop_operation_xyz
'x, y, z'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
C1 C 0.400 0.500 0.500 1
C2 C 0.475 0.500 0.500 1
O1 O 0.400 0.570 0.500 1
"""


def _walk(node):
    yield node
    children = getattr(node, "children", None)
    if isinstance(children, (list, tuple)):
        for child in children:
            yield from _walk(child)
    elif children is not None and not isinstance(children, (str, bytes)):
        yield from _walk(children)


def _component(rows, type_name: str):
    for node in _walk(rows[0]):
        node_id = getattr(node, "id", None)
        if isinstance(node_id, dict) and node_id.get("type") == type_name:
            return node
    raise AssertionError(f"missing {type_name}")


def test_empty_element_selector_is_kept_and_matches_nothing():
    group = _normalize_atom_group(
        {"id": "by-element", "selector": {"elements": []}, "color": "#00FFCC"},
        existing_ids=set(),
    )
    assert group is not None
    assert group["selector"] == {"elements": []}
    assert group["id"] == "by-element"
    assert atom_matches_selector({"elem": "C"}, group["selector"]) is False
    assert atom_matches_selector({"elem": "O"}, group["selector"]) is False
    assert _selector_kind(group["selector"]) == "elements"
    rows = _atom_groups_table_rows([group], [{"label": "C", "value": "C"}])
    picker = _component(rows, "ag-row-elements")
    assert picker.style["display"] == "block"
    assert picker.value == []


def test_empty_elements_do_not_drop_a_populated_rule():
    groups = _normalize_atom_groups(
        [
            {"id": "oxygens", "selector": {"elements": ["O"]}, "color": "#FF0000"},
            {"id": "pending", "selector": {"elements": []}, "color": "#00FFCC"},
        ]
    )
    by_id = {group["id"]: group for group in groups}
    assert by_id["oxygens"]["selector"] == {"elements": ["O"]}
    assert by_id["pending"]["selector"] == {"elements": []}
    # An empty list beside a real key is still dropped.
    kept = _normalize_atom_group(
        {"selector": {"elements": [], "labels": ["C1"]}},
        existing_ids=set(),
    )
    assert kept is not None
    assert kept["selector"] == {"labels": ["C1"]}


def test_empty_between_elements_is_not_rewritten_to_all():
    built = _build_bond_groups(
        color_ids=[{"group_id": "pending"}],
        visibles=[["yes"]],
        colors=["#FF00AA"],
        kinds=["between"],
        elements_lists=[[]],
        opacities=[1.0],
        radius_scales=[1.0],
    )
    assert built[0]["selector"] == {"between_elements": []}
    group = _normalize_bond_group(built[0], existing_ids=set())
    assert group is not None
    assert group["selector"] == {"between_elements": []}
    assert "all" not in group["selector"]
    assert _bond_selector_kind(group["selector"]) == _BOND_GROUP_KIND_BETWEEN
    bond = {"i": 0, "j": 1}
    atoms = [{"elem": "C", "label": "C1"}, {"elem": "O", "label": "O1"}]
    assert bond_matches_selector(bond, group["selector"], atoms=atoms) is False
    rows = _bond_groups_table_rows(
        [group],
        [{"label": "C", "value": "C"}, {"label": "O", "value": "O"}],
    )
    picker = _component(rows, "bg-row-elements")
    assert picker.style["display"] == "block"
    assert picker.value == []


def test_populated_between_elements_rule_is_unchanged():
    group = _normalize_bond_group(
        {"id": "oh", "selector": {"between_elements": ["O", "H"]}, "color": "#112233"},
        existing_ids=set(),
    )
    assert group is not None
    assert group["selector"] == {"between_elements": ["O", "H"]}
    groups = _normalize_bond_groups(
        [
            {"id": "oh", "selector": {"between_elements": ["O", "H"]}},
            {"id": "pending", "selector": {"between_elements": []}},
        ]
    )
    by_id = {item["id"]: item for item in groups}
    assert by_id["oh"]["selector"] == {"between_elements": ["O", "H"]}
    assert by_id["pending"]["selector"] == {"between_elements": []}


def test_editor_builds_an_empty_element_rule_without_deleting_it():
    built = _build_atom_groups(
        color_ids=[{"group_id": "pending"}],
        visibles=[["yes"]],
        colors=["#00FFCC"],
        kinds=["elements"],
        elements_lists=[[]],
        opacities=[1.0],
        materials=["__inherit__"],
        styles=["__inherit__"],
    )
    assert built[0]["selector"] == {"elements": []}
    normalized = _normalize_atom_groups(built)
    assert len(normalized) == 1
    assert normalized[0]["selector"] == {"elements": []}
    assert normalized[0]["id"] == "pending"


def test_mixed_cco_rules_color_only_the_selected_targets(tmp_path: Path):
    from mat_viewer.app import ViewerBackend
    from mat_viewer.loader import build_loaded_crystal
    from mat_viewer.presets import default_preset_path

    cif_path = tmp_path / "mixed.cco.cif"
    cif_path.write_text(_MIXED_CIF, encoding="utf-8")
    bundle = build_loaded_crystal(
        name="mixed-cco",
        cif_path=str(cif_path),
        title="mixed-cco",
        source="upload",
    )
    scene = bundle.scene
    atoms = scene["draw_atoms"]
    bonds = scene["bonds"]
    assert {atom["elem"] for atom in atoms} == {"C", "O"}
    pairs = {
        tuple(sorted((atoms[bond["i"]]["elem"], atoms[bond["j"]]["elem"])))
        for bond in bonds
    }
    assert pairs == {("C", "C"), ("C", "O")}

    atom_groups = _normalize_atom_groups(
        [{"id": "carbons", "selector": {"elements": ["C"]}, "color": "#00FFCC"}]
    )
    tagged_atoms = tag_atoms_with_groups(atoms, atom_groups)
    by_label = {atom["label"]: atom for atom in tagged_atoms}
    assert by_label["C1"]["_render_color"] == "#00ffcc"
    assert by_label["C2"]["_render_color"] == "#00ffcc"
    assert by_label["O1"]["_render_color"] is None

    bond_groups = _normalize_bond_groups(
        [
            {
                "id": "co",
                "selector": {"between_elements": ["C", "O"]},
                "color": "#FF00AA",
            }
        ]
    )
    tagged_bonds = tag_bonds_with_groups(bonds, bond_groups, atoms=atoms)
    colored = []
    plain = []
    for bond in tagged_bonds:
        pair = tuple(sorted((atoms[bond["i"]]["label"], atoms[bond["j"]]["label"])))
        if bond.get("_render_color"):
            colored.append(pair)
            assert bond["_render_color"] == "#ff00aa"
        else:
            plain.append(pair)
    assert colored == [("C1", "O1")]
    assert plain == [("C1", "C2")]

    backend = ViewerBackend(preset_path=default_preset_path(), root_dir=str(tmp_path))
    backend.add_uploaded_file_bytes(cif_path.read_bytes(), "mixed.cco.cif")
    backend.commit_patch(
        {
            "atom_groups": [
                {"id": "oxygens", "selector": {"elements": ["O"]}, "color": "#FF0000"},
                {"id": "pending", "selector": {"elements": []}, "color": "#00FFCC"},
            ],
            "bond_groups": [
                {"id": "pending-bond", "selector": {"between_elements": []}},
            ],
        }
    )
    state = backend.get_state()
    atom_by_id = {group["id"]: group for group in state["atom_groups"]}
    assert atom_by_id["oxygens"]["selector"] == {"elements": ["O"]}
    assert atom_by_id["pending"]["selector"] == {"elements": []}
    assert state["bond_groups"][0]["selector"] == {"between_elements": []}
    backend.flush_scene_store()
    stored = json.loads((tmp_path / "isolated-scenes.json").read_text(encoding="utf-8"))
    stored_groups = []
    def _collect(node):
        if isinstance(node, dict):
            selector = node.get("selector")
            if isinstance(selector, dict) and (
                "elements" in selector or "between_elements" in selector
            ):
                stored_groups.append(selector)
            for value in node.values():
                _collect(value)
        elif isinstance(node, list):
            for value in node:
                _collect(value)
    _collect(stored)
    assert {"elements": []} in stored_groups
    assert {"elements": ["O"]} in stored_groups
    assert {"between_elements": []} in stored_groups

    backend.update_atom_group("pending", {"selector": {"elements": ["C"]}})
    assert backend.list_atom_groups()
    pending = next(group for group in backend.list_atom_groups() if group["id"] == "pending")
    assert pending["selector"] == {"elements": ["C"]}
    assert backend.remove_atom_group("pending") is True
    assert "pending" not in {group["id"] for group in backend.list_atom_groups()}
    assert any(group["id"] == "oxygens" for group in backend.list_atom_groups())
    backend.close()
