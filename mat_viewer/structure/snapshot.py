"""Explicit scientific materialization.

Rendered scenes are not a structure source. Callers that already hold a
scientific snapshot can rebuild a ``MolecularCrystal`` from that record.
Plotly traces, sphere centers, and display indices are rejected.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from ase import Atoms
from molcrys_kit.constants.config import (
    KEY_ASSEMBLY,
    KEY_ATOM_ID,
    KEY_DISORDER_GROUP,
    KEY_LABEL,
    KEY_OCCUPANCY,
)
from molcrys_kit.structures.molecule import CrystalMolecule

from ..effects.materialize import crystal_from_molecules


def molecular_crystal_from_scene(scene: dict[str, Any]):
    """Rebuild a crystal from an explicit scientific snapshot.

    A scene that only contains rendered atoms is rejected. Passing
    ``scientific_snapshot`` with lattice, sites, and site ids is the
    supported bridge; it does not infer atoms from meshes.
    """

    payload = scene.get("scientific_snapshot") if isinstance(scene, dict) else None
    if not isinstance(payload, dict) or not payload.get("sites") or not payload.get("lattice"):
        raise ValueError(
            "rendered scenes are not a scientific structure source; "
            "commit through EffectSession or pass scientific_snapshot"
        )
    lattice = np.asarray(payload["lattice"], dtype=float)
    pbc = tuple(bool(value) for value in payload.get("pbc", (True, True, True)))
    grouped: dict[int, list[dict[str, Any]]] = {}
    for site in payload["sites"]:
        if not isinstance(site, dict) or not site.get("site_id"):
            raise ValueError("scientific_snapshot sites require site_id")
        grouped.setdefault(int(site.get("molecule_index", 0)), []).append(site)
    molecules = []
    for _molecule_index, sites in sorted(grouped.items()):
        atoms = Atoms(
            symbols=[str(site.get("symbol") or "X") for site in sites],
            positions=np.asarray([site.get("cartesian") or (0.0, 0.0, 0.0) for site in sites], dtype=float),
            cell=lattice,
            pbc=list(pbc),
        )
        atoms.set_array(KEY_OCCUPANCY, np.asarray([float(site.get("occupancy", 1.0)) for site in sites]))
        atoms.set_array(KEY_DISORDER_GROUP, np.asarray([int(site.get("disorder_group", 0)) for site in sites], dtype=int))
        atoms.set_array(KEY_ASSEMBLY, np.array([str(site.get("disorder_assembly") or "") for site in sites]))
        atoms.set_array(KEY_LABEL, np.array([str(site.get("label") or site.get("symbol") or "X") for site in sites]))
        atoms.set_array(KEY_ATOM_ID, np.array([str(site["site_id"]) for site in sites], dtype=str))
        molecules.append(CrystalMolecule(atoms, check_pbc=False))
    return crystal_from_molecules(lattice, molecules, pbc=pbc, metadata={"source": "scientific_snapshot"})


__all__ = ["molecular_crystal_from_scene"]
