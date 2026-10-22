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

import logging
from contextlib import contextmanager

from .shared import *
from .normalizers import *
from .rightclick import _normalize_polyhedron_specs, _normalize_polyhedron_spec
from .style_helpers import _POLYHEDRON_AUTO_COLORS
from ..topology.analysis import _MAX_REGISTERED_CN


BFDH_DEFAULT_MAX_INDEX = 2
BFDH_DEFAULT_TOP_N = 10

logger = logging.getLogger(__name__)


@contextmanager
def _bfdh_symmetry_errors():
    """Run BFDH symmetry on spglib's current error API.

    spglib still defaults to deprecated error handling and warns inside the
    symmetry helpers pymatgen uses to group Miller families. ``Warning`` is
    an ``Exception``, and this project's pytest config turns warnings into
    errors, so MolCrysKit's ``except Exception`` fallback treats that warning
    as a symmetry failure. The ranking then keeps every signed low-index
    plane as its own family with multiplicity 1. Opt into direct errors for
    the duration of the MolCrysKit call and restore the previous flag.
    """
    try:
        import spglib.error as spglib_error
    except ImportError:
        yield
        return
    previous = spglib_error.OLD_ERROR_HANDLING
    spglib_error.OLD_ERROR_HANDLING = False
    try:
        yield
    finally:
        spglib_error.OLD_ERROR_HANDLING = previous


def _closed_bfdh_mesh(crystal, facets) -> list[dict[str, Any]]:
    """Return a closed convex BFDH mesh centered at the Cartesian origin.

    Face distances come from MolCrysKit ``ImplicitShape.bfdh``: they follow
    the growth-rate proxy ``1 / d_hkl``, and both signs of every symmetry
    equivalent are included. The stored vertices are scaled so the largest
    bounding-box span equals the longest cell edge. The renderer multiplies
    by the morphology scale and then translates onto the unit-cell centre.

    ``facets`` must be the ranked families already shown in the hkl table so
    the solid uses the same Top N set.
    """
    import numpy as np
    from molcrys_kit.operations.implicit_shape import ImplicitShape
    from scipy.spatial import ConvexHull, QhullError

    lattice = np.asarray(getattr(crystal, "lattice", None), dtype=float)
    if lattice.shape != (3, 3) or not np.all(np.isfinite(lattice)):
        raise ValueError("BFDH morphology needs a finite 3x3 lattice.")
    max_dimension = float(np.max(np.linalg.norm(lattice, axis=1)))
    if not np.isfinite(max_dimension) or max_dimension <= 0.0:
        raise ValueError("BFDH morphology lattice has no positive cell edge.")
    miller_indices = [
        tuple(int(value) for value in facet.miller_index) for facet in facets
    ]
    with _bfdh_symmetry_errors():
        shape = ImplicitShape.bfdh(
            crystal,
            max_dimension,
            miller_indices=miller_indices,
        )
    vertices = np.asarray(shape.parameters.get("vertices_A"), dtype=float)
    if vertices.ndim != 2 or vertices.shape != (vertices.shape[0], 3) or len(vertices) < 4:
        raise ValueError("BFDH half-space intersection did not return a 3-D vertex set.")
    if not np.all(np.isfinite(vertices)):
        raise ValueError("BFDH half-space intersection returned non-finite vertices.")
    try:
        hull = ConvexHull(vertices)
    except QhullError as exc:
        raise ValueError(
            "BFDH half-space vertices do not form a closed 3-D polyhedron."
        ) from exc
    if not np.isfinite(hull.volume) or float(hull.volume) <= 0.0:
        raise ValueError("BFDH morphology mesh is degenerate.")

    planes: list[tuple[np.ndarray, float, list[int]]] = []
    for plane in shape.parameters.get("planes") or []:
        normal = np.asarray(plane.get("normal_cartesian"), dtype=float)
        distance = float(plane.get("distance_A"))
        miller = [int(value) for value in plane.get("miller_index") or (0, 0, 0)]
        if normal.shape != (3,) or not np.all(np.isfinite(normal)):
            continue
        planes.append((normal, distance, miller))

    grouped: dict[tuple[int, int, int], dict[str, list]] = {}
    for simplex in hull.simplices:
        triangle = vertices[np.asarray(simplex, dtype=int)]
        centroid = triangle.mean(axis=0)
        best_index = None
        best_error = float("inf")
        for index, (normal, distance, _miller) in enumerate(planes):
            error = abs(float(np.dot(normal, centroid)) - distance)
            if error < best_error:
                best_error = error
                best_index = index
        if best_index is None:
            continue
        miller_key = tuple(planes[best_index][2])
        bucket = grouped.setdefault(miller_key, {"triangles": [], "centroids": []})
        bucket["triangles"].append(triangle.tolist())
        bucket["centroids"].append(centroid)

    mesh = []
    for miller, bucket in grouped.items():
        centroids = np.asarray(bucket["centroids"], dtype=float)
        mesh.append(
            {
                "miller": list(miller),
                "triangles": bucket["triangles"],
                "centroid": centroids.mean(axis=0).tolist(),
            }
        )
    mesh.sort(key=lambda item: tuple(item["miller"]))
    if not mesh:
        raise ValueError("BFDH morphology mesh has no labelled faces.")
    return mesh


