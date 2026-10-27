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
    rows = []
    for site in snapshot.sites:
        if site.site_id == center.site_id:
            continue
        if ligands and site.symbol not in ligands:
            continue
        distance = _minimum_image_distance(center.cartesian, site.cartesian, snapshot.lattice, snapshot.pbc)
        if distance <= cutoff + 1e-8:
            rows.append((distance, site))
    rows.sort(key=lambda item: (item[0], item[1].site_id))
    detected = detect_coordination_number(
        [row[0] for row in rows],
        cutoff=cutoff,
        coords=[row[1].cartesian for row in rows],
        center=center.cartesian,
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
                "distance_A": distance,
                "distance_mode": "minimum_image",
            }
            for distance, site in rows[: int(detected["coordination_number"])]
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
        distance = _minimum_image_distance(
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
    return _result(
        snapshot,
        result_id=result_id,
        kind="composition",
        policy={"hydrogen_policy": str(query.get("hydrogen_policy") or "include")},
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


def _minimum_image_distance(left, right, lattice, pbc) -> float:
    matrix = np.asarray(lattice, dtype=float)
    delta = np.asarray(right, dtype=float) - np.asarray(left, dtype=float)
    fractional = delta @ np.linalg.inv(matrix)
    periodic = np.asarray(pbc, dtype=bool)
    fractional[periodic] -= np.round(fractional[periodic])
    return float(np.linalg.norm(fractional @ matrix))


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
