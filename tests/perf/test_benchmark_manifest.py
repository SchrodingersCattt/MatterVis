from __future__ import annotations

import json
from pathlib import Path

from mat_viewer.perf import runner
from mat_viewer.perf.manifest import SCHEMA, fixture_paths, load_manifest


def test_benchmark_manifest_has_integrity_checked_fixture_corpus():
    manifest = load_manifest()
    assert manifest["schema"] == SCHEMA
    entries = fixture_paths(manifest)
    assert len(entries) >= 6
    assert {entry["category"] for entry in entries} >= {
        "molecular", "ionic", "coordination", "porous", "low_symmetry", "stress"
    }
    assert all(len(entry["sha256"]) == 64 for entry in entries)


def test_runner_emits_versioned_json_and_csv_rows(monkeypatch, tmp_path: Path):
    entry = {
        "id": "fixture",
        "path": str(tmp_path / "fixture.cif"),
        "category": "molecular",
        "size_class": "small",
        "source": "test",
        "license": "MIT",
        "sha256": "a" * 64,
    }
    monkeypatch.setattr(runner, "load_manifest", lambda _path=None: {
        "schema": "mattervis.benchmarks/v1",
        "entries": [entry],
    })
    monkeypatch.setattr(runner, "fixture_paths", lambda _manifest: [entry])
    monkeypatch.setattr(
        runner,
        "build_pipeline_report",
        lambda *_args, **_kwargs: {
            "schema": "mattervis.perf.pipeline/v1",
            "fixture": {"sha256": "a" * 64},
            "stages": {"loader": {"mean_ms": 1.0, "median_ms": 1.0, "min_ms": 1.0, "max_ms": 1.0, "repeat": 1}},
            "figure": None,
            "exports": None,
            "oracle": {"counts": {"raw_atoms": 1}},
        },
    )

    suite = runner.run_suite(smoke=True)
    assert suite["schema"] == runner.SCHEMA
    assert suite["reports"][0]["manifest_entry"]["id"] == "fixture"
    assert suite["rows"][0]["stage"] == "loader"
    encoded = json.dumps(suite)
    assert "mattervis.perf.suite/v1" in encoded

    csv_path = tmp_path / "results.csv"
    runner.write_csv(suite["rows"], csv_path)
    assert "fixture,loader" in csv_path.read_text(encoding="utf-8")
