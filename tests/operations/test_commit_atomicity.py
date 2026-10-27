"""Atomic commit, stale revisions, and structural undo. T17–T19."""

from __future__ import annotations

from effect_builders import f1_ordered_nonorthogonal, f6_frames
from mat_viewer.effects import EffectSession
from mat_viewer.effects.errors import InjectedFailure, RevisionConflict


def test_t17_stale_candidate_and_stale_analysis_do_not_overwrite():
    session = EffectSession()
    ref = session.register(f1_ordered_nonorthogonal(), structure_id="cell")
    preview = session.preview("build_supercell", {"a": 2, "b": 2, "c": 1}, structure_id=ref.id, expected_revision=1)
    session.operate("build_supercell", {"a": 2, "b": 1, "c": 1}, structure_id=ref.id, expected_revision=1)
    try:
        session.commit_candidate(preview.candidate_id, expected_revision=1)
    except RevisionConflict:
        pass
    else:
        raise AssertionError("stale candidate committed")
    assert session.snapshot(session.current_ref("cell")).n_sites == 4
    result = session.analyze(ref, {"kind": "composition"})
    try:
        session.bind_result(result, session.current_ref("cell"))
    except RevisionConflict:
        pass
    else:
        raise AssertionError("stale analysis bound")
    assert session.result(result.result_id).ref.revision == 1


def test_t18_failure_injection_leaves_history_unchanged():
    session = EffectSession()
    ref = session.register(f1_ordered_nonorthogonal(), structure_id="cell")
    for stage in ("validate", "construct", "persist", "commit"):
        try:
            session.operate(
                "build_supercell",
                {"a": 2, "b": 2, "c": 2},
                structure_id=ref.id,
                expected_revision=1,
                inject_failure=stage,
            )
        except InjectedFailure:
            pass
        else:
            raise AssertionError(stage)
        assert session.current_ref("cell").revision == 1
        assert session.snapshot(ref).n_sites == 2
    assert session.counters.commits == 0


def test_t19_structural_undo_does_not_track_display_edits():
    session = EffectSession()
    ref = session.register(f1_ordered_nonorthogonal(), structure_id="cell")
    session.operate("build_supercell", {"a": 2, "b": 1, "c": 1}, structure_id=ref.id, expected_revision=1)
    display_before = session.counters.display_edits
    session.note_display_edit()
    session.note_display_edit()
    undone = session.undo("cell")
    assert undone.revision == 1
    assert session.snapshot(undone).n_sites == 2
    assert session.counters.display_edits == display_before + 2
    redone = session.redo("cell")
    assert session.snapshot(redone).n_sites == 4
    assert session.counters.display_edits == display_before + 2


def test_commit_replay_after_undo_restores_the_same_revision():
    session = EffectSession()
    ref = session.register(f1_ordered_nonorthogonal(), structure_id="cell")
    preview = session.preview("build_supercell", {"a": 2, "b": 1, "c": 1}, structure_id=ref.id, expected_revision=1)
    committed = session.commit_candidate(preview.candidate_id, expected_revision=1, request_id="again")
    session.undo("cell")
    assert session.current_ref("cell").revision == 1
    replay = session.commit_candidate(preview.candidate_id, expected_revision=1, request_id="again")
    assert replay.idempotent_replay
    assert session.current_ref("cell").revision == committed.output_ref.revision
    assert session.counters.commits == 1


def test_request_id_does_not_cross_structures():
    session = EffectSession()
    session.register(f1_ordered_nonorthogonal(), structure_id="left")
    session.register(f1_ordered_nonorthogonal(), structure_id="right")
    session.operate("build_supercell", {"a": 2, "b": 1, "c": 1}, structure_id="left", expected_revision=1, request_id="shared")
    other = session.operate(
        "build_supercell",
        {"a": 3, "b": 1, "c": 1},
        structure_id="right",
        expected_revision=1,
        request_id="shared",
    )
    assert other.idempotent_replay is False
    assert session.snapshot(session.current_ref("left")).n_sites == 4
    assert session.snapshot(session.current_ref("right")).n_sites == 6


def test_reloaded_request_id_does_not_commit_twice(tmp_path):
    from mat_viewer.effects.persistence import load_session, save_session

    session = EffectSession()
    ref = session.register(f1_ordered_nonorthogonal(), structure_id="cell")
    session.operate(
        "build_supercell",
        {"a": 2, "b": 1, "c": 1},
        structure_id=ref.id,
        expected_revision=1,
        request_id="persist-me",
    )
    path = tmp_path / "session.json"
    save_session(session, str(path))
    restored = load_session(str(path))
    replay = restored.operate(
        "build_supercell",
        {"a": 9, "b": 9, "c": 9},
        structure_id="cell",
        expected_revision=1,
        request_id="persist-me",
    )
    assert replay.idempotent_replay
    assert restored.current_ref("cell").revision == 2
    assert restored.snapshot(restored.current_ref("cell")).n_sites == 4


def test_t16_frame_results_do_not_cross_frames():
    first, second = f6_frames()
    session = EffectSession()
    session.register(first, structure_id="traj", frame_ref="0", properties={"charges": [1.0, -1.0], "forces": [[0.1, 0, 0], [0, 0.2, 0]]})
    session.register(second, structure_id="traj", frame_ref="1", properties={"charges": [0.5, -0.5]})
    expanded = session.operate(
        "build_supercell",
        {"a": 2, "b": 1, "c": 1},
        structure_id="traj",
        expected_revision=1,
        frame_ref="0",
        property_policy={"charges": {"action": "transform"}, "forces": {"action": "transform"}},
    )
    frame0 = session._frames[("traj", "0")][-1]
    assert len(frame0.properties["site"]["charges"]) == 4
    assert len(frame0.properties["site"]["forces"]) == 4
    assert session.snapshot(session.current_ref("traj", "1")).n_sites == 2
    result = session.analyze(session.current_ref("traj", "0"), {"kind": "composition"})
    try:
        session.bind_result(result, session.current_ref("traj", "1"))
    except RevisionConflict:
        pass
    else:
        raise AssertionError("frame bind")
    assert expanded.output_ref.frame_ref == "0"
