from __future__ import annotations

from mat_viewer.app.normalizers import _normalize_atom_group, _normalize_bond_group


def test_atom_normalizer_preserves_cli_parity_fields() -> None:
    group = _normalize_atom_group(
        {
            "selector": {"molecule_indices": ["2", "bad"]},
            "style": "space_filling",
        },
        existing_ids=set(),
    )

    assert group is not None
    assert group["selector"] == {"molecule_indices": [2]}
    assert group["style"] == "space_filling"


def test_bond_normalizer_preserves_style_and_label_selector() -> None:
    group = _normalize_bond_group(
        {
            "selector": {"labels": ["A1-B2"]},
            "style": "wireframe",
        },
        existing_ids=set(),
    )

    assert group is not None
    assert group["selector"] == {"labels": ["A1-B2"]}
    assert group["style"] == "wireframe"
