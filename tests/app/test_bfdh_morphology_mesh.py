"""COD 1000043 BFDH morphology must be a closed convex solid on the crystal.

The hkl table for fluorite (Fm-3m, a = 5.462 Å) is a plausible ranking. The
3-D overlay used to keep only the smallest-d family, so at scale 4x the
viewport showed a clipped (2,2,1) sliver instead of a habit centred on the
cell.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from scipy.spatial import ConvexHull

from mat_viewer.app.backend import ViewerBackend
from mat_viewer.app.shared import DEFAULT_PRESET_PATH
from mat_viewer.loader import build_loaded_crystal
from mat_viewer.render.morphology import _morphology_center, morphology_world_points
from mat_viewer.render.viewport import _scene_ranges

CAF2_CIF = Path("tests/fixtures/cod_1000043.cif")


def _triangle_points(facets: list[dict]) -> np.ndarray:
    points = []
    for facet in facets:
        for triangle in facet.get("triangles") or []:
            points.extend(triangle)
    return np.asarray(points, dtype=float)


def _abs_family(miller) -> tuple[int, int, int]:
    return tuple(sorted(abs(int(value)) for value in miller))


@pytest.fixture
def caf2_backend(tmp_path):
    backend = ViewerBackend(preset_path=DEFAULT_PRESET_PATH, names=[], root_dir=str(tmp_path))
    bundle = build_loaded_crystal(name="cod-1000043", cif_path=str(CAF2_CIF))
    backend.bundles[bundle.name] = bundle
    backend.structure_names.append(bundle.name)
    backend.create_scene(structure=bundle.name, label="CaF2")
    try:
        yield backend
    finally:
        backend.close()


def test_caf2_bfdh_mesh_is_closed_convex_and_centered(caf2_backend):
    result = caf2_backend.run_bfdh_analysis(max_index=2, top_n=10)

    assert result["status"] == "ok"
    assert result["warnings"] == []
    families = {tuple(facet["miller_index"]): facet for facet in result["facets"]}
    assert len(families) == 6
    assert abs(families[(1, 0, 0)]["d_hkl"] - 5.462) < 0.01
    # {100} in m-3m has six faces. Reporting 1 meant equivalents were dropped.
    assert families[(1, 0, 0)]["multiplicity"] == 6
    assert families[(1, 1, 1)]["multiplicity"] == 8

    facets = result["wulff_facets"]
    assert len(facets) >= 6
    labels = {_abs_family(facet["miller"]) for facet in facets}
    assert (0, 0, 1) in labels
    assert not all(_abs_family(facet["miller"]) == (1, 2, 2) for facet in facets)

    local = _triangle_points(facets)
    assert local.shape[1] == 3
    hull = ConvexHull(local)
    assert float(hull.volume) > 1.0
    spans = local.max(axis=0) - local.min(axis=0)
    assert float(spans.min() / spans.max()) > 0.8
    # Stored about the origin so the scale slider grows it about the cell centre.
    assert np.linalg.norm(local.mean(axis=0)) < 1e-4

    state = caf2_backend.get_state()
    assert state["display_mode"] == "unit_cell"
    state["bfdh_morphology"] = {
        "facets": facets,
        "enabled": True,
        "scale": 4.0,
        "opacity": 0.8,
    }
    scene = caf2_backend.scene_for_state(state)
    style = {"display_mode": "unit_cell", "show_unit_cell": True}
    world = morphology_world_points(scene, style)
    center = _morphology_center(scene)
    assert np.allclose(world.mean(axis=0), center, atol=1e-4)

    atoms = np.asarray([atom["cart"] for atom in scene["draw_atoms"]], dtype=float)
    atom_mid = (atoms.min(axis=0) + atoms.max(axis=0)) / 2.0
    cell_edge = float(np.max(np.linalg.norm(np.asarray(scene["M"], dtype=float), axis=1)))
    assert np.linalg.norm(world.mean(axis=0) - atom_mid) <= 0.25 * cell_edge

    world_hull = ConvexHull(world)
    for point in (center, atom_mid):
        inside = world_hull.equations[:, :3] @ point + world_hull.equations[:, 3]
        assert np.all(inside <= 1e-4)

    ranges = _scene_ranges(scene, style)
    mesh_min = world.min(axis=0)
    mesh_max = world.max(axis=0)
    for axis in range(3):
        assert ranges[axis][0] <= mesh_min[axis] + 1e-6
        assert ranges[axis][1] >= mesh_max[axis] - 1e-6
