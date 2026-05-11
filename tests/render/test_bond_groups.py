"""Phase 4: bond_groups model -- selectors, tagging, renderer integration.

A bond_group rule applies a selector
(``{"all": True}`` or ``{"between_elements": [...]}`` or
``{"labels": ["Pb1-Cl3"]}`` or ``{"is_minor": bool}``) plus optional
override fields (``color``, ``visible``, ``opacity``, ``radius_scale``).
``tag_bonds_with_groups`` writes per-bond ``_render_*`` fields that the
renderer's ``_bond_segments`` then consumes.

DO NOT REMOVE -- this guards the contract documented in
``agents/bond_groups_api.md``.
"""
from __future__ import annotations

import numpy as np
import pytest

from crystal_viewer.bond_groups import (
    bond_groups_cache_key,
    bond_matches_selector,
    tag_bonds_with_groups,
)


@pytest.fixture
def atoms_and_bonds():
    atoms = [
        {"label": "Pb1", "elem": "Pb"},
        {"label": "Cl1", "elem": "Cl"},
        {"label": "Cl2", "elem": "Cl"},
        {"label": "O1", "elem": "O"},
        {"label": "H1", "elem": "H"},
    ]
    bonds = [
        {"i": 0, "j": 1, "color_i": "#888", "color_j": "#0F0", "alpha_i": 1.0,
         "alpha_j": 1.0, "is_minor": False, "start": np.zeros(3), "end": np.array([1, 0, 0])},
        {"i": 0, "j": 2, "color_i": "#888", "color_j": "#0F0", "alpha_i": 1.0,
         "alpha_j": 1.0, "is_minor": True, "start": np.zeros(3), "end": np.array([0, 1, 0])},
        {"i": 3, "j": 4, "color_i": "#F00", "color_j": "#FFF", "alpha_i": 1.0,
         "alpha_j": 1.0, "is_minor": False, "start": np.zeros(3), "end": np.array([0, 0, 1])},
    ]
    return atoms, bonds


# ---- bond_matches_selector ---------------------------------------------


def test_all_selector_matches_every_bond(atoms_and_bonds):
    atoms, bonds = atoms_and_bonds
    for bond in bonds:
        assert bond_matches_selector(bond, {"all": True}, atoms=atoms)


def test_between_elements_pair_matches_either_ordering(atoms_and_bonds):
    atoms, bonds = atoms_and_bonds
    selector = {"between_elements": ["O", "H"]}
    assert bond_matches_selector(bonds[2], selector, atoms=atoms)
    assert not bond_matches_selector(bonds[0], selector, atoms=atoms)


def test_between_elements_single_element_matches_homo_pair(atoms_and_bonds):
    atoms, bonds = atoms_and_bonds
    homo = {"i": 1, "j": 2, "color_i": "#0F0", "color_j": "#0F0", "alpha_i": 1.0,
            "alpha_j": 1.0, "is_minor": False, "start": np.zeros(3), "end": np.array([1, 1, 0])}
    selector = {"between_elements": ["Cl"]}
    assert bond_matches_selector(homo, selector, atoms=atoms)
    assert not bond_matches_selector(bonds[0], selector, atoms=atoms)


def test_between_elements_three_elements_matches_set(atoms_and_bonds):
    """``["Cl", "Br", "I"]`` should match a bond whose endpoints are
    each in the listed halide set, even if neither pair is exact."""
    atoms, bonds = atoms_and_bonds
    selector = {"between_elements": ["Pb", "Cl", "Br"]}
    assert bond_matches_selector(bonds[0], selector, atoms=atoms)
    assert not bond_matches_selector(bonds[2], selector, atoms=atoms)


def test_labels_selector_matches_either_ordering(atoms_and_bonds):
    atoms, bonds = atoms_and_bonds
    assert bond_matches_selector(bonds[0], {"labels": ["Pb1-Cl1"]}, atoms=atoms)
    assert bond_matches_selector(bonds[0], {"labels": ["Cl1-Pb1"]}, atoms=atoms)
    assert not bond_matches_selector(bonds[2], {"labels": ["Pb1-Cl1"]}, atoms=atoms)


def test_is_minor_selector(atoms_and_bonds):
    atoms, bonds = atoms_and_bonds
    minor_only = {"is_minor": True}
    assert bond_matches_selector(bonds[1], minor_only, atoms=atoms)
    assert not bond_matches_selector(bonds[0], minor_only, atoms=atoms)


def test_unknown_selector_keys_match_nothing(atoms_and_bonds):
    atoms, bonds = atoms_and_bonds
    # Empty / unknown selector matches nothing (renderer-side belt and braces).
    assert not bond_matches_selector(bonds[0], {"frobnicate": True}, atoms=atoms)
    assert not bond_matches_selector(bonds[0], {}, atoms=atoms)


# ---- tag_bonds_with_groups ---------------------------------------------


