"""Mercury input adapter: MolCrysKit parses; MatterVis only assembles scenes.

No Mercury XML, chemistry, bond-code or symmetry interpretation belongs here.
The upstream reader emits import limitations and retains source provenance.
"""

from __future__ import annotations


def read_mryx_frames(path):
    """Return every stored structure.

    MolCrysKit ``read_mryx_atoms(index=':')`` validates every structure and
    builds the full list before this returns. A later frame selection does
    not make this adapter lazy. A direct upstream call with one index keeps
    only that structure after validation; this adapter does not use that
    path. A missing reader becomes an actionable dependency error rather
    than a second parser.
    """
    try:
        from molcrys_kit.io import read_mryx_atoms
    except ImportError as exc:
        raise ValueError(
            "MRYX input requires MolCrysKit with read_mryx_atoms support. "
            "Install MatterVis's declared MolCrysKit dependency; see docs/mryx.md."
        ) from exc
    return read_mryx_atoms(path, index=":")


def build_mryx_bundle(
    *,
    path,
    name,
    title=None,
    preset=None,
    source="upload",
    view_weights=None,
    bond_scale=None,
    bond_thresholds=None,
):
    """Build the first stored structure for single-bundle/browser entry points."""
    from .bundle_builder import build_loaded_crystal_from_atoms
    from .structure_input import _ase_atoms_to_pipeline, _atom_arrays

    frames = read_mryx_frames(path)
    atoms = frames[0]
    raw_atoms, cell, matrix, metadata = _ase_atoms_to_pipeline(atoms)
    metadata.update(
        {
            "input_format": "mryx",
            "frame_index": 0,
            "source_info": dict(atoms.info),
            "source_structure_count": len(frames),
            "input_diagnostics": list(atoms.info["mryx"]["diagnostics"]),
        }
    )
    bundle = build_loaded_crystal_from_atoms(
        name=name,
        source_path=str(path),
        raw_atoms=raw_atoms,
        cell=cell,
        M=matrix,
        title=title,
        preset=preset,
        source=source,
        view_weights=view_weights,
        bond_scale=bond_scale,
        bond_thresholds=bond_thresholds,
        scene_metadata_extra=metadata,
    )
    bundle.frame_info = {"frame_index": 0, **dict(atoms.info)}
    bundle.atom_arrays = _atom_arrays(atoms)
    return bundle
