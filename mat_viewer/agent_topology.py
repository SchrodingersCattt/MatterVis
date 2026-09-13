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
    "site",
    "sites",
    "center_images",
    "instance_overrides",
}


def _parse_site_indices(payload: dict[str, Any], index: int) -> tuple[int, ...] | None:
    if "site" in payload and "sites" in payload:
        raise ValueError(f"polyhedron {index + 1}: use site or sites, not both")
    raw = payload.get("sites", payload.get("site"))
    if raw is None:
        return None
    values = raw if isinstance(raw, list) else [raw]
    if not values:
        raise ValueError(f"polyhedron {index + 1}: sites must not be empty")
    sites: list[int] = []
    for value in values:
        if isinstance(value, bool):
            raise ValueError(
                f"polyhedron {index + 1}: site indices must be non-negative integers"
            )
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"polyhedron {index + 1}: site indices must be non-negative integers"
            ) from exc
        if not np.isfinite(numeric) or int(numeric) != numeric or int(numeric) < 0:
            raise ValueError(
                f"polyhedron {index + 1}: site indices must be non-negative integers"
            )
        sites.append(int(numeric))
    return tuple(dict.fromkeys(sites))


def _parse_instance_overrides(
    payload: dict[str, Any], index: int
) -> dict[str, dict[str, Any]]:
    raw = payload.get("instance_overrides")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"polyhedron {index + 1}: instance_overrides must be a JSON object"
        )
    parsed: dict[str, dict[str, Any]] = {}
    for label, override in raw.items():
        name = str(label).strip()
        if not name or not isinstance(override, dict):
            raise ValueError(
                f"polyhedron {index + 1}: each instance override needs a "
                "non-empty label and JSON object"
            )
        unknown = sorted(set(override) - {"color", "visible"})
        if unknown:
            raise ValueError(
                f"polyhedron {index + 1}: instance override {name!r} has "
                f"unsupported key(s): {', '.join(unknown)}"
            )
        cleaned: dict[str, Any] = {}
        if "color" in override:
            color = str(override["color"]).strip()
            if not color:
                raise ValueError(
                    f"polyhedron {index + 1}: instance override {name!r} "
                    "color must not be empty"
                )
            cleaned["color"] = color
        if "visible" in override:
            if not isinstance(override["visible"], bool):
                raise ValueError(
                    f"polyhedron {index + 1}: instance override {name!r} "
                    "visible must be a JSON boolean"
                )
            cleaned["visible"] = override["visible"]
        parsed[name] = cleaned
    return parsed


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
        opacity = float(payload.get("opacity", 0.50))
        edge_opacity = float(payload.get("edge_opacity", 0.40))
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
        sites = _parse_site_indices(payload, index)
        center_images = payload.get("center_images", False)
        if not isinstance(center_images, bool):
            raise ValueError(
                f"polyhedron {index + 1}: center_images must be a JSON boolean"
            )
        instance_overrides = _parse_instance_overrides(payload, index)
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
                "sites": sites,
                "color": (
                    str(payload["color"])
                    if payload.get("color")
                    else (None if level == "atom" else "#7C5CBF")
                ),
                "opacity": opacity,
                "edge_opacity": edge_opacity,
                "center_images": center_images,
                "instance_overrides": instance_overrides,
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


def _molecule_center_image_identity(
    display_fragment: dict[str, Any],
    source_fragment: dict[str, Any],
) -> tuple[int, int, int]:
    """Return the absolute image offset of a displayed molecular center.

    ``fragment_table.image_shift`` is relative to the unwrapped molecule
    image chosen by MolCrysKit and is not a stable half-open-cell identity.
    The display/source fractional-center difference is the invariant, just as
    it is for atom boundary replicas.
    """
    display_frac = np.asarray(display_fragment.get("frac_center"), dtype=float)
    source_frac = np.asarray(source_fragment.get("frac_center"), dtype=float)
    if display_frac.shape != (3,) or source_frac.shape != (3,):
        # Lightweight third-party bundles predating frac_center support may
        # only expose the relative scene image tag. Keep those callers
        # compatible; production fragment tables always take the invariant
        # absolute-offset path below.
        raw_shift = display_fragment.get("image_shift")
        if isinstance(raw_shift, (list, tuple)) and len(raw_shift) == 3:
            return tuple(int(value) for value in raw_shift)
        return (0, 0, 0)
    # MCK's topology centers are deliberately unwrapped so cross-boundary
    # molecules remain contiguous.  The half-open source identity is defined
    # against that center reduced to [0, 1), not against the relative tag.
    source_frac = source_frac - np.floor(source_frac)
    # Molecule centroids at a cell face can be -3e-18 or 1+3e-18 from
    # floating-point averaging. Canonicalize those numerical boundaries to
    # the half-open zero face before computing the integer image.
    source_frac[np.isclose(source_frac, 0.0, rtol=0.0, atol=1e-6)] = 0.0
    source_frac[np.isclose(source_frac, 1.0, rtol=0.0, atol=1e-6)] = 0.0
    delta = display_frac - source_frac
    image = np.rint(delta).astype(int)
    if not np.allclose(delta, image, rtol=0.0, atol=1e-6):
        raise ValueError(
            "Displayed and source molecular centers do not differ by an "
            f"integer cell offset: delta={delta.tolist()}"
        )
    return tuple(int(value) for value in image)


