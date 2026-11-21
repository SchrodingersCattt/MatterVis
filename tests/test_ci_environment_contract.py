"""The merge gate stays pinned and the drift workflow stays optional."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
RELEASE = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
DRIFT = (ROOT / ".github/workflows/compatibility-drift.yml").read_text(encoding="utf-8")
RECORDER = (ROOT / ".github/scripts/record_ci_environment.py").read_text(encoding="utf-8")
CONSTRAINTS = ROOT / "requirements/ci-constraints.txt"
BUILD_INSTALL = (
    "python -m pip install -c requirements/ci-constraints.txt "
    'build setuptools "setuptools-scm[toml]"'
)
MOLCRYS_COMMIT = "ee45c7e7c9e44f50b0b55ed46ee5dca306550541"


def _requirement_lines() -> list[str]:
    lines = []
    for raw in CONSTRAINTS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def test_constraints_pin_every_package_and_the_molcrys_commit() -> None:
    lines = _requirement_lines()
    assert lines
    for line in lines:
        pinned = "==" in line or line.startswith("molcrys-kit @ ")
        assert pinned, line
    assert "pytest==9.2.0" in lines
    assert "numpy==2.2.6" in lines
    molcrys = next(line for line in lines if line.startswith("molcrys-kit @"))
    assert molcrys.endswith(MOLCRYS_COMMIT)
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert MOLCRYS_COMMIT in pyproject


def test_merge_gate_uses_the_pinned_toolchain() -> None:
    assert "ubuntu-latest" not in CI
    assert "ubuntu-24.04" in CI
    assert 'CI_NODE_VERSION: "22.23.4"' in CI
    assert 'CI_CHROME_VERSION: "156.0.8078.47"' in CI
    assert 'CI_PLAYWRIGHT_CORE: "1.64.1"' in CI
    assert "requirements/ci-constraints.txt" in CI
    assert "chrome-version: stable" not in CI
    assert "node-version: \"22\"" not in CI
    assert "npm install --no-save playwright-core\n" not in CI
    before, docs = CI.split("docs-policy-warn:", 1)
    assert "continue-on-error" not in before
    assert "continue-on-error: true" in docs
    assert "minimum chemistry" not in CI.lower()


def test_package_build_uses_pinned_backends_without_isolation() -> None:
    lines = _requirement_lines()
    assert "setuptools==84.2.1" in lines
    assert "setuptools-scm==8.3.1" in lines
    assert "build==1.6.1" in lines
    assert "pyproject-hooks==1.3.3" in lines
    for workflow in (CI, RELEASE):
        assert "PIP_CONSTRAINT: requirements/ci-constraints.txt" in workflow
        assert BUILD_INSTALL in workflow
        assert "python -m build --no-isolation" in workflow
        assert "python -m build\n" not in workflow


def _jobs(workflow: str) -> dict[str, str]:
    lines = workflow.splitlines()
    start = lines.index("jobs:")
    jobs: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[start + 1 :]:
        if line and not line.startswith(" "):
            break
        if (
            line.startswith("  ")
            and not line.startswith("   ")
            and line.rstrip().endswith(":")
        ):
            current = line.strip()[:-1]
            jobs[current] = []
            continue
        if current is not None:
            jobs[current].append(line)
    return {name: "\n".join(body) for name, body in jobs.items()}


def _runner(job_body: str) -> str:
    runs = [
        line.strip()
        for line in job_body.splitlines()
        if line.strip().startswith("runs-on:")
    ]
    assert len(runs) == 1
    return runs[0].split(":", 1)[1].strip()


def test_expected_runner_comes_from_each_workflow() -> None:
    assert "ubuntu-24.04" not in RECORDER
    assert "ubuntu-latest" not in RECORDER
    assert "windows-latest" not in RECORDER
    assert "--expected-runner" in RECORDER
    drift_runs = [
        line.strip()
        for line in DRIFT.splitlines()
        if line.strip().startswith("runs-on:")
    ]
    drift_calls = [
        line.strip()
        for line in DRIFT.splitlines()
        if "record_ci_environment.py" in line
    ]
    assert drift_runs
    assert drift_calls
    assert all(line == "runs-on: ubuntu-latest" for line in drift_runs)
    assert all("--expected-runner ubuntu-latest" in line for line in drift_calls)

    jobs = _jobs(CI)
    windows = jobs["windows-upload-share-modes"]
    assert _runner(windows) == "windows-latest"
    assert "tests/app/test_windows_file_cleanup.py" in windows
    assert "Windows share-mode tests did not execute" in windows
    assert "--expected-runner windows-latest" in windows
    for name, body in jobs.items():
        runner = _runner(body)
        calls = [
            line.strip()
            for line in body.splitlines()
            if "record_ci_environment.py" in line
        ]
        if name == "windows-upload-share-modes":
            assert runner == "windows-latest"
            assert calls
        elif name == "docs-policy-warn":
            assert runner == "ubuntu-24.04"
            assert not calls
        else:
            assert runner == "ubuntu-24.04", name
            assert calls, name
        assert all(f"--expected-runner {runner}" in line for line in calls), name


def test_camera_job_keeps_traces_and_environment_artifacts() -> None:
    camera = CI.split("camera-webgl:", 1)[1].split("benchmark-smoke:", 1)[0]
    assert "name: browser-artifacts" in camera
    assert "path: browser-artifacts/" in camera
    assert "name: ci-diagnostics-browser" in camera
    assert "pytest-browser.xml" in camera
    assert "ci-diagnostics/" in camera


def test_drift_workflow_is_scheduled_and_not_advisory() -> None:
    assert "schedule:" in DRIFT
    assert "workflow_dispatch:" in DRIFT
    assert "pull_request:" not in DRIFT
    assert "ubuntu-latest" in DRIFT
    assert "chrome-version: stable" in DRIFT
    assert "requirements/ci-constraints.txt" not in DRIFT
    assert "continue-on-error" not in DRIFT
    assert "node .github/scripts/chrome_playwright_probe.js" in DRIFT


def test_failure_signatures_keep_only_failing_cases(tmp_path: Path) -> None:
    xml_path = tmp_path / "pytest.xml"
    xml_path.write_text(
        """<?xml version="1.0" ?>
        <testsuite>
          <testcase classname="tests.sample" name="passes"></testcase>
          <testcase classname="tests.sample" name="breaks">
            <failure message="assert 1 == 2">trace</failure>
          </testcase>
        </testsuite>
        """,
        encoding="utf-8",
    )
    output = tmp_path / "failure-signatures.json"
    subprocess.check_call(
        [
            sys.executable,
            str(ROOT / ".github/scripts/junit_failure_signatures.py"),
            str(xml_path),
            "--output",
            str(output),
        ]
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["failure_count"] == 1
    assert payload["failures"][0]["name"] == "breaks"
    assert payload["failures"][0]["message"] == "assert 1 == 2"


def test_attempt_record_marks_a_rerun(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("GITHUB_RUN_ID", "99")
    subprocess.check_call(
        [
            sys.executable,
            str(ROOT / ".github/scripts/record_ci_environment.py"),
            "--output-dir",
            str(tmp_path),
            "--expected-runner",
            "ubuntu-latest",
        ],
        env={**os.environ, "GITHUB_RUN_ATTEMPT": "2", "GITHUB_RUN_ID": "99"},
    )
    attempt = json.loads((tmp_path / "ci-attempt.json").read_text(encoding="utf-8"))
    assert attempt["run_attempt"] == 2
    assert attempt["first_attempt"] is False
    assert "cancel" in attempt["note"]
    assert (tmp_path / "ci-freeze.txt").is_file()
    environment = json.loads((tmp_path / "ci-environment.json").read_text(encoding="utf-8"))
    assert environment["declared"]["runner"] == "ubuntu-latest"
    actual = environment["actual_os"]
    assert actual["platform"]
    assert actual["pretty_name"]
    assert actual["pretty_name"] != environment["declared"]["runner"]
