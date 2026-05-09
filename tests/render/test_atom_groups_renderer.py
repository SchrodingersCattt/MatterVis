"""Phase 2 atom_groups -- renderer integration.

Pins the per-atom override semantics:

  * ``tag_atoms_with_groups`` writes ``_render_color``,
    ``_render_visible``, ``_render_opacity_scale``,
    ``_render_material``, and ``_render_style`` based on group rules
    in list order, with later-wins semantics.
  * The renderer's ``_atom_render_color`` / ``_atom_render_visible``
    helpers honour those overrides while falling back to the legacy
    monochrome flag and element palette when no rule matches.
  * Bonds touching a hidden atom are dropped from
    ``_bond_segments``; the renderer never emits a half-bond going
    to nowhere.
  * The figure-JSON cache key in ``_cached_atom_bond_meshes`` extends
    to a hash of atom_groups so changing a group rule invalidates the
    trace cache.

DO NOT REMOVE -- atom_groups is the user-visible deliverable for the
"replace monochromatic with per-group rendering" Phase 2 work.
"""
from __future__ import annotations

import numpy as np

from crystal_viewer.atom_groups import (
    atom_matches_selector,
    partition_atoms_by_render_pipeline,
    tag_atoms_with_groups,
)
from crystal_viewer.renderer import (
    _atom_groups_cache_key,
    _atom_render_color,
    _atom_render_opacity_scale,
    _atom_render_visible,
    _bond_segments,
)


def _atoms():
    return [
        {"label": "O1", "elem": "O", "cart": [0, 0, 0], "color": "#FF0D0D", "color_light": "#FF8080", "is_minor": False, "atom_radius": 0.16},
        {"label": "C1", "elem": "C", "cart": [1, 0, 0], "color": "#909090", "color_light": "#BFBFBF", "is_minor": False, "atom_radius": 0.18},
        {"label": "H1", "elem": "H", "cart": [2, 0, 0], "color": "#FFFFFF", "color_light": "#EEEEEE", "is_minor": False, "atom_radius": 0.12},
    ]


# ---- selector match ------------------------------------------------------


def test_atom_matches_selector_all():
    atom = {"elem": "C"}
    assert atom_matches_selector(atom, {"all": True}) is True


def test_atom_matches_selector_elements_membership():
    atom = {"elem": "O"}
    assert atom_matches_selector(atom, {"elements": ["O", "S"]}) is True
    assert atom_matches_selector(atom, {"elements": ["N"]}) is False


def test_atom_matches_selector_combined_keys_use_AND():
    atom = {"elem": "O", "is_minor": True}
    assert atom_matches_selector(atom, {"elements": ["O"], "is_minor": True}) is True
    assert atom_matches_selector(atom, {"elements": ["O"], "is_minor": False}) is False


def test_atom_matches_selector_empty_dict_matches_nothing():
    # Defence in depth: if a non-normalised selector reaches the
    # renderer it must not silently match every atom.
    assert atom_matches_selector({"elem": "C"}, {}) is False


# ---- tag_atoms_with_groups ----------------------------------------------


def test_tag_atoms_no_groups_leaves_render_color_unset():
    tagged = tag_atoms_with_groups(_atoms(), [])
    for atom in tagged:
        assert atom["_render_color"] is None
        assert atom["_render_color_light"] is None
        assert atom["_render_visible"] is True
        assert atom["_render_opacity_scale"] == 1.0
        assert atom["_render_material"] is None
        assert atom["_render_style"] is None


def test_tag_atoms_later_group_wins_on_overlap():
    # Two rules: "all -> grey", then "O -> red". Oxygen should end
    # up red; the rest stays grey.
    groups = [
        {"selector": {"all": True}, "color": "#888888"},
        {"selector": {"elements": ["O"]}, "color": "#FF0000"},
    ]
    tagged = tag_atoms_with_groups(_atoms(), groups)
    by_label = {atom["label"]: atom for atom in tagged}
    assert by_label["O1"]["_render_color"] == "#FF0000"
    assert by_label["C1"]["_render_color"] == "#888888"
    assert by_label["H1"]["_render_color"] == "#888888"


def test_tag_atoms_visible_false_is_recorded():
    groups = [{"selector": {"elements": ["H"]}, "visible": False}]
    tagged = tag_atoms_with_groups(_atoms(), groups)
    by_label = {atom["label"]: atom for atom in tagged}
    assert by_label["H1"]["_render_visible"] is False
    assert by_label["O1"]["_render_visible"] is True


def test_tag_atoms_opacity_replace_not_multiply():
    # Two rules: all -> 0.5, O -> 0.3. Replace semantics means O
    # ends up at 0.3 (not 0.5*0.3=0.15) and others at 0.5.
    groups = [
        {"selector": {"all": True}, "opacity": 0.5},
        {"selector": {"elements": ["O"]}, "opacity": 0.3},
    ]
    tagged = tag_atoms_with_groups(_atoms(), groups)
    by_label = {atom["label"]: atom for atom in tagged}
    assert by_label["O1"]["_render_opacity_scale"] == 0.3
    assert by_label["C1"]["_render_opacity_scale"] == 0.5


def test_tag_atoms_material_style_overrides_propagate():
    groups = [{"selector": {"elements": ["O"]}, "material": "flat", "style": "ortep"}]
    tagged = tag_atoms_with_groups(_atoms(), groups)
    by_label = {atom["label"]: atom for atom in tagged}
    assert by_label["O1"]["_render_material"] == "flat"
    assert by_label["O1"]["_render_style"] == "ortep"
    assert by_label["C1"]["_render_material"] is None


