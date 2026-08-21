"""App-independent polyhedron parsing for the agent render path."""

from __future__ import annotations

import json
from typing import Any, Iterable

import numpy as np


_POLYHEDRON_KEYS = {
    "id",
    "spec_id",
    "center",
    "center_species",
    "ligand",
    "ligand_species",
    "level",
    "center_kind",
    "enforce_enclosure",
    "centroid_offset_frac",
    "cutoff",
    "hard_cutoff",
    "fallback_max",
    "color",
    "opacity",
    "edge_opacity",
}


def parse_polyhedron_specs(raw_specs: Iterable[str]) -> list[dict[str, Any]]:
    """Parse repeatable CLI JSON without importing Dash normalizers."""
    specs: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_specs):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"polyhedron {index + 1}: invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"polyhedron {index + 1}: expected a JSON object")
        unknown = sorted(set(payload) - _POLYHEDRON_KEYS)
        if unknown:
            raise ValueError(
                f"polyhedron {index + 1}: unsupported key(s): {', '.join(unknown)}"
            )
        if (
            "id" in payload
            and "spec_id" in payload
            and str(payload["id"]).strip() != str(payload["spec_id"]).strip()
        ):
            raise ValueError(f"polyhedron {index + 1}: conflicting id and spec_id")
        for alias, canonical in (
            ("center", "center_species"),
            ("ligand", "ligand_species"),
        ):
            if (
                alias in payload
                and canonical in payload
                and str(payload[alias]).strip() != str(payload[canonical]).strip()
            ):
                raise ValueError(
                    f"polyhedron {index + 1}: conflicting {alias} and {canonical}"
                )
        center = str(
            payload.get("center_species") or payload.get("center") or ""
        ).strip()
        ligand = str(
            payload.get("ligand_species") or payload.get("ligand") or ""
        ).strip()
        if not center or not ligand:
            raise ValueError(f"polyhedron {index + 1}: center and ligand are required")
        level = str(payload.get("level") or "molecule").strip().lower()
        if level not in {"atom", "molecule"}:
            raise ValueError(f"polyhedron {index + 1}: level must be atom or molecule")
        center_kind = str(payload.get("center_kind") or "centroid").strip().lower()
        if center_kind not in {"centroid", "com", "heavy_centroid"}:
            raise ValueError(
                f"polyhedron {index + 1}: center_kind must be centroid, com, "
                "or heavy_centroid"
            )
        if level == "atom" and "center_kind" in payload:
            raise ValueError(
                f"polyhedron {index + 1}: center_kind is only valid at molecule level"
            )
        enclosure = payload.get("enforce_enclosure", True)
        if not isinstance(enclosure, bool):
            raise ValueError(
                f"polyhedron {index + 1}: enforce_enclosure must be a JSON boolean"
            )
        opacity = float(payload.get("opacity", 0.55))
        edge_opacity = float(payload.get("edge_opacity", 0.9))
        if (
            not np.isfinite(opacity)
            or not np.isfinite(edge_opacity)
            or not 0.0 <= opacity <= 1.0
            or not 0.0 <= edge_opacity <= 1.0
        ):
            raise ValueError(f"polyhedron {index + 1}: opacity must lie in [0, 1]")
        hard_cutoff = payload.get("hard_cutoff")
        if level == "atom" and hard_cutoff is not None:
            raise ValueError(
                f"polyhedron {index + 1}: hard_cutoff is only valid at molecule level; "
                "use cutoff for atom-level shells"
            )
        if hard_cutoff is not None and (
            not np.isfinite(float(hard_cutoff)) or float(hard_cutoff) <= 0.0
        ):
            raise ValueError(f"polyhedron {index + 1}: hard_cutoff must be positive")
        fallback_max = payload.get("fallback_max")
        local_cutoff = payload.get("cutoff")
        if local_cutoff is not None and (
            not np.isfinite(float(local_cutoff)) or float(local_cutoff) <= 0.0
        ):
            raise ValueError(f"polyhedron {index + 1}: cutoff must be positive")
        if fallback_max is not None:
            fallback_value = float(fallback_max)
            if (
                not np.isfinite(fallback_value)
                or int(fallback_value) != fallback_value
                or int(fallback_value) <= 0
            ):
                raise ValueError(
                    f"polyhedron {index + 1}: fallback_max must be a positive integer"
                )
        centroid_offset = float(payload.get("centroid_offset_frac", 0.25))
        if not np.isfinite(centroid_offset) or centroid_offset < 0.0:
            raise ValueError(
                f"polyhedron {index + 1}: centroid_offset_frac must be finite "
                "and non-negative"
            )
        spec_id = str(
            payload.get("spec_id") or payload.get("id") or f"polyhedron-{index + 1}"
        )
        specs.append(
            {
                "id": spec_id,
                "spec_id": spec_id,
                "center_species": center,
                "ligand_species": ligand,
                "level": level,
                "center_kind": center_kind,
                "enforce_enclosure": enclosure,
                "centroid_offset_frac": centroid_offset,
                "hard_cutoff": (
                    float(hard_cutoff) if hard_cutoff is not None else None
                ),
                "fallback_max": (
                    int(float(fallback_max)) if fallback_max is not None else None
                ),
                "cutoff": float(local_cutoff) if local_cutoff is not None else None,
                "color": str(payload.get("color") or "#7C5CBF"),
                "opacity": opacity,
                "edge_opacity": edge_opacity,
            }
        )
    return specs


