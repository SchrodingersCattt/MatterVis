"""Bounded, JSON-safe structure inspection payloads."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path


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
