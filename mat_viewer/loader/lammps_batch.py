"""Array-oriented LAMMPS dump indexing and frame loading.

This module is intentionally independent of ASE and MatterVis scene objects.
It parses only the data needed by the batch renderer and keeps one frame in
memory at a time.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable

import numpy as np

from .frame_batch import FrameBatch
from .frame_batch import frame_box_corners as frame_box_corners

_FRAME_MARKER = b"ITEM: TIMESTEP"
_COORDINATE_SETS = (
    ("x", "y", "z", False),
    ("xu", "yu", "zu", False),
    ("xs", "ys", "zs", True),
    ("xsu", "ysu", "zsu", True),
)
_BOUNDARY_FLAGS = {"pp", "ff", "ss", "mm", "fs", "fm", "sf", "sm", "mf", "ms"}


@dataclass(frozen=True)
class LammpsFrameRecord:
    """Byte offsets and header metadata for one dump frame."""

    index: int
    timestep: int
    natoms: int
    atoms_offset: int
    frame_end: int
    columns: tuple[str, ...]
    origin: np.ndarray
    cell: np.ndarray
    pbc: np.ndarray


@dataclass(frozen=True)
class LammpsDumpIndex:
    """Lightweight random-access index for one LAMMPS text dump."""

    path: Path
    records: tuple[LammpsFrameRecord, ...]

    def __len__(self) -> int:
        return len(self.records)


def _readline(handle: BinaryIO, context: str) -> bytes:
    line = handle.readline()
    if not line:
        raise ValueError(f"truncated LAMMPS dump while reading {context}")
    return line.rstrip(b"\r\n")


def _expect(handle: BinaryIO, prefix: bytes, context: str) -> bytes:
    line = _readline(handle, context)
    if not line.startswith(prefix):
        decoded = line.decode("utf-8", errors="replace")
        raise ValueError(f"expected {prefix.decode()!r} for {context}, got {decoded!r}")
    return line


def _box_from_header(
    header: bytes,
    rows: list[list[float]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    tokens = header.decode("ascii", errors="strict").split()[3:]
    flags = [token.lower() for token in tokens if token.lower() in _BOUNDARY_FLAGS]
    if len(flags) < 3:
        flags = ["pp", "pp", "pp"]
    pbc = np.asarray([flag == "pp" for flag in flags[-3:]], dtype=bool)

    triclinic = {"xy", "xz", "yz"}.issubset({token.lower() for token in tokens})
    if triclinic:
        if any(len(row) < 3 for row in rows):
            raise ValueError("triclinic BOX BOUNDS rows must contain tilt factors")
        xlo_bound, xhi_bound, xy = rows[0][:3]
        ylo_bound, yhi_bound, xz = rows[1][:3]
        zlo, zhi, yz = rows[2][:3]
        xlo = xlo_bound - min(0.0, xy, xz, xy + xz)
        xhi = xhi_bound - max(0.0, xy, xz, xy + xz)
        ylo = ylo_bound - min(0.0, yz)
        yhi = yhi_bound - max(0.0, yz)
        origin = np.asarray([xlo, ylo, zlo], dtype=np.float64)
        cell = np.asarray(
            [
                [xhi - xlo, 0.0, 0.0],
                [xy, yhi - ylo, 0.0],
                [xz, yz, zhi - zlo],
            ],
            dtype=np.float64,
        )
    else:
        if any(len(row) < 2 for row in rows):
            raise ValueError(
                "orthogonal BOX BOUNDS rows require lower and upper values"
            )
        lows = np.asarray([row[0] for row in rows], dtype=np.float64)
        highs = np.asarray([row[1] for row in rows], dtype=np.float64)
        origin = lows
        cell = np.diag(highs - lows)
    return origin, cell, pbc


def _scan_frame_offsets(handle: BinaryIO, *, chunk_size: int = 64 << 20) -> list[int]:
    offsets: list[int] = []
    overlap = b""
    absolute_end = 0
    while True:
        chunk = handle.read(chunk_size)
        if not chunk:
            break
        data = overlap + chunk
        data_start = absolute_end - len(overlap)
        search = 0
        while True:
            found = data.find(_FRAME_MARKER, search)
            if found < 0:
                break
            absolute = data_start + found
            preceded_by_newline = absolute == 0 or (
                found > 0 and data[found - 1 : found] == b"\n"
            )
            if preceded_by_newline and (not offsets or absolute > offsets[-1]):
                offsets.append(absolute)
            search = found + len(_FRAME_MARKER)
        absolute_end += len(chunk)
        overlap = data[-len(_FRAME_MARKER) :]
    return offsets


def _parse_record(
    handle: BinaryIO,
    *,
    index: int,
    offset: int,
    frame_end: int,
) -> LammpsFrameRecord:
    handle.seek(offset)
    _expect(handle, _FRAME_MARKER, "frame marker")
    timestep = int(_readline(handle, "timestep"))
    _expect(handle, b"ITEM: NUMBER OF ATOMS", "atom-count header")
    natoms = int(_readline(handle, "atom count"))
    box_header = _expect(handle, b"ITEM: BOX BOUNDS", "box header")
    rows: list[list[float]] = []
    for axis in "xyz":
        raw = _readline(handle, f"{axis} box bounds")
        try:
            rows.append([float(value) for value in raw.split()])
        except ValueError as exc:
            raise ValueError(f"invalid {axis} BOX BOUNDS row") from exc
    origin, cell, pbc = _box_from_header(box_header, rows)
    atoms_header = _expect(handle, b"ITEM: ATOMS", "atom columns")
    columns = tuple(
        value.decode("ascii", errors="strict").lower()
        for value in atoms_header.split()[2:]
    )
    if not columns:
        raise ValueError("LAMMPS ATOMS header has no columns")
    return LammpsFrameRecord(
        index=index,
        timestep=timestep,
        natoms=natoms,
        atoms_offset=handle.tell(),
        frame_end=frame_end,
        columns=columns,
        origin=origin,
        cell=cell,
        pbc=pbc,
    )


def index_lammps_dump(path: str | Path) -> LammpsDumpIndex:
    """Index frame offsets and parse only frame headers."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"LAMMPS dump not found: {source}")
    with source.open("rb") as handle:
        offsets = _scan_frame_offsets(handle)
        if not offsets:
            raise ValueError(f"no LAMMPS dump frames found in {source}")
        records = tuple(
            _parse_record(
                handle,
                index=index,
                offset=offset,
                frame_end=(
                    offsets[index + 1]
                    if index + 1 < len(offsets)
                    else source.stat().st_size
                ),
            )
            for index, offset in enumerate(offsets)
        )
    return LammpsDumpIndex(path=source, records=records)


