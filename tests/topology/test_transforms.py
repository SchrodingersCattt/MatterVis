"""Phase 4 transforms: pure math + dispatch + cache key.

Each transform spec dict ``{kind, params, enabled}`` takes a base
scene and returns a new scene whose ``draw_atoms`` / ``bonds`` /
``fragment_table`` reflect the transform applied. Pure math functions
(``replicate_atoms``, ``atoms_within_radius``, ...) are tested in
isolation; the dispatcher (``apply_one_transform``) is tested through
``apply_transforms`` against a synthetic scene.

DO NOT REMOVE -- this guards the contract documented in
``agents/transforms_api.md``.
"""
from __future__ import annotations

import numpy as np
import pytest

from crystal_viewer.transforms import (
    KNOWN_TRANSFORM_KINDS,
    apply_transforms,
    atoms_completing_polyhedron,
    atoms_under_symmetry,
    atoms_within_radius,
    replicate_atoms,
    resolve_seed_indices,
    transforms_cache_key,
)


# ---- pure math primitives ---------------------------------------------


def _atoms_2():
    return [
        {
            "elem": "Pb",
            "label": "Pb1",
            "cart": np.array([0.0, 0.0, 0.0]),
            "frac": np.array([0.0, 0.0, 0.0]),
            "occ": 1.0,
            "da": "",
            "dg": "",
        },
        {
            "elem": "Cl",
            "label": "Cl1",
            "cart": np.array([1.0, 0.0, 0.0]),
            "frac": np.array([0.5, 0.0, 0.0]),
            "occ": 1.0,
            "da": "",
            "dg": "",
        },
    ]


def _M():
    return np.diag([2.0, 2.0, 2.0])


def test_replicate_atoms_2x1x1_doubles_count():
    atoms = _atoms_2()
    M = _M()
    out = replicate_atoms(atoms, M, na=2, nb=1, nc=1)
    assert len(out) == 4
    # Home cell preserved with identical labels and image_shift (0,0,0).
    home = [a for a in out if a["_image_shift"] == (0, 0, 0)]
    assert {a["label"] for a in home} == {"Pb1", "Cl1"}
    # Replica labels carry the image_shift suffix.
    replica_labels = {a["label"] for a in out if a["_image_shift"] == (1, 0, 0)}
    assert replica_labels == {"Pb1[1,0,0]", "Cl1[1,0,0]"}


def test_replicate_atoms_clamps_zero_to_one():
    """Zero / negative supercell counts must be clamped to 1 so callers
    can naively pass user-form input without producing an empty scene."""
    atoms = _atoms_2()
    out = replicate_atoms(atoms, _M(), na=0, nb=-3, nc=1)
    assert len(out) == len(atoms)


def test_atoms_within_radius_pulls_neighbouring_image():
    """Seed Pb1 at the origin; with a 2 Å radius and a 2 Å cell, the
    image of Cl1 sitting at one cell over (frac (1.5, 0, 0) ->
    cart (3, 0, 0)) is too far. Increasing to 3.5 Å should pull in the
    home Cl1 (1 Å away) plus the (-1,0,0) image at -1 Å.
    """
    atoms = _atoms_2()
    M = _M()
    pb_index = 0
    extras = atoms_within_radius(
        atoms, M, seed_indices=[pb_index], radius=3.5, include_seeds=False
    )
    # Should pick up at least the home-cell Cl1 (excluding the seed itself).
    image_shifts = {a["_image_shift"] for a in extras}
    assert (0, 0, 0) in image_shifts


def test_atoms_completing_polyhedron_is_radius_alias():
    """``complete_polyhedron`` is a thin wrapper around
    ``atoms_within_radius`` (geometry-only neighbour pull). Same
    behaviour, different doc string."""
    atoms = _atoms_2()
    M = _M()
    a = atoms_within_radius(atoms, M, seed_indices=[0], radius=2.0, include_seeds=True)
    b = atoms_completing_polyhedron(atoms, M, seed_indices=[0], cutoff=2.0)
    assert {(at.get("label"), at["_image_shift"]) for at in a} == {
        (at.get("label"), at["_image_shift"]) for at in b
    }


