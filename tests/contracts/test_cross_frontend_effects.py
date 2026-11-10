"""The same structural command has the same scientific effect. T22."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from effect_builders import f1_ordered_nonorthogonal
from mat_viewer.effects import EffectSession
from mat_viewer.effects.persistence import export_structure


def test_t22_cli_matches_python_supercell(tmp_path: Path):
    session = EffectSession()
    ref = session.register(f1_ordered_nonorthogonal(), structure_id="f1")
    session.operate("build_supercell", {"a": 2, "b": 3, "c": 1}, structure_id="f1", expected_revision=1)
    source = tmp_path / "f1.cif"
    exported = export_structure(session.snapshot(ref), session.checkout(ref), fmt="cif")
    source.write_text(exported["text"], encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "mat_viewer",
            "operate",
            "build-supercell",
            str(source),
            "--a",
            "2",
            "--b",
            "3",
            "--c",
            "1",
            "--json",
            "-o",
            str(tmp_path / "out.cif"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["output"]["revision"] == 2
    assert payload["n_sites"] == 12
    assert payload["source_n_sites"] == 2
    assert (tmp_path / "out.cif").is_file()
    rejected = subprocess.run(
        [sys.executable, "-m", "mat_viewer", "operate", "supercell", str(source), "--a", "2", "--b", "2", "--c", "2"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0
    assert "build-supercell" in rejected.stderr + rejected.stdout


def test_operate_intent_is_a_view_noop():
    from mat_viewer.app.reducer import operation_from_intent, reduce_state

    state = {"structure": "urea", "transforms": [], "camera": {"position": [1, 2, 3]}}
    operation = operation_from_intent(
        {"type": "operate", "scene_id": "scene-a", "payload": {"command": "build_supercell", "params": {"a": 2, "b": 1, "c": 1}}}
    )
    reduced, _invalidations = reduce_state(state, operation)
    assert reduced["transforms"] == []
    assert reduced["camera"] == state["camera"]
    assert operation.name == "operate"
    from mat_viewer.app.reducer import operation_patch

    assert operation_patch(operation) == {}