def _coordinate_columns(columns: tuple[str, ...]) -> tuple[tuple[str, str, str], bool]:
    present = set(columns)
    for x_name, y_name, z_name, scaled in _COORDINATE_SETS:
        names = (x_name, y_name, z_name)
        if set(names).issubset(present):
            return names, scaled
    raise ValueError("LAMMPS dump requires x/y/z, xu/yu/zu, xs/ys/zs, or xsu/ysu/zsu")


def _symbol_to_number(symbols: np.ndarray) -> np.ndarray:
    from ase.data import atomic_numbers

    unique, inverse = np.unique(symbols, return_inverse=True)
    try:
        values = np.asarray(
            [atomic_numbers[str(symbol)] for symbol in unique],
            dtype=np.uint8,
        )
    except KeyError as exc:
        raise ValueError(
            f"unknown element symbol in LAMMPS dump: {exc.args[0]}"
        ) from exc
    return values[inverse]


def _numeric_table(
    block: bytes,
    *,
    columns: tuple[str, ...],
    selected: tuple[str, ...],
    natoms: int,
) -> np.ndarray:
    usecols = tuple(columns.index(name) for name in selected)
    data = np.loadtxt(
        io.BytesIO(block),
        dtype=np.float64,
        usecols=usecols,
        ndmin=2,
    )
    if data.shape != (natoms, len(selected)):
        raise ValueError(
            f"LAMMPS frame declares {natoms} atoms but parsed numeric table is {data.shape}"
        )
    return data


