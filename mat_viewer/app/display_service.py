"""Display ownership for transforms and layer bindings.

These methods write view state only. They do not commit scientific
revisions. ``ViewerBackend`` forwards the public methods here so the
old operations and analysis mixins do not keep a second implementation.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..topology.analysis import _MAX_REGISTERED_CN, DEFAULT_CENTROID_OFFSET_FRAC
from ..transforms import MAX_ATOMS_AFTER_TRANSFORM
from .normalizers import (
    _normalize_polyhedron_spec,
    _normalize_transform,
)
from .style_helpers import _POLYHEDRON_AUTO_COLORS


class DisplayService:
    """View transforms, polyhedron layers, and other display bindings."""

    def __init__(self, backend):
        self.backend = backend

    def validate_transform(
        self,
        transform: dict[str, Any],
        *,
        scene_id: str | None = None,
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
        target_scene_id = scene_id or self.backend.active_scene_id()
        state = self.backend.get_state(target_scene_id)
        base_state = dict(state)
        base_state["transforms"] = []
        atom_count = len(self.backend.scene_for_state(base_state).get("draw_atoms") or [])
        projected = atom_count * counts[0] * counts[1] * counts[2]
        if projected > MAX_ATOMS_AFTER_TRANSFORM:
            raise ValueError(
                f"repeat transform would produce {projected} atoms, "
                f"exceeds MAX_ATOMS_AFTER_TRANSFORM={MAX_ATOMS_AFTER_TRANSFORM}"
            )

    # ------------------------------------------------------------------
    # Disorder resolve (original)
    # ------------------------------------------------------------------

    def list_transforms(self, scene_id: str | None = None) -> list[dict[str, Any]]:
        return list(self.backend.get_state(scene_id).get("transforms") or [])

    def _resolve_transforms(self, scene_id: str | None) -> tuple[str | None, list[dict[str, Any]]]:
        scene_id = scene_id or self.backend.active_scene_id()
        return scene_id, [dict(t) for t in (self.backend.get_state(scene_id).get("transforms") or [])]

    def add_transform(
        self,
        kind: str,
        params: dict[str, Any] | None = None,
        *,
        name: str | None = None,
        enabled: bool = True,
        scene_id: str | None = None,
        transform_id: str | None = None,
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
        state = self.backend.get_state(scene_id)
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
            fragments = (self.backend.get_bundle(state["structure"]).fragment_table or [])
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
            scene = self.backend.scene_for_state(estimate_state)
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
        self.backend.patch_state(patch, scene_id=scene_id)
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
        scene_id: str | None = None,
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
                self.backend.patch_state({"transforms": transforms}, scene_id=scene_id)
                return replacement
        raise KeyError(f"unknown transform id: {transform_id!r}")

    def remove_transform(self, transform_id: str, *, scene_id: str | None = None) -> bool:
        scene_id, transforms = self._resolve_transforms(scene_id)
        before = len(transforms)
        transforms = [t for t in transforms if t["id"] != transform_id]
        if len(transforms) == before:
            return False
        self.backend.patch_state({"transforms": transforms}, scene_id=scene_id)
        return True

    def reorder_transforms(
        self,
        ordered_ids: Iterable[str],
        *,
        scene_id: str | None = None,
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
        self.backend.patch_state({"transforms": ordered}, scene_id=scene_id)
        return ordered


    def list_polyhedron_specs(self, scene_id: str | None = None) -> list[dict[str, Any]]:
        state = self.backend.get_state(scene_id)
        return list(state.get("polyhedron_specs") or [])

    def _resolve_specs(self, scene_id: str | None) -> tuple[str | None, list[dict[str, Any]]]:
        scene_id = scene_id or self.backend.active_scene_id()
        state = self.backend.get_state(scene_id)
        specs = list(state.get("polyhedron_specs") or [])
        return scene_id, [dict(spec) for spec in specs]

    def add_polyhedron_spec(
        self,
        center_species: str,
        ligand_species: str | None = None,
        *,
        name: str | None = None,
        color: str | None = None,
        enabled: bool = True,
        enforce_enclosure: bool = True,
        centroid_offset_frac: float | None = DEFAULT_CENTROID_OFFSET_FRAC,
        level: str | None = None,
        center_kind: str | None = None,
        hard_cutoff: float | None = None,
        fallback_max: int | None = None,
        scene_id: str | None = None,
        spec_id: str | None = None,
    ) -> dict[str, Any]:
        scene_id, specs = self._resolve_specs(scene_id)
        fallback_color = _POLYHEDRON_AUTO_COLORS[len(specs) % len(_POLYHEDRON_AUTO_COLORS)]
        existing_ids = {spec["id"] for spec in specs}
        spec = _normalize_polyhedron_spec(
            {
                "id": spec_id,
                "name": name,
                "center_species": center_species,
                "ligand_species": ligand_species,
                "color": color,
                "enabled": enabled,
                "enforce_enclosure": enforce_enclosure,
                "centroid_offset_frac": centroid_offset_frac,
                "level": level,
                "center_kind": center_kind,
                "hard_cutoff": hard_cutoff,
                "fallback_max": fallback_max,
            },
            fallback_color=fallback_color,
            existing_ids=existing_ids,
        )
        if spec is None:
            raise ValueError(
                f"invalid polyhedron spec (missing center_species?): {center_species!r}"
            )
        specs.append(spec)
        self.backend.patch_state({"polyhedron_specs": specs}, scene_id=scene_id)
        return spec

    def update_polyhedron_spec(
        self,
        spec_id: str,
        patch: dict[str, Any],
        *,
        scene_id: str | None = None,
    ) -> dict[str, Any]:
        scene_id, specs = self._resolve_specs(scene_id)
        for index, spec in enumerate(specs):
            if spec["id"] == spec_id:
                merged = dict(spec)
                merged.update(patch or {})
                merged["id"] = spec_id
                replacement = _normalize_polyhedron_spec(
                    merged,
                    fallback_color=spec["color"],
                    existing_ids={s["id"] for s in specs if s["id"] != spec_id},
                )
                if replacement is None:
                    raise ValueError(
                        f"invalid polyhedron spec patch for {spec_id!r}: {patch!r}"
                    )
                specs[index] = replacement
                self.backend.patch_state({"polyhedron_specs": specs}, scene_id=scene_id)
                return replacement
        raise KeyError(f"unknown polyhedron spec id: {spec_id!r}")

    def remove_polyhedron_spec(
        self,
        spec_id: str,
        *,
        scene_id: str | None = None,
    ) -> bool:
        scene_id, specs = self._resolve_specs(scene_id)
        before = len(specs)
        specs = [spec for spec in specs if spec["id"] != spec_id]
        if len(specs) == before:
            return False
        self.backend.patch_state({"polyhedron_specs": specs}, scene_id=scene_id)
        return True

    def reorder_polyhedron_specs(
        self,
        ordered_ids: Iterable[str],
        *,
        scene_id: str | None = None,
    ) -> list[dict[str, Any]]:
        scene_id, specs = self._resolve_specs(scene_id)
        index_by_id = {spec["id"]: spec for spec in specs}
        wanted = [str(item) for item in ordered_ids]
        if set(wanted) != set(index_by_id):
            raise ValueError(
                "reorder list must contain exactly the existing spec ids; "
                f"got {wanted!r}, have {sorted(index_by_id)}"
            )
        ordered = [index_by_id[spec_id] for spec_id in wanted]
        self.backend.patch_state({"polyhedron_specs": ordered}, scene_id=scene_id)
        return ordered

    # ---- polyhedron instance overrides --------------------------------
    #
    # These were in _OverlaysBackendMixin but depend on _resolve_specs
    # which is owned here.  A per-fragment override of the spec-level
    # colour / visibility.  Applies on top of the existing spec colour
    # without mutating it, so the right-click path stays scoped to the
    # picked instance only.

    def set_polyhedron_instance_override(
        self,
        spec_id: str,
        fragment_label: str,
        override: dict[str, Any],
        *,
        scene_id: str | None = None,
    ) -> dict[str, Any]:
        from .normalizers import _coerce_hex_color

        scene_id, specs = self._resolve_specs(scene_id)
        for index, spec in enumerate(specs):
            if spec["id"] != spec_id:
                continue
            current = dict(spec.get("instance_overrides") or {})
            cleaned: dict[str, Any] = {}
            color = override.get("color") if isinstance(override, dict) else None
            if color:
                hex_color = _coerce_hex_color(color, "")
                if hex_color:
                    cleaned["color"] = hex_color
            if isinstance(override, dict) and "visible" in override:
                cleaned["visible"] = bool(override["visible"])
            if cleaned:
                current[str(fragment_label)] = cleaned
            else:
                current.pop(str(fragment_label), None)
            spec_patch = dict(spec)
            spec_patch["instance_overrides"] = current
            specs[index] = spec_patch
            self.backend.patch_state({"polyhedron_specs": specs}, scene_id=scene_id)
            return spec_patch
        raise KeyError(f"unknown polyhedron spec id: {spec_id!r}")

    def clear_polyhedron_instance_override(
        self,
        spec_id: str,
        fragment_label: str,
        *,
        scene_id: str | None = None,
    ) -> dict[str, Any]:
        return self.set_polyhedron_instance_override(
            spec_id,
            fragment_label,
            {},
            scene_id=scene_id,
        )

    # ------------------------------------------------------------------
    # Analysis dispatch
    # ------------------------------------------------------------------

    def _effective_polyhedron_specs(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Resolve the per-render list of explicit named polyhedron specs.

        Returns *all* specs (enabled or disabled). The renderer decides
        per-spec visibility via ``meta.spec_id`` tags.
        """
        explicit = list(state.get("polyhedron_specs") or [])
        if explicit:
            resolved = [dict(spec) for spec in explicit]
            # Upgrade persisted inorganic defaults created before bounded
            # coordination shells were added; reopening such a scene must
            # not revive an unbounded periodic shell classification.
            for spec in resolved:
                if (
                    str(spec.get("id") or "").startswith("ionic-coordination")
                    and str(spec.get("level") or "molecule") == "atom"
                    and spec.get("fallback_max") is None
                ):
                    spec["fallback_max"] = _MAX_REGISTERED_CN
            return resolved
        return []

