from __future__ import annotations

import json
import numpy as np
from PIL import Image
import pytest

from mat_viewer.loader.frame_batch import frame_batch_from_ase
from mat_viewer.render.batch_pipeline import render_array_input
from mat_viewer.render.cpu.batch import NUMBA_AVAILABLE
from mat_viewer.render.renderer_selection import select_renderer


def test_auto_route_uses_atom_frames_and_not_input_format_or_overlays() -> None:
    plain = select_renderer(
        "auto",
        atom_frames=100_000,
        backend="cpu",
        representation="ball",
        output_format="png",
    )
    # Overlay layers are intentionally absent from the selector API.
    assert plain.selected == "batch"
    assert plain.reason == "atom_frames >= 100000"


def test_static_large_structure_and_long_trajectory_share_route_metric() -> None:
    static = select_renderer(
        "auto",
        atom_frames=150_000,
        backend="cpu",
        representation="ball_stick",
        output_format="png",
    )
    trajectory = select_renderer(
        "auto",
        atom_frames=150_000,
        backend="cpu",
        representation="ball_stick",
        output_format="mp4",
    )
    assert static.selected == trajectory.selected == "batch"


def test_force_general_and_force_batch_are_explicit() -> None:
    general = select_renderer(
        "general",
        atom_frames=1_000_000,
        backend="cpu",
        representation="ball",
        output_format="gif",
    )
    batch = select_renderer(
        "batch",
        atom_frames=1,
        backend="cpu",
        representation="ball",
        output_format="gif",
    )
    assert general.selected == "general"
    assert batch.selected == "batch"


def test_ase_formats_normalise_to_contiguous_frame_batch() -> None:
    from ase import Atoms

    atoms = Atoms(
        "CO",
        positions=[[0.0, 0.0, 0.0], [1.2, 0.0, 0.0]],
        pbc=False,
    )
    frame = frame_batch_from_ase(atoms, source_index=3)
    assert frame.positions.flags.c_contiguous
    assert frame.atomic_numbers.flags.c_contiguous
    assert frame.natoms == 2
    assert frame.index == 3
    np.testing.assert_array_equal(frame.pbc, [False, False, False])


def _render_extxyz(
    source,
    output,
    *,
    frame_indices,
    show_cell=None,
    show_axes=False,
    vector_overlays=None,
):
    return render_array_input(
        source,
        output,
        input_format="extxyz",
        type_map=None,
        frame_indices=frame_indices,
        repeat=(1, 1, 1),
        width=160,
        height=120,
        scale=1,
        fps=5.0,
        projection="orthographic",
        camera_axis=None,
        view_direction=None,
        camera_position=None,
        camera_up=None,
        fit_multiplier=1.8,
        zoom=1.0,
        framing_margin=1.12,
        atom_scale=1.0,
        background=(255, 255, 255, 255),
        show_hydrogen=True,
        show_cell=show_cell,
        show_axes=show_axes,
        show_labels=False,
        cell_color=(51, 51, 51),
        cell_width_px=2.0,
        bonded=False,
        bond_radius=0.15,
        bond_skin=0.5,
        vector_overlays=vector_overlays,
    )


@pytest.mark.skipif(not NUMBA_AVAILABLE, reason="batch renderer requires numba")
def test_extxyz_static_and_animation_use_the_same_array_pipeline(tmp_path) -> None:
    from ase import Atoms
    from ase.io import write

    frames = [
        Atoms("CO", positions=[[0, 0, 0], [1.2 + offset, 0, 0]], pbc=False)
        for offset in (0.0, 0.1)
    ]
    source = tmp_path / "molecule.extxyz"
    write(source, frames, format="extxyz")

    png = tmp_path / "frame.png"
    static = _render_extxyz(source, png, frame_indices=(0,))
    assert static.selected_frames == (0,)
    assert Image.open(png).size == (160, 120)
    assert np.any(np.asarray(Image.open(png))[:, :, :3] != 255)
    explicit_no_cell = tmp_path / "frame-no-cell.png"
    _render_extxyz(
        source,
        explicit_no_cell,
        frame_indices=(0,),
        show_cell=False,
    )
    np.testing.assert_array_equal(
        np.asarray(Image.open(png)),
        np.asarray(Image.open(explicit_no_cell)),
    )

    gif = tmp_path / "movie.gif"
    animation = _render_extxyz(source, gif, frame_indices=(0, 1))
    assert animation.selected_frames == (0, 1)
    assert Image.open(gif).n_frames == 2
    assert animation.profile["shared_viewport"] is True


@pytest.mark.skipif(not NUMBA_AVAILABLE, reason="batch renderer requires numba")
def test_cli_force_batch_is_format_neutral(tmp_path, capsys) -> None:
    from ase import Atoms
    from ase.io import write

    from mat_viewer.cli import main

    source = tmp_path / "cluster.xyz"
    output = tmp_path / "cluster.png"
    write(source, Atoms("CO", positions=[[0, 0, 0], [1.2, 0, 0]]))
    main(
        [
            "render",
            str(source),
            "-o",
            str(output),
            "--renderer",
            "batch",
            "--style",
            "ball",
            "--backend",
            "cpu",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    selection = payload["result"]["metadata"]["renderer_selection"]
    assert selection["selected"] == "batch"
    assert selection["atom_frames"] == 2
    assert output.is_file()


@pytest.mark.skipif(not NUMBA_AVAILABLE, reason="batch renderer requires numba")
def test_batch_composes_axes_and_vector_layers(tmp_path) -> None:
    from ase import Atoms
    from ase.io import write

    source = tmp_path / "periodic.extxyz"
    atoms = Atoms(
        "CO",
        positions=[[1.5, 1.5, 1.5], [2.7, 1.5, 1.5]],
        cell=[5, 5, 5],
        pbc=True,
    )
    write(source, atoms, format="extxyz")
    plain = tmp_path / "plain.png"
    layered = tmp_path / "layered.png"
    _render_extxyz(source, plain, frame_indices=(0,))
    _render_extxyz(
        source,
        layered,
        frame_indices=(0,),
        show_axes=True,
        vector_overlays=[
            {
                "id": "mode",
                "magnitude_mode": "absolute",
                "arrows": [
                    {
                        "id": "atom-0",
                        "origin": [1.5, 1.5, 1.5],
                        "vector": [1.0, 0.0, 0.0],
                    }
                ],
            }
        ],
    )
    assert not np.array_equal(
        np.asarray(Image.open(plain)), np.asarray(Image.open(layered))
    )
