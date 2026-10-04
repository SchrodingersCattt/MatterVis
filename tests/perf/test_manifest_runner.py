from __future__ import annotations

import json

import pytest

from mat_viewer.perf import runner
from mat_viewer.perf.manifest import fixture_paths, load_manifest


def test_default_benchmark_manifest_resolves_and_verifies() -> None:
    manifest = load_manifest()
    entries = fixture_paths(manifest)

    assert manifest["schema"] == "mattervis.benchmarks/v1"
    assert len(entries) >= 6
    assert {entry["id"] for entry in entries} >= {
        "molecular-water",
        "ionic-nacl",
        "coordination-zinc",
        "porous-framework",
        "low-symmetry",
        "dap4-stress",
    }
    assert all(entry["sha256"] for entry in entries)


def test_manifest_rejects_duplicate_paths(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema": "mattervis.benchmarks/v1",
                "entries": [
                    {"id": "one", "path": "a.cif", "category": "x", "size_class": "small"},
                    {"id": "two", "path": "a.cif", "category": "x", "size_class": "small"},
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unique id/path"):
        load_manifest(path)


def test_runner_smoke_contract_without_rebuilding_figures(monkeypatch) -> None:
    def fake_report(path, **kwargs):
        return {
            "fixture": {"path": str(path)},
            "stages": {"loader": {"mean_ms": 1.0, "repeat": 1}},
            "figure": None,
            "exports": None,
        }

    monkeypatch.setattr(runner, "build_pipeline_report", fake_report)
    suite = runner.run_suite(smoke=True)

    assert suite["schema"] == "mattervis.perf.suite/v1"
    assert suite["manifest"]["entries"] == 1
    assert suite["reports"][0]["manifest_entry"]["id"] == "molecular-water"
    assert suite["rows"][0]["stage"] == "loader"
