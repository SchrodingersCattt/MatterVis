"""A preview cannot smuggle a mutated crystal or mapping into the commit."""

from __future__ import annotations

from effect_builders import f1_ordered_nonorthogonal
from mat_viewer.effects import EffectSession
from mat_viewer.effects.errors import OperationRejected


def test_mutating_the_returned_preview_does_not_change_the_commit():
    session = EffectSession()
    ref = session.register(f1_ordered_nonorthogonal(), structure_id="cell")
    preview = session.preview(
        "build_supercell",
        {"a": 2, "b": 1, "c": 1},
        structure_id=ref.id,
        expected_revision=1,
    )
    assert not hasattr(preview, "crystal")
    try:
        preview.provenance["command"] = "tampered"
    except TypeError:
        pass
    else:
        raise AssertionError("returned preview provenance is writable")
    committed = session.commit_candidate(preview.candidate_id, expected_revision=1)
    assert committed.fingerprint == preview.fingerprint
    assert session.snapshot(committed.output_ref).n_sites == 4
    assert session.current_ref("cell").revision == 2


def test_mutating_the_retained_candidate_is_rejected_at_commit():
    session = EffectSession()
    ref = session.register(f1_ordered_nonorthogonal(), structure_id="cell")
    preview = session.preview(
        "build_supercell",
        {"a": 2, "b": 1, "c": 1},
        structure_id=ref.id,
        expected_revision=1,
    )
    retained = session._candidates[preview.candidate_id]
    retained.crystal.molecules[0].positions[0, 0] += 3.0
    try:
        session.commit_candidate(preview.candidate_id, expected_revision=1)
    except OperationRejected:
        pass
    else:
        raise AssertionError("mutated candidate committed")
    assert session.current_ref("cell").revision == 1
    assert session.counters.commits == 0
    assert session.snapshot(ref).n_sites == 2
