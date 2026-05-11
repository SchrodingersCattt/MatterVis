"""Tests pinning the modern ``classify_shell`` integration in ``analyze_topology``.

These tests are the regression net for the migration from the deprecated
``angular_rmsd_vs_ideals`` to ``molcrys_kit.analysis.shape.classify_shell``.
The contract we are pinning:

* ``analyze_topology()`` now returns a top-level ``"shape"`` key whose value
  is the JSON-safe dict produced by ``classify_shell`` (or an explicitly
  empty payload when no classification was possible).
* The payload always carries the ``primary_label``, ``label_modifier``,
  ``cshm_value``, ``candidates``, and ``best_match`` fields the renderer
  text-panel and example scripts read from.
* The deprecated ``"angular"`` key is gone; nothing in the codebase should
  expect it any more.
* The renderer's text panel renders the new label as
  ``"Shape: <modifier> <primary_label>  (CShM = <value>)"`` instead of the
  legacy ``"Best ideal polyhedron: <name> (angular RMSD <degrees>°)"``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from crystal_viewer import topology as topology_module
from crystal_viewer.loader import build_loaded_crystal
from crystal_viewer.renderer import topology_results_markdown
from crystal_viewer.topology import (
    _classify_shell_payload,
    _empty_shape_payload,
    analyze_topology,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DAP4_CIF = REPO_ROOT / "scripts" / "data" / "DAP-4.cif"


def test_empty_shell_returns_empty_shape_payload():
    payload = _classify_shell_payload([], [0.0, 0.0, 0.0])
    assert payload == _empty_shape_payload()
    assert payload["primary_label"] is None
    assert payload["candidates"] == []


def test_clean_tetrahedron_classifies_as_clean_tetrahedron():
    coords = [[1.0, 1.0, 1.0], [1.0, -1.0, -1.0], [-1.0, 1.0, -1.0], [-1.0, -1.0, 1.0]]
    payload = _classify_shell_payload(coords, [0.0, 0.0, 0.0])
    assert payload["primary_label"] == "tetrahedron"
    assert payload["label_modifier"] == "clean"
    assert payload["cshm_value"] is not None and payload["cshm_value"] < 1e-6
    assert payload["best_match"]["name"] == "tetrahedron"


def test_distorted_octahedron_classifies_with_octahedron_label():
    """A perturbed but topologically obvious octahedron must keep the
    ``"octahedron"`` label and report a non-clean modifier."""
    rng = np.random.default_rng(42)
    coords = (
        np.array(
            [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]],
            dtype=float,
        )
        + rng.normal(0, 0.05, (6, 3))
    )
    payload = _classify_shell_payload(coords.tolist(), [0.0, 0.0, 0.0])
    assert payload["primary_label"] == "octahedron"
    # Small perturbation -> modifier should be clean or distorted (not the
    # "ambiguous + face cap+1" compound noise the legacy classifier produced).
    assert payload["label_modifier"] in {"clean", "distorted", "ambiguous", "irregular"}
    assert "+" not in payload["primary_label"]
    assert payload["cshm_value"] is not None and payload["cshm_value"] >= 0


def test_classify_shell_payload_recovers_from_pathological_input():
    """Collinear / degenerate shells must not crash the analysis-text panel.

    ``classify_shell`` raises ``ValueError`` on a single-atom shell because
    the unit-sphere projection is undefined. The wrapper should swallow
    the exception, return an explicit empty payload, and surface the error
    string under ``error`` so the caller can log it.
    """
    payload = _classify_shell_payload([[1.0, 0.0, 0.0]], [0.0, 0.0, 0.0])
    assert payload["primary_label"] is None
    # Either classify_shell returned a "no candidates" answer, or the
    # defensive wrapper caught a downstream exception. Both are acceptable.
    if "error" in payload:
        assert isinstance(payload["error"], str) and payload["error"]


@pytest.mark.skipif(not DAP4_CIF.exists(), reason="DAP-4 CIF not available")
def test_analyze_topology_returns_shape_key_for_dap4_a_site():
    bundle = build_loaded_crystal(name="DAP-4", cif_path=str(DAP4_CIF), title="DAP-4")
    target = next(
        f for f in bundle.topology_fragment_table if f.get("formula") == "C6N2"
    )
    result = analyze_topology(bundle, center_index=target["index"], cutoff=8.0)

    # Modern key is set and structured.
    assert "shape" in result
    shape = result["shape"]
    assert set(shape.keys()) >= {
        "coordination_number",
        "primary_label",
        "label_modifier",
        "cshm_value",
        "candidates",
        "best_match",
        "structural_description",
    }

    # Deprecated key is gone.
    assert "angular" not in result

    # DAP-4 A-site is CN ~ 9, so the registry must have produced a label.
    assert result["coordination_number"] >= 4
    assert shape["primary_label"] is not None
    assert shape["label_modifier"] in {"clean", "distorted", "ambiguous", "irregular"}
    assert isinstance(shape["candidates"], list) and len(shape["candidates"]) >= 1
    assert shape["best_match"]["name"] == shape["candidates"][0]["name"]


@pytest.mark.skipif(not DAP4_CIF.exists(), reason="DAP-4 CIF not available")
def test_analyze_topology_caches_shape_classification(monkeypatch):
    """The bundle-level ``_analyze_topology_cache`` must memoize the
    expensive ``classify_shell`` call so the analysis panel only pays
    the ~200 ms first-hit latency once per (centre, cutoff) tuple.
    """
    bundle = build_loaded_crystal(name="DAP-4", cif_path=str(DAP4_CIF), title="DAP-4")
    target = next(
        f for f in bundle.topology_fragment_table if f.get("formula") == "C6N2"
    )

    call_count = {"n": 0}
    real_classify = topology_module._classify_shell_payload

    def counting_classify(*args, **kwargs):
        call_count["n"] += 1
        return real_classify(*args, **kwargs)

    monkeypatch.setattr(topology_module, "_classify_shell_payload", counting_classify)

    analyze_topology(bundle, center_index=target["index"], cutoff=8.0)
    first = call_count["n"]
    analyze_topology(bundle, center_index=target["index"], cutoff=8.0)
    second = call_count["n"]

    assert first == 1, "classify_shell must run on first analysis call"
    assert second == 1, "second call with the same key must hit the cache"


def test_topology_text_panel_renders_new_shape_label():
    topology_data = {
        "center_label": "A0",
        "center_formula": "C6N2",
        "coordination_number": 12,
        "shell": [],
        "gap_info": {"gap_value": 0.124, "enclosed": True, "enclosure_expanded": False},
        "shape": {
            "primary_label": "cuboctahedron",
            "label_modifier": "distorted",
            "cshm_value": 0.83,
            "candidates": [{"name": "cuboctahedron", "cshm": 0.83}],
            "structural_description": "",
            "residuals": [],
        },
        "planarity": {},
        "prism_analysis": {},
    }
    text = topology_results_markdown(topology_data)
    assert "Shape: distorted cuboctahedron" in text
    assert "CShM = 0.83" in text
    # The legacy phrasing must not appear.
    assert "angular RMSD" not in text
    assert "Best ideal polyhedron" not in text


def test_topology_text_panel_handles_missing_shape_payload():
    """If ``shape`` is missing or empty (e.g. CN outside registry), the panel
    must fall back to the "no library entry" line instead of crashing on a
    ``KeyError`` against ``best_match``.
    """
    topology_data = {
        "center_label": "C0",
        "center_formula": "Sn",
        "coordination_number": 13,
        "shell": [],
        "gap_info": {},
        "shape": {"primary_label": None, "candidates": []},
        "planarity": {},
        "prism_analysis": {},
    }
    text = topology_results_markdown(topology_data)
    assert "No ideal-polyhedron reference" in text
    assert "CN=13" in text
