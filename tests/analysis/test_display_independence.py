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
    assert second.to_dict()["value"] == first.to_dict()["value"]
    assert second.result_id == first.result_id
    assert "camera" not in dict(first.policy)
    assert "visibility" not in dict(first.policy)
    assert session.counters.scientific_computations == computations
    assert session.counters.cache_hits == 1


def test_t24_display_edits_do_not_rebuild_or_recompute(tmp_path):
    import json
    from pathlib import Path

    from mat_viewer.app import ViewerBackend

    local = tmp_path / ".local"
    local.mkdir()
    cif = Path("docs/qa/urea-default.cif")
    (local / "catalog.local.json").write_text(
        json.dumps({"structures": {"urea": {"cif_path": str(cif.resolve()), "title": "urea"}}}),
        encoding="utf-8",
    )
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path), names=["urea"])
    try:
        bundle = backend.get_bundle("urea")
        session = backend.effect_session()
        ref = session.register(bundle.crystal, structure_id=bundle.name, viewer_name=bundle.name)
        builds = session.counters.structure_builds
        science = session.counters.scientific_computations
        raw = len(bundle.raw_atoms)
        fingerprint = session.fingerprint(ref)
        scene_id = backend.active_scene_id()
        for index in range(4):
            backend.patch_state(
                {
                    "atom_scale": 0.4 + 0.05 * index,
                    "camera": {"position": [index, 2, 8], "focal_point": [0, 0, 0], "up": [0, 1, 0]},
                },
                scene_id=scene_id,
                broadcast=False,
            )
            backend.scene_for_state(backend.get_state(scene_id))
        assert len(bundle.raw_atoms) == raw
        assert session.fingerprint(ref) == fingerprint
        assert session.counters.structure_builds == builds
        assert session.counters.scientific_computations == science
        center = next(site.site_id for site in session.snapshot(ref).sites)
        session.analyze(ref, {"kind": "composition"})
        session.analyze(ref, {"kind": "composition", "camera": {"eye": [9, 9, 9]}})
        assert session.counters.scientific_computations == science + 1
    finally:
        backend.close(wait=False)
