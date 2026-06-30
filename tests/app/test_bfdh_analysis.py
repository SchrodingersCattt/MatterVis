"""Unit tests for BFDH analysis integration."""
from __future__ import annotations

import pytest

from crystal_viewer.app.backend import ViewerBackend
from crystal_viewer.app.shared import DEFAULT_PRESET_PATH
from crystal_viewer.loader import build_empty_bundle


@pytest.fixture
def backend_with_crystal(tmp_path):
    from crystal_viewer.app.shared import WORKSPACE_DIR
    import os
    from crystal_viewer.loader import build_loaded_crystal
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
