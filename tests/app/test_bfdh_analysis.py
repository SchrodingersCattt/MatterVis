"""Unit tests for BFDH analysis integration."""
from __future__ import annotations

from pathlib import Path

import pytest

from mat_viewer.app.backend import ViewerBackend
from mat_viewer.app.shared import DEFAULT_PRESET_PATH
from mat_viewer.loader import build_empty_bundle

SY_CIF = Path("scripts/data/SY.cif")


@pytest.fixture
def backend_with_crystal(tmp_path):
    import os

    from mat_viewer.app.shared import WORKSPACE_DIR
    from mat_viewer.loader import build_loaded_crystal
    backend = ViewerBackend(preset_path=DEFAULT_PRESET_PATH, root_dir=os.path.join(WORKSPACE_DIR, "scripts", "data"))
    cif_path = os.path.join(WORKSPACE_DIR, "scripts", "data", "SY.cif")
    bundle = build_loaded_crystal(name="SY", cif_path=cif_path)
    backend.bundles["SY"] = bundle
    if "SY" not in backend.structure_names:
        backend.structure_names.append("SY")
    backend.create_scene(structure="SY", label="SY")
    return backend


@pytest.fixture
def backend_empty(tmp_path):
    backend = ViewerBackend(preset_path=DEFAULT_PRESET_PATH, root_dir=str(tmp_path))
    # Create an empty bundle without a crystal
    empty = build_empty_bundle(name="empty")
    backend.bundles["empty"] = empty
    backend.structure_names.append("empty")
    backend.create_scene(structure="empty", label="empty")
    return backend


@pytest.mark.skipif(not SY_CIF.exists(), reason="local SY CIF fixture is not present")
def test_run_bfdh_analysis_success(backend_with_crystal):
    """Test that BFDH analysis returns expected facets for a valid crystal."""
    backend = backend_with_crystal
    scene_id = backend.active_scene_id()
    
    result = backend.run_bfdh_analysis(scene_id=scene_id, max_index=1, top_n=3)
    
    assert result["status"] == "ok"
    assert not result["warnings"]
    assert "facets" in result
    assert len(result["facets"]) > 0
    
    first_facet = result["facets"][0]
    assert "miller_index" in first_facet
    assert "d_hkl" in first_facet
    assert "relative_morphological_importance" in first_facet


def test_run_bfdh_analysis_no_crystal(backend_empty):
    """Test that BFDH analysis handles missing crystal gracefully."""
    backend = backend_empty
    scene_id = backend.active_scene_id()
    
    result = backend.run_bfdh_analysis(scene_id=scene_id)
    
    assert result["status"] == "error"
    assert "requires bundle.crystal" in result["warnings"][0]
    assert result["facets"] == []


@pytest.mark.parametrize("max_index", [0, -1])
def test_run_bfdh_analysis_rejects_nonpositive_max_index(backend_empty, max_index):
    result = backend_empty.run_bfdh_analysis(max_index=max_index)

    assert result["status"] == "error"
    assert result["facets"] == []
    assert "at least 1" in result["warnings"][0]


def test_run_bfdh_analysis_defaults_zero_top_n_to_ten(backend_empty, monkeypatch):
    import molcrys_kit.analysis

    calls = []
    bundle = backend_empty.get_bundle(backend_empty.get_state()["structure"])
    bundle.crystal = object()

    def fake_enumerate(crystal, *, max_index, top_n):
        calls.append((crystal, max_index, top_n))
        return []

    monkeypatch.setattr(molcrys_kit.analysis, "enumerate_bfdh_facets", fake_enumerate)
    result = backend_empty.run_bfdh_analysis(max_index=1, top_n=0)

    assert result["status"] == "ok"
    assert calls == [(bundle.crystal, 1, 10)]