# ---- partitioning --------------------------------------------------------


def test_partition_drops_hidden_atoms():
    atoms = _atoms()
    atoms[2]["_render_visible"] = False  # H1 hidden
    atoms[2]["_render_material"] = None
    atoms[2]["_render_style"] = None
    for a in atoms[:2]:
        a["_render_visible"] = True
        a["_render_material"] = None
        a["_render_style"] = None
    buckets = partition_atoms_by_render_pipeline(atoms, scene_material="mesh", scene_style="ball_stick")
    assert ("mesh", "ball_stick") in buckets
    visible_labels = {a["label"] for atoms_in in buckets.values() for a in atoms_in}
    assert "H1" not in visible_labels
    assert "O1" in visible_labels


def test_partition_groups_by_effective_material_style():
    atoms = _atoms()
    for a in atoms:
        a["_render_visible"] = True
        a["_render_material"] = None
        a["_render_style"] = None
    atoms[0]["_render_material"] = "flat"  # O1 rendered flat
    buckets = partition_atoms_by_render_pipeline(atoms, scene_material="mesh", scene_style="ball_stick")
    assert ("flat", "ball_stick") in buckets and ("mesh", "ball_stick") in buckets
    assert {a["label"] for a in buckets[("flat", "ball_stick")]} == {"O1"}
    assert {a["label"] for a in buckets[("mesh", "ball_stick")]} == {"C1", "H1"}


# ---- renderer helper fallbacks ------------------------------------------


def test_atom_render_color_falls_back_to_element_color_when_no_override():
    atom = {"color": "#FF0D0D", "color_light": "#FF8080"}
    style = {}
    assert _atom_render_color(atom, style) == "#FF0D0D"
    assert _atom_render_color(atom, style, light=True) == "#FF8080"


def test_atom_render_color_monochrome_still_blackens_when_no_override():
    atom = {"color": "#FF0D0D", "color_light": "#FF8080"}
    assert _atom_render_color(atom, {"monochrome": True}) == "#000000"


def test_atom_render_color_override_beats_monochrome():
    atom = {"color": "#FF0D0D", "color_light": "#FF8080", "_render_color": "#00FF00"}
    assert _atom_render_color(atom, {"monochrome": True}) == "#00FF00"


def test_atom_render_visible_default_true():
    assert _atom_render_visible({}) is True
    assert _atom_render_visible({"_render_visible": False}) is False


def test_atom_render_opacity_scale_default_one():
    assert _atom_render_opacity_scale({}) == 1.0
    assert _atom_render_opacity_scale({"_render_opacity_scale": 0.4}) == 0.4
    assert _atom_render_opacity_scale({"_render_opacity_scale": 7.0}) == 1.0


# ---- bond filtering ------------------------------------------------------


def test_bond_segments_drops_bonds_touching_hidden_atoms():
    atoms = [
        {"label": "O1", "elem": "O", "cart": [0, 0, 0], "color": "#FF0D0D", "color_light": "#FF8080", "is_minor": False, "atom_radius": 0.16, "_render_visible": True},
        {"label": "H1", "elem": "H", "cart": [1, 0, 0], "color": "#FFFFFF", "color_light": "#EEEEEE", "is_minor": False, "atom_radius": 0.12, "_render_visible": False},
    ]
    scene = {
        "draw_atoms": atoms,
        "bonds": [
            {
                "i": 0, "j": 1,
                "start": np.array([0.0, 0.0, 0.0]),
                "end": np.array([1.0, 0.0, 0.0]),
                "color_i": "#FF0D0D", "color_j": "#FFFFFF",
                "is_minor": False,
            }
        ],
    }
    style = {"bond_radius": 0.1}
    segs = list(_bond_segments(scene, style))
    assert segs == [], "bond touching hidden atom must not be rendered"


def test_bond_segments_uses_render_color_override_for_endpoint_halves():
    atoms = [
        {"label": "O1", "elem": "O", "cart": [0, 0, 0], "color": "#FF0D0D", "color_light": "#FF8080", "is_minor": False, "atom_radius": 0.16, "_render_visible": True, "_render_color": "#00FF00"},
        {"label": "C1", "elem": "C", "cart": [1, 0, 0], "color": "#909090", "color_light": "#BFBFBF", "is_minor": False, "atom_radius": 0.18, "_render_visible": True},
    ]
    scene = {
        "draw_atoms": atoms,
        "bonds": [
            {
                "i": 0, "j": 1,
                "start": np.array([0.0, 0.0, 0.0]),
                "end": np.array([1.0, 0.0, 0.0]),
                "color_i": "#FF0D0D", "color_j": "#909090",
                "is_minor": False,
            }
        ],
    }
    style = {"bond_radius": 0.1}
    segs = list(_bond_segments(scene, style))
    assert len(segs) == 2
    half_colors = {color for color, *_ in segs}
    assert "#00FF00" in half_colors


# ---- cache key invalidation ----------------------------------------------


def test_atom_groups_cache_key_is_stable_for_equal_groups():
    g1 = [{"id": "a", "selector": {"all": True}, "color": "#000000", "visible": True}]
    g2 = [{"id": "a", "selector": {"all": True}, "color": "#000000", "visible": True}]
    assert _atom_groups_cache_key(g1) == _atom_groups_cache_key(g2)


def test_atom_groups_cache_key_changes_when_color_changes():
    g1 = [{"id": "a", "selector": {"all": True}, "color": "#000000", "visible": True}]
    g2 = [{"id": "a", "selector": {"all": True}, "color": "#FF0000", "visible": True}]
    assert _atom_groups_cache_key(g1) != _atom_groups_cache_key(g2)
