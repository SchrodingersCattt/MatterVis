from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pytest
from ase import Atoms
from ase.io import write
from ase.io.trajectory import Trajectory

from mat_viewer.cli import _build_tui_parser
from mat_viewer.loader import (
    LoadedCrystal,
    iter_atomistic_frames,
    load_atomistic_input,
    load_structure_input,
)
from mat_viewer.render.cli import (
    _build_render_parser,
    _parse_frame_indices,
    _render_main,
)
from mat_viewer.tui.loader_adapter import load_for_tui


@pytest.fixture
def structure_files(tmp_path: Path) -> dict[str, Path]:
    first = Atoms(
        "SiO2",
        positions=[[1.0, 1.0, 1.0], [2.5, 1.0, 1.0], [1.0, 2.5, 1.0]],
        cell=[8.0, 8.0, 8.0],
        pbc=True,
    )
    second = first.copy()
    second.positions[0] += [0.4, 0.2, 0.0]
    second.info["step"] = 1

    files = {
        "vasp": tmp_path / "POSCAR",
        "extxyz": tmp_path / "frames.extxyz",
        "traj": tmp_path / "frames.traj",
        "dump": tmp_path / "frames.dump",
        "data": tmp_path / "system.data",
    }
    write(files["vasp"], first, format="vasp")
    write(files["extxyz"], [first, second], format="extxyz")
    with Trajectory(files["traj"], "w") as trajectory:
        trajectory.write(first)
        trajectory.write(second)

    files["dump"].write_text("""ITEM: TIMESTEP
0
ITEM: NUMBER OF ATOMS
3
ITEM: BOX BOUNDS pp pp pp
0 8
0 8
0 8
ITEM: ATOMS id type x y z
1 1 1.0 1.0 1.0
2 2 2.5 1.0 1.0
3 2 1.0 2.5 1.0
ITEM: TIMESTEP
1
ITEM: NUMBER OF ATOMS
3
ITEM: BOX BOUNDS pp pp pp
0 8
0 8
0 8
ITEM: ATOMS id type x y z
1 1 1.2 1.1 1.0
2 2 2.7 1.1 1.0
3 2 1.2 2.6 1.0
""")
    files["data"].write_text("""LAMMPS data file

3 atoms
2 atom types

0 8 xlo xhi
0 8 ylo yhi
0 8 zlo zhi

Masses

1 28.085
2 15.999

Atoms # atomic

1 1 1.0 1.0 1.0
2 2 2.5 1.0 1.0
3 2 1.0 2.5 1.0
""")
    return files


@pytest.mark.parametrize(
    ("key", "expected_frames"),
    [
        ("vasp", 1),
        ("extxyz", 2),
        ("traj", 2),
        ("dump", 2),
        ("data", 1),
    ],
)
def test_supported_inputs_converge_on_loaded_crystal(
    structure_files: dict[str, Path],
    key: str,
    expected_frames: int,
) -> None:
    type_map = ["Si", "O"] if key in {"dump", "data"} else None
    loaded = load_structure_input(structure_files[key], type_map=type_map)

    assert loaded.n_frames == expected_frames
    assert all(isinstance(frame.bundle, LoadedCrystal) for frame in loaded.frames)
    assert all(len(frame.bundle.raw_atoms) == 3 for frame in loaded.frames)
    assert {atom["elem"] for atom in loaded.frames[0].bundle.raw_atoms} == {"Si", "O"}


def test_ase_frame_metadata_preserves_custom_atom_arrays(tmp_path: Path) -> None:
    atoms = Atoms(
        "SiO",
        positions=[[0.0, 0.0, 0.0], [1.5, 0.0, 0.0]],
        cell=[8.0, 8.0, 8.0],
        pbc=True,
    )
    atoms.info["time_ps"] = 2.5
    atoms.arrays["local_vector"] = np.array(
        [[0.1, 0.0, 0.0], [0.0, 0.2, 0.0]]
    )
    path = tmp_path / "metadata.extxyz"
    write(path, atoms, format="extxyz")

    atomistic = load_atomistic_input(path, frame_indices=[0])
    loaded = load_structure_input(path, frame_indices=[0])

    assert atomistic.frames[0].info["time_ps"] == pytest.approx(2.5)
    assert atomistic.frames[0].atom_arrays["local_vector"] == pytest.approx(
        atoms.arrays["local_vector"]
    )
    assert loaded.frames[0].atom_arrays["local_vector"] == pytest.approx(
        atoms.arrays["local_vector"]
    )
    assert loaded.frames[0].bundle.frame_info["time_ps"] == pytest.approx(2.5)
    assert loaded.frames[0].bundle.atom_arrays["local_vector"] == pytest.approx(
        atoms.arrays["local_vector"]
    )
    scene = loaded.frames[0].bundle.scene
    source_index = scene["draw_atoms"][0]["_source_index"]
    assert loaded.frames[0].bundle.atom_arrays["local_vector"][source_index] == pytest.approx(
        atoms.arrays["local_vector"][source_index]
    )