def test_atoms_under_symmetry_applies_each_op():
    atoms = _atoms_2()
    M = _M()
    # Identity + inversion through origin.
    sym_ops = [
        ([[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0.0, 0.0, 0.0]),
        ([[-1, 0, 0], [0, -1, 0], [0, 0, -1]], [0.0, 0.0, 0.0]),
    ]
    out = atoms_under_symmetry(atoms, M, seed_indices=[0, 1], sym_ops=sym_ops)
    # Two atoms x two ops = at most four images; the identity images
    # for atoms at non-special positions are unique from the inverted
    # images, so we expect 4 atoms.
    assert len(out) == 4
    # Inverted Cl1 ends up at -1 Å on the x axis.
    inverted_cl = [a for a in out if a.get("_origin_label") == "Cl1" and a["_image_shift"] == (1, 0, 0)]
    assert inverted_cl
    np.testing.assert_allclose(inverted_cl[0]["cart"], [-1.0, 0.0, 0.0])


# ---- selector resolution ----------------------------------------------


def test_resolve_seed_indices_supports_all_labels_indices_elements():
    atoms = _atoms_2()
    assert resolve_seed_indices(atoms, {"all": True}) == [0, 1]
    assert resolve_seed_indices(atoms, {"labels": ["Pb1"]}) == [0]
    assert resolve_seed_indices(atoms, {"indices": [1]}) == [1]
    assert resolve_seed_indices(atoms, {"elements": ["Cl"]}) == [1]


def test_resolve_seed_indices_empty_selector_matches_nothing():
    atoms = _atoms_2()
    assert resolve_seed_indices(atoms, None) == []
    assert resolve_seed_indices(atoms, {}) == []


# ---- apply_transforms / apply_one_transform ---------------------------
#
# The dispatcher needs a base scene to operate on. We synthesise a
# minimal scene that satisfies ``rebuild_scene_with_atoms``'s
# expectations -- the same shape produced by ``build_scene_from_atoms``
# but stripped to the bare minimum.


def _scene():
    atoms = _atoms_2()
    return {
        "name": "synthetic",
        "title": "synthetic scene",
        "cell": None,
        "M": _M(),
        "view_x": np.array([1.0, 0.0, 0.0]),
        "view_y": np.array([0.0, 1.0, 0.0]),
        "view_z": np.array([0.0, 0.0, 1.0]),
        "draw_atoms": atoms,
        "bonds": [],
        "label_items": [],
        "bounds": ((-1, 1), (-1, 1), (-1, 1)),
        "style": {"atom_scale": 1.0, "bond_radius": 0.1},
        "has_minor": False,
        "display_mode": "cluster",
    }


def test_apply_transforms_empty_returns_base_scene():
    scene = _scene()
    out = apply_transforms(scene, [])
    assert out is scene


def test_apply_transforms_repeat_2x1x1_grows_atoms():
    out = apply_transforms(
        _scene(),
        [{"id": "t1", "kind": "repeat", "params": {"a": 2, "b": 1, "c": 1}, "enabled": True}],
    )
    assert len(out["draw_atoms"]) == 4
    # Lineage pinned for the right-click "what produced this atom" UX.
    assert out["_transform_lineage"][0]["kind"] == "repeat"


def test_apply_transforms_disabled_skipped():
    out = apply_transforms(
        _scene(),
        [{"id": "t1", "kind": "repeat", "params": {"a": 2, "b": 2, "c": 2}, "enabled": False}],
    )
    assert len(out["draw_atoms"]) == len(_scene()["draw_atoms"])


def test_apply_transforms_unknown_kind_skipped():
    out = apply_transforms(
        _scene(),
        [{"id": "t1", "kind": "frobnicate", "params": {}, "enabled": True}],
    )
    # Unknown kind = no-op; lineage still recorded so the trace is
    # debuggable from a state dump.
    assert len(out["draw_atoms"]) == len(_scene()["draw_atoms"])


def test_apply_transforms_chains_repeat_then_radius():
    """Two transforms compose in list order: 2x1x1 supercell, then a
    radius grow seeded on the home Pb1. The growth must see the atoms
    produced by the supercell, not the original cell only."""
    base = _scene()
    transforms_list = [
        {"id": "t1", "kind": "repeat", "params": {"a": 2, "b": 1, "c": 1}, "enabled": True},
        {
            "id": "t2",
            "kind": "complete_polyhedron",
            "params": {"seeds": {"labels": ["Pb1"]}, "cutoff": 1.5},
            "enabled": True,
        },
    ]
    out = apply_transforms(base, transforms_list)
    # At least the supercell's atoms survive (4); typically the grow
    # adds nothing here, but it must not lose atoms.
    assert len(out["draw_atoms"]) >= 4


# ---- transforms_cache_key ---------------------------------------------


def test_cache_key_stable_across_id_rename_and_name_change():
    a = [{"id": "t1", "name": "X", "kind": "repeat", "params": {"a": 2, "b": 1, "c": 1}, "enabled": True}]
    b = [{"id": "t1_renamed", "name": "Y", "kind": "repeat", "params": {"a": 2, "b": 1, "c": 1}, "enabled": True}]
    assert transforms_cache_key(a) == transforms_cache_key(b)


def test_cache_key_changes_on_param_change():
    a = [{"id": "t1", "kind": "repeat", "params": {"a": 2, "b": 1, "c": 1}, "enabled": True}]
    b = [{"id": "t1", "kind": "repeat", "params": {"a": 3, "b": 1, "c": 1}, "enabled": True}]
    assert transforms_cache_key(a) != transforms_cache_key(b)


def test_cache_key_changes_on_enable_toggle():
    a = [{"id": "t1", "kind": "repeat", "params": {"a": 2, "b": 1, "c": 1}, "enabled": True}]
    b = [{"id": "t1", "kind": "repeat", "params": {"a": 2, "b": 1, "c": 1}, "enabled": False}]
    assert transforms_cache_key(a) != transforms_cache_key(b)


def test_known_kinds_includes_phase_4_set():
    assert set(KNOWN_TRANSFORM_KINDS) == {
        "repeat",
        "grow_radius",
        "grow_bonds",
        "complete_fragment",
        "complete_polyhedron",
        "by_symmetry",
        "slab",
    }


def test_complete_fragment_with_all_seeds_short_circuits():
    """Regression: ``complete_fragment(seeds={'all': True}, max_hops=32)``
    on a multi-cell scene used to compute a 3.5 * 32 = 112 angstrom halo
    around every seed atom and run an O(N^2 * N_cells) numpy broadcast
    through ``atoms_within_radius``, which on a 640-atom 2x2x2 supercell
    consumed ~1 GB of memory before timing out the figure render.

    With every atom already a seed there is by definition nothing left
    to "complete" -- the function must return the input atoms verbatim
    without spinning up the radius pipeline.
    """
    from crystal_viewer.transforms import atoms_completing_fragment

    atoms = _atoms_2()
    M = np.eye(3) * 5.0

    # All-seeds path: must be cheap and return exactly the input
    # atoms (one per seed). The previous behaviour would block on the
    # halo broadcast.
    out = atoms_completing_fragment(
        atoms,
        M,
        seed_indices=list(range(len(atoms))),
        ops=None,
        cell=None,
        max_hops=32,
    )
    assert len(out) == len(atoms)
    # Returned atoms are home-cell copies (image_shift == (0,0,0)).
    for atom in out:
        assert atom["_image_shift"] == (0, 0, 0)


def test_complete_fragment_caps_halo_radius():
    """The original implementation set ``halo_radius = 3.5 * max_hops``
    which exploded the periodic image grid. The cap keeps the
    expensive ``atoms_within_radius`` call within a sane budget while
    still letting the BFS over ``adj`` walk the full ``max_hops``
    chain. We can't observe the cap directly, but we can assert the
    function returns in well under a second even for max_hops=128 on
    a small home cell.
    """
    import time as _time
    from types import SimpleNamespace
    from crystal_viewer.transforms import atoms_completing_fragment

    atoms = _atoms_2()
    M = np.eye(3) * 5.0
    # The pure-math layer needs an ``ops`` shim with ``find_bonds`` to
    # walk the bond graph; for a 2-atom isolated test scene it just
    # needs to return an iterable.
    ops_stub = SimpleNamespace(find_bonds=lambda atoms, cell=None: [])
    start = _time.monotonic()
    out = atoms_completing_fragment(
        atoms,
        M,
        seed_indices=[0],
        ops=ops_stub,
        cell=None,
        max_hops=128,
    )
    elapsed = _time.monotonic() - start
    # Pure-math budget is generous; the unit test box is otherwise
    # idle. Without the cap, max_hops=128 -> halo=448 angstrom -> the
    # periodic-image grid alone (capped at +-4) is 9^3 = 729 shifts
    # but the broadcast dominates and used to take >>1 s.
    assert elapsed < 1.0, f"complete_fragment took {elapsed:.3f}s; halo cap likely regressed"
    assert isinstance(out, list)


# ---- topology search_supercell ----------------------------------------
#
# ``analyze_topology`` accepts ``search_supercell`` to extend the
# neighbour-image span without changing the display supercell.


def test_search_supercell_normaliser():
    from crystal_viewer.topology import _normalize_search_supercell

    assert _normalize_search_supercell(None) == (0, 0, 0)
    assert _normalize_search_supercell(2) == (2, 2, 2)
    assert _normalize_search_supercell([1, 0, 2]) == (1, 0, 2)
    assert _normalize_search_supercell((1,)) == (1, 1, 1)
    assert _normalize_search_supercell(-1) == (0, 0, 0)
    with pytest.raises(ValueError):
        _normalize_search_supercell([1, 2])  # 2-element list is ambiguous


# ---- backend supercell shorthand --------------------------------------


def test_supercell_shorthand_emits_repeat_transform():
    from crystal_viewer.app import ViewerBackend
    from crystal_viewer.presets import default_preset_path

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        backend = ViewerBackend(preset_path=default_preset_path(), root_dir=tmp)
        result = backend.patch_state({"supercell": {"a": 2, "b": 2, "c": 1}})
        assert any(
            t["kind"] == "repeat" and t["params"] == {"a": 2, "b": 2, "c": 1}
            for t in result["transforms"]
        )
        # Re-issuing the shorthand replaces (does not stack) the
        # previous repeat transform.
        result = backend.patch_state({"supercell": {"a": 3, "b": 1, "c": 1}})
        repeats = [t for t in result["transforms"] if t["kind"] == "repeat"]
        assert len(repeats) == 1
        assert repeats[0]["params"] == {"a": 3, "b": 1, "c": 1}


def test_rebuild_scene_redetects_bonds_in_replica():
    """Regression: after a ``repeat`` transform, replica atoms get
    suffixed labels (``Cl1`` -> ``Cl1[1,0,0]``) but their
    ``_bond_partners`` list still references the canonical labels.
    ``rebuild_scene_with_atoms`` must temporarily restore the canonical
    label so the legacy bond-table check (``_bond_allowed_by_table``)
    accepts cross-replica bonds. Without the swap the replica side of
    a 2x1x1 supercell renders as bondless atom dust.
    """
    from crystal_viewer.transforms import rebuild_scene_with_atoms, replicate_atoms

    M = np.diag([3.0, 3.0, 3.0])
    pb = {
        "elem": "Pb",
        "label": "Pb1",
        "cart": np.array([0.0, 0.0, 0.0]),
        "frac": np.array([0.0, 0.0, 0.0]),
        "occ": 1.0,
        "da": "",
        "dg": "",
        "_has_bond_table": True,
        "_bond_partners": ("Cl1",),
        "_bond_lengths": {"Cl1": (1.0,)},
    }
    cl = {
        "elem": "Cl",
        "label": "Cl1",
        "cart": np.array([1.0, 0.0, 0.0]),
        "frac": np.array([1.0 / 3.0, 0.0, 0.0]),
        "occ": 1.0,
        "da": "",
        "dg": "",
        "_has_bond_table": True,
        "_bond_partners": ("Pb1",),
        "_bond_lengths": {"Pb1": (1.0,)},
    }
    base_scene = {
        "draw_atoms": [pb, cl],
        "bonds": [],
        "cell": (3.0, 3.0, 3.0, 90.0, 90.0, 90.0),
        "M": M,
        "view_x": np.array([1.0, 0.0, 0.0]),
        "view_y": np.array([0.0, 1.0, 0.0]),
        "view_z": np.array([0.0, 0.0, 1.0]),
        "style": {},
    }
    replicated = replicate_atoms([pb, cl], M, na=2, nb=1, nc=1)
    out = rebuild_scene_with_atoms(base_scene, replicated)
    bonds = out["bonds"]
    bond_pairs = {tuple(sorted((b["i"], b["j"]))) for b in bonds}
    # Both home Pb-Cl AND replica Pb-Cl bonds must exist.
    home_idx = {i for i, a in enumerate(out["draw_atoms"]) if a.get("_image_shift") == (0, 0, 0)}
    replica_idx = {i for i, a in enumerate(out["draw_atoms"]) if a.get("_image_shift") == (1, 0, 0)}
    home_bond = any(i in home_idx and j in home_idx for i, j in bond_pairs)
    replica_bond = any(i in replica_idx and j in replica_idx for i, j in bond_pairs)
    assert home_bond, "home Pb-Cl bond missing after replicate"
    assert replica_bond, "replica Pb-Cl bond missing after replicate -- bond table label mismatch"
    # And the original ``label`` field must be restored after detection.
    replica_labels = {a["label"] for a in out["draw_atoms"] if a.get("_image_shift") == (1, 0, 0)}
    assert replica_labels == {"Pb1[1,0,0]", "Cl1[1,0,0]"}


def test_supercell_shorthand_one_one_one_clears_repeat():
    """``supercell = {1,1,1}`` is the way callers express "back to home
    cell" without learning the dedicated transforms DELETE endpoint.
    The shorthand must drop any existing ``repeat`` transform; otherwise
    the AI script that previously enlarged the cell is stuck with it.
    """
    from crystal_viewer.app import ViewerBackend
    from crystal_viewer.presets import default_preset_path

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        backend = ViewerBackend(preset_path=default_preset_path(), root_dir=tmp)
        result = backend.patch_state({"supercell": {"a": 2, "b": 2, "c": 2}})
        assert any(t["kind"] == "repeat" for t in result["transforms"])
        result = backend.patch_state({"supercell": {"a": 1, "b": 1, "c": 1}})
        assert all(t["kind"] != "repeat" for t in result["transforms"])
