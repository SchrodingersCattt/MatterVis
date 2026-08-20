from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

from mat_viewer.perf.bench_pipeline import build_pipeline_report


ORACLES = Path(__file__).with_name("oracles") / "pipeline_v1.json"
ENTRIES = json.loads(ORACLES.read_text(encoding="utf-8"))["entries"]


def _installed_mck_revision() -> str | None:
    try:
        direct_url = json.loads(importlib.metadata.distribution("molcrys-kit").read_text("direct_url.json") or "null")
        commit_id = (direct_url or {}).get("vcs_info", {}).get("commit_id")
        if commit_id:
            return str(commit_id)
        source_url = str((direct_url or {}).get("url") or "")
        parsed = urlparse(source_url)
        if parsed.scheme != "file":
            return None
        local_path = url2pathname(parsed.path)
        if parsed.netloc:
            local_path = f"//{parsed.netloc}{local_path}"
        source = Path(local_path)
        return subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (FileNotFoundError, importlib.metadata.PackageNotFoundError, subprocess.CalledProcessError, TypeError, ValueError):
        return None


def _require_expected_mck(entry: dict) -> None:
    expected = entry["producer"]["molcrys_kit_revision"]
    actual = _installed_mck_revision()
    if actual is None:
        pytest.skip("installed MolCrysKit distribution does not expose a source revision")
    if actual != expected:
        pytest.fail(
            f"oracle requires MolCrysKit {expected}, installed revision is {actual or 'unknown'}"
        )


def test_installed_mck_revision_prefers_pep610_vcs_commit(monkeypatch):
    direct_url = json.dumps({
        "url": "https://github.com/SchrodingersCattt/MolCrysKit.git",
        "vcs_info": {"commit_id": "expected-git-commit"},
    })
    monkeypatch.setattr(
        importlib.metadata,
        "distribution",
        lambda _name: SimpleNamespace(read_text=lambda _path: direct_url),
    )
    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda *_args, **_kwargs: pytest.fail("PEP 610 commit must avoid a git subprocess"),
    )

    assert _installed_mck_revision() == "expected-git-commit"


def test_installed_mck_revision_resolves_pep610_file_url(monkeypatch, tmp_path):
    source = tmp_path / "MolCrysKit checkout"
    direct_url = json.dumps({"url": source.as_uri(), "dir_info": {}})
    observed = []
    monkeypatch.setattr(
        importlib.metadata,
        "distribution",
        lambda _name: SimpleNamespace(read_text=lambda _path: direct_url),
    )

    def fake_check_output(command, **_kwargs):
        observed.append(command)
        return "expected-local-commit"

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

    assert _installed_mck_revision() == "expected-local-commit"
    assert observed == [["git", "-C", str(source), "rev-parse", "HEAD"]]


def test_revision_mismatch_is_an_explicit_failure(monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "_installed_mck_revision", lambda: "wrong-revision")

    with pytest.raises(pytest.fail.Exception, match="requires MolCrysKit"):
        _require_expected_mck({"producer": {"molcrys_kit_revision": "expected-revision"}})


def test_revision_check_skips_when_distribution_has_no_source_revision(monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "_installed_mck_revision", lambda: None)

    with pytest.raises(pytest.skip.Exception, match="does not expose a source revision"):
        _require_expected_mck({"producer": {"molcrys_kit_revision": "expected-revision"}})


def test_dap4_pipeline_oracle():
    entry = ENTRIES["dap4-mattervis-051df0b-mck-a503bbd"]
    fixture = Path(__file__).resolve().parents[2] / "scripts" / "data" / "DAP-4.cif"

    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == entry["fixture"]["sha256"]
    _require_expected_mck(entry)
    report = build_pipeline_report(fixture, include_figure=False)

    assert report["oracle"] == entry["oracle"]


@pytest.mark.slow
@pytest.mark.timeout(600)
def test_dap_o4_formula_unit_oracle_when_fixture_is_configured():
    fixture = os.environ.get("MATTERVIS_DAP_O4_CIF")
    if not fixture:
        pytest.skip("set MATTERVIS_DAP_O4_CIF to run the external DAP-O4 oracle")
    path = Path(fixture)
    expected = ENTRIES["dap-o4-external-mck-00fa232"]

    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected["fixture"]["sha256"]
    _require_expected_mck(expected)
    report = build_pipeline_report(path, include_unit_cell=False, include_figure=False)

    assert report["oracle"] == expected["oracle"]
