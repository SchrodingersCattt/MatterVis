"""Atom-centred polyhedra (kind="atom") -- MolCrysKit driven.

Covers:

- :func:`crystal_viewer.topology.atom_centered_polyhedra` returns
  per-central-atom overlays in the same shape the renderer consumes
  for fragment-centred ``spec_results``.
- :func:`crystal_viewer.topology.suggest_default_polyhedron_specs`
  produces chemistry-meaningful defaults for perchlorate hybrids
  (DAP-4 -> ClO4 tetrahedra) and stays empty when no
  textbook coordination chemistry is present.
- ``ViewerBackend`` round-trips ``polyhedron_specs[*].kind == "atom"``
  through the spec normaliser, the geometry compute path, and the
  renderer-facing ``spec_results``.
- Atom-only mode (every spec has ``kind="atom"``) does NOT require
  a fragment anchor; ``topology_for_state`` produces overlays even
  when no fragment formula matches any spec centre species.

DO NOT REMOVE -- the chemistry-sensible default polyhedra and the
atom-centred pipeline are the user-visible deliverable for the
SY/DAP-4 perchlorate fix.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from crystal_viewer.app import WORKSPACE_DIR, ViewerBackend, _normalize_polyhedron_spec
from crystal_viewer.presets import default_preset_path
from crystal_viewer.topology import (
    atom_centered_polyhedra,
    suggest_default_polyhedron_specs,
)


@pytest.fixture
def backend(tmp_path: Path) -> ViewerBackend:
    return ViewerBackend(preset_path=default_preset_path(), root_dir=WORKSPACE_DIR)


# ---- atom_centered_polyhedra --------------------------------------------


def test_atom_centered_polyhedra_returns_clo4_tetrahedra_for_dap4(backend: ViewerBackend):
    bundle = backend.get_bundle("DAP-4")
    overlays = atom_centered_polyhedra(bundle, central="Cl", ligand="O", search_cutoff=2.0)
    assert overlays, "DAP-4 must yield at least one ClO4 tetrahedron"
    # Every ClO4 tetrahedron has exactly four oxygens at ~1.45 A.
    for overlay in overlays:
        assert overlay["coordination_number"] == 4, (
            f"Cl center got CN={overlay['coordination_number']}; expected 4 for ClO4"
        )
        distances = overlay["distances"]
        assert all(1.3 <= d <= 1.7 for d in distances), (
            f"Cl-O distances outside textbook ClO4 range: {distances}"
        )
        # shell_coords must be plain Python lists (numpy arrays make
        # the renderer's ``if not shell:`` truth check ambiguous).
        assert isinstance(overlay["shell_coords"], list)
        assert all(isinstance(point, list) for point in overlay["shell_coords"])


def test_atom_centered_polyhedra_drops_below_minimum_vertices(backend: ViewerBackend):
    # CN<3 cannot form a polygon for ConvexHull -- the helper must
    # silently filter them out so the renderer never crashes.
    bundle = backend.get_bundle("DAP-4")
    # Tight-tight cutoff: no Cl atom has even one O within 1.0 A.
    overlays = atom_centered_polyhedra(bundle, central="Cl", ligand="O", search_cutoff=1.0)
    assert overlays == []


def test_atom_centered_polyhedra_handles_unknown_species(backend: ViewerBackend):
    bundle = backend.get_bundle("DAP-4")
    # Element not present in DAP-4 -> empty list, not exception.
    assert atom_centered_polyhedra(bundle, central="Pb", ligand="Cl") == []


# ---- suggest_default_polyhedron_specs -----------------------------------


def test_suggest_defaults_for_dap4_includes_clo4_only(backend: ViewerBackend):
    bundle = backend.get_bundle("DAP-4")
    suggestions = suggest_default_polyhedron_specs(bundle)
    pairs = {(s["center_species"], s["ligand_species"]) for s in suggestions}
    # Cl-O perchlorate must be suggested.
    assert ("Cl", "O") in pairs, pairs
    # Organic-cation N is far enough from O (>1.6 A) that the
    # nitrate-style cap rules it out -- the default list MUST NOT
    # include N-O for this hybrid perchlorate, otherwise huge
    # cuboctahedra would appear in the default render.
    assert ("N", "O") not in pairs, (
        "DAP-4's organic N atoms are not nitrate centres; "
        "default polyhedra must not draw N-O cuboctahedra"
    )


def test_suggest_defaults_carries_search_cutoff(backend: ViewerBackend):
    bundle = backend.get_bundle("DAP-4")
    suggestions = suggest_default_polyhedron_specs(bundle)
    cl_specs = [s for s in suggestions if s["center_species"] == "Cl"]
    assert cl_specs, "Cl-O suggestion missing"
    assert cl_specs[0].get("search_cutoff") is not None, (
        "auto-suggested specs must carry the per-pair covalent cap "
        "so the geometry compute path stays tight by default"
    )
    assert cl_specs[0]["search_cutoff"] <= 2.5


# ---- spec normaliser round-trip -----------------------------------------


def test_normalize_polyhedron_spec_preserves_kind_and_search_cutoff():
    spec = _normalize_polyhedron_spec(
        {
            "center_species": "Cl",
            "ligand_species": "O",
            "kind": "atom",
            "search_cutoff": 2.0,
        },
        fallback_color="#7c5cbf",
        existing_ids=set(),
    )
    assert spec is not None
    assert spec["kind"] == "atom"
    assert spec["search_cutoff"] == 2.0
    # Unknown kind values fall back to "fragment" rather than raising.
    fallback = _normalize_polyhedron_spec(
        {"center_species": "ClO4", "kind": "garbage"},
        fallback_color="#7c5cbf",
        existing_ids=set(),
    )
    assert fallback is not None
    assert fallback["kind"] == "fragment"
    assert fallback["search_cutoff"] is None


# ---- end-to-end via ViewerBackend ---------------------------------------


def test_topology_for_state_renders_atom_specs_without_fragment_anchor(backend: ViewerBackend):
    # Wipe the chemistry defaults and POST a single atom-centred spec
    # for an element symbol that has NO fragment-graph match (Cl is
    # part of the ClO4 fragment formula, never a fragment by itself);
    # the older topology_for_state would early-return None here.
    for spec in backend.list_polyhedron_specs():
        backend.remove_polyhedron_spec(spec["id"])
    spec = backend.add_polyhedron_spec(
        center_species="Cl",
        ligand_species="O",
        kind="atom",
        search_cutoff=2.0,
        color="#FF00FF",
        name="ClO4 tetrahedra",
    )
    state = backend.get_state()
    topology = backend.topology_for_state(state)
    assert topology is not None, (
        "atom-only mode must still produce a topology; previously the "
        "fragment anchor lookup early-returned None when no spec center "
        "matched a fragment formula"
    )
    spec_results = topology.get("spec_results") or []
    by_id = {entry["spec_id"]: entry for entry in spec_results}
    assert spec["id"] in by_id, by_id
    overlays = by_id[spec["id"]].get("overlays") or []
    assert overlays, "atom-centred spec must yield ClO4 overlays"
    assert by_id[spec["id"]].get("kind") == "atom"


def test_default_state_for_dap4_ships_with_clo4_atom_spec(backend: ViewerBackend):
    state = backend.get_state()
    pairs = {
        (spec.get("kind"), spec.get("center_species"), spec.get("ligand_species"))
        for spec in state.get("polyhedron_specs") or []
    }
    assert ("atom", "Cl", "O") in pairs, pairs


def test_topology_cache_key_includes_search_cutoff(backend: ViewerBackend):
    """Same (kind, centre, ligand) but different search_cutoff must
    invalidate the geometry cache; otherwise tightening a covalent cap
    in the UI silently shows stale (too-wide) polyhedra."""
    for spec in backend.list_polyhedron_specs():
        backend.remove_polyhedron_spec(spec["id"])
    spec = backend.add_polyhedron_spec(
        center_species="Cl",
        ligand_species="O",
        kind="atom",
        search_cutoff=2.0,
    )
    state = backend.get_state()
    topology_wide = backend.topology_for_state(state)
    assert topology_wide is not None
    overlays_wide = (topology_wide.get("spec_results") or [{}])[0].get("overlays") or []
    cn_wide = max((o.get("coordination_number", 0) for o in overlays_wide), default=0)

    backend.update_polyhedron_spec(spec["id"], {"search_cutoff": 1.0})
    state = backend.get_state()
    topology_tight = backend.topology_for_state(state)
    assert topology_tight is not None
    overlays_tight = (topology_tight.get("spec_results") or [{}])[0].get("overlays") or []
    # Tightening below the bond length must filter every ClO4 out.
    assert overlays_tight == [], (
        "search_cutoff=1.0 cannot capture any Cl-O bond; the tight "
        "spec must produce zero overlays even though the wide spec "
        "produced several -- otherwise the geometry cache key is "
        "missing the search_cutoff field"
    )
    assert cn_wide == 4
