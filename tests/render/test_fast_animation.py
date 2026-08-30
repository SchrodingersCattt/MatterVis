from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from mat_viewer.loader.lammps_batch import FrameBatch, LammpsFrameRecord
from mat_viewer.render.contracts import CameraSpec, RenderPlan, ViewportPlan
from mat_viewer.render.cpu.batch import (
    NUMBA_AVAILABLE,
    SphereBatch,
    build_sphere_batch,
    element_style_tables,
    quantize_global_palette,
    render_frame_batch,
)
from mat_viewer.render.cpu.raster import render_rgba
from mat_viewer.render.fast_animation import (
    _GlobalPaletteGifWriter,
    _default_workers,
    fit_shared_camera,
    render_lammps_animation,
)
from mat_viewer.render.fast_cli import _color8
from mat_viewer.render.geometry import sphere_primitive


def _frame(
    positions: list[list[float]],
    numbers: list[int],
    *,
    cell: np.ndarray | None = None,
) -> FrameBatch:
    return FrameBatch(
        positions=np.asarray(positions, dtype=np.float32),
        atomic_numbers=np.asarray(numbers, dtype=np.uint8),
        atom_ids=np.arange(1, len(numbers) + 1, dtype=np.int32),
        origin=np.zeros(3),
        cell=np.eye(3) * 4.0 if cell is None else cell,
        pbc=np.ones(3, dtype=bool),
        timestep=0,
        source_index=0,
    )


def _camera(projection: str = "orthographic") -> CameraSpec:
    return CameraSpec(
        position=(0.0, 0.0, 6.0),
        target=(0.0, 0.0, 0.0),
        up=(0.0, 1.0, 0.0),
        projection=projection,
        near=0.1,
        far=20.0,
        ortho_scale=2.0,
        fov_y_deg=45.0,
    )


@pytest.mark.skipif(not NUMBA_AVAILABLE, reason="batch renderer requires numba")
def test_sphere_batch_is_contiguous_and_renders_depth_cell_and_perspective() -> None:
    frame = _frame([[-0.55, 0.0, 0.0], [0.55, 0.0, 0.0]], [6, 8])
    projected = np.asarray([[-0.55, 0.0, -6.0], [0.55, 0.0, -6.0]])
    spheres = build_sphere_batch(frame, projected, atom_scale=1.0)

    assert isinstance(spheres, SphereBatch)
    assert spheres.camera_positions.flags.c_contiguous
    assert spheres.atomic_numbers.flags.c_contiguous
    for projection in ("orthographic", "perspective"):
        rendered = render_frame_batch(
            frame,
            _camera(projection),
            width=128,
            height=96,
            show_cell=True,
        )
        foreground = np.any(rendered.rgba[:, :, :3] != 255, axis=2)
        assert foreground.sum() > 100
        assert np.isfinite(rendered.depth).sum() > 100


@pytest.mark.skipif(not NUMBA_AVAILABLE, reason="batch renderer requires numba")
def test_batch_sphere_matches_legacy_cpu_pixels() -> None:
    frame = _frame([[0.0, 0.0, 0.0]], [6])
    camera = _camera()
    colors, radii = element_style_tables()
    legacy = render_rgba(
        RenderPlan(
            width=128,
            height=96,
            background=(1.0, 1.0, 1.0, 1.0),
            viewports=(
                ViewportPlan(
                    semantic_id="main",
                    camera=camera,
                    primitives=(
                        sphere_primitive(
                            "C1",
                            (0.0, 0.0, 0.0),
                            float(radii[6]),
                            tuple(colors[6] / 255.0),
                        ),
                    ),
                ),
            ),
        )
    )
    batch = render_frame_batch(
        frame, camera, width=128, height=96, show_cell=False
    ).rgba
    np.testing.assert_array_equal(batch, legacy)


