"""Operation backend: disorder resolve and display transforms.

Display transforms (repeat, grow, slab, and the other render-only
kinds) mutate the rendered scene without touching the source crystal.
Toggle ``enabled`` and the view snaps back to the base scene. Disorder
resolution lists ordered replicas of the loaded structure.
"""
from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *
from .normalizers import *
from ..operations.disorder import resolve_disorder as _resolve_disorder_replicas
from ..transforms import MAX_ATOMS_AFTER_TRANSFORM


class _OperationsBackendMixin:
    """Disorder resolve and display-transform CRUD."""

    def validate_transform(
        self,
        transform: dict[str, Any],
        *,
        scene_id: Optional[str] = None,
    ) -> None:
        """Validate an enabled display transform before committing it.

        Transform assembly normally happens in the asynchronous render path,
        but editor input errors need to be reported by the operation callback
        while the offending row is still visible.  Keep this check small and
        deterministic; chemistry and rendering remain in the transform
        pipeline.
        """
        if not isinstance(transform, dict) or not transform.get("enabled", True):
            return
        kind = str(transform.get("kind") or "")
        params = transform.get("params") or {}
        if kind == "by_symmetry":
            raise NotImplementedError(
                "Apply symmetry transform is not available for the current strict transform API"
            )
        if kind == "slab":
            miller = params.get("miller")
            if not isinstance(miller, (list, tuple)) or len(miller) != 3:
                raise ValueError("slab Miller indices must contain exactly three integers")
            if all(int(value) == 0 for value in miller):
                raise ValueError("slab Miller indices cannot all be zero")
        if kind != "repeat":
            return
        try:
            counts = tuple(max(1, int(params.get(axis, 1) or 1)) for axis in ("a", "b", "c"))
        except (TypeError, ValueError) as exc:
            raise ValueError("repeat counts must be positive integers") from exc
        target_scene_id = scene_id or self.active_scene_id()
        state = self.get_state(target_scene_id)
        base_state = dict(state)
        base_state["transforms"] = []
        atom_count = len(self.scene_for_state(base_state).get("draw_atoms") or [])
        projected = atom_count * counts[0] * counts[1] * counts[2]
        if projected > MAX_ATOMS_AFTER_TRANSFORM:
            raise ValueError(
                f"repeat transform would produce {projected} atoms, "
                f"exceeds MAX_ATOMS_AFTER_TRANSFORM={MAX_ATOMS_AFTER_TRANSFORM}"
            )

    # ------------------------------------------------------------------
    # Disorder resolve (original)
    # ------------------------------------------------------------------

    def resolve_disorder(
        self,
        scene_id: Optional[str] = None,
        *,
        method: str = "enumerate",
        count: int = 5,
        seed: int | None = None,
    ) -> list[dict[str, Any]]:
        state = self.get_state(scene_id)
        bundle = self.get_bundle(str(state.get("structure") or ""))
        return _resolve_disorder_replicas(bundle, method=method, count=count, seed=seed)

    # ------------------------------------------------------------------
    # Transform CRUD (was in _OverlaysBackendMixin)
    # ------------------------------------------------------------------

    def list_transforms(self, scene_id: Optional[str] = None) -> list[dict[str, Any]]:
        return list(self.get_state(scene_id).get("transforms") or [])

    def _resolve_transforms(self, scene_id: Optional[str]) -> tuple[Optional[str], list[dict[str, Any]]]:
        scene_id = scene_id or self.active_scene_id()
        return scene_id, [dict(t) for t in (self.get_state(scene_id).get("transforms") or [])]

    def add_transform(
        self,
        kind: str,
        params: Optional[dict[str, Any]] = None,
        *,
        name: Optional[str] = None,
        enabled: bool = True,
        scene_id: Optional[str] = None,
        transform_id: Optional[str] = None,
        auto_promote: bool = True,
        replace_repeat: bool = False,
    ) -> dict[str, Any]:
        scene_id, transforms = self._resolve_transforms(scene_id)
        # Quick Repeat presets can request replacement of the active repeat.
        # Generic API transform creation keeps ordered composition semantics.
        if replace_repeat and str(kind) == "repeat":
            transforms = [t for t in transforms if t.get("kind") != "repeat"]
        existing_ids = {t["id"] for t in transforms}
        transform = _normalize_transform(
            {
                "id": transform_id,
                "name": name,
                "kind": kind,
                "params": params or {},
                "enabled": enabled,
            },
            existing_ids=existing_ids,
        )
        if transform is None:
            raise ValueError(f"invalid transform spec (unknown kind?): kind={kind!r}, params={params!r}")
        self.validate_transform(transform, scene_id=scene_id)
        state = self.get_state(scene_id)
        warnings: list[str] = []
        promoted_from: str | None = None
        mutates_geometry = transform["kind"] in {
            "repeat",
            "grow_radius",
            "grow_bonds",
            "complete_fragment",
            "complete_polyhedron",
            "by_symmetry",
            "slab",
        }
        if mutates_geometry and state.get("display_mode") == "formula_unit":
            message = (
                "display_mode=formula_unit trims transform output; "
                "MatterVis promoted the scene to unit_cell for this transform."
            )
            warnings.append(message)
            if auto_promote:
                promoted_from = "formula_unit"
                state["display_mode"] = "unit_cell"
            else:
                warnings[-1] = (
                    "display_mode=formula_unit will trim transform output; "
                    "set display_mode=unit_cell before rendering."
                )
        if transform["kind"] == "slab":
            fragments = (self.get_bundle(state["structure"]).fragment_table or [])
            if len(fragments) > 1:
                warnings.append(
                    "slab transform on a molecular crystal can cut covalent fragments; "
                    "validate the result before using it as a surface model."
                )
        if transform["kind"] == "repeat":
            # ``transforms`` is already the post-replace list, so a 3×3×3
            # preset that replaces 2×2×2 is counted from the home cell.
            # Counting the expanded draw list rejects a legal replacement
            # (DAP-4 2× is 2688 atoms; 2688×27 exceeds the cap).
            estimate_state = dict(state)
            estimate_state["transforms"] = list(transforms)
            scene = self.scene_for_state(estimate_state)
            home_count = 0
            for atom in scene.get("draw_atoms") or []:
                if (
                    atom.get("_is_boundary_replica")
                    or atom.get("_is_framework_context_replica")
                    or atom.get("_is_bonded_image_replica")
                ):
                    continue
                home_count += 1
            if home_count == 0:
                home_count = len(scene.get("draw_atoms") or [])
            repeat_atoms = (
                home_count
                * int(transform["params"].get("a", 1) or 1)
                * int(transform["params"].get("b", 1) or 1)
                * int(transform["params"].get("c", 1) or 1)
            )
            if repeat_atoms > MAX_ATOMS_AFTER_TRANSFORM:
                raise ValueError(
                    f"repeat transform would produce {repeat_atoms} atoms, "
                    f"exceeds MAX_ATOMS_AFTER_TRANSFORM={MAX_ATOMS_AFTER_TRANSFORM}"
                )
        transforms.append(transform)
        patch = {"transforms": transforms}
        if promoted_from:
            patch["display_mode"] = state["display_mode"]
        self.patch_state(patch, scene_id=scene_id)
        response = dict(transform)
        if warnings:
            response["warnings"] = warnings
        if promoted_from:
            response["display_mode_auto_promoted"] = f"{promoted_from} -> {state['display_mode']}"
        return response

    def update_transform(
        self,
        transform_id: str,
        patch: dict[str, Any],
        *,
        scene_id: Optional[str] = None,
    ) -> dict[str, Any]:
        scene_id, transforms = self._resolve_transforms(scene_id)
        for index, transform in enumerate(transforms):
            if transform["id"] == transform_id:
                merged = dict(transform)
                merged.update(patch or {})
                merged["id"] = transform_id
                replacement = _normalize_transform(
                    merged,
                    existing_ids={t["id"] for t in transforms if t["id"] != transform_id},
                )
                if replacement is None:
                    raise ValueError(
                        f"invalid transform patch for {transform_id!r}: {patch!r}"
                    )
                self.validate_transform(replacement, scene_id=scene_id)
                transforms[index] = replacement
                self.patch_state({"transforms": transforms}, scene_id=scene_id)
                return replacement
        raise KeyError(f"unknown transform id: {transform_id!r}")

    def remove_transform(self, transform_id: str, *, scene_id: Optional[str] = None) -> bool:
        scene_id, transforms = self._resolve_transforms(scene_id)
        before = len(transforms)
        transforms = [t for t in transforms if t["id"] != transform_id]
        if len(transforms) == before:
            return False
        self.patch_state({"transforms": transforms}, scene_id=scene_id)
        return True

    def reorder_transforms(
        self,
        ordered_ids: Iterable[str],
        *,
        scene_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        scene_id, transforms = self._resolve_transforms(scene_id)
        index_by_id = {t["id"]: t for t in transforms}
        wanted = [str(item) for item in ordered_ids]
        if set(wanted) != set(index_by_id):
            raise ValueError(
                "reorder list must contain exactly the existing transform ids; "
                f"got {wanted!r}, have {sorted(index_by_id)}"
            )
        ordered = [index_by_id[transform_id] for transform_id in wanted]
        self.patch_state({"transforms": ordered}, scene_id=scene_id)
        return ordered


__all__ = ["_OperationsBackendMixin"]
