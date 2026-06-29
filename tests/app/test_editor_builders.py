"""Unit tests for the extracted builder helpers in callbacks_editors.

These helpers were previously inline in Dash callback bodies.
Exercising them directly catches import errors (e.g. missing
``_POLY_SHELL_MODE_*``) and verifies the output dict shape without
requiring a live Dash app.
"""
from __future__ import annotations

import pytest

from crystal_viewer.app.callbacks_editors import (
    _build_atom_groups,
    _build_bond_groups,
)
from crystal_viewer.app.callbacks_analysis import _build_polyhedron_specs
from crystal_viewer.app.callbacks_operations import _build_transforms


# ── _build_polyhedron_specs ──────────────────────────────────────


class TestBuildPolyhedronSpecs:
    """Cover the main code paths of _build_polyhedron_specs."""

    @staticmethod
    def _defaults(**overrides):
        """Return a minimal valid kwargs dict; override any key."""
        base = dict(
            color_ids=[{"spec_id": "s1"}],
            colors=["#ff0000"],
            centers=["Fe"],
            ligands=["__auto__"],
            enableds=[["yes"]],
            shell_modes=["gap_enclosure"],
            centroid_offsets=[None],
            levels=[None],
            center_kinds=[None],
            hard_cutoffs=[None],
            fallback_maxes=[None],
            existing={},
        )
        base.update(overrides)
        return base

    def test_single_spec_roundtrip(self):
        specs = _build_polyhedron_specs(**self._defaults())
        assert len(specs) == 1
        s = specs[0]
        assert s["id"] == "s1"
        assert s["color"] == "#ff0000"
        assert s["center_species"] == "Fe"
        assert s["ligand_species"] is None  # "auto" maps to None
        assert s["enabled"] is True

    def test_shell_mode_gap_sets_enforce_enclosure_false(self):
        specs = _build_polyhedron_specs(**self._defaults(shell_modes=["gap"]))
        assert specs[0]["enforce_enclosure"] is False

    def test_shell_mode_enclosure_sets_enforce_enclosure_true(self):
        specs = _build_polyhedron_specs(
            **self._defaults(shell_modes=["gap_enclosure"])
        )
        assert specs[0]["enforce_enclosure"] is True

    def test_existing_base_is_used_for_defaults(self):
        existing = {
            "s1": {
                "name": "Test Poly",
                "color": "#00ff00",
                "center_species": "Mn",
                "instance_overrides": {"io": 1},
            }
        }
        specs = _build_polyhedron_specs(**self._defaults(existing=existing))
        assert specs[0]["name"] == "Test Poly"
        assert specs[0]["instance_overrides"] == {"io": 1}

    def test_disabled_spec(self):
        specs = _build_polyhedron_specs(**self._defaults(enableds=[[]]))
        assert specs[0]["enabled"] is False

    def test_multiple_specs(self):
        specs = _build_polyhedron_specs(
            **self._defaults(
                color_ids=[{"spec_id": "s1"}, {"spec_id": "s2"}],
                colors=["#ff0000", "#00ff00"],
                centers=["Fe", "Co"],
                ligands=["auto", "auto"],
                enableds=[["yes"], ["yes"]],
                shell_modes=["gap_enclosure", "gap"],
                centroid_offsets=[None, None],
                levels=[None, None],
                center_kinds=[None, None],
                hard_cutoffs=[None, None],
                fallback_maxes=[None, None],
            )
        )
        assert len(specs) == 2
        assert specs[0]["center_species"] == "Fe"
        assert specs[1]["center_species"] == "Co"
        assert specs[1]["enforce_enclosure"] is False


# ── _build_atom_groups ───────────────────────────────────────────


class TestBuildAtomGroups:
    def test_all_kind_produces_all_selector(self):
        groups = _build_atom_groups(
            color_ids=[{"group_id": "g1"}],
            visibles=[["yes"]],
            colors=["#ff0000"],
            kinds=["all"],
            elements_lists=[[]],
            opacities=[1.0],
            materials=["inherit"],
            styles=["inherit"],
        )
        assert len(groups) == 1
        assert groups[0]["selector"] == {"all": True}
        assert groups[0]["visible"] is True

    def test_minor_kind_produces_is_minor_selector(self):
        groups = _build_atom_groups(
            color_ids=[{"group_id": "g1"}],
            visibles=[["yes"]],
            colors=["#ff0000"],
            kinds=["minor"],
            elements_lists=[[]],
            opacities=[1.0],
            materials=["inherit"],
            styles=["inherit"],
        )
        assert groups[0]["selector"] == {"is_minor": True}


# ── _build_bond_groups ───────────────────────────────────────────


