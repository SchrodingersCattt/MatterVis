from __future__ import annotations

import time

import numpy as np
import pytest

from mat_viewer.loader.lammps_batch import FrameBatch
from mat_viewer.render.contracts import CameraSpec
from mat_viewer.render.cpu.batch import NUMBA_AVAILABLE, render_frame_batch


def _frame(positions: np.ndarray) -> FrameBatch:
    natoms = len(positions)
    return FrameBatch(
        positions=positions,
        atomic_numbers=np.full(natoms, 13, dtype=np.uint8),
        atom_ids=np.arange(1, natoms + 1, dtype=np.int32),
        origin=np.asarray([-50.0, -50.0, -50.0]),
        cell=np.eye(3) * 100.0,
        pbc=np.ones(3, dtype=bool),
        timestep=0,
        source_index=0,
    )


def _camera() -> CameraSpec:
    return CameraSpec(
        position=(0.0, 0.0, 120.0),
        target=(0.0, 0.0, 0.0),
        up=(0.0, 1.0, 0.0),
        projection="orthographic",
        near=0.1,
        far=300.0,
        ortho_scale=55.0,
        fov_y_deg=45.0,
    )


@pytest.mark.skipif(not NUMBA_AVAILABLE, reason="batch renderer requires numba")
def test_100k_analytic_spheres_render_without_sampling() -> None:
    rng = np.random.default_rng(20260826)
    positions = np.ascontiguousarray(
        rng.uniform(-50.0, 50.0, size=(100_000, 3)),
        dtype=np.float32,
    )
    frame = _frame(positions)
    camera = _camera()

    render_frame_batch(
        _frame(positions[:4]),
        camera,
        width=32,
        height=24,
        show_cell=False,
    )
    started = time.perf_counter()
    rendered = render_frame_batch(
        frame,
        camera,
        width=1200,
        height=900,
        show_cell=False,
    )
    elapsed = time.perf_counter() - started

    assert frame.natoms == 100_000
    assert rendered.rgba.shape == (900, 1200, 4)
    assert np.any(rendered.rgba[:, :, :3] != 255)
    assert elapsed < 5.0
