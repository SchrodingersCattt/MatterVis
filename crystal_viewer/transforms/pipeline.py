from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .core import (
    MAX_ATOMS_AFTER_TRANSFORM,
    atoms_completing_fragment,
    atoms_completing_polyhedron,
    atoms_under_symmetry,
    atoms_within_bonds,
    atoms_within_radius,
    rebuild_scene_with_atoms,
    replicate_atoms,
    resolve_seed_indices,
    slab_atoms_from_bundle,
)

def _normalise_repeat_params(params: Dict[str, Any]) -> Tuple[int, int, int]:
    return (
        max(1, int(params.get("a", 1) or 1)),
        max(1, int(params.get("b", 1) or 1)),
        max(1, int(params.get("c", 1) or 1)),
    )


def apply_one_transform(
    scene: Dict[str, Any],
    transform: Dict[str, Any],
    *,
    bundle: Any = None,
    style: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Dispatch one transform-spec dict onto ``scene`` and return a new scene."""
    from ..scene import scene_ops

    ops = scene_ops()
    if not transform.get("enabled", True):
        return scene
    kind = str(transform.get("kind") or "")
    params = transform.get("params") or {}
    seeds = params.get("seeds")
    atoms = scene.get("draw_atoms") or []
    M = np.asarray(scene.get("M"), dtype=float)
    cell = scene.get("cell")

    if kind == "repeat":
        na, nb, nc = _normalise_repeat_params(params)
        if (na, nb, nc) == (1, 1, 1):
            return scene
        new_atoms = replicate_atoms(atoms, M, na=na, nb=nb, nc=nc)
        return rebuild_scene_with_atoms(scene, new_atoms, style=style)

    if kind == "grow_radius":
        seed_indices = resolve_seed_indices(atoms, seeds)
        radius = float(params.get("radius", 0.0) or 0.0)
        extra = atoms_within_radius(
            atoms,
            M,
            seed_indices=seed_indices,
            radius=radius,
            include_seeds=False,
        )
        merged = _merge_atoms(atoms, extra)
        return rebuild_scene_with_atoms(scene, merged, style=style)

    if kind == "grow_bonds":
        seed_indices = resolve_seed_indices(atoms, seeds)
        hops = int(params.get("hops", 1) or 1)
        extra = atoms_within_bonds(
            atoms,
            M,
            seed_indices=seed_indices,
            hops=hops,
            ops=ops,
            cell=cell,
        )
        merged = _merge_atoms(atoms, extra)
        return rebuild_scene_with_atoms(scene, merged, style=style)

    if kind == "complete_fragment":
        seed_indices = resolve_seed_indices(atoms, seeds)
        max_hops = int(params.get("max_hops", 32) or 32)
        extra = atoms_completing_fragment(
            atoms,
            M,
            seed_indices=seed_indices,
            ops=ops,
            cell=cell,
            max_hops=max_hops,
        )
        merged = _merge_atoms(atoms, extra)
        return rebuild_scene_with_atoms(scene, merged, style=style)

    if kind == "complete_polyhedron":
        seed_indices = resolve_seed_indices(atoms, seeds)
        cutoff = float(params.get("cutoff", 4.0) or 4.0)
        extra = atoms_completing_polyhedron(
            atoms,
            M,
            seed_indices=seed_indices,
            cutoff=cutoff,
        )
        merged = _merge_atoms(atoms, extra)
        return rebuild_scene_with_atoms(scene, merged, style=style)

    if kind == "by_symmetry":
        seed_indices = resolve_seed_indices(atoms, seeds)
        sym_ops_raw = params.get("ops") or []
        sym_ops: List[Tuple[Sequence[Sequence[float]], Sequence[float]]] = []
        for entry in sym_ops_raw:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                continue
            sym_ops.append((entry[0], entry[1]))
        extra = atoms_under_symmetry(
            atoms,
            M,
            seed_indices=seed_indices,
            sym_ops=sym_ops,
        )
        merged = _merge_atoms(atoms, extra)
        return rebuild_scene_with_atoms(scene, merged, style=style)

    if kind == "slab":
        if bundle is None:
            return scene
        slab_atoms, slab_M = slab_atoms_from_bundle(
            bundle,
            miller=tuple(params.get("miller", (1, 0, 0))),
            layers=params.get("layers"),
            min_thickness=params.get("min_thickness"),
            vacuum=float(params.get("vacuum", 10.0) or 10.0),
        )
        # The slab replaces the home cell entirely; pass an Identity-ish
        # SimpleNamespace cell so unit-cell-box callers still get
        # finite numbers without crashing on missing attributes.
        slab_cell = SimpleNamespace(
            a=float(np.linalg.norm(slab_M[0])),
            b=float(np.linalg.norm(slab_M[1])),
            c=float(np.linalg.norm(slab_M[2])),
            alpha=90.0,
            beta=90.0,
            gamma=90.0,
            volume=abs(float(np.linalg.det(slab_M))),
        )
        return rebuild_scene_with_atoms(
            scene,
            slab_atoms,
            style=style,
            cell_override=slab_cell,
            M_override=slab_M,
        )

    return scene


def _merge_atoms(
    base_atoms: Sequence[Dict[str, Any]],
    extra_atoms: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge ``extra_atoms`` onto ``base_atoms`` keeping unique
    ``(_origin_label, _image_shift)`` keys. Base atoms always win on
    duplicates so labels stay stable across re-runs.
    """
    seen: Dict[Tuple[str, Tuple[int, int, int]], int] = {}
    merged = [dict(atom) for atom in base_atoms]
    for idx, atom in enumerate(merged):
        key = (
            atom.get("_origin_label") or atom.get("label"),
            atom.get("_image_shift", (0, 0, 0)),
        )
        seen[key] = idx
    for atom in extra_atoms:
        key = (
            atom.get("_origin_label") or atom.get("label"),
            atom.get("_image_shift", (0, 0, 0)),
        )
        if key in seen:
            continue
        merged.append(atom)
        seen[key] = len(merged) - 1
    return merged


def apply_transforms(
    base_scene: Dict[str, Any],
    transforms: Sequence[Dict[str, Any]],
    *,
    bundle: Any = None,
    style: Optional[Dict[str, Any]] = None,
    max_atoms: int = MAX_ATOMS_AFTER_TRANSFORM,
) -> Dict[str, Any]:
    """Compose ``transforms`` in list order on ``base_scene``.

    Returns the original scene unchanged when ``transforms`` is empty
    so the cache hit-rate of the no-transform path stays at 100%
    (most users never enable a transform).
    """
    if not transforms:
        return base_scene
    scene = base_scene
    lineage: List[Dict[str, Any]] = []
    for transform in transforms:
        if not transform.get("enabled", True):
            continue
        scene = apply_one_transform(scene, transform, bundle=bundle, style=style)
        lineage.append(
            {
                "id": transform.get("id"),
                "kind": transform.get("kind"),
                "params": copy.deepcopy(transform.get("params") or {}),
            }
        )
        if len(scene.get("draw_atoms") or []) > max_atoms:
            raise ValueError(
                f"transform pipeline produced {len(scene['draw_atoms'])} atoms, "
                f"exceeds MAX_ATOMS_AFTER_TRANSFORM={max_atoms}. "
                "Reduce supercell size, lower grow radius / hops, or remove an op."
            )
    if scene is not base_scene:
        scene = dict(scene)
        scene["_transform_lineage"] = lineage
    return scene


def transforms_cache_key(transforms: Sequence[Dict[str, Any]]) -> Tuple:
    """Deterministic cache key for the transform list. Used by the
    backend's per-bundle scene cache so toggling a transform's
    ``enabled`` flag is a hash-lookup instead of a rebuild.

    We hash on ``kind`` + ``enabled`` + sorted ``params`` keys. The
    ``id`` is intentionally NOT in the key -- a rename without
    geometry change should hit the cache.
    """
    if not transforms:
        return ("none",)
    parts: List[Tuple] = []
    for transform in transforms:
        params = transform.get("params") or {}
        flat_params = tuple(
            (str(k), _hashable(params[k])) for k in sorted(params.keys())
        )
        parts.append(
            (
                str(transform.get("kind") or ""),
                bool(transform.get("enabled", True)),
                flat_params,
            )
        )
    return tuple(parts)


def _hashable(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((str(k), _hashable(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_hashable(v) for v in value)
    if isinstance(value, np.ndarray):
        return tuple(value.flatten().tolist())
    return value
