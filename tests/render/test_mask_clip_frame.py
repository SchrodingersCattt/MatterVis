"""Mask, smooth, and primitive-cell clip, then frame the stored mesh.

The field, the density, and the cell are constructed here. The density mask
is the cube extractor. Smoothing is PyVista Taubin with the figure's pass
band, iteration count, and collapse rejection. The clip is
``cell_clip_planes``. Both projections then read the stored vertices.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import pyvista as pv

from mat_viewer.cli import _camera_spec
from mat_viewer.cube.cpu import cube_isosurface_meshes
from mat_viewer.cube.io import BOHR_TO_ANGSTROM, CubeData
from mat_viewer.render.cell_clip import cell_clip_planes
from mat_viewer.render.isosurface_framing import (
    cube_grid_corner_extents,
    ensure_structure_isosurfaces,
    orthographic_scale_for_points,
    scale_perspective_distance,
)

# Two scalar wells. The density ceiling removes the first. The primitive
# cell cuts the remaining well on the plane x = 7.75 Å. The grid is large
# enough that the fitted radius stays above the 0.5 Å floor, so the
# perspective distance follows the clip.
_SHAPE = (21, 21, 21)
_SPACING = 0.5
_HIGH_WELL = np.array([2.5, 5.0, 5.0])
_LOW_WELL = np.array([7.5, 5.0, 5.0])
_FIELD_SIGMA = 1.1
_DENSITY_SIGMA = 1.4
_ISOVALUE = 0.5
_STRIDE = 2
_DENSITY_CUTOFF = 0.05
_CELL_ORIGIN = np.array([7.75, 3.50, 3.50])
_CELL_VECTORS = np.diag([2.00, 3.00, 3.00])
_TAUBIN_ITERATIONS = 20
_TAUBIN_PASS_BAND = 0.1
_COLLAPSE_SPAN_FRACTION = 0.5
_PLANE_TOLERANCE = 1.0e-8
_VIEWPORT = (900, 720)


def _cube(values: np.ndarray, name: str) -> CubeData:
    return CubeData(
        title=name,
        comment="",
        atoms=[],
        origin=np.zeros(3),
        axes=np.eye(3) * _SPACING,
        values=np.asarray(values, dtype=float),
        path=Path(name),
    )


def _wells() -> tuple[CubeData, CubeData]:
    coords = np.indices(_SHAPE, dtype=float) * _SPACING
    high = np.sqrt(sum((coords[axis] - _HIGH_WELL[axis]) ** 2 for axis in range(3)))
    low = np.sqrt(sum((coords[axis] - _LOW_WELL[axis]) ** 2 for axis in range(3)))
    field = 1.0 - 1.6 * np.exp(-((high / _FIELD_SIGMA) ** 2)) - 1.6 * np.exp(
        -((low / _FIELD_SIGMA) ** 2)
    )
    density = 0.01 + 0.20 * np.exp(-((high / _DENSITY_SIGMA) ** 2))
    return _cube(field, "field.cube"), _cube(density, "density.cube")


def _write_cube(path: Path, cube: CubeData) -> None:
    bohr = 1.0 / BOHR_TO_ANGSTROM
    origin = np.asarray(cube.origin, dtype=float) * bohr
    axes = np.asarray(cube.axes, dtype=float) * bohr
    shape = cube.values.shape
    values = np.asarray(cube.values, dtype=float).ravel()
    lines = [
        " ".join(f"{value:.8e}" for value in values[index : index + 6])
        for index in range(0, len(values), 6)
    ]
    path.write_text(
        "\n".join(
            [
                cube.title,
                "synthetic grid",
                f"0 {origin[0]:.8e} {origin[1]:.8e} {origin[2]:.8e}",
                f"{shape[0]} {axes[0, 0]:.8e} {axes[0, 1]:.8e} {axes[0, 2]:.8e}",
                f"{shape[1]} {axes[1, 0]:.8e} {axes[1, 1]:.8e} {axes[1, 2]:.8e}",
                f"{shape[2]} {axes[2, 0]:.8e} {axes[2, 1]:.8e} {axes[2, 2]:.8e}",
                *lines,
                "",
            ]
        ),
        encoding="utf-8",
    )


def _structure(cube: CubeData) -> SimpleNamespace:
    scene = {
        "M": cube.lattice,
        "cube_data": cube,
        "draw_atoms": [],
        "bounds": {
            "center": (cube.lattice.sum(axis=0) * 0.5).tolist(),
            "mins": [0.0, 0.0, 0.0],
            "maxs": cube.lattice.sum(axis=0).tolist(),
        },
    }
    bundle = SimpleNamespace(M=cube.lattice, cube_data=cube, scene=scene)
    return SimpleNamespace(
        frames=(SimpleNamespace(bundle=bundle),),
        input_format="cube",
    )


def _camera_args(**overrides) -> Namespace:
    values = {
        "output": "figure.png",
        "width": _VIEWPORT[0],
        "height": _VIEWPORT[1],
        "show_hydrogen": True,
        "show_unit_cell": True,
        "camera_axis": None,
        "view_direction": None,
        "camera_position": None,
        "camera_up": None,
        "camera_target": None,
        "camera_distance": 1.8,
        "projection": "orthographic",
        "ortho_scale": None,
        "style": "ball_stick",
        "atom_scale": 1.0,
        "bond_radius": 0.15,
        "min_atom_radius": None,
        "isovalue": _ISOVALUE,
        "isosurface_stride": _STRIDE,
        "zoom": 1.0,
        "framing_margin": 1.12,
        "view": "auto",
        "isosurface_density_mask": False,
        "isosurface_density_cube": None,
        "isosurface_density_cutoff": None,
    }
    values.update(overrides)
    return Namespace(**values)


def _closer_to(vertices: np.ndarray, target: np.ndarray, other: np.ndarray) -> np.ndarray:
    return np.linalg.norm(vertices - target, axis=1) < np.linalg.norm(
        vertices - other, axis=1
    )


def _edge_lengths(vertices: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    lengths = []
    for face in triangles:
        for start, end in ((0, 1), (1, 2), (2, 0)):
            lengths.append(float(np.linalg.norm(vertices[face[start]] - vertices[face[end]])))
    return np.asarray(lengths, dtype=float)


def _smooth_taubin(
    vertices: np.ndarray, triangles: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Taubin smooth with the figure helper's collapse rejection."""

    cells = np.column_stack([np.full(len(triangles), 3), np.asarray(triangles)]).ravel()
    mesh = pv.PolyData(np.asarray(vertices, dtype=float), cells)
    before = np.asarray(mesh.points)
    smoothed = mesh.smooth_taubin(
        n_iter=_TAUBIN_ITERATIONS,
        pass_band=_TAUBIN_PASS_BAND,
        normalize_coordinates=True,
    )
    after = np.asarray(smoothed.points)
    before_span = float(np.linalg.norm(before.max(axis=0) - before.min(axis=0)))
    after_span = float(np.linalg.norm(after.max(axis=0) - after.min(axis=0)))
    if before_span > 0.0 and after_span < _COLLAPSE_SPAN_FRACTION * before_span:
        return before, np.asarray(triangles, dtype=np.int64)
    faces = np.asarray(smoothed.faces).reshape(-1, 4)[:, 1:]
    return after, faces


