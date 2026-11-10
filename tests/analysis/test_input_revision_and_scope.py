"""Input identity for distance and multi-view isolation. T06, T09."""

from __future__ import annotations

from effect_builders import f1_ordered_nonorthogonal, f3_octahedron
from mat_viewer.effects import EffectSession
from mat_viewer.effects.errors import ValidationError


def test_t06_two_views_do_not_share_scientific_mutations():
    session = EffectSession()
    left = session.register(f1_ordered_nonorthogonal(), structure_id="shared")
    right_crystal = f1_ordered_nonorthogonal()
    # A second frame is an independent scientific input of the same structure id.
    session.register(right_crystal, structure_id="shared", frame_ref="view-b")
    session.operate("build_supercell", {"a": 2, "b": 1, "c": 1}, structure_id="shared", expected_revision=1, frame_ref="0")
    assert session.snapshot(left).n_sites == 2
    assert session.snapshot(session.current_ref("shared", "view-b")).n_sites == 2
    assert session.snapshot(session.current_ref("shared", "0")).n_sites == 4


def test_t09_picked_image_and_minimum_image_stay_distinct():
    session = EffectSession()
    ref = session.register(f3_octahedron(), structure_id="oct")
    sites = session.snapshot(ref).sites
    left = next(site.site_id for site in sites if site.symbol == "O")
    right = next(site.site_id for site in sites if site.symbol == "O" and site.site_id != left)
    picked = session.analyze(
        ref,
        {
            "kind": "distance",
            "distance_mode": "picked_image",
            "left": {"site_id": left, "image": [0, 0, 0]},
            "right": {"site_id": right, "image": [1, 0, 0]},
        },
    )
    minimum = session.analyze(
        ref,
        {
            "kind": "distance",
            "distance_mode": "minimum_image",
            "left": {"site_id": left, "image": [1, 0, 0]},
            "right": {"site_id": right, "image": [0, 0, 0]},
        },
    )
    picked_value = picked.to_dict()["value"]
    minimum_value = minimum.to_dict()["value"]
    assert picked_value["distance_mode"] == "picked_image"
    assert minimum_value["distance_mode"] == "minimum_image"
    assert picked_value["distance_A"] > minimum_value["distance_A"]
    try:
        session.analyze(ref, {"kind": "distance", "left": {"site_id": left}, "right": {"site_id": right}})
    except ValidationError:
        pass
    else:
        raise AssertionError("missing mode")
