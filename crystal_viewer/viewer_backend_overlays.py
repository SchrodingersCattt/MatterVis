from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .app_shared import *
from .app_normalizers import *
from .app_rightclick import _normalize_polyhedron_specs


class _OverlaysBackendMixin:
    def list_polyhedron_specs(self, scene_id: Optional[str] = None) -> list[dict[str, Any]]:
        state = self.get_state(scene_id)
        return list(state.get("polyhedron_specs") or [])

    def _resolve_specs(self, scene_id: Optional[str]) -> tuple[Optional[str], list[dict[str, Any]]]:
        scene_id = scene_id or self.active_scene_id()
        state = self.get_state(scene_id)
        specs = list(state.get("polyhedron_specs") or [])
        return scene_id, [dict(spec) for spec in specs]

    def add_polyhedron_spec(
        self,
        center_species: str,
        ligand_species: Optional[str] = None,
        *,
        name: Optional[str] = None,
        color: Optional[str] = None,
        enabled: bool = True,
        enforce_enclosure: bool = True,
        centroid_offset_frac: Optional[float] = DEFAULT_CENTROID_OFFSET_FRAC,
        scene_id: Optional[str] = None,
        spec_id: Optional[str] = None,
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
            },
            fallback_color=fallback_color,
            existing_ids=existing_ids,
        )
        if spec is None:
            raise ValueError(
                f"invalid polyhedron spec (missing center_species?): {center_species!r}"
            )
        specs.append(spec)
        self.patch_state({"polyhedron_specs": specs}, scene_id=scene_id)
        return spec

    def update_polyhedron_spec(
        self,
        spec_id: str,
        patch: dict[str, Any],
        *,
        scene_id: Optional[str] = None,
    ) -> dict[str, Any]:
        scene_id, specs = self._resolve_specs(scene_id)
        for index, spec in enumerate(specs):
            if spec["id"] == spec_id:
                merged = dict(spec)
                merged.update(patch or {})
                merged["id"] = spec_id
                # Re-normalise via the single-row helper so the same
                # color/species coercion as POST applies.
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
                self.patch_state({"polyhedron_specs": specs}, scene_id=scene_id)
                return replacement
        raise KeyError(f"unknown polyhedron spec id: {spec_id!r}")

    def remove_polyhedron_spec(
        self,
        spec_id: str,
        *,
        scene_id: Optional[str] = None,
    ) -> bool:
        scene_id, specs = self._resolve_specs(scene_id)
        before = len(specs)
        specs = [spec for spec in specs if spec["id"] != spec_id]
        if len(specs) == before:
            return False
        self.patch_state({"polyhedron_specs": specs}, scene_id=scene_id)
        return True

    def reorder_polyhedron_specs(
        self,
        ordered_ids: Iterable[str],
        *,
        scene_id: Optional[str] = None,
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
        self.patch_state({"polyhedron_specs": ordered}, scene_id=scene_id)
        return ordered

    # ---- atom_groups CRUD ---------------------------------------------
    #
    # Same shape as polyhedron CRUD: scoped to one scene, persisted via
    # patch_state, returns the canonical post-normalisation list. See
    # agents/atom_groups_api.md.

    def list_atom_groups(self, scene_id: Optional[str] = None) -> list[dict[str, Any]]:
        return list(self.get_state(scene_id).get("atom_groups") or [])

    def _resolve_atom_groups(self, scene_id: Optional[str]) -> tuple[Optional[str], list[dict[str, Any]]]:
        scene_id = scene_id or self.active_scene_id()
        return scene_id, [dict(group) for group in (self.get_state(scene_id).get("atom_groups") or [])]

    def add_atom_group(
        self,
        selector: dict[str, Any],
        *,
        name: Optional[str] = None,
        color: Optional[str] = None,
        color_light: Optional[str] = None,
        visible: bool = True,
        opacity: Optional[float] = None,
        material: Optional[str] = None,
        style: Optional[str] = None,
        scene_id: Optional[str] = None,
        group_id: Optional[str] = None,
    ) -> dict[str, Any]:
        scene_id, groups = self._resolve_atom_groups(scene_id)
        existing_ids = {grp["id"] for grp in groups}
        group = _normalize_atom_group(
            {
                "id": group_id,
                "name": name,
                "selector": selector,
                "color": color,
                "color_light": color_light,
                "visible": visible,
                "opacity": opacity,
                "material": material,
                "style": style,
            },
            existing_ids=existing_ids,
        )
        if group is None:
            raise ValueError(
                f"invalid atom_group payload (missing/empty selector?): {selector!r}"
            )
        groups.append(group)
        self.patch_state({"atom_groups": groups}, scene_id=scene_id)
        return group

    def update_atom_group(
        self,
        group_id: str,
        patch: dict[str, Any],
        *,
        scene_id: Optional[str] = None,
    ) -> dict[str, Any]:
        scene_id, groups = self._resolve_atom_groups(scene_id)
        for index, group in enumerate(groups):
            if group["id"] == group_id:
                merged = dict(group)
                merged.update(patch or {})
                merged["id"] = group_id
                replacement = _normalize_atom_group(
                    merged,
                    existing_ids={g["id"] for g in groups if g["id"] != group_id},
                )
                if replacement is None:
                    raise ValueError(
                        f"invalid atom_group patch for {group_id!r}: {patch!r}"
                    )
                groups[index] = replacement
                self.patch_state({"atom_groups": groups}, scene_id=scene_id)
                return replacement
        raise KeyError(f"unknown atom_group id: {group_id!r}")

    def remove_atom_group(self, group_id: str, *, scene_id: Optional[str] = None) -> bool:
        scene_id, groups = self._resolve_atom_groups(scene_id)
        before = len(groups)
        groups = [grp for grp in groups if grp["id"] != group_id]
        if len(groups) == before:
            return False
        self.patch_state({"atom_groups": groups}, scene_id=scene_id)
        return True

    def reorder_atom_groups(
        self,
        ordered_ids: Iterable[str],
        *,
        scene_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        scene_id, groups = self._resolve_atom_groups(scene_id)
        index_by_id = {grp["id"]: grp for grp in groups}
        wanted = [str(item) for item in ordered_ids]
        if set(wanted) != set(index_by_id):
            raise ValueError(
                "reorder list must contain exactly the existing atom_group ids; "
                f"got {wanted!r}, have {sorted(index_by_id)}"
            )
        ordered = [index_by_id[group_id] for group_id in wanted]
        self.patch_state({"atom_groups": ordered}, scene_id=scene_id)
        return ordered

    # ---- bond_groups CRUD ---------------------------------------------
    #
    # Mirror of atom_groups CRUD; see ``agents/bond_groups_api.md``.

    def list_bond_groups(self, scene_id: Optional[str] = None) -> list[dict[str, Any]]:
        return list(self.get_state(scene_id).get("bond_groups") or [])

    def _resolve_bond_groups(self, scene_id: Optional[str]) -> tuple[Optional[str], list[dict[str, Any]]]:
        scene_id = scene_id or self.active_scene_id()
        return scene_id, [dict(group) for group in (self.get_state(scene_id).get("bond_groups") or [])]

    def add_bond_group(
        self,
        selector: dict[str, Any],
        *,
        name: Optional[str] = None,
        color: Optional[str] = None,
        visible: bool = True,
        opacity: Optional[float] = None,
        radius_scale: Optional[float] = None,
        scene_id: Optional[str] = None,
        group_id: Optional[str] = None,
    ) -> dict[str, Any]:
        scene_id, groups = self._resolve_bond_groups(scene_id)
        existing_ids = {grp["id"] for grp in groups}
        group = _normalize_bond_group(
            {
                "id": group_id,
                "name": name,
                "selector": selector,
                "color": color,
                "visible": visible,
                "opacity": opacity,
                "radius_scale": radius_scale,
            },
            existing_ids=existing_ids,
        )
        if group is None:
            raise ValueError(
                f"invalid bond_group payload (missing/empty selector?): {selector!r}"
            )
        groups.append(group)
        self.patch_state({"bond_groups": groups}, scene_id=scene_id)
        return group

    def update_bond_group(
        self,
        group_id: str,
        patch: dict[str, Any],
        *,
        scene_id: Optional[str] = None,
    ) -> dict[str, Any]:
        scene_id, groups = self._resolve_bond_groups(scene_id)
        for index, group in enumerate(groups):
            if group["id"] == group_id:
                merged = dict(group)
                merged.update(patch or {})
                merged["id"] = group_id
                replacement = _normalize_bond_group(
                    merged,
                    existing_ids={g["id"] for g in groups if g["id"] != group_id},
                )
                if replacement is None:
                    raise ValueError(
                        f"invalid bond_group patch for {group_id!r}: {patch!r}"
                    )
                groups[index] = replacement
                self.patch_state({"bond_groups": groups}, scene_id=scene_id)
                return replacement
        raise KeyError(f"unknown bond_group id: {group_id!r}")

    def remove_bond_group(self, group_id: str, *, scene_id: Optional[str] = None) -> bool:
        scene_id, groups = self._resolve_bond_groups(scene_id)
        before = len(groups)
        groups = [grp for grp in groups if grp["id"] != group_id]
        if len(groups) == before:
            return False
        self.patch_state({"bond_groups": groups}, scene_id=scene_id)
        return True

    def reorder_bond_groups(
        self,
        ordered_ids: Iterable[str],
        *,
        scene_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        scene_id, groups = self._resolve_bond_groups(scene_id)
        index_by_id = {grp["id"]: grp for grp in groups}
        wanted = [str(item) for item in ordered_ids]
        if set(wanted) != set(index_by_id):
            raise ValueError(
                "reorder list must contain exactly the existing bond_group ids; "
                f"got {wanted!r}, have {sorted(index_by_id)}"
            )
        ordered = [index_by_id[group_id] for group_id in wanted]
        self.patch_state({"bond_groups": ordered}, scene_id=scene_id)
        return ordered

    # ---- transforms CRUD ----------------------------------------------
    #
    # Mirrors atom_groups CRUD. The whole pipeline is a list; ordering
    # matters (each transform takes the result of the previous one as
    # its input scene). See ``agents/transforms_api.md``.

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
            from .transforms import MAX_ATOMS_AFTER_TRANSFORM

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

    # ---- polyhedron instance overrides --------------------------------
    #
    # A per-fragment override of the spec-level colour / visibility.
    # Applies on top of the existing spec colour without mutating it,
    # so the right-click "Set this one cyan" path stays scoped to the
    # picked instance only.

    def set_polyhedron_instance_override(
        self,
        spec_id: str,
        fragment_label: str,
        override: dict[str, Any],
        *,
        scene_id: Optional[str] = None,
    ) -> dict[str, Any]:
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
            self.patch_state({"polyhedron_specs": specs}, scene_id=scene_id)
            return spec_patch
        raise KeyError(f"unknown polyhedron spec id: {spec_id!r}")

    def clear_polyhedron_instance_override(
        self,
        spec_id: str,
        fragment_label: str,
        *,
        scene_id: Optional[str] = None,
    ) -> dict[str, Any]:
        return self.set_polyhedron_instance_override(
            spec_id,
            fragment_label,
            {},
            scene_id=scene_id,
        )

    # ---- topology computation -----------------------------------------

