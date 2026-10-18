from __future__ import annotations

from mat_viewer.render.picking import (
    adapt_plotly_hit,
    atom_instance_id,
    bond_instance_id,
    facet_instance_id,
    normalize_target,
    pick_trace_metadata,
    polyhedron_instance_id,
    rank_hit_candidates,
    resolve_hit,
    resolve_hit_candidate,
)


def test_atom_ids_keep_periodic_and_disorder_instances_distinct():
    base = {"label": "C1", "_source_index": 4, "dg": "."}
    periodic = {**base, "_image_shift": [1, 0, 0]}
    disorder = {**base, "dg": "A"}
    assert atom_instance_id(base, 0) != atom_instance_id(periodic, 1)
    assert atom_instance_id(base, 0) != atom_instance_id(disorder, 2)


def test_atom_ids_do_not_embed_transient_display_index_when_source_exists():
    atom = {"label": "C1", "_source_index": 4}
    assert atom_instance_id(atom, 0) == atom_instance_id(atom, 17)
    assert "display:" not in atom_instance_id(atom, 17)


def test_anonymous_atom_ids_use_semantic_fingerprint_not_display_order():
    atom = {"label": "C1", "elem": "C", "cart": [1.0, 2.0, 3.0]}
    assert atom_instance_id(atom, 0) == atom_instance_id(atom, 17)
    assert "display:" not in atom_instance_id(atom, 17)


def test_bond_id_is_endpoint_and_image_specific():
    atoms = [{"label": "C1", "_source_index": 1}, {"label": "O1", "_source_index": 2}]
    first = {"i": 0, "j": 1}
    image = {"i": 0, "j": 1, "image_shift": [0, 0, 1]}
    assert bond_instance_id(first, atoms, 0) != bond_instance_id(image, atoms, 0)


def test_polyhedron_and_facet_ids_expose_parent_identity():
    overlay = {"center_source_index": 3, "center_image_shift": [1, 0, 0]}
    assert polyhedron_instance_id("oct", overlay).startswith("polyhedron:spec:oct")
    assert facet_instance_id([1, 0, 0], 2).endswith(":1,0,0:2")


def test_anonymous_polyhedron_ids_use_coordinates_not_overlay_ordinal():
    overlay = {"center_coords": [1.0, 2.0, 3.0]}
    assert polyhedron_instance_id("oct", overlay, 0) == polyhedron_instance_id("oct", overlay, 9)
    assert "display:" not in polyhedron_instance_id("oct", overlay, 9)


def test_target_normalizer_rejects_missing_or_unknown_ids():
    assert normalize_target({"kind": "atom"}) is None
    assert normalize_target({"kind": "unknown", "id": "x"}) is None
    target = normalize_target({"kind": "facet", "id": "facet:f:1"})
    assert target == {"kind": "facet", "id": "facet:f:1"}


def test_opaque_front_surface_cuts_off_transparent_and_far_hits():
    candidates = [
        {"kind": "atom", "id": "atom:far", "depth": 4.0},
        {"kind": "geometry", "id": "mesh:transparent", "depth": 1.0, "opacity": 0.35},
        {"kind": "polyhedron", "id": "poly:front", "depth": 2.0, "opacity": 1.0},
    ]
    picked = resolve_hit(candidates)
    assert picked == {"kind": "geometry", "id": "mesh:transparent"}
    # The opaque face is still the occlusion boundary: a far atom cannot win
    # merely because a transparent surface sits in front of it.
    selected = resolve_hit_candidate(candidates)
    assert selected is not None and selected["id"] == "mesh:transparent"
    assert any(row["id"] == "atom:far" for row in rank_hit_candidates(candidates))


def test_atom_wins_same_depth_tie_with_thin_bond_probe():
    candidates = [
        {"kind": "bond", "id": "bond:c--o", "depth": 1.0, "pixel_distance": 0.1},
        {"kind": "atom", "id": "atom:c", "depth": 1.0, "pixel_distance": 0.4},
    ]
    assert resolve_hit(candidates) == {"kind": "atom", "id": "atom:c"}


def test_probe_metadata_is_pickable_but_never_an_occluder():
    metadata = pick_trace_metadata("atom_selection", opacity=1.0)
    assert metadata["mv_pick_probe"] is True
    assert metadata["mv_pick_occludes"] is False
    assert metadata["mv_pick_tolerance_px"] > 0


def test_plotly_adapter_reads_semantic_customdata_and_trace_depth():
    point = {
        "customdata": ["atom", 0, "C1", "C", 0, "", "atom:source:4", 4],
        "depth": 2.5,
        "curveNumber": 7,
        "pointNumber": 2,
        "data": {"meta": {"mv_role": "atom_selection"}, "marker": {"opacity": 0.02}},
    }
    hit = adapt_plotly_hit(point)
    assert hit is not None
    assert hit["target"] == {"kind": "atom", "id": "atom:source:4", "label": "C1", "element": "C", "fragment_label": "", "source_index": 4}
    assert hit["depth"] == 2.5
    assert hit["mv_pick_probe"] is True