def test_iter_atomistic_frames_streams_selected_source_order(
    structure_files: dict[str, Path],
) -> None:
    streamed = list(
        iter_atomistic_frames(structure_files["traj"], frame_indices=[1])
    )

    assert len(streamed) == 1
    frame, input_format = streamed[0]
    assert frame.index == 1
    assert frame.info["step"] == 1
    assert input_format == "traj"


def test_trajectory_frame_selection_reaches_tui(
    structure_files: dict[str, Path],
) -> None:
    crystal = load_for_tui(str(structure_files["traj"]), frame=1)

    assert crystal.metadata["input_format"] == "traj"
    assert crystal.metadata["frame_index"] == 1
    assert crystal.n_atoms == 3


def test_lammps_type_map_is_required_to_preserve_element_identity(
    structure_files: dict[str, Path],
) -> None:
    loaded = load_structure_input(
        structure_files["dump"],
        type_map=["Si", "O"],
    )
    symbols = [atom["elem"] for atom in loaded.frames[0].bundle.raw_atoms]

    assert symbols == ["Si", "O", "O"]


@pytest.mark.parametrize(
    ("value", "stride", "expected"),
    [
        (None, 2, [0, 2, 4]),
        ("1:5:2", 1, [1, 3]),
        ("::-1", 2, [5, 3, 1]),
    ],
)
def test_frame_range_uses_python_slice_semantics(
    value: str | None,
    stride: int,
    expected: list[int],
) -> None:
    assert _parse_frame_indices(6, value, stride) == expected


def test_render_parser_exposes_generic_input_contract() -> None:
    parser = argparse.ArgumentParser()
    _build_render_parser(parser.add_subparsers(dest="command"))

    args = parser.parse_args(
        [
            "render",
            "run.dump",
            "--type-map",
            "Si",
            "O",
            "--frame-range",
            "::2",
            "--stride",
            "3",
            "--fps",
            "8",
            "-o",
            "run.gif",
        ]
    )

    assert args.input == "run.dump"
    assert args.type_map == ["Si", "O"]
    assert args.frame_range == "::2"
    assert args.stride == 3
    assert args.fps == 8


@pytest.mark.parametrize("key", ["vasp", "extxyz", "traj", "dump", "data"])
def test_render_cli_accepts_every_structure_adapter(
    structure_files: dict[str, Path],
    tmp_path: Path,
    key: str,
) -> None:
    parser = argparse.ArgumentParser()
    _build_render_parser(parser.add_subparsers(dest="command"))
    output = tmp_path / f"{key}.html"
    argv = ["render", str(structure_files[key]), "-o", str(output)]
    if key in {"dump", "data"}:
        argv.extend(["--type-map", "Si", "O"])
    if key in {"extxyz", "traj", "dump"}:
        argv.extend(["--frame", "1"])
    args = parser.parse_args(argv)

    _render_main(args)

    assert output.is_file()
    assert output.stat().st_size > 1000


@pytest.mark.parametrize("extension", [".gif", ".mp4"])
def test_render_cli_exports_real_animation(
    structure_files: dict[str, Path],
    tmp_path: Path,
    extension: str,
) -> None:
    import imageio.v3 as iio

    parser = argparse.ArgumentParser()
    _build_render_parser(parser.add_subparsers(dest="command"))
    output = tmp_path / f"trajectory{extension}"
    args = parser.parse_args(
        [
            "render",
            str(structure_files["traj"]),
            "--view",
            "unit_cell",
            "--width",
            "200",
            "--height",
            "160",
            "--scale",
            "1",
            "--fps",
            "5",
            "-o",
            str(output),
        ]
    )

    _render_main(args)

    assert output.is_file()
    assert output.stat().st_size > 1000
    frames = list(iio.imiter(output))
    assert len(frames) == 2
    assert all(frame.shape[:2] == (160, 200) for frame in frames)


