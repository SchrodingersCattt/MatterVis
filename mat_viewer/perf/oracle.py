"""Deterministic scientific signatures for performance regression reports."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Iterable

import numpy as np

SCHEMA = "mattervis.perf.oracle/v1"


def _rounded(values: Any, digits: int = 8) -> list[float]:
    array = np.asarray(values if values is not None else (), dtype=float).reshape(-1)
    return [round(float(value), digits) for value in array]


def _digest(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_index(atom: dict[str, Any], fallback: int) -> int:
    return int(atom.get("_source_index", fallback))


def _wrapped_frac(atom: dict[str, Any]) -> list[float]:
    frac = np.asarray(
        atom.get("frac") if atom.get("frac") is not None else (), dtype=float
    )
    return _rounded(frac - np.floor(frac))


def _image_shift(atom: dict[str, Any]) -> list[int]:
    return [int(value) for value in atom.get("_image_shift", (0, 0, 0))]


def _source_identity(atom: dict[str, Any], fallback: int) -> tuple[Any, ...]:
    return (
        str(
            atom.get("_raw_instance_id")
            or atom.get("_asym_label")
            or atom.get("label")
            or ""
        ),
        _source_index(atom, fallback),
        int(atom.get("_symop_index", 0) or 0),
        tuple(_image_shift(atom)),
        tuple(_wrapped_frac(atom)),
    )


def _atom_rows(atoms: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for index, atom in enumerate(atoms):
        rows.append(
            {
                "index": index,
                "source_index": _source_index(atom, index),
                "label": str(atom.get("label") or ""),
                "element": str(atom.get("elem") or ""),
                "wrapped_frac": _wrapped_frac(atom),
                "occupancy": round(float(atom.get("occ", 1.0) or 0.0), 8),
                "assembly": str(atom.get("da") or ""),
                "group": str(atom.get("dg") or ""),
                "symop": int(atom.get("_symop_index", 0) or 0),
                "is_minor": bool(atom.get("_is_minor", False)),
                "minor_flag_explicit": "_is_minor" in atom,
                "asym_label": str(atom.get("_asym_label") or ""),
                "raw_instance_id": str(atom.get("_raw_instance_id") or ""),
                "image_shift": _image_shift(atom),
            }
        )
    rows = sorted(
        rows,
        key=lambda row: (
            row["raw_instance_id"] or row["asym_label"] or row["label"],
            row["source_index"],
            row["symop"],
            row["image_shift"],
            row["wrapped_frac"],
            row["occupancy"],
            row["assembly"],
            row["group"],
            row["is_minor"],
        ),
    )
    for index, row in enumerate(rows):
        row["index"] = index
    return rows


def _analysis_payload(analysis: Any) -> dict[str, Any]:
    if analysis is None:
        return {"mol_indices": [], "bond_pairs": [], "species_map": {}, "per_fu": {}}
    return {
        "mol_indices": sorted(
            (
                sorted(int(value) for value in members)
                for members in analysis.mol_indices
            ),
            key=lambda members: tuple(members),
        ),
        "bond_pairs": [
            list(map(int, pair))
            for pair in sorted(tuple(sorted(pair)) for pair in analysis.bond_pairs)
        ],
        "species_map": {
            str(key): sorted(int(value) for value in members)
            for key, members in sorted(
                analysis.species_map.items(), key=lambda item: str(item[0])
            )
        },
        "per_fu": {
            str(key): int(value) for key, value in sorted(analysis.per_fu.items())
        },
    }


def _formula_unit_payload(
    atoms: Iterable[dict[str, Any]],
    raw_atoms: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for index, atom in enumerate(atoms):
        source_index = _source_index(atom, index)
        explicit_shift = atom.get("_image_shift")
        if explicit_shift is None and 0 <= source_index < len(raw_atoms):
            source_frac = np.asarray(raw_atoms[source_index].get("frac"), dtype=float)
            display_frac = np.asarray(atom.get("frac"), dtype=float)
            explicit_shift = np.rint(display_frac - source_frac).astype(int)
        image_shift = explicit_shift if explicit_shift is not None else (0, 0, 0)
        rows.append(
            {
                "source_index": source_index,
                "image_shift": [int(value) for value in image_shift],
                "element": str(atom.get("elem") or ""),
                "label": str(atom.get("label") or ""),
            }
        )
    return sorted(
        rows,
        key=lambda row: (row["source_index"], row["image_shift"], row["label"]),
    )


def _scene_payload(scene: dict[str, Any] | None) -> dict[str, Any]:
    if not scene:
        return {"display_mode": None, "atoms": [], "bonds": [], "fragments": []}
    atoms = list(scene.get("draw_atoms") or [])
    atom_ids = [
        {
            "identity": list(_source_identity(atom, index)),
            "source_index": _source_index(atom, index),
            "label": str(atom.get("label") or ""),
            "element": str(atom.get("elem") or ""),
            "image_shift": _image_shift(atom),
            "cart": _rounded(atom.get("cart")),
            "is_minor": bool(atom.get("is_minor", atom.get("_is_minor", False))),
        }
        for index, atom in enumerate(atoms)
    ]
    bonds = []
    for bond in scene.get("bonds") or []:
        left, right = int(bond["i"]), int(bond["j"])
        endpoint_rows = [
            (left, list(_source_identity(atoms[left], left))),
            (right, list(_source_identity(atoms[right], right))),
        ]
        endpoint_rows.sort(key=lambda row: tuple(row[1]))
        start = _rounded(bond.get("start"))
        end = _rounded(bond.get("end"))
        if endpoint_rows[0][0] != left:
            start, end = end, start
        bonds.append(
            {
                "endpoints": [row[1] for row in endpoint_rows],
                "start": start,
                "end": end,
                "is_minor": bool(bond.get("is_minor", False)),
            }
        )
    bonds.sort(key=lambda row: (row["endpoints"], row["start"], row["end"]))
    fragments = [
        {
            "label": str(fragment.get("label") or ""),
            "formula": str(fragment.get("formula") or ""),
            "cluster_size": int(fragment.get("cluster_size", 0) or 0),
            "heavy_atom_count": int(fragment.get("heavy_atom_count", 0) or 0),
            "source_molecule_index": fragment.get("source_molecule_index"),
        }
        for fragment in scene.get("fragment_table") or []
    ]
    fragments.sort(
        key=lambda row: (
            row["formula"],
            row["label"],
            str(row["source_molecule_index"]),
        )
    )
    return {
        "display_mode": scene.get("display_mode"),
        "atoms": atom_ids,
        "bonds": bonds,
        "fragments": fragments,
    }


def _figure_payload(figure: Any) -> dict[str, Any]:
    if figure is None:
        return {"trace_roles": [], "selection_schemas": {}}
    payload = figure.to_plotly_json() if hasattr(figure, "to_plotly_json") else figure
    roles = []
    schemas: dict[str, list[Any]] = {}
    for trace in payload.get("data", []):
        meta = trace.get("meta") if isinstance(trace, dict) else None
        role = meta.get("mv_role") if isinstance(meta, dict) else None
        name = str(trace.get("name") or "")
        roles.append(str(role or name or trace.get("type") or ""))
        customdata = trace.get("customdata")
        first = customdata[0] if customdata is not None and len(customdata) else None
        if isinstance(first, (list, tuple)) and first:
            schemas[str(first[0])] = [type(value).__name__ for value in first]
    return {"trace_roles": roles, "selection_schemas": dict(sorted(schemas.items()))}


def build_oracle_signature(
    bundle: Any,
    *,
    scene: dict[str, Any] | None = None,
    figure: Any = None,
) -> dict[str, Any]:
    """Return compact section digests plus useful counts for one pipeline run."""
    raw_rows = _atom_rows(bundle.raw_atoms)
    sections = {
        "raw_atoms": raw_rows,
        "disorder": {
            "major": [row["index"] for row in raw_rows if row["is_minor"] is False],
            "minor": [row["index"] for row in raw_rows if row["is_minor"]],
        },
        "analysis": _analysis_payload(getattr(bundle, "molcrys_analysis", None)),
        "formula_unit": _formula_unit_payload(
            getattr(bundle, "formula_unit_atoms", ()),
            list(bundle.raw_atoms),
        ),
        "scene": _scene_payload(scene or getattr(bundle, "scene", None)),
        "figure": _figure_payload(figure),
    }
    section_digests = {name: _digest(value) for name, value in sections.items()}
    scene_payload = sections["scene"]
    analysis_payload = sections["analysis"]
    fragment_formulas = Counter(row["formula"] for row in scene_payload["fragments"])
    return {
        "schema": SCHEMA,
        "overall_digest": _digest(section_digests),
        "section_digests": section_digests,
        "counts": {
            "raw_atoms": len(raw_rows),
            "major_atoms": len(sections["disorder"]["major"]),
            "minor_atoms": len(sections["disorder"]["minor"]),
            "molecules": len(analysis_payload["mol_indices"]),
            "canonical_bonds": len(analysis_payload["bond_pairs"]),
            "formula_unit_atoms": len(sections["formula_unit"]),
            "scene_atoms": len(scene_payload["atoms"]),
            "scene_bonds": len(scene_payload["bonds"]),
            "fragment_formulas": dict(sorted(fragment_formulas.items())),
        },
    }


__all__ = ["SCHEMA", "build_oracle_signature"]
