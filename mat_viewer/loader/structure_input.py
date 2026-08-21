"""Unified atomistic file input for MatterVis renderers and terminal views.

Every supported file format is parsed into a canonical LoadedCrystal frame.
Format adapters stop at parsing; scene construction and rendering remain shared.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .bundle_builder import build_loaded_crystal_from_atoms
from .core import LoadedCrystal, build_loaded_crystal

_FORMAT_ALIASES = {
    "ase-traj": "traj",
    "lammps-dump": "lammps-dump-text",
    "lammps-data": "lammps-data",
    "poscar": "vasp",
    "vasp": "vasp",
}

_SUFFIX_FORMATS = {
    ".conf": "lammps-data",
    ".data": "lammps-data",
    ".dump": "lammps-dump-text",
    ".lammpstrj": "lammps-dump-text",
    ".lammpsdump": "lammps-dump-text",
    ".poscar": "vasp",
    ".traj": "traj",
    ".vasp": "vasp",
}


@dataclass(frozen=True)
class StructureFrame:
    """One canonical renderable frame from an atomistic input."""

    index: int
    bundle: LoadedCrystal
    info: dict[str, Any]
    atom_arrays: dict[str, np.ndarray]


@dataclass(frozen=True)
class AtomisticFrame:
    """One parsed ASE frame before expensive MatterVis canonicalisation."""

    index: int
    atoms: Any
    info: dict[str, Any]
    atom_arrays: dict[str, np.ndarray]


@dataclass(frozen=True)
class AtomisticInput:
    """Selected ASE frames with source order and metadata preserved."""

    path: Path
    input_format: str
    frames: tuple[AtomisticFrame, ...]
    total_frames: int


@dataclass(frozen=True)
class StructureInput:
    """A parsed structure or trajectory with canonical renderable frames."""

    path: Path
    input_format: str
    frames: tuple[StructureFrame, ...]
    total_frames: int

    @property
    def n_frames(self) -> int:
        return self.total_frames


def _normalise_format(path: Path, input_format: str | None) -> str | None:
    if input_format:
        value = input_format.strip().lower()
        if value in {"", "auto"}:
            return None
        return _FORMAT_ALIASES.get(value, value)
    if path.name.upper() in {"POSCAR", "CONTCAR"}:
        return "vasp"
    if path.suffix.lower() == ".cif":
        return "cif"
    if path.suffix.lower() == ".cube":
        return "cube"
    return _SUFFIX_FORMATS.get(path.suffix.lower())


def _validate_type_map(type_map: Iterable[str] | None) -> list[str] | None:
    if type_map is None:
        return None
    from ase.data import atomic_numbers

    symbols = [str(value).strip() for value in type_map if str(value).strip()]
    unknown = [symbol for symbol in symbols if symbol not in atomic_numbers]
    if unknown:
        raise ValueError(f"unknown element symbols in --type-map: {', '.join(unknown)}")
    return symbols or None


def _dump_declares_elements(path: Path) -> bool:
    try:
        with path.open("rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("ITEM: ATOMS"):
                    columns = {value.lower() for value in line.split()[2:]}
                    return bool(columns & {"element", "symbol"})
    except OSError:
        return False
    return False


def _validate_lammps_mapping(
    path: Path,
    input_format: str | None,
    type_map: list[str] | None,
) -> None:
    if type_map:
        return
    if input_format == "lammps-data":
        raise ValueError(
            "LAMMPS data/configuration files require the complete --type-map "
            "in numeric atom-type order"
        )
    if input_format and input_format.startswith("lammps-dump"):
        if input_format == "lammps-dump-text" and _dump_declares_elements(path):
            return
        raise ValueError(
            "LAMMPS trajectories with numeric atom types require the complete "
            "--type-map in numeric atom-type order"
        )


def _prepare_source(
    path: str | Path,
    input_format: str | None,
    type_map: Iterable[str] | None,
) -> tuple[Path, str | None, list[str] | None]:
    source_path = Path(path).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"structure file not found: {source_path}")
    resolved_format = _normalise_format(source_path, input_format)
    symbols = _validate_type_map(type_map)
    _validate_lammps_mapping(source_path, resolved_format, symbols)
    return source_path, resolved_format, symbols


def _ase_read_kwargs(
    input_format: str | None, type_map: list[str] | None
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if input_format and input_format.startswith("lammps-dump") and type_map:
        kwargs["specorder"] = type_map
    elif input_format == "lammps-data" and type_map:
        from ase.data import atomic_numbers

        kwargs["Z_of_type"] = {
            index: atomic_numbers[symbol]
            for index, symbol in enumerate(type_map, start=1)
        }
    return kwargs


def _ase_frames(
    source_path: Path,
    resolved_format: str | None,
    symbols: list[str] | None,
):
    from ase.io import iread as ase_iread

    kwargs = _ase_read_kwargs(resolved_format, symbols)
    try:
        yield from ase_iread(
            str(source_path),
            index=":",
            format=resolved_format,
            **kwargs,
        )
    except Exception as exc:
        suffix_hint = (
            " Pass --input-format explicitly for ambiguous files such as "
            "LAMMPS data/configuration files."
        )
        raise ValueError(
            f"could not read {source_path.name!r}"
            f"{f' as {resolved_format}' if resolved_format else ''}: {exc}."
            f"{suffix_hint}"
        ) from exc


def count_structure_frames(
    path: str | Path,
    *,
    input_format: str | None = None,
    type_map: Iterable[str] | None = None,
) -> int:
    """Count source frames without building molecular analyses or render scenes."""
    source_path, resolved_format, symbols = _prepare_source(
        path, input_format, type_map
    )
    if resolved_format in {"cif", "cube"}:
        return 1
    return sum(1 for _ in _ase_frames(source_path, resolved_format, symbols))


def _cell_from_matrix(matrix: np.ndarray):
    from ..structure.cif_parse import cell_from_matrix

    return cell_from_matrix(matrix)


def _ase_atoms_to_pipeline(
    atoms,
) -> tuple[list[dict[str, Any]], Any, np.ndarray, dict[str, Any]]:
    positions = np.asarray(atoms.get_positions(), dtype=float)
    matrix = np.asarray(atoms.cell.array, dtype=float)
    has_lattice = bool(abs(np.linalg.det(matrix)) > 1e-8)

    origin_shift = np.zeros(3, dtype=float)
    if not has_lattice:
        if len(positions):
            mins = positions.min(axis=0)
            spans = np.maximum(positions.max(axis=0) - mins, 1.0)
        else:
            mins = np.zeros(3, dtype=float)
            spans = np.ones(3, dtype=float)
        padding = 5.0
        origin_shift = mins - padding
        positions = positions - origin_shift
        matrix = np.diag(spans + 2.0 * padding)

    inverse = np.linalg.inv(matrix)
    fractional = positions @ inverse
    symbols = atoms.get_chemical_symbols()
    raw_atoms: list[dict[str, Any]] = []
    for index, (symbol, cart, frac) in enumerate(zip(symbols, positions, fractional)):
        label = f"{symbol}{index + 1}"
        raw_atoms.append(
            {
                "elem": symbol,
                "cart": np.asarray(cart, dtype=float),
                "frac": np.asarray(frac, dtype=float),
                "label": label,
                "_asym_label": label,
                "occ": 1.0,
                "dg": ".",
                "da": ".",
                "_symop_index": 0,
                "_source_index": index,
                "_bond_partners": (),
                "_bond_lengths": {},
                "_has_bond_table": False,
            }
        )

    metadata = {
        "has_lattice": has_lattice,
        "pbc": [bool(value) for value in atoms.pbc],
        "synthetic_cell": not has_lattice,
        "origin_shift": origin_shift.tolist(),
    }
    return raw_atoms, _cell_from_matrix(matrix), matrix, metadata


def build_loaded_crystal_from_ase(
    atoms,
    *,
    path: str | Path,
    frame_index: int,
    input_format: str,
) -> LoadedCrystal:
    """Convert one ASE Atoms frame into the canonical LoadedCrystal class."""
    source_path = str(Path(path).resolve())
    raw_atoms, cell, matrix, metadata = _ase_atoms_to_pipeline(atoms)
    metadata.update(
        {
            "frame_index": int(frame_index),
            "input_format": input_format,
            "source_info": dict(atoms.info),
        }
    )
    bundle = build_loaded_crystal_from_atoms(
        name=Path(path).stem,
        source_path=source_path,
        raw_atoms=raw_atoms,
        cell=cell,
        M=matrix,
        title=Path(path).stem,
        source="ase",
        scene_metadata_extra=metadata,
    )
    bundle.frame_info = {"frame_index": int(frame_index), **dict(atoms.info)}
    bundle.atom_arrays = _atom_arrays(atoms)
    return bundle


def _normalise_requested_indices(
    requested: list[int],
    total_frames: int,
) -> list[int]:
    resolved = [value + total_frames if value < 0 else value for value in requested]
    invalid = [
        original
        for original, value in zip(requested, resolved)
        if not 0 <= value < total_frames
    ]
    if invalid:
        raise ValueError(
            f"frame {invalid[0]} is out of range for {total_frames} frame(s)"
        )
    return resolved


def _selected_ase_atoms(
    source_path: Path,
    resolved_format: str | None,
    symbols: list[str] | None,
    frame_indices: Iterable[int] | None,
) -> tuple[list[tuple[int, Any]], int]:
    requested = (
        None if frame_indices is None else [int(value) for value in frame_indices]
    )
    if requested is not None and not requested:
        raise ValueError("at least one frame must be selected")

    positive = {value for value in requested or [] if value >= 0}
    tail_depth = max((-value for value in requested or [] if value < 0), default=0)
    tail: deque[tuple[int, Any]] = deque(maxlen=tail_depth or None)
    chosen: dict[int, Any] = {}
    all_frames: list[tuple[int, Any]] = []

    total_frames = 0
    for index, atoms in enumerate(_ase_frames(source_path, resolved_format, symbols)):
        total_frames = index + 1
        if requested is None:
            all_frames.append((index, atoms))
        else:
            if index in positive:
                chosen[index] = atoms
            if tail_depth:
                tail.append((index, atoms))

    if not total_frames:
        raise ValueError(f"no atomistic frames found in {source_path}")
    if requested is None:
        return all_frames, total_frames

    chosen.update(tail)
    resolved = _normalise_requested_indices(requested, total_frames)
    missing = [index for index in resolved if index not in chosen]
    if missing:
        raise ValueError(
            f"frame {missing[0]} is out of range for {total_frames} frame(s)"
        )
    return [(index, chosen[index]) for index in resolved], total_frames


def _atom_arrays(atoms) -> dict[str, np.ndarray]:
    return {
        str(name): np.array(values, copy=True)
        for name, values in atoms.arrays.items()
        if name not in {"numbers", "positions"}
    }


def load_atomistic_input(
    path: str | Path,
    *,
    input_format: str | None = None,
    type_map: Iterable[str] | None = None,
    frame_indices: Iterable[int] | None = None,
) -> AtomisticInput:
    """Load selected ASE frames without building canonical render scenes."""
    source_path, resolved_format, symbols = _prepare_source(
        path, input_format, type_map
    )
    if resolved_format in {"cif", "cube"}:
        raise ValueError(
            "load_atomistic_input supports ASE-backed inputs, not CIF or Cube"
        )
    selected_atoms, total_frames = _selected_ase_atoms(
        source_path,
        resolved_format,
        symbols,
        frame_indices,
    )
    format_name = resolved_format or "ase-auto"
    frames = tuple(
        AtomisticFrame(
            index=index,
            atoms=atoms,
            info={"frame_index": index, **dict(atoms.info)},
            atom_arrays=_atom_arrays(atoms),
        )
        for index, atoms in selected_atoms
    )
    return AtomisticInput(source_path, format_name, frames, total_frames)


def iter_atomistic_frames(
    path: str | Path,
    *,
    input_format: str | None = None,
    type_map: Iterable[str] | None = None,
    frame_indices: Iterable[int] | None = None,
):
    """Yield selected ASE frames in source order with bounded memory.

    ``frame_indices`` must already contain non-negative source indices.  This
    is the contract used after the CLI resolves Python slices against the
    counted trajectory length.  Output ordering can be restored separately by
    callers that requested a reverse slice.
    """
    source_path, resolved_format, symbols = _prepare_source(
        path, input_format, type_map
    )
    if resolved_format in {"cif", "cube"}:
        raise ValueError(
            "iter_atomistic_frames supports ASE-backed inputs, not CIF or Cube"
        )
    requested = (
        None if frame_indices is None else [int(value) for value in frame_indices]
    )
    if requested is not None and any(value < 0 for value in requested):
        raise ValueError("streaming frame indices must be non-negative")
    selected = None if requested is None else set(requested)
    found: set[int] = set()
    format_name = resolved_format or "ase-auto"
    for index, atoms in enumerate(_ase_frames(source_path, resolved_format, symbols)):
        if selected is not None and index not in selected:
            continue
        found.add(index)
        yield (
            AtomisticFrame(
                index=index,
                atoms=atoms,
                info={"frame_index": index, **dict(atoms.info)},
                atom_arrays=_atom_arrays(atoms),
            ),
            format_name,
        )
    if selected is not None:
        missing = sorted(selected - found)
        if missing:
            raise ValueError(f"frame {missing[0]} is out of range")


def canonicalise_atomistic_frame(
    frame: AtomisticFrame,
    *,
    path: str | Path,
    input_format: str,
) -> StructureFrame:
    """Build one canonical MatterVis frame while retaining ASE metadata."""
    bundle = build_loaded_crystal_from_ase(
        frame.atoms,
        path=path,
        frame_index=frame.index,
        input_format=input_format,
    )
    return StructureFrame(
        frame.index,
        bundle,
        dict(bundle.frame_info),
        {
            name: np.array(values, copy=True)
            for name, values in bundle.atom_arrays.items()
        },
    )


def load_structure_input(
    path: str | Path,
    *,
    input_format: str | None = None,
    type_map: Iterable[str] | None = None,
    frame_indices: Iterable[int] | None = None,
) -> StructureInput:
    """Load selected frames into the canonical renderable structure class."""
    source_path, resolved_format, symbols = _prepare_source(
        path, input_format, type_map
    )
    requested = (
        None if frame_indices is None else [int(value) for value in frame_indices]
    )

    if resolved_format in {"cif", "cube"}:
        selected = (
            [0] if requested is None else _normalise_requested_indices(requested, 1)
        )
        if resolved_format == "cif":
            bundle = build_loaded_crystal(
                name=source_path.stem,
                cif_path=str(source_path),
                title=source_path.stem,
                source="upload",
            )
        else:
            from .cube_adapter import load_cube_file

            bundle = load_cube_file(source_path)
        frames = tuple(
            StructureFrame(0, bundle, {"frame_index": 0}, {}) for _ in selected
        )
        return StructureInput(source_path, resolved_format, frames, 1)

    atomistic = load_atomistic_input(
        source_path,
        input_format=resolved_format,
        type_map=symbols,
        frame_indices=requested,
    )
    canonical_frames = tuple(
        canonicalise_atomistic_frame(
            frame,
            path=source_path,
            input_format=atomistic.input_format,
        )
        for frame in atomistic.frames
    )
    return StructureInput(
        source_path,
        atomistic.input_format,
        canonical_frames,
        atomistic.total_frames,
    )
