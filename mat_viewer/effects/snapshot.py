"""Immutable scientific snapshots.

The public record is tuples and plain numbers. The owning
``MolecularCrystal`` stays behind the session and is never handed to
Display or Analysis.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from .properties import property_fingerprint
from .refs import StructureRef


@dataclass(frozen=True, slots=True)
class SiteView:
    site_id: str
    symbol: str
    label: str
    occupancy: float
    fractional: tuple[float, float, float]
    cartesian: tuple[float, float, float]
    molecule_index: int
    local_index: int
    global_index: int
    disorder_group: int
    disorder_assembly: str
    image_shift: tuple[int, int, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "site_id": self.site_id,
            "symbol": self.symbol,
            "label": self.label,
            "occupancy": self.occupancy,
            "fractional": list(self.fractional),
            "cartesian": list(self.cartesian),
            "molecule_index": self.molecule_index,
            "local_index": self.local_index,
            "global_index": self.global_index,
            "disorder_group": self.disorder_group,
            "disorder_assembly": self.disorder_assembly,
            "image_shift": list(self.image_shift),
        }


@dataclass(frozen=True, slots=True)
class BondView:
    left_site_id: str
    right_site_id: str
    left_global_index: int
    right_global_index: int
    distance_A: float
    role: str
    vector_A: tuple[float, float, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_site_id": self.left_site_id,
            "right_site_id": self.right_site_id,
            "left_global_index": self.left_global_index,
            "right_global_index": self.right_global_index,
            "distance_A": self.distance_A,
            "role": self.role,
            "vector_A": list(self.vector_A),
        }


@dataclass(frozen=True, slots=True)
class ScientificSnapshot:
    ref: StructureRef
    lattice: tuple[tuple[float, float, float], ...]
    pbc: tuple[bool, bool, bool]
    sites: tuple[SiteView, ...]
    bonds: tuple[BondView, ...]
    properties: tuple
    provenance: tuple
    fingerprint: str

    @property
    def n_sites(self) -> int:
        return len(self.sites)

    @property
    def species_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for site in self.sites:
            counts[site.symbol] = counts.get(site.symbol, 0) + 1
        return counts

    @property
    def volume(self) -> float:
        return float(abs(np.linalg.det(np.asarray(self.lattice, dtype=float))))

    def lattice_parameters(self) -> dict[str, float]:
        matrix = np.asarray(self.lattice, dtype=float)
        lengths = np.linalg.norm(matrix, axis=1)

        def angle(first: int, second: int) -> float:
            cosine = float(np.dot(matrix[first], matrix[second]) / (lengths[first] * lengths[second]))
            return float(np.degrees(np.arccos(float(np.clip(cosine, -1.0, 1.0)))))

        return {
            "a": float(lengths[0]),
            "b": float(lengths[1]),
            "c": float(lengths[2]),
            "alpha": angle(1, 2),
            "beta": angle(0, 2),
            "gamma": angle(0, 1),
            "volume": self.volume,
        }

    def site(self, site_id: str) -> SiteView:
        for item in self.sites:
            if item.site_id == site_id:
                return item
        raise KeyError(site_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref.to_dict(),
            "lattice": [list(row) for row in self.lattice],
            "pbc": list(self.pbc),
            "sites": [site.to_dict() for site in self.sites],
            "bonds": [bond.to_dict() for bond in self.bonds],
            "properties": properties_from_fingerprint(self.properties),
            "provenance": dict(self.provenance),
            "fingerprint": self.fingerprint,
            "n_sites": self.n_sites,
            "species_counts": self.species_counts,
            "lattice_parameters": self.lattice_parameters(),
        }


def properties_from_fingerprint(payload: tuple) -> dict[str, Any]:
    site, structure = payload
    return {"site": dict(site), "structure": dict(structure)}


def snapshot_from_crystal(
    crystal,
    *,
    ref: StructureRef,
    properties: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> ScientificSnapshot:
    """Project MolCrysKit site and bond records into an immutable snapshot."""

    lattice = _matrix(crystal.lattice)
    pbc = tuple(bool(value) for value in crystal.pbc)
    records = tuple(crystal.get_site_records())
    by_global = {int(record.global_index): str(record.site_id) for record in records}
    sites = tuple(_site_view(record) for record in records)
    bonds = tuple(
        BondView(
            left_site_id=by_global[int(record.left_global_index)],
            right_site_id=by_global[int(record.right_global_index)],
            left_global_index=int(record.left_global_index),
            right_global_index=int(record.right_global_index),
            distance_A=float(record.distance_A),
            role=str(record.role),
            vector_A=_vector(record.vector_A),
        )
        for record in crystal.get_bond_records()
    )
    property_payload = tuple(property_fingerprint(properties))
    provenance_payload = tuple(sorted(_plain_provenance(provenance).items()))
    body = {
        "lattice": lattice,
        "pbc": pbc,
        "sites": [_site_fingerprint(site) for site in sites],
        "bonds": [_bond_fingerprint(bond) for bond in bonds],
        "properties": property_payload,
        "frame_ref": ref.frame_ref,
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return ScientificSnapshot(
        ref=ref,
        lattice=lattice,
        pbc=pbc,
        sites=sites,
        bonds=bonds,
        properties=property_payload,
        provenance=provenance_payload,
        fingerprint=fingerprint,
    )


def content_equal(left: ScientificSnapshot, right: ScientificSnapshot) -> bool:
    """Compare scientific content, ignoring structure id and revision."""

    return left.fingerprint == right.fingerprint and left.ref.frame_ref == right.ref.frame_ref


def _site_view(record) -> SiteView:
    image = getattr(record, "image_shift", (0, 0, 0)) or (0, 0, 0)
    return SiteView(
        site_id=str(record.site_id),
        symbol=str(record.symbol),
        label=str(record.label),
        occupancy=float(record.occupancy),
        fractional=_vector(record.fractional_position),
        cartesian=_vector(record.cartesian_position_A),
        molecule_index=int(record.molecule_index),
        local_index=int(record.local_index),
        global_index=int(record.global_index),
        disorder_group=int(record.disorder_group or 0),
        disorder_assembly="" if record.disorder_assembly in (None, ".") else str(record.disorder_assembly),
        image_shift=tuple(int(value) for value in image),
    )


def _site_fingerprint(site: SiteView) -> list:
    return [
        site.site_id,
        site.symbol,
        site.label,
        round(site.occupancy, 8),
        [round(value, 8) for value in site.fractional],
        [round(value, 8) for value in site.cartesian],
        site.disorder_group,
        site.disorder_assembly,
    ]


def _bond_fingerprint(bond: BondView) -> list:
    pair = tuple(sorted((bond.left_site_id, bond.right_site_id)))
    return [pair[0], pair[1], round(bond.distance_A, 6), bond.role]


def _matrix(value) -> tuple[tuple[float, float, float], ...]:
    array = np.asarray(value, dtype=float)
    if array.shape != (3, 3) or not np.all(np.isfinite(array)):
        raise ValueError("lattice must be a finite 3x3 row-vector matrix")
    return tuple(tuple(float(item) for item in row) for row in array)


def _vector(value) -> tuple[float, float, float]:
    array = np.asarray(value, dtype=float).reshape(3)
    return tuple(float(item) for item in array)


def _plain_provenance(provenance: Mapping[str, Any] | None) -> dict[str, Any]:
    if not provenance:
        return {}
    plain: dict[str, Any] = {}
    for key, value in provenance.items():
        plain[str(key)] = _plain(value)
    return plain


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, np.ndarray):
        return _plain(value.tolist())
    return str(value)


__all__ = [
    "BondView",
    "ScientificSnapshot",
    "SiteView",
    "content_equal",
    "properties_from_fingerprint",
    "snapshot_from_crystal",
]
