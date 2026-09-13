"""Bounded, JSON-safe structure inspection payloads."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from ..properties import catalog_from_arrays, catalog_from_columns, merge_catalogs


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_nonperiodic_structure(structure) -> bool:
    frames = tuple(getattr(structure, "frames", ()) or ())
    if not frames:
        return False
    scene = getattr(frames[0].bundle, "scene", {}) or {}
    if bool(scene.get("synthetic_cell", False)):
        return True
    if scene.get("has_lattice") is False:
        return True
    pbc = scene.get("pbc")
    return pbc is not None and not any(bool(value) for value in pbc)


def _records(crystal, method: str) -> list | None:
    function = getattr(crystal, method, None)
    if not callable(function):
        return None
    return list(function())


def inspect_payload(structure) -> dict:
    selected = structure.frames[0]
    bundle = selected.bundle
    analysis = getattr(bundle, "molcrys_analysis", None)
    crystal = getattr(analysis, "crystal", None)
    metadata = bundle.metadata() if hasattr(bundle, "metadata") else {}
    scene = getattr(bundle, "scene", {}) or {}
    has_lattice = scene.get("has_lattice")
    pbc = scene.get("pbc")
    synthetic_cell = bool(scene.get("synthetic_cell", False))
    warnings = list(metadata.get("warnings") or [])
    site_records = (
        _records(crystal, "get_site_records") if crystal is not None else None
    )
    bond_records = (
        _records(crystal, "get_bond_records") if crystal is not None else None
    )
    disordered_sites = []
    if site_records is not None:
        for record in site_records:
            occupancy = float(getattr(record, "occupancy", 1.0))
            group = getattr(record, "disorder_group", 0)
            if occupancy < 1.0 - 1.0e-8 or group not in (None, 0, "0", ".", "?"):
                disordered_sites.append(record)
    if disordered_sites and not any(
        "disorder" in warning.lower() for warning in warnings
    ):
        warnings.append(
            f"MolCrysKit reports disorder in {len(disordered_sites)} of "
            f"{len(site_records)} sites."
        )
    return {
        "schema": "mattervis.inspect/v1",
        "ok": True,
        "source": {
            "path": str(structure.path),
            "sha256": file_sha256(Path(structure.path)),
            "input_format": structure.input_format,
            "frame": selected.index,
            "total_frames": structure.total_frames,
        },
        "structure": {
            "site_records": None if site_records is None else len(site_records),
            "bond_records": None if bond_records is None else len(bond_records),
            "parsed_atoms": len(getattr(bundle, "raw_atoms", ()) or ()),
            "displayed_atoms": len(scene.get("draw_atoms", ()) or ()),
            "fragments": len(getattr(bundle, "fragment_table", ()) or ()),
            "has_disorder": bool(disordered_sites),
            "has_lattice": has_lattice,
            "pbc": None if pbc is None else [bool(value) for value in pbc],
            "synthetic_cell": synthetic_cell,
            "periodic": not is_nonperiodic_structure(structure),
        },
        "warnings": warnings,
    }


def inspect_properties_payload(
    path: str | Path,
    *,
    input_format: str | None = None,
    type_map=None,
    frame: int = 0,
    property_data: str | Path | None = None,
) -> dict:
    """Discover bounded field metadata without constructing a render scene."""

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"structure file not found: {source}")
    format_name = str(input_format or "").strip().lower()
    lammps = format_name in {
        "lammps-dump",
        "lammps-dump-text",
    } or source.suffix.lower() in {
        ".dump",
        ".lammpstrj",
        ".lammpsdump",
    }
    catalogs = []
    total_frames = 1
    selected_frame = int(frame)
    if lammps:
        from ..loader.lammps_batch import index_lammps_dump

        index = index_lammps_dump(source)
        total_frames = len(index)
        selected_frame = (
            selected_frame + total_frames if selected_frame < 0 else selected_frame
        )
        if not 0 <= selected_frame < total_frames:
            raise ValueError(
                f"frame {frame} is out of range for {total_frames} frame(s)"
            )
        catalogs.append(catalog_from_columns(index.records[selected_frame].columns))
        resolved_format = "lammps-dump-text"
    elif source.suffix.lower() not in {".cif", ".cube"} and format_name not in {
        "cif",
        "cube",
    }:
        from ..loader.structure_input import load_atomistic_input

        loaded = load_atomistic_input(
            source,
            input_format=input_format,
            type_map=type_map,
            frame_indices=[selected_frame],
        )
        total_frames = loaded.total_frames
        selected_frame = loaded.frames[0].index
        catalogs.append(catalog_from_arrays(loaded.frames[0].atom_arrays))
        resolved_format = loaded.input_format
    else:
        resolved_format = format_name or source.suffix.lower().lstrip(".")
    manifest_hash = None
    if property_data is not None:
        from ..loader.property_sidecar import load_atom_property_manifest

        manifest = load_atom_property_manifest(property_data)
        catalogs.append(manifest.catalog())
        manifest_hash = manifest.manifest_hash
    catalog = merge_catalogs(*catalogs) if catalogs else catalog_from_arrays({})
    return {
        "schema": "mattervis.atom-property-catalog/v1",
        "ok": True,
        "source": {
            "path": str(source),
            "input_format": resolved_format,
            "frame": selected_frame,
            "total_frames": total_frames,
        },
        "properties": catalog.to_dict(),
        "manifest_hash": manifest_hash,
        "warnings": [],
    }


__all__ = [
    "file_sha256",
    "inspect_payload",
    "inspect_properties_payload",
    "is_nonperiodic_structure",
]
