"""Analysis backend: polyhedron spec CRUD + analysis dispatch.

Follows the ``mck analyze`` CLI pattern from MolCrysKit: read-only
computation producing visual overlays and structured reports. Never
mutates the source crystal.

Owns:
- Polyhedron spec CRUD (extracted from ``_OverlaysBackendMixin``)
- ``run_analysis(specs, scene_id)`` — synchronous polyhedron computation
- Analysis status / warning surfacing

The topology geometry computation (``compute_topology_geometry``, etc.)
remains in ``backend_topology.py``; this mixin provides the user-facing
analysis surface that calls into it.
"""
from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *
from .normalizers import *
from .rightclick import _normalize_polyhedron_specs, _normalize_polyhedron_spec
from .style_helpers import _POLYHEDRON_AUTO_COLORS


class _AnalysisBackendMixin:
    """Polyhedron spec CRUD + analysis dispatch.

    Every method works on the active scene by default; callers may pass
    ``scene_id`` to target a specific tab.  Methods return the persisted
    list of specs (post-normalisation) and emit a broadcast so every
    connected client picks up the change.

    Delegates geometry computation to ``_TopologyBackendMixin`` methods
    on the same ``self`` instance (the composite ``ViewerBackend``).
    """

    # ------------------------------------------------------------------
    # Polyhedron spec CRUD (was in _OverlaysBackendMixin)
    # ------------------------------------------------------------------

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
        level: Optional[str] = None,
        center_kind: Optional[str] = None,
        hard_cutoff: Optional[float] = None,
        fallback_max: Optional[int] = None,
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
        scene_id: Optional[str] = None,
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
            return [dict(spec) for spec in explicit]
        return []

    def run_analysis(
        self,
        scene_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Run polyhedron analysis synchronously and return the results.

        Wraps ``topology_for_state_sync`` so callers (UI, REST) get a
        single call that includes status/warning metadata. Returns:

        ``{"geometry": ..., "status": "ok"|"no_specs"|"no_results",
          "warnings": [...], "spec_count": int}``
        """
        state = self.get_state(scene_id)
        scene_id = state.get("scene_id") or self.active_scene_id()
        effective_specs = self._effective_polyhedron_specs(state)

        if not effective_specs:
            return {
                "geometry": None,
                "status": "no_specs",
                "warnings": ["No polyhedron specs registered. Add at least one "
                             "centre species + ligand pair in the Analysis panel."],
                "spec_count": 0,
            }

        if not state.get("topology_enabled", False):
            return {
                "geometry": None,
                "status": "disabled",
                "warnings": ["Topology is disabled for this scene. "
                             "Check 'Show polyhedra overlay' to enable."],
                "spec_count": len(effective_specs),
            }

        try:
            geometry = self.topology_for_state_sync(state)
        except Exception as exc:
            return {
                "geometry": None,
                "status": "error",
                "warnings": [f"Analysis failed: {exc}"],
                "spec_count": len(effective_specs),
            }

        if geometry is None:
            return {
                "geometry": None,
                "status": "no_results",
                "warnings": ["Analysis produced no geometry. The selected fragment "
                             "may not have enough ligand neighbours in the cutoff "
                             "radius."],
                "spec_count": len(effective_specs),
            }

        spec_results = geometry.get("spec_results") or []
        drawable_specs = sum(
            1 for sr in spec_results
            if any(
                (overlay.get("shell_coords") or []) and (overlay.get("hull") or {}).get("simplices")
                for overlay in (sr.get("overlays") or [])
            )
        )
        warnings = list(geometry.get("warnings") or [])
        if drawable_specs == 0 and spec_results:
            warnings.insert(0, "No spec produced drawable polyhedra "
                              "(need ≥4 non-coplanar ligand points per centre).")

        return {
            "geometry": geometry,
            "status": "ok",
            "warnings": warnings,
            "spec_count": len(effective_specs),
            "drawable_spec_count": drawable_specs,
        }

    def run_bfdh_analysis(
        self,
        scene_id: Optional[str] = None,
        *,
        max_index: int = 2,
        top_n: int = 10,
    ) -> dict[str, Any]:
        """Run BFDH morphology analysis synchronously and return the results.

        Returns:
        ``{"facets": [...], "status": "ok"|"error", "warnings": [...]}``
        """
        state = self.get_state(scene_id)
        structure = str(state.get("structure") or "")
        bundle = self.get_bundle(structure)
        crystal = getattr(bundle, "crystal", None)

        if crystal is None:
            return {
                "facets": [],
                "status": "error",
                "warnings": ["BFDH analysis requires bundle.crystal (MolecularCrystal)."],
            }

        try:
            from molcrys_kit.analysis import enumerate_bfdh_facets
            from pymatgen.core.lattice import Lattice
            from pymatgen.analysis.wulff import WulffShape
            import numpy as np

            facets = enumerate_bfdh_facets(crystal, max_index=max_index, top_n=top_n)
            
            wulff_facets = []
            if facets:
                try:
                    lattice = Lattice(crystal.lattice)
                    miller_list = [f.miller_index for f in facets]
                    # Use d_hkl directly as "energy". In Wulff construction,
                    # face distance from centre = 1/energy. With energy=d_hkl
                    # the output vertices are in Angstrom scale, and the face
                    # ranking is identical to energy=1/d_hkl (same visible
                    # set, only the coordinate scale differs).
                    e_surf_list = [f.d_hkl for f in facets]
                    
                    wulff = WulffShape(lattice, miller_list, e_surf_list)
                    
                    for facet in wulff.facets:
                        triangles = []
                        all_pts = []
                        for tri in facet.points:
                            tri_pts = [v.tolist() for v in tri]
                            triangles.append(tri_pts)
                            all_pts.extend(tri_pts)
                        
                        if all_pts:
                            centroid = np.mean(all_pts, axis=0).tolist()
                            wulff_facets.append({
                                "miller": facet.miller,
                                "triangles": triangles,
                                "centroid": centroid
                            })
                except Exception as wulff_exc:
                    # WulffShape might fail if facets don't enclose a 3D volume
                    pass

            return {
                "facets": [f.as_dict() for f in facets],
                "wulff_facets": wulff_facets,
                "status": "ok",
                "warnings": [],
            }
        except Exception as exc:
            return {
                "facets": [],
                "status": "error",
                "warnings": [f"BFDH analysis failed: {exc}"],
            }


__all__ = ["_AnalysisBackendMixin"]
