from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image

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


def test_batch_cli_overlay_loader_is_importable_without_legacy_cli_symbol() -> None:
    """The batch path must keep working when numba enables its fast branch."""
    from mat_viewer.render.fast_cli import _load_vector_overlays
    from mat_viewer.render.overlay.io import load_overlay_file

    assert _load_vector_overlays is load_overlay_file


def test_batch_cli_exercises_vector_overlay_loader_contract(tmp_path, monkeypatch) -> None:
    """The numba-gated batch branch must pass the option name to the loader."""
    import argparse
    from types import SimpleNamespace

    from ase import Atoms
    from ase.io import write

    from mat_viewer.cli import _build_render_parser
    from mat_viewer.render import batch_pipeline, fast_cli
    from mat_viewer.render.fast_cli import RendererDecision, WorkloadInspection

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    _build_render_parser(subparsers)

    source = tmp_path / "cluster.xyz"
    write(source, Atoms("CO", positions=[[0, 0, 0], [1.2, 0, 0]]))
    output = tmp_path / "cluster.png"
    vectors = tmp_path / "vectors.json"
    vectors.write_text("[]", encoding="utf-8")
    args = parser.parse_args(
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
            "--vector-overlays",
            str(vectors),
        ]
    )

    decision = RendererDecision(
        requested="batch",
        selected="batch",
        atom_frames=2,
        threshold=100_000,
        reason="test",
    )
    workload = WorkloadInspection(
        frame_indices=(0,),
        atom_frames=2,
        lammps_dump=False,
    )
    monkeypatch.setattr(
        fast_cli, "renderer_decision", lambda _args: (decision, workload)
    )

    def fake_render_array_input(*_args, **_kwargs):
        output.write_bytes(b"fake")
        return SimpleNamespace(
            output=output,
            output_sha256="0" * 64,
            selected_frames=(0,),
            profile={},
        )

    monkeypatch.setattr(batch_pipeline, "render_array_input", fake_render_array_input)
    monkeypatch.setattr(fast_cli, "_result_payload", lambda *_args, **_kwargs: {"ok": True})

    assert fast_cli.render_batch_if_selected(args, install_command="") == {"ok": True}


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
    show_hydrogen=True,
    vector_overlays=None,
    ortho_scale=None,
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
        ortho_scale=ortho_scale,
        atom_scale=1.0,
        background=(255, 255, 255, 255),
        show_hydrogen=show_hydrogen,
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
    with Image.open(png) as image:
        png_pixels = np.asarray(image)
        assert image.size == (160, 120)
    assert np.any(png_pixels[:, :, :3] != 255)
    explicit_no_cell = tmp_path / "frame-no-cell.png"
    _render_extxyz(
        source,
        explicit_no_cell,
        frame_indices=(0,),
        show_cell=False,
    )
    with Image.open(png) as image:
        default_pixels = np.asarray(image)
    with Image.open(explicit_no_cell) as image:
        no_cell_pixels = np.asarray(image)
    np.testing.assert_array_equal(default_pixels, no_cell_pixels)

    gif = tmp_path / "movie.gif"
    animation = _render_extxyz(source, gif, frame_indices=(0, 1))
    assert animation.selected_frames == (0, 1)
    with Image.open(gif) as image:
        assert image.n_frames == 2
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
    with Image.open(plain) as image:
        plain_pixels = np.asarray(image)
    with Image.open(layered) as image:
        layered_pixels = np.asarray(image)
    assert not np.array_equal(plain_pixels, layered_pixels)


@pytest.mark.skipif(not NUMBA_AVAILABLE, reason="batch renderer requires numba")
def test_hidden_periodic_cell_fits_visible_atoms_not_vacuum(tmp_path) -> None:
    from ase import Atoms
    from ase.io import write

    source = tmp_path / "slab.extxyz"
    atoms = Atoms(
        "CO",
        positions=[[1.0, 1.0, 2.0], [2.2, 1.0, 2.0]],
        cell=[10.0, 10.0, 200.0],
        pbc=[True, True, False],
    )
    write(source, atoms, format="extxyz")

    hidden = _render_extxyz(
        source,
        tmp_path / "hidden.png",
        frame_indices=(0,),
        show_cell=False,
    )
    shown = _render_extxyz(
        source,
        tmp_path / "shown.png",
        frame_indices=(0,),
        show_cell=True,
    )

    assert hidden.profile["camera_fit"] == "visible_atoms"
    assert hidden.camera.target[2] == pytest.approx(2.0)
    assert hidden.camera.ortho_scale < shown.camera.ortho_scale / 10.0


@pytest.mark.skipif(not NUMBA_AVAILABLE, reason="batch renderer requires numba")
def test_all_background_static_render_fails_explicitly(tmp_path) -> None:
    from ase import Atoms
    from ase.io import write

    source = tmp_path / "hydrogen.extxyz"
    write(
        source,
        Atoms("H2", positions=[[0.0, 0.0, 0.0], [0.74, 0.0, 0.0]], pbc=False),
        format="extxyz",
    )

    with pytest.raises(RuntimeError, match="all-background frame"):
        _render_extxyz(
            source,
            tmp_path / "hidden-hydrogen.png",
            frame_indices=(0,),
            show_cell=False,
            show_hydrogen=False,
        )
