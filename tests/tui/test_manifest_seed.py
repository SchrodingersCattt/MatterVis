from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = ROOT / "scripts" / "tui_benchmark"
SCHEMA_ROOT = BENCHMARK_ROOT / "schemas"
ORACLE_PATH = BENCHMARK_ROOT / "manifests" / "synthetic_oracles.v1.json"
ELIGIBILITY_PATH = BENCHMARK_ROOT / "manifests" / "task_eligibility.v1.json"
EXPECTED_ARMS = {
    "IMG",
    "TUI-ART",
    "TUI-INT",
    "TUI-STRUCT",
    "CIF",
    "ASE",
    "PMG",
    "MCK",
    "MV-API",
    "TUI-V1",
}
EXPECTED_STATUSES = {"eligible", "abstention_test", "not_applicable"}


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_unique(values, *, what: str) -> None:
    values = list(values)
    assert len(values) == len(set(values)), f"duplicate {what}: {values}"


def test_seed_schemas_are_closed_draft_2020_12_documents() -> None:
    schemas = {}
    for path in sorted(SCHEMA_ROOT.glob("*.schema.json")):
        payload = _read_json(path)
        assert payload["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert payload["type"] == "object"
        assert payload["additionalProperties"] is False
        schemas[path.name] = payload

    identity_ref = schemas["oracle_manifest.schema.json"]["properties"]["cases"][
        "items"
    ]["properties"]["identities"]["$ref"]
    assert identity_ref in schemas
    oracle_base = schemas["oracle_manifest.schema.json"]["$id"].rsplit("/", 1)[0]
    assert schemas[identity_ref]["$id"] == f"{oracle_base}/{identity_ref}"


def test_oracle_fixtures_exist_and_match_hashes() -> None:
    manifest = _read_json(ORACLE_PATH)
    assert manifest["schema"] == "mattervis.tui-benchmark.oracle/v1"
    assert manifest["license"] == "CC0-1.0"

    _assert_unique((case["case_id"] for case in manifest["cases"]), what="case IDs")
    for case in manifest["cases"]:
        fixture = BENCHMARK_ROOT / case["fixture_path"]
        assert fixture.is_file()
        assert hashlib.sha256(fixture.read_bytes()).hexdigest() == case["fixture_sha256"]
        assert case["oracle"]["uses_tui_implementation"] is False
        assert case["generator"]["kind"] == "analytic"
        assert case["oracle"]["kind"] == "analytic"


def test_layered_identity_references_resolve() -> None:
    manifest = _read_json(ORACLE_PATH)
    for case in manifest["cases"]:
        identities = case["identities"]
        source_site_ids = [
            item["source_site_id"] for item in identities["source_sites"]
        ]
        expanded_ids = [
            item["expanded_instance_id"] for item in identities["expanded_instances"]
        ]
        display_ids = [
            item["display_copy_id"] for item in identities["display_copies"]
        ]
        molecule_ids = [item["molecule_id"] for item in identities["molecules"]]
        fragment_ids = [item["fragment_id"] for item in identities["fragments"]]

        _assert_unique(source_site_ids, what=f"{case['case_id']} source IDs")
        _assert_unique(expanded_ids, what=f"{case['case_id']} expanded IDs")
        _assert_unique(display_ids, what=f"{case['case_id']} display IDs")
        _assert_unique(molecule_ids, what=f"{case['case_id']} molecule IDs")
        _assert_unique(fragment_ids, what=f"{case['case_id']} fragment IDs")

        source_sites = set(source_site_ids)
        expanded = set(expanded_ids)
        molecules = set(molecule_ids)

        source_indices = []
        for item in identities["expanded_instances"]:
            assert item["source_site_id"] in source_sites
            source_indices.append(item["source_index"])
        _assert_unique(source_indices, what=f"{case['case_id']} source indices")

        for item in identities["display_copies"]:
            assert item["expanded_instance_id"] in expanded
            assert len(item["image_shift"]) == 3
            assert all(isinstance(value, int) for value in item["image_shift"])

        for item in identities["molecules"]:
            assert set(item["expanded_instance_ids"]) <= expanded
        for item in identities["fragments"]:
            assert set(item["expanded_instance_ids"]) <= expanded
            if item.get("molecule_id") is not None:
                assert item["molecule_id"] in molecules


def test_seed_tasks_have_complete_eligibility_rows() -> None:
    oracle = _read_json(ORACLE_PATH)
    eligibility = _read_json(ELIGIBILITY_PATH)
    assert eligibility["schema"] == "mattervis.tui-benchmark.eligibility/v1"
    assert set(eligibility["arms"]) == EXPECTED_ARMS
    assert len(eligibility["arms"]) == len(EXPECTED_ARMS)

    oracle_task_ids = [
        task["task_id"]
        for case in oracle["cases"]
        for task in case["tasks"]
    ]
    eligibility_task_ids = [row["task_id"] for row in eligibility["tasks"]]
    _assert_unique(oracle_task_ids, what="oracle task IDs")
    _assert_unique(eligibility_task_ids, what="eligibility task IDs")
    oracle_tasks = set(oracle_task_ids)
    rows = {row["task_id"]: row for row in eligibility["tasks"]}
    assert set(rows) == oracle_tasks

    for task_id, row in rows.items():
        assert row["suite"] in {"representation", "end_to_end", "conformance"}
        assert set(row["arms"]) == EXPECTED_ARMS
        for arm, status in row["arms"].items():
            assert arm in EXPECTED_ARMS
            assert status["status"] in EXPECTED_STATUSES
            assert status["reason"].strip()
        assert row["arms"]["TUI-V1"]["status"] == "not_applicable", task_id

    for case in oracle["cases"]:
        for task in case["tasks"]:
            eligible_arms = {
                arm
                for arm, status in rows[task["task_id"]]["arms"].items()
                if status["status"] == "eligible"
            }
            assert set(task["observable_by"]) == eligible_arms


def test_audit_payload_generation_is_deterministic() -> None:
    script = ROOT / "scripts" / "tui_agent_audit.py"
    spec = importlib.util.spec_from_file_location("tui_agent_audit", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    first = module.build_payload()
    second = module.build_payload()
    assert first["schema"] == "mattervis.tui-characterization/v1"
    assert set(first["measurements"]) == {
        "lattice",
        "projection",
        "zoom",
        "small_terminal",
        "disorder",
        "formula_unit",
        "pbc_bonds",
        "observation_scopes",
        "rotation_scale",
    }
    assert first["measurements"] == second["measurements"]
    assert first["fixtures"] == second["fixtures"]
    assert first["inputs"] == second["inputs"]