def test_animation_cli_does_not_load_all_canonical_frames(
    structure_files: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mat_viewer.render import cli

    def fail(*args, **kwargs):
        raise AssertionError("animation CLI must use the streaming frame path")

    monkeypatch.setattr(cli, "load_structure_input", fail, raising=False)
    parser = argparse.ArgumentParser()
    _build_render_parser(parser.add_subparsers(dest="command"))
    output = tmp_path / "streaming.gif"
    args = parser.parse_args(
        [
            "render",
            str(structure_files["traj"]),
            "--width",
            "100",
            "--height",
            "80",
            "--scale",
            "1",
            "-o",
            str(output),
        ]
    )

    _render_main(args)

    assert output.is_file()


def test_animation_viewport_has_one_world_center_and_scale() -> None:
    from mat_viewer.renderer import uniform_viewport

    scenes = [
        {"draw_atoms": [{"cart": np.array([0.0, 0.0, 0.0]), "atom_radius": 0.2}]},
        {"draw_atoms": [{"cart": np.array([10.0, 0.0, 0.0]), "atom_radius": 0.2}]},
    ]

    uniform_viewport(scenes, shared_center=True)

    assert scenes[0]["viewport"] == scenes[1]["viewport"]
    assert scenes[0]["viewport"]["center"] == pytest.approx([5.0, 0.0, 0.0])
    assert scenes[0]["viewport"]["half_span"] > 5.0


def test_viewport_accumulator_matches_uniform_viewport() -> None:
    from mat_viewer.renderer import ViewportAccumulator, uniform_viewport

    scenes = [
        {"draw_atoms": [{"cart": np.array([0.0, 0.0, 0.0]), "atom_radius": 0.2}]},
        {"draw_atoms": [{"cart": np.array([10.0, 0.0, 0.0]), "atom_radius": 0.2}]},
    ]
    accumulator = ViewportAccumulator()
    for scene in scenes:
        accumulator.update(scene)
    uniform_viewport(scenes, shared_center=True)

    assert accumulator.viewport() == scenes[0]["viewport"]


def test_tui_parser_exposes_same_generic_input_contract() -> None:
    parser = argparse.ArgumentParser()
    _build_tui_parser(parser.add_subparsers(dest="command"))

    args = parser.parse_args(
        [
            "tui",
            "run.dump",
            "--input-format",
            "lammps-dump-text",
            "--type-map",
            "O",
            "H",
            "--frame",
            "20",
        ]
    )

    assert args.FILE == "run.dump"
    assert args.input_format == "lammps-dump-text"
    assert args.type_map == ["O", "H"]
    assert args.frame == 20


@pytest.mark.parametrize("key", ["dump", "data"])
def test_numeric_lammps_inputs_reject_missing_type_map(
    structure_files: dict[str, Path],
    key: str,
) -> None:
    with pytest.raises(ValueError, match="complete --type-map"):
        load_structure_input(structure_files[key])


def test_lammps_dump_with_element_column_needs_no_type_map(
    structure_files: dict[str, Path],
    tmp_path: Path,
) -> None:
    source = structure_files["dump"].read_text()
    source = source.replace(
        "ITEM: ATOMS id type x y z",
        "ITEM: ATOMS id element x y z",
    )
    source = source.replace("1 1 1.0 1.0 1.0", "1 Si 1.0 1.0 1.0")
    source = source.replace("2 2 2.5 1.0 1.0", "2 O 2.5 1.0 1.0")
    source = source.replace("3 2 1.0 2.5 1.0", "3 O 1.0 2.5 1.0")
    source = source.replace("1 1 1.2 1.1 1.0", "1 Si 1.2 1.1 1.0")
    source = source.replace("2 2 2.7 1.1 1.0", "2 O 2.7 1.1 1.0")
    source = source.replace("3 2 1.2 2.6 1.0", "3 O 1.2 2.6 1.0")
    path = tmp_path / "elements.dump"
    path.write_text(source)

    loaded = load_structure_input(path, frame_indices=[0])

    assert [atom["elem"] for atom in loaded.frames[0].bundle.raw_atoms] == [
        "Si",
        "O",
        "O",
    ]


def test_only_selected_trajectory_frame_is_canonicalised(
    structure_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mat_viewer.loader import structure_input

    original = structure_input.build_loaded_crystal_from_ase
    calls: list[int] = []

    def record(atoms, **kwargs):
        calls.append(kwargs["frame_index"])
        return original(atoms, **kwargs)

    monkeypatch.setattr(structure_input, "build_loaded_crystal_from_ase", record)
    loaded = load_structure_input(
        structure_files["traj"],
        frame_indices=[-1],
    )

    assert loaded.n_frames == 2
    assert [frame.index for frame in loaded.frames] == [1]
    assert calls == [1]


def test_count_frames_does_not_build_render_bundles(
    structure_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mat_viewer.loader import count_structure_frames, structure_input

    def fail(*args, **kwargs):
        raise AssertionError("frame counting must not build a render bundle")

    monkeypatch.setattr(structure_input, "build_loaded_crystal_from_ase", fail)

    assert count_structure_frames(structure_files["traj"]) == 2
