from __future__ import annotations

import json

import numpy as np
import pytest

from mat_viewer.cli import main
from mat_viewer.render.cli_groups import parse_group_arguments
from mat_viewer.render.contracts import LinePrimitive
from mat_viewer.render.planning import prepare_render
from mat_viewer.style.bond_groups import bond_groups_cache_key


def _mixed_scene() -> dict:
    atoms = [
        {
            "elem": "C",
            "label": "C1",
            "cart": [0.0, 0.0, 0.0],
            "_molecule_index": 0,
        },
        {
            "elem": "N",
            "label": "N1",
            "cart": [1.0, 0.0, 0.0],
            "_molecule_index": 0,
        },
        {
            "elem": "Si",
            "label": "Si1",
            "cart": [0.0, 2.0, 0.0],
            "_molecule_index": 1,
        },
        {
            "elem": "O",
            "label": "O1",
            "cart": [1.0, 2.0, 0.0],
            "_molecule_index": 1,
        },
    ]
    bonds = [
        {"i": 0, "j": 1, "start": [0, 0, 0], "end": [1, 0, 0]},
        {"i": 2, "j": 3, "start": [0, 2, 0], "end": [1, 2, 0]},
    ]
    return {"atoms": atoms, "bonds": bonds, "matrix": np.eye(3) * 4.0}


def test_compact_group_grammar_preserves_later_wins_order() -> None:
    groups = parse_group_arguments(
        [
            ["all", "color=#777777"],
            [
                "element:O+major",
                "style=space_filling",
                "opacity=0.7",
                "material=flat",
            ],
        ],
        kind="atom",
    )

    assert [group["id"] for group in groups] == ["cli-atom-1", "cli-atom-2"]
    assert groups[1]["selector"] == {
        "elements": ["O"],
        "is_minor": False,
    }
    assert groups[1]["style"] == "space_filling"


def test_molecule_style_selects_only_internal_component_bonds() -> None:
    plan = prepare_render(
        _mixed_scene(),
        render={"representation": "ball", "show_cell": False},
        atom_groups=[
            {
                "selector": {"molecule_indices": [0]},
                "style": "ball_stick",
            }
        ],
    )

    bond_ids = [
        primitive.semantic_id
        for primitive in plan.primitives
        if primitive.semantic_id.startswith("bond:")
    ]
    assert bond_ids
    assert all(":0-1" in semantic_id for semantic_id in bond_ids)
    assert not any(":2-3" in semantic_id for semantic_id in bond_ids)


def test_bond_group_can_switch_one_pair_to_wireframe() -> None:
    plan = prepare_render(
        _mixed_scene(),
        render={"representation": "ball_stick", "show_cell": False},
        bond_groups=[
            {
                "selector": {"between_elements": ["C", "N"]},
                "style": "wireframe",
                "color": "#336699",
                "radius_scale": 2.0,
            }
        ],
    )

    lines = [
        primitive
        for primitive in plan.primitives
        if isinstance(primitive, LinePrimitive)
        and primitive.semantic_id.startswith("bond:")
    ]
    assert [line.semantic_id for line in lines] == ["bond:0:0-1"]
    assert lines[0].width_px == pytest.approx(2.4)
    assert any(
        primitive.semantic_id.startswith("bond:1:2-3") for primitive in plan.primitives
    )


def test_space_filling_scene_does_not_emit_bonds() -> None:
    plan = prepare_render(
        _mixed_scene(),
        render={"representation": "space_filling", "show_cell": False},
    )

    assert not any(
        primitive.semantic_id.startswith("bond:") for primitive in plan.primitives
    )


def test_cli_check_receipt_contains_normalized_groups(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(
        [
            "render",
            "structure.xyz",
            "-o",
            str(tmp_path / "figure.png"),
            "--check",
            "--json",
            "--atom-group",
            "molecule:0",
            "style=ball_stick",
            "--bond-group",
            "between:C,H",
            "visible=false",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["style_groups"]["atom"][0]["selector"] == {"molecule_indices": [0]}
    assert payload["style_groups"]["bond"][0]["visible"] is False


def test_bond_label_selector_and_cache_key_are_ordered_strings() -> None:
    groups = parse_group_arguments(
        [["label:A1-B2,B3-C4", "visible=false"]],
        kind="bond",
    )

    assert groups[0]["selector"]["labels"] == ["A1-B2", "B3-C4"]
    assert bond_groups_cache_key(groups) != bond_groups_cache_key(
        parse_group_arguments(
            [["label:A1-B2,D5-E6", "visible=false"]],
            kind="bond",
        )
    )


def test_cli_group_grammar_covers_every_public_api_field() -> None:
    atom_groups = parse_group_arguments(
        [
            ["all", "visible=true"],
            [
                (
                    "element:C+label:C1+index:0+fragment:A0+"
                    "fragment-index:2+molecule:1+minor"
                ),
                "color=#112233",
                "color_light=#445566",
                "visible=false",
                "opacity=0.5",
                "style=ball_stick",
                "material=flat",
            ],
        ],
        kind="atom",
    )
    atom_selector_keys = {key for group in atom_groups for key in group["selector"]}
    atom_override_keys = {
        key
        for group in atom_groups
        for key in group
        if key not in {"id", "name", "selector", "enabled"}
    }
    assert atom_selector_keys == {
        "all",
        "elements",
        "is_minor",
        "labels",
        "atom_indices",
        "fragment_labels",
        "fragment_indices",
        "molecule_indices",
    }
    assert atom_override_keys == {
        "color",
        "color_light",
        "visible",
        "opacity",
        "style",
        "material",
    }

    bond_groups = parse_group_arguments(
        [
            ["all", "visible=true"],
            [
                "between:C,N+label:C1-N1+major",
                "color=#112233",
                "visible=false",
                "opacity=0.5",
                "style=wireframe",
                "radius_scale=1.5",
            ],
        ],
        kind="bond",
    )
    bond_selector_keys = {key for group in bond_groups for key in group["selector"]}
    bond_override_keys = {
        key
        for group in bond_groups
        for key in group
        if key not in {"id", "name", "selector", "enabled"}
    }
    assert bond_selector_keys == {"all", "between_elements", "labels", "is_minor"}
    assert bond_override_keys == {
        "color",
        "visible",
        "opacity",
        "style",
        "radius_scale",
    }
