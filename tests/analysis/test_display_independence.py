"""Analysis does not follow visibility, camera, or display repetition. T07, T08, T24."""

from __future__ import annotations

from effect_builders import f2_hydrogen_pbc, f3_octahedron
from mat_viewer.effects import EffectSession


def test_t07_hydrogen_visibility_does_not_edit_sites_or_bonds():
    session = EffectSession()
    ref = session.register(f2_hydrogen_pbc(), structure_id="water")
    before = session.snapshot(ref)
    assert before.species_counts.get("H", 0) == 2
    session.note_display_edit()
    after = session.snapshot(ref)
    assert after.fingerprint == before.fingerprint
    assert len(after.bonds) == len(before.bonds)
    assert session.counters.structure_builds == 1


def test_t08_coordination_is_independent_of_visibility_and_camera():
    session = EffectSession()
    ref = session.register(f3_octahedron(), structure_id="oct")
    center = next(site.site_id for site in session.snapshot(ref).sites if site.symbol == "Ni")
    query = {
        "kind": "coordination",
        "center_site_id": center,
        "cutoff": 2.5,
        "ligand_symbols": ["O"],
        "camera": {"eye": [1, 2, 30]},
        "visibility": {"O": False},
        "topology_enabled": False,
        "color": "#ff00aa",
    }
    first = session.analyze(ref, query)
    assert first.to_dict()["value"]["coordination_number"] == 6
    computations = session.counters.scientific_computations
    second = session.analyze(ref, {**query, "camera": {"eye": [4, 5, 6]}, "visibility": {"O": True}})
    assert second.to_dict()["value"]["coordination_number"] == 6
    assert second.result_id == first.result_id
    assert session.counters.scientific_computations == computations
    assert session.counters.cache_hits == 1


def test_t24_display_edits_do_not_rebuild_or_recompute():
    session = EffectSession()
    ref = session.register(f3_octahedron(), structure_id="oct")
    builds = session.counters.structure_builds
    science = session.counters.scientific_computations
    for _ in range(20):
        session.note_display_edit()
    assert session.counters.structure_builds == builds
    assert session.counters.scientific_computations == science
    assert session.fingerprint(ref) == session.snapshot(ref).fingerprint
