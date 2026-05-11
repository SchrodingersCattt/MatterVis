"""Phase 4 atom-group selectors: ``labels``, ``atom_indices``,
``fragment_labels``, ``fragment_indices``.

The original ``elements`` / ``is_minor`` / ``all`` selectors are
covered by ``tests/render/test_atom_groups_renderer.py``; this file
guards the additive Phase-4 selectors that the right-click "set this
one cyan" path and AI scripting agents rely on.
"""
from __future__ import annotations

import pytest

from crystal_viewer.atom_groups import atom_matches_selector, tag_atoms_with_groups
from crystal_viewer.app import _coerce_atom_selector


# ---- _coerce_atom_selector --------------------------------------------


def test_coerce_atom_selector_accepts_labels_list():
    selector = _coerce_atom_selector({"labels": ["Pb1", "Cl3"]})
    assert selector == {"labels": ["Pb1", "Cl3"]}


def test_coerce_atom_selector_accepts_atom_indices():
    selector = _coerce_atom_selector({"atom_indices": [0, 1, "2"]})
    assert selector == {"atom_indices": [0, 1, 2]}


def test_coerce_atom_selector_accepts_fragment_labels_and_indices():
    selector = _coerce_atom_selector(
        {"fragment_labels": ["A0", "B1"], "fragment_indices": [0, 5]}
    )
    assert selector["fragment_labels"] == ["A0", "B1"]
    assert selector["fragment_indices"] == [0, 5]


def test_coerce_atom_selector_drops_empty_lists():
    """Empty lists in keys must NOT register the key. Otherwise an
    AND-combined selector with an empty ``labels`` filter would
    accidentally exclude every atom."""
    selector = _coerce_atom_selector({"labels": [], "elements": ["O"]})
    assert "labels" not in selector
    assert selector["elements"] == ["O"]


# ---- atom_matches_selector ---------------------------------------------


def test_labels_selector_matches_by_atom_label():
    atom = {"label": "Pb1", "elem": "Pb", "is_minor": False}
    assert atom_matches_selector(atom, {"labels": ["Pb1", "Cl3"]})
    assert not atom_matches_selector(atom, {"labels": ["Cl3"]})


def test_atom_indices_selector_requires_index_threaded():
    atom = {"label": "Pb1", "elem": "Pb", "is_minor": False}
    # Without atom_index threaded -> selector is silently a no-op.
    assert not atom_matches_selector(atom, {"atom_indices": [0]})
    # With atom_index -> match.
    assert atom_matches_selector(atom, {"atom_indices": [3]}, atom_index=3)
    assert not atom_matches_selector(atom, {"atom_indices": [4]}, atom_index=3)


def test_fragment_labels_selector_requires_label_threaded():
    atom = {"label": "Pb1", "elem": "Pb", "is_minor": False}
    assert not atom_matches_selector(atom, {"fragment_labels": ["B0"]})
    assert atom_matches_selector(
        atom, {"fragment_labels": ["B0"]}, fragment_label="B0"
    )
    assert not atom_matches_selector(
        atom, {"fragment_labels": ["B0"]}, fragment_label="B1"
    )


def test_fragment_indices_selector_accepts_int_label():
    """``fragment_indices`` reads the threaded label; we accept both
    string ("B0" -> rejected because not all-digit) and bare integer
    forms ("0" / 0)."""
    atom = {"label": "Pb1", "elem": "Pb", "is_minor": False}
    assert atom_matches_selector(
        atom, {"fragment_indices": [0]}, fragment_label="0"
    )
    assert atom_matches_selector(
        atom, {"fragment_indices": [0, 5]}, fragment_label=5
    )
    assert not atom_matches_selector(
        atom, {"fragment_indices": [0]}, fragment_label="B0"
    )


def test_combined_label_and_element_filter_is_AND():
    """Multiple keys in one selector are intersected (AND) -- the
    atom must satisfy every key."""
    pb = {"label": "Pb1", "elem": "Pb", "is_minor": False}
    cl = {"label": "Cl1", "elem": "Cl", "is_minor": False}
    selector = {"labels": ["Pb1", "Cl1"], "elements": ["Pb"]}
    assert atom_matches_selector(pb, selector)
    assert not atom_matches_selector(cl, selector)


# ---- tag_atoms_with_groups threads fragment_labels --------------------


def test_tag_atoms_threads_fragment_labels():
    atoms = [
        {"label": "Pb1", "elem": "Pb", "color": "#888", "color_light": "#aaa", "is_minor": False},
        {"label": "Cl1", "elem": "Cl", "color": "#0F0", "color_light": "#0F0", "is_minor": False},
    ]
    tagged = tag_atoms_with_groups(
        atoms,
        [
            {
                "id": "g1",
                "selector": {"fragment_labels": ["B0"]},
                "color": "#FF0000",
            }
        ],
        fragment_labels=["B0", "X0"],
    )
    assert tagged[0]["_render_color"] == "#FF0000"
    assert tagged[1]["_render_color"] is None