def _coerce_bfdh_max_index(value: Any) -> int:
    """Coerce the UI/API value and reject invalid Miller index bounds."""
    try:
        max_index = int(value)
    except (TypeError, ValueError):
        return BFDH_DEFAULT_MAX_INDEX
    if max_index < 1:
        raise ValueError("Max Index must be at least 1")
    return max_index


def _coerce_bfdh_top_n(value: Any) -> int:
    """Coerce Top N; blank and non-positive values use the default 10."""
    try:
        top_n = int(value)
    except (TypeError, ValueError):
        return BFDH_DEFAULT_TOP_N
    return BFDH_DEFAULT_TOP_N if top_n < 1 else top_n


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
        max_index: int = BFDH_DEFAULT_MAX_INDEX,
        top_n: int = BFDH_DEFAULT_TOP_N,
    ) -> dict[str, Any]:
        """Run BFDH morphology analysis synchronously and return the results.

        Returns:
        ``{"facets": [...], "status": "ok"|"error", "warnings": [...]}``
        """
        try:
            max_index = _coerce_bfdh_max_index(max_index)
        except ValueError as exc:
            return {
                "facets": [],
                "status": "error",
                "warnings": [str(exc)],
            }
        top_n = _coerce_bfdh_top_n(top_n)

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

            # include_equivalents / include_negative fill the table's
            # multiplicity. The mesh is a separate MolCrysKit half-space
            # intersection of those families; pymatgen's WulffShape with
            # energy=d_hkl keeps only the smallest-d family and labels every
            # symmetry copy with the representative index.
            with _bfdh_symmetry_errors():
                facets = enumerate_bfdh_facets(
                    crystal,
                    max_index=max_index,
                    top_n=top_n,
                    include_equivalents=True,
                    include_negative=True,
                )
            warnings: list[str] = []
            wulff_facets: list[dict[str, Any]] = []
            if facets:
                # Ranking and the closed mesh are separate. A mesh-build
                # failure (including non-ValueError) drops only the mesh so
                # the hkl table from enumerate_bfdh_facets still renders.
                try:
                    wulff_facets = _closed_bfdh_mesh(crystal, facets)
                except Exception as exc:
                    logger.exception("BFDH morphology mesh build failed")
                    warnings.append(
                        f"BFDH mesh unavailable: {type(exc).__name__}: {exc}"
                    )
                if not wulff_facets and not warnings:
                    warnings.append(
                        "BFDH ranking succeeded, but the morphology mesh is empty."
                    )

            return {
                "facets": [f.as_dict() for f in facets],
                "wulff_facets": wulff_facets,
                "status": "ok",
                "warnings": warnings,
            }
        except Exception as exc:
            return {
                "facets": [],
                "status": "error",
                "warnings": [f"BFDH analysis failed: {exc}"],
            }


__all__ = ["_AnalysisBackendMixin"]