def test_tag_bonds_writes_render_color_visible_opacity_radius(atoms_and_bonds):
    atoms, bonds = atoms_and_bonds
    groups = [
        {
            "id": "g1",
            "selector": {"between_elements": ["O", "H"]},
            "color": "#FF00FF",
            "opacity": 0.5,
            "radius_scale": 1.5,
        }
    ]
    tagged = tag_bonds_with_groups(bonds, groups, atoms=atoms)
    matching = next(t for t in tagged if t.get("i") == 3 and t.get("j") == 4)
    other = next(t for t in tagged if t.get("i") == 0 and t.get("j") == 1)
    assert matching["_render_color"] == "#FF00FF"
    assert matching["_render_opacity_scale"] == pytest.approx(0.5)
    assert matching["_render_radius_scale"] == pytest.approx(1.5)
    assert other["_render_color"] is None
    assert other["_render_radius_scale"] == pytest.approx(1.0)


def test_tag_bonds_later_rule_wins(atoms_and_bonds):
    atoms, bonds = atoms_and_bonds
    groups = [
        {"id": "g1", "selector": {"all": True}, "color": "#000000"},
        {"id": "g2", "selector": {"between_elements": ["O", "H"]}, "color": "#FF0000"},
    ]
    tagged = tag_bonds_with_groups(bonds, groups, atoms=atoms)
    pb_cl = next(t for t in tagged if t.get("i") == 0 and t.get("j") == 1)
    o_h = next(t for t in tagged if t.get("i") == 3 and t.get("j") == 4)
    assert pb_cl["_render_color"] == "#000000"
    assert o_h["_render_color"] == "#FF0000"


def test_tag_bonds_visible_false_drops_render(atoms_and_bonds):
    atoms, bonds = atoms_and_bonds
    groups = [{"id": "g1", "selector": {"all": True}, "visible": False}]
    tagged = tag_bonds_with_groups(bonds, groups, atoms=atoms)
    assert all(t["_render_visible"] is False for t in tagged)


# ---- renderer integration ----------------------------------------------
#
# ``_bond_segments`` (with_scales=True) is the layer the cylinder
# builder uses. A bond with ``_render_visible=False`` must vanish; a
# bond with ``_render_color`` must use that colour for both halves
# (overriding the per-atom colour); per-bond radius/opacity scales
# must be yielded for downstream bucketing.


def test_bond_segments_skips_invisible_bonds(atoms_and_bonds):
    from crystal_viewer.renderer import _bond_segments

    atoms, bonds = atoms_and_bonds
    bonds = [dict(b) for b in bonds]
    bonds[0]["_render_visible"] = False
    scene = {"draw_atoms": atoms, "bonds": bonds}
    style = {"bond_radius": 0.1}
    segments = list(_bond_segments(scene, style))
    # bond 0 (Pb1-Cl1) is hidden; bonds 1 (Pb1-Cl2 minor) and 2 (O1-H1)
    # each contribute two halves -> 4 segments total.
    assert len(segments) == 4


def test_bond_segments_render_color_overrides_atom_colors(atoms_and_bonds):
    from crystal_viewer.renderer import _bond_segments

    atoms, bonds = atoms_and_bonds
    tagged = tag_bonds_with_groups(
        bonds,
        [{"id": "g1", "selector": {"between_elements": ["O", "H"]}, "color": "#FF00FF"}],
        atoms=atoms,
    )
    scene = {"draw_atoms": atoms, "bonds": tagged}
    style = {"bond_radius": 0.1}
    segments = list(_bond_segments(scene, style))
    # The O-H bond (last bond) yields two halves both painted with the
    # override colour.
    matching = [seg for seg in segments if seg[0] == "#FF00FF"]
    assert len(matching) == 2


def test_bond_segments_yields_per_bond_scales(atoms_and_bonds):
    from crystal_viewer.renderer import _bond_segments

    atoms, bonds = atoms_and_bonds
    tagged = tag_bonds_with_groups(
        bonds,
        [{"id": "g1", "selector": {"between_elements": ["O", "H"]}, "radius_scale": 2.5, "opacity": 0.4}],
        atoms=atoms,
    )
    scene = {"draw_atoms": atoms, "bonds": tagged}
    style = {"bond_radius": 0.1}
    segments_with_scales = list(_bond_segments(scene, style, with_scales=True))
    # Default radius_scale=1.0, opacity=1.0 for non-matching bonds; 2.5 / 0.4
    # for the matching O-H bond's two halves.
    assert any(seg[4] == pytest.approx(2.5) and seg[5] == pytest.approx(0.4) for seg in segments_with_scales)
    assert any(seg[4] == pytest.approx(1.0) and seg[5] == pytest.approx(1.0) for seg in segments_with_scales)


# ---- cache key ---------------------------------------------------------


def test_bond_groups_cache_key_stable_across_id_rename():
    a = [{"id": "g1", "name": "before", "selector": {"all": True}, "color": "#000000"}]
    b = [{"id": "g1", "name": "AFTER", "selector": {"all": True}, "color": "#000000"}]
    assert bond_groups_cache_key(a) == bond_groups_cache_key(b)


def test_bond_groups_cache_key_changes_on_color_change():
    a = [{"id": "g1", "selector": {"all": True}, "color": "#000000"}]
    b = [{"id": "g1", "selector": {"all": True}, "color": "#FFFFFF"}]
    assert bond_groups_cache_key(a) != bond_groups_cache_key(b)