@pytest.mark.skipif(not NUMBA_AVAILABLE, reason="batch renderer requires numba")
def test_bond_batch_changes_pixels_between_atom_centres() -> None:
    frame = _frame([[-0.8, 0.0, 0.0], [0.8, 0.0, 0.0]], [6, 8])
    camera = _camera()
    without = render_frame_batch(frame, camera, width=160, height=120, show_cell=False)
    bonds = SimpleNamespace(pairs=np.asarray([[0, 1]], dtype=np.int32))
    with_bond = render_frame_batch(
        frame,
        camera,
        width=160,
        height=120,
        show_cell=False,
        bonds=bonds,
        bond_radius=0.12,
    )

    centre = (60, 80)
    assert np.all(without.rgba[centre][:3] == 255)
    assert np.any(with_bond.rgba[centre][:3] != 255)


@pytest.mark.skipif(not NUMBA_AVAILABLE, reason="batch renderer requires numba")
def test_bond_batch_inherits_endpoint_atom_colors() -> None:
    frame = replace(
        _frame([[-0.8, 0.0, 0.0], [0.8, 0.0, 0.0]], [6, 8]),
        atom_colors=np.asarray([[255, 0, 0], [0, 0, 255]], dtype=np.uint8),
    )
    bonds = SimpleNamespace(pairs=np.asarray([[0, 1]], dtype=np.int32))
    rendered = render_frame_batch(
        frame,
        _camera(),
        width=160,
        height=120,
        show_cell=False,
        bonds=bonds,
        bond_radius=0.12,
    )
    first_half = rendered.rgba[60, 79, :3]
    second_half = rendered.rgba[60, 80, :3]
    assert first_half[0] > first_half[2]
    assert second_half[2] > second_half[0]


@pytest.mark.skipif(not NUMBA_AVAILABLE, reason="batch renderer requires numba")
def test_bond_batch_uses_minimum_image_vectors() -> None:
    frame = _frame([[-1.9, 0.0, 0.0], [1.9, 0.0, 0.0]], [6, 6])
    bonds = SimpleNamespace(
        pairs=np.asarray([[0, 1]], dtype=np.int32),
        vectors=np.asarray([[-0.2, 0.0, 0.0]], dtype=np.float32),
    )

    rendered = render_frame_batch(
        frame,
        _camera(),
        width=160,
        height=120,
        show_cell=False,
        bonds=bonds,
        bond_radius=0.12,
    )

    assert np.all(rendered.rgba[60, 80, :3] == 255)
    assert np.any(rendered.rgba[60, :35, :3] != 255)


def _image_descriptors_have_no_local_palettes(path: Path) -> int:
    data = path.read_bytes()
    assert data[:6] in {b"GIF87a", b"GIF89a"}
    packed = data[10]
    cursor = 13
    if packed & 0x80:
        cursor += 3 * (2 ** ((packed & 0x07) + 1))
    frames = 0
    while cursor < len(data):
        marker = data[cursor]
        if marker == 0x3B:
            break
        if marker == 0x21:
            cursor += 2
            while True:
                size = data[cursor]
                cursor += 1
                if size == 0:
                    break
                cursor += size
            continue
        assert marker == 0x2C
        frames += 1
        local_packed = data[cursor + 9]
        assert not (local_packed & 0x80)
        cursor += 10
        cursor += 1
        while True:
            size = data[cursor]
            cursor += 1
            if size == 0:
                break
            cursor += size
    return frames


def test_global_palette_quantization_and_streaming_gif(tmp_path: Path) -> None:
    rgb = np.asarray(
        [[[255, 0, 0], [0, 255, 0]], [[0, 0, 255], [255, 255, 255]]],
        dtype=np.uint8,
    )
    indices = quantize_global_palette(rgb)
    assert indices.shape == (2, 2)
    assert indices.dtype == np.uint8
    assert int(indices.max()) <= 251

    output = tmp_path / "global.gif"
    writer = _GlobalPaletteGifWriter(output, fps=5.0)
    writer.append_data(np.tile(rgb, (8, 8, 1)))
    writer.append_data(np.tile(rgb[::-1], (8, 8, 1)))
    writer.close()

    with Image.open(output) as image:
        assert image.n_frames == 2
        assert image.info["duration"] == 200
        assert image.info["loop"] == 0
    assert _image_descriptors_have_no_local_palettes(output) == 2