def _matches(fragment: dict[str, Any], spec: dict[str, Any]) -> bool:
    if spec["level"] == "atom":
        return spec["center_species"] in {
            str(element) for element in fragment.get("elem_set") or ()
        }
    return (fragment.get("formula") or fragment.get("species")) == spec[
        "center_species"
    ]


def _topology_fragment(bundle, display_fragment: dict[str, Any]):
    candidates = list(getattr(bundle, "topology_fragment_table", ()) or ())
    source_index = display_fragment.get("source_molecule_index")
    if source_index is not None:
        match = next(
            (
                fragment
                for fragment in candidates
                if fragment.get("source_molecule_index") == source_index
            ),
            None,
        )
        if match is not None:
            return match
    formula = display_fragment.get("formula") or display_fragment.get("species")
    same_formula = [
        fragment
        for fragment in candidates
        if (fragment.get("formula") or fragment.get("species")) == formula
    ]
    if not same_formula:
        return candidates[0] if candidates else None
    display_center = np.asarray(display_fragment.get("center", (0, 0, 0)), dtype=float)
    return min(
        same_formula,
        key=lambda fragment: float(
            np.linalg.norm(
                np.asarray(fragment.get("center", (0, 0, 0)), dtype=float)
                - display_center
            )
        ),
    )


def build_topology_data(
    structure,
    raw_specs: Iterable[str],
    *,
    site_index: int | None = None,
    cutoff: float = 10.0,
) -> dict[str, Any] | None:
    """Build RenderPlan polyhedra from MolCrysKit topology primitives."""
    specs = parse_polyhedron_specs(raw_specs)
    if not specs:
        return None
    if not np.isfinite(cutoff) or float(cutoff) <= 0.0:
        raise ValueError("polyhedron cutoff must be finite and positive")
    selected = structure.frames[0]
    bundle = selected.bundle
    scene = getattr(bundle, "scene", {}) or {}
    fragments = list(scene.get("fragment_table") or bundle.fragment_table or ())
    if not fragments:
        raise ValueError("polyhedra require at least one MolCrysKit fragment")

    from .topology import analyze_topology

    spec_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    for spec in specs:
        candidates = [fragment for fragment in fragments if _matches(fragment, spec)]
        if site_index is not None:
            candidates = [
                fragment
                for fragment in candidates
                if int(fragment.get("index", -1)) == int(site_index)
            ]
        if not candidates:
            raise ValueError(
                f"no displayed {spec['center_species']} center matches "
                f"polyhedron {spec['id']}"
            )
        overlays: list[dict[str, Any]] = []
        for display_fragment in candidates:
            topology_fragment = _topology_fragment(bundle, display_fragment)
            if topology_fragment is None:
                warnings.append(
                    f"polyhedron {spec['id']} skipped displayed fragment "
                    f"{display_fragment.get('index')}: no MolCrysKit topology fragment"
                )
                continue
            result = analyze_topology(
                bundle,
                center_index=int(topology_fragment["index"]),
                cutoff=float(spec["cutoff"] or cutoff),
                display_center=display_fragment.get("center"),
                display_label=display_fragment.get("label"),
                display_type=display_fragment.get("type"),
                ligand_species=[spec["ligand_species"]],
                level=spec["level"],
                center_species=spec["center_species"],
                enforce_enclosure=spec["enforce_enclosure"],
                centroid_offset_frac=spec["centroid_offset_frac"],
                center_kind=spec["center_kind"],
                hard_cutoff=spec["hard_cutoff"],
                fallback_max=spec["fallback_max"],
            )
            hull = result.get("hull") or {}
            shell = result.get("shell_coords") or []
            if len(shell) < 4 or not hull.get("simplices"):
                warnings.append(
                    f"polyhedron {spec['id']} skipped displayed fragment "
                    f"{display_fragment.get('index')}: shell is not a drawable "
                    "non-coplanar hull"
                )
                continue
            overlays.append(
                {
                    "center_coords": result.get("center_coords"),
                    "center_label": result.get("center_label"),
                    "shell_coords": shell,
                    "hull": hull,
                    "color": spec["color"],
                }
            )
        if not overlays:
            raise ValueError(
                f"polyhedron {spec['id']} has no drawable non-coplanar shell"
            )
        spec_results.append({**spec, "overlays": overlays})
    return {"spec_results": spec_results, "warnings": warnings}


__all__ = ["build_topology_data", "parse_polyhedron_specs"]