def _plane_inside(point: np.ndarray, origin: np.ndarray, normal: np.ndarray) -> bool:
    return float((point - origin) @ normal) >= -_PLANE_TOLERANCE


def _edge_hit(
    start: np.ndarray,
    end: np.ndarray,
    origin: np.ndarray,
    normal: np.ndarray,
) -> np.ndarray:
    start_distance = float((start - origin) @ normal)
    end_distance = float((end - origin) @ normal)
    weight = start_distance / (start_distance - end_distance)
    return start + weight * (end - start)


def _clip_polygon(
    polygon: list[np.ndarray], origin: np.ndarray, normal: np.ndarray
) -> list[np.ndarray]:
    if len(polygon) < 3:
        return []
    kept: list[np.ndarray] = []
    previous = polygon[-1]
    previous_inside = _plane_inside(previous, origin, normal)
    for current in polygon:
        current_inside = _plane_inside(current, origin, normal)
        if current_inside:
            if not previous_inside:
                kept.append(_edge_hit(previous, current, origin, normal))
            kept.append(current)
        elif previous_inside:
            kept.append(_edge_hit(previous, current, origin, normal))
        previous = current
        previous_inside = current_inside
    return kept


def _clip_to_cell(
    vertices: np.ndarray, triangles: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    planes = cell_clip_planes(_CELL_ORIGIN, _CELL_VECTORS)
    clipped_vertices: list[np.ndarray] = []
    clipped_faces: list[tuple[int, int, int]] = []
    for face in triangles:
        polygon = [np.asarray(vertices[int(index)], dtype=float) for index in face]
        for origin, normal in planes:
            polygon = _clip_polygon(polygon, origin, normal)
            if len(polygon) < 3:
                break
        else:
            start = len(clipped_vertices)
            clipped_vertices.extend(polygon)
            for index in range(1, len(polygon) - 1):
                clipped_faces.append((start, start + index, start + index + 1))
    return (
        np.asarray(clipped_vertices, dtype=float),
        np.asarray(clipped_faces, dtype=np.int64),
    )


def _inside_cell(vertices: np.ndarray) -> np.ndarray:
    planes = cell_clip_planes(_CELL_ORIGIN, _CELL_VECTORS)
    return np.asarray(
        [
            all(_plane_inside(point, origin, normal) for origin, normal in planes)
            for point in vertices
        ]
    )


def _store(structure: SimpleNamespace, vertices: np.ndarray, triangles: np.ndarray) -> None:
    mesh = {
        "id": "cube:positive",
        "vertices": np.asarray(vertices, dtype=float),
        "triangles": np.asarray(triangles, dtype=np.int64),
    }
    scene = structure.frames[0].bundle.scene
    scene["isosurfaces"] = [mesh]
    structure.frames[0].bundle.cube_data.surface_meshes = [mesh]


def _bounds(vertices: np.ndarray) -> tuple[np.ndarray, float]:
    minimum = vertices.min(axis=0)
    maximum = vertices.max(axis=0)
    target = 0.5 * (minimum + maximum)
    radius = max(float(np.linalg.norm(0.5 * (maximum - minimum))), 0.5)
    return target, radius


def _cameras(structure: SimpleNamespace) -> tuple[dict[str, object], dict]:
    cameras = {}
    record = {}
    for projection in ("orthographic", "perspective"):
        args = _camera_args(show_unit_cell=True, projection=projection)
        cameras[projection] = _camera_spec(
            structure,
            args,
            display="unit_cell",
        )
        record = args.camera_fit_record
    return cameras, record


def test_masked_smoothed_clipped_mesh_frames_both_projections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    field, density = _wells()
    density_path = tmp_path / "density.cube"
    _write_cube(density_path, density)
    plain = cube_isosurface_meshes(field, isovalue=_ISOVALUE, stride=_STRIDE)
    plain_vertices = np.vstack([mesh["vertices"] for mesh in plain])
    assert np.any(_closer_to(plain_vertices, _HIGH_WELL, _LOW_WELL))
    assert np.any(_closer_to(plain_vertices, _LOW_WELL, _HIGH_WELL))

    structure = _structure(field)
    ensure_structure_isosurfaces(
        structure,
        _camera_args(
            isosurface_density_mask=True,
            isosurface_density_cube=str(density_path),
            isosurface_density_cutoff=_DENSITY_CUTOFF,
        ),
    )
    stored = structure.frames[0].bundle.scene["isosurfaces"]
    assert stored
    assert stored[0]["metadata"]["density_mask"]["cutoff_e_per_bohr3"] == _DENSITY_CUTOFF
    masked_vertices = np.asarray(stored[0]["vertices"], dtype=float)
    masked_triangles = np.asarray(stored[0]["triangles"], dtype=np.int64)
    assert np.all(_closer_to(masked_vertices, _LOW_WELL, _HIGH_WELL))
    coarse_voxel = _STRIDE * _SPACING
    assert float(_edge_lengths(masked_vertices, masked_triangles).min()) < coarse_voxel

    smoothed, smoothed_faces = _smooth_taubin(masked_vertices, masked_triangles)
    shift = float(np.linalg.norm(smoothed - masked_vertices, axis=1).max())
    assert shift > 1.0e-3
    masked_span = float(np.linalg.norm(masked_vertices.max(0) - masked_vertices.min(0)))
    smoothed_span = float(np.linalg.norm(smoothed.max(0) - smoothed.min(0)))
    assert smoothed_span > _COLLAPSE_SPAN_FRACTION * masked_span
    assert not np.all(_inside_cell(smoothed))

    clipped, clipped_faces = _clip_to_cell(smoothed, smoothed_faces)
    assert len(clipped) >= 3
    assert np.all(_inside_cell(clipped))
    assert float(clipped[:, 0].min()) == pytest.approx(_CELL_ORIGIN[0])
    assert float(clipped[:, 0].min()) > float(smoothed[:, 0].min())

    _store(structure, clipped, clipped_faces)

    def _must_not_march(*_args, **_kwargs):
        raise AssertionError("stored isosurface list must not be remeshed")

    monkeypatch.setattr("mat_viewer.cube.cpu.cube_isosurface_meshes", _must_not_march)
    cameras, record = _cameras(structure)
    assert record["mode"] == "isosurface_content"
    assert record["includes_cell"] is False
    grid_min, grid_max = cube_grid_corner_extents(structure.frames[0].bundle.scene)
    assert grid_min is not None and grid_max is not None
    cell_center = np.asarray(
        structure.frames[0].bundle.scene["bounds"]["center"], dtype=float
    )
    target, radius = _bounds(clipped)
    assert np.linalg.norm(target - cell_center) > 0.4
    assert np.linalg.norm(grid_max - grid_min) > np.linalg.norm(
        clipped.max(0) - clipped.min(0)
    )

    for camera in cameras.values():
        assert np.allclose(camera.target, target)
        assert np.linalg.norm(np.asarray(camera.target) - cell_center) > 0.4

    ortho = cameras["orthographic"]
    perspective = cameras["perspective"]
    assert np.allclose(ortho.target, perspective.target)
    direction = np.asarray(ortho.position) - np.asarray(ortho.target)
    expected_scale = orthographic_scale_for_points(
        clipped,
        np.zeros(len(clipped), dtype=float),
        target,
        direction,
        np.asarray(ortho.up),
        aspect=_VIEWPORT[0] / _VIEWPORT[1],
        zoom=1.0,
        framing_margin=1.12,
    )
    assert ortho.ortho_scale == pytest.approx(expected_scale)
    expected_distance = scale_perspective_distance(
        1.8 * radius / np.sin(np.radians(22.5)),
        zoom=1.0,
        framing_margin=1.12,
    )
    assert np.linalg.norm(
        np.asarray(perspective.position) - np.asarray(perspective.target)
    ) == pytest.approx(expected_distance)

    open_structure = _structure(field)
    _store(open_structure, smoothed, smoothed_faces)
    open_cameras, _open_record = _cameras(open_structure)
    open_target, open_radius = _bounds(smoothed)
    assert open_radius > radius
    assert open_cameras["orthographic"].ortho_scale > ortho.ortho_scale
    open_distance = np.linalg.norm(
        np.asarray(open_cameras["perspective"].position)
        - np.asarray(open_cameras["perspective"].target)
    )
    clipped_distance = np.linalg.norm(
        np.asarray(perspective.position) - np.asarray(perspective.target)
    )
    assert open_distance > clipped_distance
    assert not np.allclose(open_target, target)