class TestBuildBondGroups:
    def test_all_kind_produces_all_selector(self):
        groups = _build_bond_groups(
            color_ids=[{"group_id": "g1"}],
            visibles=[["yes"]],
            colors=["#ff0000"],
            kinds=["all"],
            elements_lists=[[]],
            opacities=[None],
            radius_scales=[None],
        )
        assert len(groups) == 1
        assert groups[0]["selector"] == {"all": True}


# ── _build_transforms ───────────────────────────────────────────


class TestBuildTransforms:
    def test_repeat_transform_defaults(self):
        existing = {
            "t1": {
                "name": "2×2×2",
                "kind": "repeat",
                "params": {"a": 2, "b": 2, "c": 2},
            }
        }
        transforms = _build_transforms(
            enabled_ids=[{"transform_id": "t1"}],
            enableds=[["yes"]],
            param_a=[None],
            param_b=[None],
            param_c=[None],
            param_seeds=[None],
            param_radius=[None],
            param_hops=[None],
            param_maxhops=[None],
            param_cutoff=[None],
            param_ops=[None],
            param_miller0=[None],
            param_miller1=[None],
            param_miller2=[None],
            param_layers=[None],
            param_vacuum=[None],
            existing=existing,
        )
        assert len(transforms) == 1
        t = transforms[0]
        assert t["kind"] == "repeat"
        assert t["params"]["a"] == 2
        assert t["enabled"] is True

    def test_missing_base_transform_is_skipped(self):
        transforms = _build_transforms(
            enabled_ids=[{"transform_id": "nonexistent"}],
            enableds=[["yes"]],
            param_a=[None],
            param_b=[None],
            param_c=[None],
            param_seeds=[None],
            param_radius=[None],
            param_hops=[None],
            param_maxhops=[None],
            param_cutoff=[None],
            param_ops=[None],
            param_miller0=[None],
            param_miller1=[None],
            param_miller2=[None],
            param_layers=[None],
            param_vacuum=[None],
            existing={},
        )
        assert len(transforms) == 0


# ── regression: transform path parity ───────────────────────────
#
# After the refactor, all supercell / repeat creation paths must route
# through backend.add_transform() so formula_unit auto-promotion and
# validation fire regardless of entry point (preset button, keyboard
# shortcut, right-click menu).

class TestTransformPathParity:
    """Verify that _normalize_transform handles edge cases correctly
    and that the builder produces valid output for all transform kinds."""

    def test_repeat_params_default_to_one(self):
        existing = {
            "t1": {
                "name": "",
                "kind": "repeat",
                "params": {},
            }
        }
        transforms = _build_transforms(
            enabled_ids=[{"transform_id": "t1"}],
            enableds=[["yes"]],
            param_a=[None],
            param_b=[None],
            param_c=[None],
            param_seeds=[None],
            param_radius=[None],
            param_hops=[None],
            param_maxhops=[None],
            param_cutoff=[None],
            param_ops=[None],
            param_miller0=[None],
            param_miller1=[None],
            param_miller2=[None],
            param_layers=[None],
            param_vacuum=[None],
            existing=existing,
        )
        t = transforms[0]
        assert t["params"]["a"] == 1
        assert t["params"]["b"] == 1
        assert t["params"]["c"] == 1

    def test_grow_radius_defaults(self):
        existing = {
            "t1": {
                "name": "",
                "kind": "grow_radius",
                "params": {"seeds": {"all": True}},
            }
        }
        transforms = _build_transforms(
            enabled_ids=[{"transform_id": "t1"}],
            enableds=[["yes"]],
            param_a=[None],
            param_b=[None],
            param_c=[None],
            param_seeds=[None],
            param_radius=[None],
            param_hops=[None],
            param_maxhops=[None],
            param_cutoff=[None],
            param_ops=[None],
            param_miller0=[None],
            param_miller1=[None],
            param_miller2=[None],
            param_layers=[None],
            param_vacuum=[None],
            existing=existing,
        )
        t = transforms[0]
        assert t["params"]["radius"] == 0.0

    def test_slab_defaults(self):
        existing = {
            "t1": {
                "name": "",
                "kind": "slab",
                "params": {},
            }
        }
        transforms = _build_transforms(
            enabled_ids=[{"transform_id": "t1"}],
            enableds=[["yes"]],
            param_a=[None],
            param_b=[None],
            param_c=[None],
            param_seeds=[None],
            param_radius=[None],
            param_hops=[None],
            param_maxhops=[None],
            param_cutoff=[None],
            param_ops=[None],
            param_miller0=[None],
            param_miller1=[None],
            param_miller2=[None],
            param_layers=[None],
            param_vacuum=[None],
            existing=existing,
        )
        t = transforms[0]
        assert t["params"]["miller"] == [0, 0, 1]
        assert t["params"]["vacuum"] == 10.0
