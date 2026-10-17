from __future__ import annotations

from mat_viewer.render.picking import (
    atom_instance_id,
    bond_instance_id,
    facet_instance_id,
    normalize_target,
    polyhedron_instance_id,
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
