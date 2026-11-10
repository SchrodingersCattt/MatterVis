"""Pure analysis over an explicit scientific snapshot.

Results do not read camera, color, visibility, or ``topology_enabled``.
Neighbor acceptance uses MolCrysKit's ``detect_coordination_number``;
minimum-image arithmetic stays in this module because it is geometry,
not a second bond-perception engine.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
from molcrys_kit.analysis.packing_shell import detect_coordination_number

from .errors import ValidationError
from .refs import StructureRef
from .snapshot import ScientificSnapshot


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    result_id: str
    ref: StructureRef
    kind: str
    policy: tuple
    value: tuple
    units: str
    provenance: tuple
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "input": self.ref.to_dict(),
            "kind": self.kind,
            "policy": dict(self.policy),
            "value": _thaw(self.value),
            "units": self.units,
            "provenance": dict(self.provenance),
            "warnings": list(self.warnings),
        }


_QUERY_FIELDS = {
    "coordination": ("kind", "center_site_id", "cutoff", "ligand_symbols"),
    "distance": ("kind", "distance_mode", "left", "right"),
    "composition": ("kind", "hydrogen_policy"),
}


def canonical_query(query: Mapping[str, Any]) -> dict[str, Any]:
    """Drop camera, color, and visibility keys before caching or computing."""

    kind = str(query.get("kind") or "")
    fields = _QUERY_FIELDS.get(kind)
    if fields is None:
        return {"kind": kind}
    return {key: query[key] for key in fields if key in query}


def analyze(snapshot: ScientificSnapshot, query: Mapping[str, Any], *, result_id: str) -> AnalysisResult:
    query = canonical_query(query)
    kind = str(query.get("kind") or "")
    if kind == "coordination":
        return _coordination(snapshot, query, result_id=result_id)
    if kind == "distance":
        return _distance(snapshot, query, result_id=result_id)
    if kind == "composition":
        return _composition(snapshot, query, result_id=result_id)
    raise ValidationError(f"unknown analysis kind: {kind!r}")


def query_key(snapshot: ScientificSnapshot, query: Mapping[str, Any]) -> str:
    body = {
        "fingerprint": snapshot.fingerprint,
        "frame_ref": snapshot.ref.frame_ref,
        "query": canonical_query(query),
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _coordination(snapshot: ScientificSnapshot, query: Mapping[str, Any], *, result_id: str) -> AnalysisResult:
    center_id = str(query.get("center_site_id") or "")
    if not center_id:
        raise ValidationError("coordination requires center_site_id")
    try:
        cutoff = float(query["cutoff"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("coordination requires a numeric cutoff") from exc
    if cutoff <= 0:
        raise ValidationError("coordination cutoff must be positive")
    ligands = {str(item) for item in query.get("ligand_symbols") or []}
    center = snapshot.site(center_id)
    rows = _periodic_neighbors(snapshot, center, cutoff, ligands)
    rows.sort(key=lambda item: (item[0], item[1].site_id, item[2]))
    lattice = np.asarray(snapshot.lattice, dtype=float)
    detected = detect_coordination_number(
        [row[0] for row in rows],
        cutoff=cutoff,
        coords=[
            np.asarray(site.cartesian, dtype=float) + np.asarray(image, dtype=float) @ lattice
            for _distance, site, image in rows
        ],
        center=np.asarray(center.cartesian, dtype=float),
        enforce_enclosure=False,
    )
    value = {
        "coordination_number": int(detected["coordination_number"]),
        "mode": detected.get("mode"),
        "cutoff": cutoff,
        "center_site_id": center.site_id,
        "neighbors": [
            {
                "site_id": site.site_id,
                "symbol": site.symbol,
                "image": list(image),
                "distance_A": distance,
                "distance_mode": "minimum_image",
            }
            for distance, site, image in rows[: int(detected["coordination_number"])]
        ],
    }
    return _result(
        snapshot,
        result_id=result_id,
        kind="coordination",
        policy={"cutoff": cutoff, "ligand_symbols": sorted(ligands), "distance_mode": "minimum_image"},
        value=value,
        units="Angstrom",
    )


def _distance(snapshot: ScientificSnapshot, query: Mapping[str, Any], *, result_id: str) -> AnalysisResult:
    mode = str(query.get("distance_mode") or "")
    if mode not in {"picked_image", "minimum_image"}:
        raise ValidationError("distance_mode must be 'picked_image' or 'minimum_image'")
    left = _endpoint(snapshot, query.get("left"), label="left")
    right = _endpoint(snapshot, query.get("right"), label="right")
    if mode == "picked_image":
        distance = float(np.linalg.norm(np.subtract(left["cartesian"], right["cartesian"])))
    else:
        distance = minimum_image_distance(
            left["source_cartesian"],
            right["source_cartesian"],
            snapshot.lattice,
            snapshot.pbc,
        )
    value = {
        "distance_A": distance,
        "distance_mode": mode,
        "left": left["reference"],
        "right": right["reference"],
    }
    return _result(
        snapshot,
        result_id=result_id,
        kind="distance",
        policy={"distance_mode": mode},
        value=value,
        units="Angstrom",
    )


def _composition(snapshot: ScientificSnapshot, query: Mapping[str, Any], *, result_id: str) -> AnalysisResult:
    policy = str(query.get("hydrogen_policy") or "include")
    if policy != "include":
        raise ValidationError(
            "hydrogen_policy must be 'include'; hiding hydrogens is a display filter and does not change composition"
        )
    return _result(
        snapshot,
        result_id=result_id,
        kind="composition",
        policy={"hydrogen_policy": policy},
        value={"species_counts": snapshot.species_counts, "n_sites": snapshot.n_sites},
        units="count",
    )


def _endpoint(snapshot: ScientificSnapshot, payload: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or not payload.get("site_id"):
        raise ValidationError(f"distance {label} requires a site_id")
    site = snapshot.site(str(payload["site_id"]))
    image = payload.get("image") or (0, 0, 0)
    if not isinstance(image, (list, tuple)) or len(image) != 3:
        raise ValidationError(f"distance {label} image must contain three integers")
    image_t = tuple(int(value) for value in image)
    cartesian = np.asarray(site.cartesian, dtype=float) + np.asarray(image_t, dtype=float) @ np.asarray(snapshot.lattice, dtype=float)
    return {
        "cartesian": cartesian,
        "source_cartesian": np.asarray(site.cartesian, dtype=float),
        "reference": {"site_id": site.site_id, "image": list(image_t)},
    }


def minimum_image_distance(left, right, lattice, pbc) -> float:
    """Shortest periodic distance. Component-wise fractional rounding is not used."""

    delta = np.asarray(right, dtype=float) - np.asarray(left, dtype=float)
    return float(minimum_image_distances(delta, lattice, pbc)[0])


def minimum_image_distances(deltas, lattice, pbc) -> np.ndarray:
    """Return one minimum-image length for each Cartesian displacement."""

    from ase.geometry import find_mic

    vectors = np.atleast_2d(np.asarray(deltas, dtype=float))
    _mic, distances = find_mic(
        vectors,
        np.asarray(lattice, dtype=float),
        pbc=np.asarray(tuple(bool(flag) for flag in pbc), dtype=bool),
    )
    return np.asarray(distances, dtype=float).reshape(-1)


def _periodic_neighbors(snapshot: ScientificSnapshot, center, cutoff: float, ligands: set[str]):
    lattice = np.asarray(snapshot.lattice, dtype=float)
    center_cart = np.asarray(center.cartesian, dtype=float)
    ranges = _image_ranges(lattice, snapshot.pbc, cutoff)
    rows = []
    for site in snapshot.sites:
        if ligands and site.symbol not in ligands:
            continue
        base = np.asarray(site.cartesian, dtype=float) - center_cart
        for image in _images(ranges):
            if site.site_id == center.site_id and image == (0, 0, 0):
                continue
            distance = float(np.linalg.norm(base + np.asarray(image, dtype=float) @ lattice))
            if 1e-8 < distance <= cutoff + 1e-8:
                rows.append((distance, site, image))
    return rows


def _image_ranges(lattice: np.ndarray, pbc, cutoff: float) -> list[range]:
    periodic = tuple(bool(flag) for flag in pbc)
    try:
        volume = abs(float(np.linalg.det(lattice)))
    except np.linalg.LinAlgError:
        volume = 0.0
    ranges = []
    for axis, flag in enumerate(periodic):
        if not flag:
            ranges.append(range(1))
            continue
        others = [index for index in range(3) if index != axis]
        face = np.cross(lattice[others[0]], lattice[others[1]])
        norm = float(np.linalg.norm(face))
        height = volume / norm if norm > 1e-12 and volume > 0.0 else float(np.linalg.norm(lattice[axis]))
        count = int(np.ceil(float(cutoff) / max(height, 1e-8))) + 1
        if count > 6:
            raise ValidationError("coordination cutoff spans too many periodic cells")
        ranges.append(range(-count, count + 1))
    return ranges


def _images(ranges: list[range]):
    for first in ranges[0]:
        for second in ranges[1]:
            for third in ranges[2]:
                yield (int(first), int(second), int(third))


def _result(snapshot, *, result_id, kind, policy, value, units) -> AnalysisResult:
    return AnalysisResult(
        result_id=result_id,
        ref=snapshot.ref,
        kind=kind,
        policy=tuple(sorted(policy.items())),
        value=_freeze(value),
        units=units,
        provenance=(
            ("input_fingerprint", snapshot.fingerprint),
            ("method", "molcrys_kit.detect_coordination_number" if kind == "coordination" else "mattervis.effects.analysis"),
            ("frame_ref", snapshot.ref.frame_ref),
        ),
        warnings=(),
    )


def _freeze(value: Any):
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, (int, str, bool)) or value is None:
        return value
    return str(value)


def _thaw(value: Any) -> Any:
    if isinstance(value, tuple) and all(isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str) for item in value):
        return {key: _thaw(item) for key, item in value}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    return value


__all__ = ["AnalysisResult", "analyze", "canonical_query", "query_key"]
