from __future__ import annotations

from .bundle_builder import build_loaded_crystal_from_atoms  # noqa: F401
from .core import (  # noqa: F401
    LoadedCrystal,
    _fragment_table_from_atoms,
    _has_shelx_occupancy_disorder,
    _tag_shelx_occupancy_disorder,
    _unwrapped_atoms_from_atoms,
    build_bundle_scene,
    build_empty_bundle,
    build_loaded_crystal,
    # perf_log is a module-level name in core.py (from ``from .. import perf_log``)
    # and callers do ``from mat_viewer.loader import perf_log``.
    perf_log,
)
from .uploads import (  # noqa: F401
    bundle_json,
    infer_uploaded_name,
    load_default_catalog,
    load_uploaded_cif,
    write_uploaded_cif,
)

__all__ = [
    "LoadedCrystal",
    "_fragment_table_from_atoms",
    "_has_shelx_occupancy_disorder",
    "_tag_shelx_occupancy_disorder",
    "_unwrapped_atoms_from_atoms",
    "build_bundle_scene",
    "build_empty_bundle",
    "build_loaded_crystal",
    "build_loaded_crystal_from_atoms",
    "build_loaded_crystal_from_ase",
    "canonicalise_atomistic_frame",
    "count_structure_frames",
    "bundle_json",
    "FrameBatch",
    "frame_batch_from_ase",
    "LammpsDumpIndex",
    "LammpsFrameRecord",
    "frame_box_corners",
    "index_lammps_dump",
    "StructureFrame",
    "StructureInput",
    "AtomisticFrame",
    "AtomisticInput",
    "load_atomistic_input",
    "iter_atomistic_frames",
    "load_structure_input",
    "read_lammps_frame",
    "repeat_frame",
    "infer_uploaded_name",
    "load_default_catalog",
    "load_uploaded_cif",
    "perf_log",
    "write_uploaded_cif",
]

from .frame_batch import (  # noqa: F401
    FrameBatch,
    frame_batch_from_ase,
    frame_box_corners,
)
from .lammps_batch import (  # noqa: F401
    LammpsDumpIndex,
    LammpsFrameRecord,
    index_lammps_dump,
    read_lammps_frame,
    repeat_frame,
)
from .structure_input import (  # noqa: F401
    AtomisticFrame,
    AtomisticInput,
    StructureFrame,
    StructureInput,
    build_loaded_crystal_from_ase,
    canonicalise_atomistic_frame,
    count_structure_frames,
    iter_atomistic_frames,
    load_atomistic_input,
    load_structure_input,
)
