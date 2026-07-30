from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import subprocess
from pathlib import Path

import pytest

from crystal_viewer.perf.bench_pipeline import build_pipeline_report


ORACLES = Path(__file__).with_name("oracles") / "pipeline_v1.json"
ENTRY = "dap-o4-external-mck-00fa232"


def _installed_mck_revision() -> str | None:
    try:
        direct_url = json.loads(
            importlib.metadata.distribution("molcrys-kit").read_text("direct_url.json") or "null"
        )
        source = Path(str((direct_url or {}).get("url") or "").replace("file://", ""))
        return subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (FileNotFoundError, importlib.metadata.PackageNotFoundError, subprocess.CalledProcessError, TypeError, ValueError):
        return None


@pytest.mark.slow
@pytest.mark.timeout(600)
def test_dap_o4_formula_unit_oracle_when_fixture_is_configured():
    fixture = os.environ.get("MATTERVIS_DAP_O4_CIF")
    if not fixture:
        pytest.skip("set MATTERVIS_DAP_O4_CIF to run the external DAP-O4 oracle")
    path = Path(fixture)
    expected = json.loads(ORACLES.read_text(encoding="utf-8"))["entries"][ENTRY]

    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected["fixture"]["sha256"]
    if _installed_mck_revision() != expected["producer"]["molcrys_kit_revision"]:
        pytest.skip("DAP-O4 oracle is pinned to a different MolCrysKit revision")
    report = build_pipeline_report(path, include_unit_cell=False, include_figure=False)

    assert report["oracle"] == expected["oracle"]