def _element_numbers(
    block: bytes,
    *,
    record: LammpsFrameRecord,
    type_map: list[str] | None,
) -> np.ndarray:
    columns = record.columns
    symbol_name = next(
        (name for name in ("element", "symbol") if name in columns), None
    )
    if symbol_name is not None:
        symbols = np.loadtxt(
            io.BytesIO(block),
            dtype="U4",
            usecols=(columns.index(symbol_name),),
            ndmin=1,
        )
        if symbols.shape != (record.natoms,):
            raise ValueError(
                f"LAMMPS frame declares {record.natoms} atoms but parsed "
                f"{symbols.shape[0]} element symbols"
            )
        return _symbol_to_number(symbols)
    if "type" not in columns:
        raise ValueError("LAMMPS dump has neither element/symbol nor numeric type")
    if not type_map:
        raise ValueError("numeric LAMMPS atom types require type_map in type order")
    from ase.data import atomic_numbers

    mapped = np.asarray(
        [0] + [atomic_numbers[symbol] for symbol in type_map],
        dtype=np.uint8,
    )
    types = _numeric_table(
        block,
        columns=columns,
        selected=("type",),
        natoms=record.natoms,
    )[:, 0].astype(np.int64)
    if np.any(types < 1) or np.any(types >= len(mapped)):
        raise ValueError("LAMMPS atom type falls outside the supplied type_map")
    return mapped[types]


def read_lammps_frame(
    dump_index: LammpsDumpIndex,
    frame: int,
    *,
    type_map: Iterable[str] | None = None,
    sort_by_id: bool = False,
) -> FrameBatch:
    """Read one indexed frame into contiguous arrays."""
    record = dump_index.records[frame]
    with dump_index.path.open("rb") as handle:
        handle.seek(record.atoms_offset)
        block = handle.read(record.frame_end - record.atoms_offset)
    coordinate_names, scaled = _coordinate_columns(record.columns)
    selected = coordinate_names + (("id",) if "id" in record.columns else ())
    numeric = _numeric_table(
        block,
        columns=record.columns,
        selected=selected,
        natoms=record.natoms,
    )
    positions = numeric[:, :3]
    if scaled:
        positions = record.origin + positions @ record.cell
    atom_ids = (
        numeric[:, 3].astype(np.int64, copy=False) if "id" in record.columns else None
    )
    atomic_numbers = _element_numbers(
        block,
        record=record,
        type_map=list(type_map) if type_map is not None else None,
    )
    if sort_by_id and atom_ids is not None:
        order = np.argsort(atom_ids, kind="stable")
        positions = positions[order]
        atomic_numbers = atomic_numbers[order]
        atom_ids = atom_ids[order]
    return FrameBatch(
        positions=np.ascontiguousarray(positions, dtype=np.float32),
        atomic_numbers=np.ascontiguousarray(atomic_numbers, dtype=np.uint8),
        atom_ids=(
            np.ascontiguousarray(atom_ids, dtype=np.int64)
            if atom_ids is not None
            else None
        ),
        origin=record.origin.copy(),
        cell=record.cell.copy(),
        pbc=record.pbc.copy(),
        timestep=record.timestep,
        source_index=record.index,
    )


def repeat_frame(frame: FrameBatch, repeat: tuple[int, int, int]) -> FrameBatch:
    """Repeat one periodic frame without constructing per-atom objects."""
    factors = np.asarray(repeat, dtype=np.int64)
    if factors.shape != (3,) or np.any(factors < 1):
        raise ValueError("repeat must contain three positive integers")
    shifts = np.stack(
        np.meshgrid(
            np.arange(factors[0]),
            np.arange(factors[1]),
            np.arange(factors[2]),
            indexing="ij",
        ),
        axis=-1,
    ).reshape(-1, 3)
    translations = shifts @ frame.cell
    positions = (frame.positions[None, :, :] + translations[:, None, :]).reshape(-1, 3)
    repeated_cell = frame.cell * factors[:, None]
    atom_ids = None
    if frame.atom_ids is not None:
        atom_ids = np.tile(frame.atom_ids, len(translations))
    return FrameBatch(
        positions=np.ascontiguousarray(positions, dtype=np.float32),
        atomic_numbers=np.tile(frame.atomic_numbers, len(translations)),
        atom_ids=atom_ids,
        origin=frame.origin.copy(),
        cell=np.ascontiguousarray(repeated_cell, dtype=np.float64),
        pbc=frame.pbc.copy(),
        timestep=frame.timestep,
        source_index=frame.source_index,
    )
