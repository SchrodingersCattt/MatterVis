"""Revision numbers stay unique, and request retries do not rewind history."""

from __future__ import annotations

from effect_builders import f1_ordered_nonorthogonal
from mat_viewer.effects import EffectSession
from mat_viewer.effects.errors import RevisionConflict
from mat_viewer.effects.refs import StructureRef


def test_undo_then_a_different_commit_does_not_reuse_the_revision_number():
    session = EffectSession()
    ref = session.register(f1_ordered_nonorthogonal(), structure_id="cell")
    first = session.operate(
        "build_supercell",
        {"a": 2, "b": 1, "c": 1},
        structure_id=ref.id,
        expected_revision=1,
        request_id="first",
    )
    assert first.output_ref.revision == 2
    first_fingerprint = session.fingerprint(first.output_ref)
    bound = session.analyze(first.output_ref, {"kind": "composition"})
    session.undo("cell")
    second = session.operate(
        "build_supercell",
        {"a": 3, "b": 1, "c": 1},
        structure_id=ref.id,
        expected_revision=1,
    )
    assert second.output_ref.revision == 3
    assert second.output_ref != first.output_ref
    assert session.current_ref("cell") == second.output_ref
    assert session.fingerprint(second.output_ref) != first_fingerprint
    assert session.snapshot(second.output_ref).n_sites == 6
    try:
        session.snapshot(StructureRef("cell", 2, "0"))
    except KeyError:
        pass
    else:
        raise AssertionError("retired revision 2 was still addressable as the new structure")
    try:
        session.bind_result(bound, second.output_ref)
    except RevisionConflict:
        pass
    else:
        raise AssertionError("analysis from the retired revision bound to the new structure")
    assert bound.result_id not in session._results


def test_retrying_an_older_request_does_not_rewind_the_current_revision():
    session = EffectSession()
    ref = session.register(f1_ordered_nonorthogonal(), structure_id="cell")
    first = session.operate(
        "build_supercell",
        {"a": 2, "b": 1, "c": 1},
        structure_id=ref.id,
        expected_revision=1,
        request_id="alpha",
    )
    second = session.operate(
        "build_supercell",
        {"a": 2, "b": 1, "c": 1},
        structure_id=ref.id,
        expected_revision=first.output_ref.revision,
        request_id="beta",
    )
    replay = session.operate(
        "build_supercell",
        {"a": 2, "b": 1, "c": 1},
        structure_id=ref.id,
        expected_revision=1,
        request_id="alpha",
    )
    assert replay.idempotent_replay
    assert replay.output_ref == first.output_ref
    assert session.current_ref("cell") == second.output_ref
    assert session.counters.commits == 2
    try:
        session.operate(
            "replace_species",
            {"symbol": "N", "sites": [{"site_id": "m0:a0"}]},
            structure_id=ref.id,
            expected_revision=second.output_ref.revision,
            request_id="alpha",
        )
    except RevisionConflict:
        pass
    else:
        raise AssertionError("request id accepted a different command")
    assert session.current_ref("cell") == second.output_ref
    assert session.counters.commits == 2
