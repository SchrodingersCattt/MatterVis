"""Phone-facing Analysis, Display, and Operate snapshots.

The GPU on the phone draws this payload. This module does not rasterize.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def analyze_cif(path: str | Path) -> dict[str, Any]:
    """Read one CIF into an immutable site snapshot."""
    crystal = _read(path)
    snapshot = _project(crystal)
    snapshot["revision"] = 1
    snapshot["source"] = str(path)
    return snapshot


def display(snapshot: dict[str, Any], *, disorder_group: str | int | None = None) -> dict[str, Any]:
    """Change only the view. Occupancy stays the atom alpha."""
    selected = None if disorder_group is None else str(disorder_group)
    sites = []
    for site in snapshot["sites"]:
        item = dict(site)
        group = str(site["disorder_group"])
        ordered = group in {"0", ".", ""}
        item["visible"] = selected is None or ordered or group == selected
        item["alpha"] = float(site["occupancy"])
        sites.append(item)
    shown = dict(snapshot)
    shown["sites"] = sites
    shown["display"] = {"disorder_group": selected}
    return shown


def operate_commit_major(path: str | Path) -> dict[str, Any]:
    """Commit the major disorder component through EffectSession."""
    from mat_viewer.effects import EffectSession
    from molcrys_kit.analysis.disorder.process import (
        generate_ordered_replicas_from_disordered_sites,
    )

    source = _read(path)
    replicas = generate_ordered_replicas_from_disordered_sites(
        str(path),
        method="optimal",
        generate_count=1,
        return_kept_indices=True,
    )
    if not replicas:
        raise ValueError("disorder generation produced no candidate")
    major, kept = replicas[0]
    session = EffectSession()
    ref = session.register(source, structure_id="phone")
    committed = session.operate(
        "commit_disorder_candidate",
        {
            "crystal": major,
            "kept_indices": [int(index) for index in kept],
            "method": "optimal",
        },
        structure_id=ref.id,
        expected_revision=ref.revision,
    )
    saved = session.checkout(committed.output_ref)
    snapshot = _project(saved)
    snapshot["revision"] = int(committed.output_ref.revision)
    snapshot["source"] = str(path)
    snapshot["operation"] = "commit_disorder_candidate"
    return snapshot


def _read(path: str | Path, **kwargs):
    from molcrys_kit.io.cif import read_mol_crystal

    return read_mol_crystal(str(path), attach_chemistry=False, **kwargs)


def _project(crystal) -> dict[str, Any]:
    sites = []
    for record in crystal.get_site_records():
        occupancy = float(record.occupancy)
        sites.append(
            {
                "site_id": str(record.site_id),
                "symbol": str(record.symbol),
                "position": [float(value) for value in record.cartesian_position_A],
                "occupancy": occupancy,
                "alpha": occupancy,
                "disorder_group": str(record.disorder_group),
                "disorder_assembly": record.disorder_assembly or ".",
                "visible": True,
            }
        )
    bonds = [
        {
            "left": int(record.left_global_index),
            "right": int(record.right_global_index),
            "role": str(record.role),
        }
        for record in crystal.get_bond_records()
    ]
    return {
        "cell": [[float(value) for value in row] for row in crystal.lattice],
        "sites": sites,
        "bonds": bonds,
    }