def test_fast_cli_accepts_six_or_eight_digit_colours() -> None:
    assert _color8("#ffffff", alpha=True) == (255, 255, 255, 255)
    assert _color8("#01020304", alpha=True) == (1, 2, 3, 4)
    assert _color8("#01020304", alpha=False) == (1, 2, 3)


def test_worker_override_and_shared_camera_controls() -> None:
    record = LammpsFrameRecord(
        index=0,
        timestep=0,
        natoms=1,
        atoms_offset=0,
        frame_end=0,
        columns=("element", "x", "y", "z"),
        origin=np.zeros(3),
        cell=np.diag([2.0, 4.0, 6.0]),
        pbc=np.ones(3, dtype=bool),
    )
    base = fit_shared_camera(
        (record,),
        repeat=(1, 1, 1),
        width=1200,
        height=900,
        projection="orthographic",
        framing_margin=1.2,
        zoom=1.0,
    )
    zoomed = fit_shared_camera(
        (record,),
        repeat=(1, 1, 1),
        width=1200,
        height=900,
        projection="orthographic",
        framing_margin=1.2,
        zoom=2.0,
    )
    assert zoomed.ortho_scale == pytest.approx(base.ortho_scale / 2.0)
    assert _default_workers(32) == 32


@pytest.mark.skipif(not NUMBA_AVAILABLE, reason="batch renderer requires numba")
def test_small_lammps_animation_preserves_order_and_profile(tmp_path: Path) -> None:
    frames = []
    for index in range(3):
        frames.append(f"""ITEM: TIMESTEP
{index * 10}
ITEM: NUMBER OF ATOMS
2
ITEM: BOX BOUNDS pp pp pp
0 4
0 4
0 4
ITEM: ATOMS id element x y z
2 O {2.5 + index * 0.1} 2 2
1 C {1.5 + index * 0.1} 2 2
""")
    source = tmp_path / "small.lammpstrj"
    source.write_text("".join(frames), encoding="utf-8")
    output = tmp_path / "small.gif"
    profile_path = tmp_path / "profile.json"

    result = render_lammps_animation(
        source,
        output,
        width=96,
        height=72,
        fps=5.0,
        workers=2,
        profile_path=profile_path,
    )

    with Image.open(output) as image:
        assert image.n_frames == 3
    assert result.profile["timesteps"] == [0, 10, 20]
    assert result.profile["settings"]["shared_viewport"] is True
    assert result.profile["settings"]["workers"] == 2
    assert result.profile["source"]["frames_selected"] == 3
    assert profile_path.is_file()


@pytest.mark.skipif(not NUMBA_AVAILABLE, reason="batch renderer requires numba")
def test_no_cell_animation_fits_visible_atoms_not_vacuum(tmp_path: Path) -> None:
    frames = []
    for index, z_value in enumerate((2.0, 3.0)):
        frames.append(
            f"""ITEM: TIMESTEP
{index}
ITEM: NUMBER OF ATOMS
2
ITEM: BOX BOUNDS pp pp ff
0 10
0 10
0 200
ITEM: ATOMS id element x y z
1 C 1 1 {z_value}
2 O 2.2 1 {z_value}
"""
        )
    source = tmp_path / "slab.lammpstrj"
    source.write_text("".join(frames), encoding="utf-8")

    result = render_lammps_animation(
        source,
        tmp_path / "slab.gif",
        width=160,
        height=120,
        show_cell=False,
        workers=1,
    )

    assert result.profile["settings"]["camera_fit"] == "visible_atoms"
    assert result.camera.target[2] == pytest.approx(2.5)
    assert result.camera.ortho_scale < 10.0
    assert all(value > 0 for value in result.profile["foreground_pixels"])
