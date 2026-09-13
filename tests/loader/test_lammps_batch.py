from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mat_viewer.loader.lammps_batch import (
    frame_box_corners,
    index_lammps_dump,
    read_lammps_frame,
    read_lammps_property_frame,
    repeat_frame,
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_numeric_types_arbitrary_columns_mixed_pbc_and_order(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "ordered.dump",
        """ITEM: TIMESTEP
10
ITEM: NUMBER OF ATOMS
2
ITEM: BOX BOUNDS pp ff pp
-1 3
2 8
4 14
ITEM: ATOMS z id type y x
6 2 2 4 1
5 1 1 3 0
ITEM: TIMESTEP
20
ITEM: NUMBER OF ATOMS
2
ITEM: BOX BOUNDS pp ff pp
-1 3
2 8
4 14
ITEM: ATOMS type x id z y
1 0.5 1 5.5 3.5
2 1.5 2 6.5 4.5
""",
    )
    dump_index = index_lammps_dump(source)
    assert [record.timestep for record in dump_index.records] == [10, 20]
    assert [record.columns for record in dump_index.records] == [
        ("z", "id", "type", "y", "x"),
        ("type", "x", "id", "z", "y"),
    ]
    frame = read_lammps_frame(dump_index, 0, type_map=["Al", "O"], sort_by_id=True)
    assert frame.atom_ids.tolist() == [1, 2]
    assert frame.atomic_numbers.tolist() == [13, 8]
    np.testing.assert_allclose(frame.positions, [[0, 3, 5], [1, 4, 6]])
    np.testing.assert_array_equal(frame.pbc, [True, False, True])


def test_scaled_triclinic_elements_and_repeat(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "triclinic.lammpstrj",
        """ITEM: TIMESTEP
0
ITEM: NUMBER OF ATOMS
2
ITEM: BOX BOUNDS xy xz yz pp pp ff
-1 11 1
-2 8 -2
3 9 0.5
ITEM: ATOMS element zs id xs ys
Al 0.5 8 0.0 0.0
O 1.0 7 1.0 1.0
""",
    )
    frame = read_lammps_frame(index_lammps_dump(source), 0, sort_by_id=True)
    expected_origin = np.asarray([1.0, -2.0, 3.0])
    expected_cell = np.asarray([[9.0, 0.0, 0.0], [1.0, 9.5, 0.0], [-2.0, 0.5, 6.0]])
    np.testing.assert_allclose(frame.origin, expected_origin)
    np.testing.assert_allclose(frame.cell, expected_cell)
    np.testing.assert_allclose(
        frame.positions,
        [
            expected_origin + expected_cell.sum(axis=0),
            expected_origin + 0.5 * expected_cell[2],
        ],
    )
    repeated = repeat_frame(frame, (3, 1, 1))
    assert repeated.natoms == 6
    np.testing.assert_allclose(repeated.cell[0], 3 * frame.cell[0])
    np.testing.assert_allclose(repeated.positions[2:4], frame.positions + frame.cell[0])
    corners = frame_box_corners(repeated)
    assert corners.shape == (8, 3)
    np.testing.assert_allclose(corners[-1], repeated.origin + repeated.cell.sum(axis=0))


@pytest.mark.parametrize(
    ("coordinates", "expected"),
    [
        ("x y z", [1.0, 2.0, 3.0]),
        ("xu yu zu", [1.0, 2.0, 3.0]),
        ("xs ys zs", [2.0, 4.0, 6.0]),
        ("xsu ysu zsu", [2.0, 4.0, 6.0]),
    ],
)
def test_coordinate_dialects(
    tmp_path: Path, coordinates: str, expected: list[float]
) -> None:
    source = _write(
        tmp_path / f"{coordinates.split()[0]}.dump",
        f"""ITEM: TIMESTEP
0
ITEM: NUMBER OF ATOMS
1
ITEM: BOX BOUNDS pp pp pp
0 2
0 2
0 2
ITEM: ATOMS element {coordinates}
H 1 2 3
""",
    )
    frame = read_lammps_frame(index_lammps_dump(source), 0)
    np.testing.assert_allclose(frame.positions[0], expected)


def test_corrupt_frame_is_rejected(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "broken.dump",
        """ITEM: TIMESTEP
0
ITEM: NUMBER OF ATOMS
2
ITEM: BOX BOUNDS pp pp pp
0 1
0 1
0 1
ITEM: ATOMS element x y z
H 0 0 0
""",
    )
    dump_index = index_lammps_dump(source)
    with pytest.raises(ValueError, match="declares 2 atoms"):
        read_lammps_frame(dump_index, 0)


def test_property_only_scan_and_repeat_preserve_values(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "property.dump",
        """ITEM: TIMESTEP
5
ITEM: NUMBER OF ATOMS
2
ITEM: BOX BOUNDS pp pp pp
0 3
0 3
0 3
ITEM: ATOMS id element x y z q vx vy vz ignored
2 O 1 0 0 -0.4 3 4 0 99
1 H 0 0 0 0.2 0 0 2 98
""",
    )
    dump = index_lammps_dump(source)
    property_frame = read_lammps_property_frame(
        dump, 0, property_columns=("q", "vx", "vy", "vz")
    )
    assert property_frame.atom_ids.tolist() == [2, 1]
    assert set(property_frame.atom_arrays) == {"q", "vx", "vy", "vz"}
    assert property_frame.atom_arrays["q"].tolist() == pytest.approx([-0.4, 0.2])

    frame = read_lammps_frame(dump, 0, property_columns=("q",), sort_by_id=True)
    repeated = repeat_frame(frame, (2, 1, 1))
    assert frame.atom_arrays["q"].tolist() == pytest.approx([0.2, -0.4])
    assert repeated.atom_arrays["q"].tolist() == pytest.approx([0.2, -0.4, 0.2, -0.4])
