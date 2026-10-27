"""Effect ownership. T01."""

from __future__ import annotations

from effect_builders import f1_ordered_nonorthogonal
from mat_viewer.effects import EffectSession
from mat_viewer.effects.operations import IMPLEMENTED_COMMANDS


def test_t01_display_and_analysis_do_not_commit():
    session = EffectSession()
    ref = session.register(f1_ordered_nonorthogonal(), structure_id="cell")
    fingerprint = session.fingerprint(ref)
    commits = session.counters.commits
    for _ in range(5):
        session.note_display_edit()
    session.analyze(ref, {"kind": "composition", "camera": {"eye": [0, 0, 1]}, "topology_enabled": False})
    session.analyze(ref, {"kind": "composition", "visibility": {"C": False}})
    assert session.fingerprint(ref) == fingerprint
    assert session.current_ref("cell").revision == 1
    assert session.counters.commits == commits
    assert session.counters.scientific_computations == 1
    assert session.counters.cache_hits == 1


def test_display_service_source_has_no_commit_call():
    from pathlib import Path

    text = Path("mat_viewer/app/display_service.py").read_text(encoding="utf-8")
    assert ".commit(" not in text
    assert "create_supercell" not in text
    analysis = Path("mat_viewer/effects/analysis.py").read_text(encoding="utf-8")
    assert "commit_candidate" not in analysis
    assert "state.get(\"topology_enabled\"" not in analysis
    assert "query[\"topology_enabled\"]" not in analysis


def test_capabilities_match_the_nine_operations():
    commands = [item["name"] for item in EffectSession().capabilities()["commands"]]
    assert commands == list(IMPLEMENTED_COMMANDS)
    assert "supercell" in EffectSession().capabilities()["aliases_rejected"]
