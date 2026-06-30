"""Operation backend: disorder resolve, display transforms, structural operations.

Follows the ``mck operate`` CLI pattern from MolCrysKit: operations
that modify what the user sees. Two sub-flavours:

1. **Display transforms** — repeat, grow, slab, etc. Mutate the rendered
   scene dict without touching the source crystal.  Reversible; toggle
   'enabled' and the view snaps back to the base scene.

2. **Structural operations** — delegate to ``molcrys_kit.operations.*``
   to produce a new ``LoadedCrystal`` bundle + scene tab.  These are
   the "real" crystal mutations (cf. ``mck operate supercell``) and
   create persistent output that survives a page reload.

Also owns disorder-resolution, which was the original occupant of this
mixin.
"""
from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *
from .normalizers import *
from ..operations.disorder import resolve_disorder as _resolve_disorder_replicas
from ..transforms import MAX_ATOMS_AFTER_TRANSFORM


# Structural operation kinds that *will* delegate to molcrys_kit.operations.
# Currently these are stubs; the display-transform equivalents (repeat,
# slab) cover the interactive exploration path.  The full structural path
# (which creates new LoadedCrystal bundles from molcrys_kit output) needs
# a ``build_loaded_crystal_from_crystal`` bridge in the loader layer.
_STRUCTURAL_OP_KINDS: dict[str, str] = {
    "supercell": "molcrys_kit.operations.supercell.generate_supercell",
    "slab": "molcrys_kit.operations.surface.generate_topological_slab",
    "add_h": "molcrys_kit.operations.add_h.add_hydrogens",
    "desolvate": "molcrys_kit.operations.desolvate.desolvate",
}

# Display transforms that have a structural equivalent.
# Used to guide users: "Repeat 2×2×2 is a display transform; for a
# structural supercell use the Operation panel."
_DISPLAY_TO_STRUCTURAL_HINT: dict[str, str] = {
    "repeat": "supercell",
    "slab": "slab",
}


class _OperationsBackendMixin:
    """Disorder resolve, transform CRUD, and structural operations."""

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
    ) -> dict[str, Any]:
        scene_id, transforms = self._resolve_transforms(scene_id)
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
            scene = self.scene_for_state(state)
            atom_count = len(scene.get("draw_atoms") or [])
            repeat_atoms = (
                atom_count
                * int(transform["params"].get("a", 1))
                * int(transform["params"].get("b", 1))
                * int(transform["params"].get("c", 1))
            )
            if repeat_atoms > MAX_ATOMS_AFTER_TRANSFORM:
                raise ValueError(
                    f"repeat transform would produce {repeat_atoms} atoms, "
                    f"exceeds MAX_ATOMS_AFTER_TRANSFORM={MAX_ATOMS_AFTER_TRANSFORM}"
                )
        # Hint: structural equivalent exists
        structural_hint = _DISPLAY_TO_STRUCTURAL_HINT.get(kind)
        if structural_hint:
            hint_msg = (
                f"'{kind}' is a display transform. For a structural "
                f"'{structural_hint}' that creates a new scene tab, "
                "use the Operation → Structural section."
            )
            if hint_msg not in warnings:
                warnings.append(hint_msg)
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

    # ------------------------------------------------------------------
    # Structural operations (stub — delegates to molcrys_kit eventually)
    # ------------------------------------------------------------------

    def structural_operation_kinds(self) -> list[dict[str, Any]]:
        """Return the list of supported structural operation kinds with
        descriptions. The display-transform equivalents handle interactive
        exploration; these are the "produce a new scene tab" versions."""
        return [
            {
                "kind": "supercell",
                "label": "Supercell",
                "description": "Generate a structural supercell via molcrys_kit (new scene tab).",
                "status": "planned",
                "display_equivalent": "repeat",
            },
            {
                "kind": "slab",
                "label": "Slab",
                "description": "Cut a surface slab via molcrys_kit (new scene tab).",
                "status": "planned",
                "display_equivalent": "slab",
            },
            {
                "kind": "add_h",
                "label": "Add hydrogens",
                "description": "Add missing hydrogen atoms via molcrys_kit.",
                "status": "planned",
                "display_equivalent": None,
            },
            {
                "kind": "desolvate",
                "label": "Desolvate",
                "description": "Remove solvent molecules via molcrys_kit.",
                "status": "planned",
                "display_equivalent": None,
            },
        ]


__all__ = ["_OperationsBackendMixin"]
