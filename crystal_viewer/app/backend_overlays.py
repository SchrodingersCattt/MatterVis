from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *
from .normalizers import *


class _OverlaysBackendMixin:
    """Atom-group, bond-group, and overlay-override CRUD.

    Polyhedron spec CRUD moved to ``_AnalysisBackendMixin``;
    transform CRUD moved to ``_OperationsBackendMixin``.
    Those mixins appear before this one in ``ViewerBackend``'s MRO
    so their methods take priority.
    """

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

    # ---- atom_groups CRUD ---------------------------------------------

    # ---- overlay_overrides CRUD ----------------------------------------
    #
    # Manual 2D viewport components mirror the other per-scene lists.
    # Paper-anchored entries store paper coordinates; world-anchored
    # entries store a target plus pixel offset and are reprojected by
    # the overlay painter.

    def list_overlay_overrides(self, scene_id: Optional[str] = None) -> list[dict[str, Any]]:
        return list(self.get_state(scene_id).get("overlay_overrides") or [])

    def _resolve_overlay_overrides(self, scene_id: Optional[str]) -> tuple[Optional[str], list[dict[str, Any]]]:
        scene_id = scene_id or self.active_scene_id()
        return scene_id, [
            dict(item) for item in (self.get_state(scene_id).get("overlay_overrides") or [])
        ]

    def _normalize_overlay_override(
        self,
        raw: dict[str, Any],
        *,
        existing_ids: set[str],
    ) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        kind = str(raw.get("kind") or "").strip()
        if not kind:
            return None
        anchor = str(raw.get("anchor") or "paper").strip()
        if anchor not in {"paper", "world"}:
            raise ValueError("overlay override anchor must be 'paper' or 'world'")
        override_id = str(raw.get("id") or "").strip()
        if not override_id or override_id in existing_ids:
            override_id = f"overlay_{uuid.uuid4().hex[:10]}"
            while override_id in existing_ids:  # pragma: no cover - astronomically unlikely
                override_id = f"overlay_{uuid.uuid4().hex[:10]}"
        existing_ids.add(override_id)
        out = dict(raw)
        out["id"] = override_id
        out["kind"] = kind
        out["anchor"] = anchor
        return out

    def add_overlay_override(
        self,
        override: dict[str, Any],
        *,
        scene_id: Optional[str] = None,
        override_id: Optional[str] = None,
    ) -> dict[str, Any]:
        scene_id, overrides = self._resolve_overlay_overrides(scene_id)
        raw = dict(override or {})
        if override_id is not None:
            raw["id"] = override_id
        item = self._normalize_overlay_override(raw, existing_ids={o["id"] for o in overrides})
        if item is None:
            raise ValueError("invalid overlay override payload")
        overrides.append(item)
        self.patch_state({"overlay_overrides": overrides}, scene_id=scene_id)
        return item

    def update_overlay_override(
        self,
        override_id: str,
        patch: dict[str, Any],
        *,
        scene_id: Optional[str] = None,
    ) -> dict[str, Any]:
        scene_id, overrides = self._resolve_overlay_overrides(scene_id)
        for index, item in enumerate(overrides):
            if item["id"] == override_id:
                merged = dict(item)
                merged.update(patch or {})
                merged["id"] = override_id
                replacement = self._normalize_overlay_override(
                    merged,
                    existing_ids={o["id"] for o in overrides if o["id"] != override_id},
                )
                if replacement is None:
                    raise ValueError("invalid overlay override patch")
                overrides[index] = replacement
                self.patch_state({"overlay_overrides": overrides}, scene_id=scene_id)
                return replacement
        raise KeyError(f"unknown overlay override id: {override_id!r}")

    def remove_overlay_override(
        self,
        override_id: str,
        *,
        scene_id: Optional[str] = None,
    ) -> bool:
        scene_id, overrides = self._resolve_overlay_overrides(scene_id)
        before = len(overrides)
        overrides = [item for item in overrides if item["id"] != override_id]
        if len(overrides) == before:
            return False
        self.patch_state({"overlay_overrides": overrides}, scene_id=scene_id)
        return True

    def reorder_overlay_overrides(
        self,
        ordered_ids: Iterable[str],
        *,
        scene_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        scene_id, overrides = self._resolve_overlay_overrides(scene_id)
        index_by_id = {item["id"]: item for item in overrides}
        wanted = [str(item) for item in ordered_ids]
        if set(wanted) != set(index_by_id):
            raise ValueError(
                "reorder list must contain exactly the existing overlay override ids; "
                f"got {wanted!r}, have {sorted(index_by_id)}"
            )
        ordered = [index_by_id[item_id] for item_id in wanted]
        self.patch_state({"overlay_overrides": ordered}, scene_id=scene_id)
        return ordered

    # ---- topology computation -----------------------------------------

