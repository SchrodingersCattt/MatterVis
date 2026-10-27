"""Analysis backend: scientific reports and disorder candidates.

Polyhedron colors, visibility, and instance overrides live on
``DisplayService``. This mixin computes reports and retains disorder
candidates. It does not commit structures by itself.

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
    """Scientific reports and retained disorder candidates.

    Layer colors and visibility are owned by ``DisplayService``. Geometry
    computation still delegates to ``_TopologyBackendMixin`` on the same
    ``ViewerBackend`` instance.
    """

    def resolve_disorder(
        self,
        scene_id=None,
        *,
        method: str = "enumerate",
        count: int = 5,
        seed: int | None = None,
    ):
        """List disorder candidates and retain their scientific crystals.

        Highlight indices remain a preview. ``candidate_id`` points at the
        crystal stored on the effect session, which is what a later commit
        applies.
        """
        from ..operations.disorder import resolve_disorder as _resolve_disorder_replicas

        state = self.get_state(scene_id)
        bundle = self.get_bundle(str(state.get("structure") or ""))
        summaries = _resolve_disorder_replicas(bundle, method=method, count=count, seed=seed)
        crystal = getattr(bundle, "crystal", None)
        if crystal is None or not summaries:
            return summaries
        try:
            # Generate the same seeded set once and retain each crystal.
            from molcrys_kit.analysis.disorder.process import generate_ordered_replicas_from_disordered_sites
            replicas = generate_ordered_replicas_from_disordered_sites(
                crystal=crystal.copy(),
                generate_count=int(count) if str(method) != "optimal" else 1,
                method=str(method),
                random_seed=None if seed in (None, "") else int(seed),
                return_kept_indices=True,
            )
        except Exception:
            return summaries
        by_kept = {}
        for replica_crystal, kept in replicas or []:
            by_kept[tuple(sorted(int(index) for index in kept))] = (replica_crystal, [int(index) for index in kept])
        session = self.effect_session()
        structure_id = str(getattr(bundle, "scientific_structure_id", None) or bundle.name)
        frame = str(getattr(bundle, "scientific_frame", "0"))
        if not session.has(structure_id, frame):
            session.register(crystal, structure_id=structure_id, frame_ref=frame, viewer_name=bundle.name)
            bundle.scientific_structure_id = structure_id
            bundle.scientific_revision = 1
            bundle.scientific_frame = frame
        for summary in summaries:
            kept_tuple = tuple(sorted(int(index) for index in summary.get("kept_indices") or []))
            found = by_kept.get(kept_tuple)
            if found is None:
                continue
            replica_crystal, kept_indices = found
            candidate = session.preview(
                "commit_disorder_candidate",
                {"crystal": replica_crystal, "kept_indices": kept_indices, "method": method, "seed": seed},
                structure_id=structure_id,
                expected_revision=session.current_ref(structure_id, frame).revision,
                frame_ref=frame,
            )
            summary["candidate_id"] = candidate.candidate_id
        return summaries

    def run_analysis(
        self,
        scene_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Run polyhedron analysis synchronously and return the results.

        Computes from the registered specs even when the polyhedron overlay
        is off. The overlay flag only controls drawing. Returns:

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

        # The legacy facade still hands the computation a state whose
        # overlay flag is on, without writing that flag back. The
        # scientific flag is what lets the core run when the stored
        # overlay is off.
        analysis_state = dict(state)
        analysis_state["topology_enabled"] = True
        try:
            geometry = self.topology_for_state(analysis_state, scientific=True)
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