def _molecule_center_periodic_shifts(
    source_fragment: dict[str, Any],
    matrix: Any,
    *,
    include_images: bool,
    include_boundary_replicas: bool,
) -> list[tuple[int, int, int]]:
    """Return complete-cell image shifts from the source molecule COM.

    The source topology fragment is authoritative.  Scene fragment rows may
    be split or re-centred when a molecule crosses a cell face, so using their
    COMs can create half-cell pseudo-images.  Images are selected solely from
    the wrapped source COM, with the same 0.03 fractional-face tolerance as
    atom/molecule boundary replication.
    """
    if not include_images or not include_boundary_replicas:
        return [(0, 0, 0)]
    frac = np.asarray(source_fragment.get("frac_center"), dtype=float)
    if frac.shape != (3,):
        center = np.asarray(source_fragment.get("center"), dtype=float)
        matrix_arr = np.asarray(matrix, dtype=float)
        if center.shape != (3,) or matrix_arr.shape != (3, 3):
            return [(0, 0, 0)]
        frac = center @ np.linalg.inv(matrix_arr)
    frac = frac - np.floor(frac)
    frac[np.isclose(frac, 1.0, rtol=0.0, atol=1e-6)] = 0.0
    per_axis: list[list[int]] = [[0], [0], [0]]
    for axis, value in enumerate(frac):
        if value <= 0.03 + 1e-6:
            per_axis[axis] = [0, 1]
        elif value >= 1.0 - 0.03 - 1e-6:
            per_axis[axis] = [0, -1]
    return [
        (int(a), int(b), int(c))
        for a in per_axis[0]
        for b in per_axis[1]
        for c in per_axis[2]
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
    cutoff: float | None = None,
    display: str | None = None,
    show_hydrogen: bool = True,
    include_boundary_replicas: bool = True,
    include_cross_boundary_bond_endpoints: bool = True,
) -> dict[str, Any] | None:
    """Build RenderPlan polyhedra from MolCrysKit topology primitives."""
    specs = parse_polyhedron_specs(raw_specs)
    if not specs:
        return None
    if cutoff is not None and (not np.isfinite(cutoff) or float(cutoff) <= 0.0):
        raise ValueError("polyhedron cutoff must be finite and positive")
    selected = structure.frames[0]
    bundle = selected.bundle
    if display is None:
        scene = getattr(bundle, "scene", {}) or {}
    else:
        from .loader.core import build_bundle_scene

        scene = build_bundle_scene(
            bundle,
            display_mode=display,
            show_hydrogen=show_hydrogen,
            include_boundary_replicas=include_boundary_replicas,
            include_cross_boundary_bond_endpoints=(
                include_cross_boundary_bond_endpoints
            ),
        )
    fragments = list(scene.get("fragment_table") or bundle.fragment_table or ())

    from .topology import (
        analyze_topology,
        atom_overlay,
        display_atom_centers_for_spec,
        extract_atom_coordination_shells,
    )

    spec_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    for spec in specs:
        if spec["level"] == "atom":
            selected_sites = set(spec["sites"]) if spec["sites"] is not None else None
            if site_index is not None:
                selected_sites = (
                    {int(site_index)}
                    if selected_sites is None
                    else selected_sites & {int(site_index)}
                )
            centers = display_atom_centers_for_spec(
                bundle,
                scene,
                spec,
                source_indices=selected_sites,
                include_images=spec["center_images"],
            )
            if not centers:
                raise ValueError(
                    f"no displayed {spec['center_species']} atom matches "
                    f"polyhedron {spec['id']}"
                )
            atom_cutoff = spec["cutoff"] if spec["cutoff"] is not None else cutoff
            shells = extract_atom_coordination_shells(
                bundle,
                atom_cutoff,
                center_species=spec["center_species"],
                ligand_species=spec["ligand_species"],
                source_indices={center["source_index"] for center in centers},
                enforce_enclosure=spec["enforce_enclosure"],
                centroid_offset_frac=spec["centroid_offset_frac"],
                fallback_max=spec["fallback_max"],
            )
            overlays: list[dict[str, Any]] = []
            for center in centers:
                shell = shells.get(center["source_index"])
                if shell is None:
                    continue
                overlay = atom_overlay(shell, center)
                hull = overlay.get("hull") or {}
                if len(overlay.get("shell_coords") or []) < 4 or not hull.get(
                    "simplices"
                ):
                    warnings.append(
                        f"polyhedron {spec['id']} skipped source atom "
                        f"{center['source_index']}: shell is not a drawable "
                        "non-coplanar hull"
                    )
                    continue
                overlay["color"] = spec["color"] or overlay["color"]
                override = next(
                    (
                        spec["instance_overrides"][key]
                        for key in (
                            str(overlay.get("center_source_index")),
                            str(overlay.get("center_label") or ""),
                        )
                        if key in spec["instance_overrides"]
                    ),
                    None,
                )
                if override:
                    overlay = dict(overlay)
                    if "color" in override:
                        overlay["color"] = override["color"]
                    if "visible" in override:
                        overlay["visible"] = bool(override["visible"])
                overlays.append(overlay)
            if not overlays:
                raise ValueError(
                    f"polyhedron {spec['id']} has no drawable non-coplanar shell"
                )
            spec_results.append({**spec, "overlays": overlays})
            continue

        candidates: list[
            tuple[dict[str, Any], dict[str, Any], tuple[int, int, int]]
        ] = []
        selected_sites = set(spec["sites"]) if spec["sites"] is not None else None
        if site_index is not None:
            selected_sites = (
                {int(site_index)}
                if selected_sites is None
                else selected_sites & {int(site_index)}
            )
        matrix = getattr(bundle, "M", None)
        matrix_arr = np.asarray(matrix, dtype=float)
        if matrix_arr.shape != (3, 3):
            # Lightweight test/third-party bundles may omit M; their fake
            # fractional coordinates are already Cartesian unit-cell values.
            matrix_arr = np.eye(3, dtype=float)
        source_fragments = list(
            getattr(bundle, "topology_fragment_table", None)
            or bundle.fragment_table
            or ()
        )
        display_index = 0
        for topology_fragment in source_fragments:
            if not _matches(topology_fragment, spec):
                continue
            source_index = int(topology_fragment["index"])
            if selected_sites is not None and source_index not in selected_sites:
                continue
            source_frac = np.asarray(
                topology_fragment.get("frac_center"), dtype=float
            )
            if source_frac.shape != (3,):
                source_center = np.asarray(
                    topology_fragment.get("center"), dtype=float
                )
                source_frac = source_center @ np.linalg.inv(matrix_arr)
            source_frac = source_frac - np.floor(source_frac)
            source_frac[np.isclose(source_frac, 1.0, rtol=0.0, atol=1e-6)] = 0.0
            for image_shift in _molecule_center_periodic_shifts(
                topology_fragment,
                matrix,
                include_images=spec["center_images"],
                include_boundary_replicas=include_boundary_replicas,
            ):
                display_frac = source_frac + np.asarray(image_shift, dtype=float)
                display_center = display_frac @ matrix_arr
                display_fragment = {
                    "index": display_index,
                    "center": display_center.tolist(),
                    "frac_center": display_frac.tolist(),
                    "label": topology_fragment.get("label")
                    or f"{spec['center_species']}{source_index}",
                    "type": topology_fragment.get("type"),
                }
                candidates.append((display_fragment, topology_fragment, image_shift))
                display_index += 1
        if not candidates:
            raise ValueError(
                f"no displayed {spec['center_species']} center matches "
                f"polyhedron {spec['id']}"
            )
        overlays: list[dict[str, Any]] = []
        for display_fragment, topology_fragment, image_shift in candidates:
            result = analyze_topology(
                bundle,
                center_index=int(topology_fragment["index"]),
                cutoff=float(spec["cutoff"] or cutoff or 10.0),
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
            overlay = {
                "center_coords": result.get("center_coords"),
                "center_label": result.get("center_label"),
                "center_source_index": int(topology_fragment["index"]),
                "center_display_index": int(display_fragment.get("index", -1)),
                "center_image": list(image_shift),
                "center_image_shift": list(image_shift),
                "shell_coords": shell,
                "distances": result.get("distances") or [],
                "source_center_coords": result.get("source_center_coords"),
                "hull": hull,
                "color": spec["color"],
            }
            override = next(
                (
                    spec["instance_overrides"][key]
                    for key in (
                        str(overlay["center_source_index"]),
                        str(overlay.get("center_label") or ""),
                    )
                    if key in spec["instance_overrides"]
                ),
                None,
            )
            if override:
                if "color" in override:
                    overlay["color"] = override["color"]
                if "visible" in override:
                    overlay["visible"] = bool(override["visible"])
            overlays.append(overlay)
        if not overlays:
            raise ValueError(
                f"polyhedron {spec['id']} has no drawable non-coplanar shell"
            )
        spec_results.append({**spec, "overlays": overlays})
    return {"spec_results": spec_results, "warnings": warnings}


def polyhedron_summary(topology_data) -> list[dict]:
    """Summarize effective polyhedron count and paint for render receipts."""
    summaries: list[dict] = []
    for result in (topology_data or {}).get("spec_results") or []:
        overlays = [
            overlay
            for overlay in result.get("overlays") or []
            if overlay.get("visible", True)
        ]
        colors = sorted(
            {
                str(overlay.get("color") or result.get("color"))
                for overlay in overlays
                if overlay.get("color") or result.get("color")
            }
        )
        coordination_numbers = sorted(
            {len(overlay.get("distances") or []) for overlay in overlays}
        )
        source_centers = {
            int(overlay["center_source_index"])
            for overlay in overlays
            if overlay.get("center_source_index") is not None
        }
        center_image_shifts = sorted(
            {
                tuple(
                    int(value)
                    for value in overlay.get(
                        "center_image_shift", overlay.get("center_image", (0, 0, 0))
                    )
                )
                for overlay in overlays
            }
        )
        center_image_pairs = sorted(
            (
                int(overlay["center_source_index"]),
                int(overlay["center_display_index"]),
                tuple(
                    int(value)
                    for value in overlay.get(
                        "center_image_shift", overlay.get("center_image", (0, 0, 0))
                    )
                ),
            )
            for overlay in overlays
            if overlay.get("center_source_index") is not None
            and overlay.get("center_display_index") is not None
        )
        summary = {
            "id": result.get("spec_id") or result.get("id"),
            "level": result.get("level"),
            "center": result.get("center_species"),
            "ligand": result.get("ligand_species"),
            "displayed_centers": len(overlays),
            "unique_source_centers": len(source_centers) or len(overlays),
            "center_images": bool(result.get("center_images", False)),
            "center_image_shifts": [list(shift) for shift in center_image_shifts],
            "effective_colors": colors,
            "coordination_numbers": coordination_numbers,
        }
        if result.get("level") == "molecule":
            summary["center_image_pairs"] = [
                {
                    "source_center_index": source_index,
                    "display_center_index": display_index,
                    "image_shift": list(shift),
                }
                for source_index, display_index, shift in center_image_pairs
            ]
        summaries.append(summary)
    return summaries


def topology_fit_points(topology_data) -> np.ndarray:
    """Return finite polyhedron vertices that must remain inside the viewport."""
    points = []
    for result in (topology_data or {}).get("spec_results") or ():
        for overlay in result.get("overlays") or ():
            shell = np.asarray(overlay.get("shell_coords") or (), dtype=float)
            if (
                shell.ndim == 2
                and shell.shape[1:] == (3,)
                and np.all(np.isfinite(shell))
            ):
                points.append(shell)
    return np.vstack(points) if points else np.zeros((0, 3), dtype=float)


__all__ = [
    "build_topology_data",
    "parse_polyhedron_specs",
    "polyhedron_summary",
    "topology_fit_points",
]
