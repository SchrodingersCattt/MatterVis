from __future__ import annotations
# ruff: noqa: F401,F403,F405

from collections import OrderedDict

from .shared import *
from .normalizers import *
from .rightclick import _normalize_polyhedron_specs
from ..topology import (
    atom_overlay as _atom_overlay,
    display_atom_centers_for_spec as _display_atom_centers_for_spec,
    extract_atom_coordination_shells,
)
from ..cache_keys import topology_geometry_cache_key


_TOPOLOGY_CACHE_LIMIT = 8


from .topology_geometry import (
    compute_topology_geometry,
    compute_topology_geometry_payload,
    _dedupe_disorder_center_fragments,
    _fragment_minor_score,
    _fragment_matches_polyhedron_spec,
    _overlay_drawable_hull_size,
    map_display_fragment_to_topology_for_bundle,
    _pbc_distance_for_bundle,
)



class _TopologyBackendMixin:
    def topology_candidates(self, structure: str, fragment_type: Optional[str] = None) -> list[dict[str, Any]]:
        state = self.get_state()
        if state["structure"] != structure:
            state = self.normalize_state({"structure": structure})
        fragments = self.scene_for_state(state).get("fragment_table", [])
        if fragment_type and fragment_type not in ("", "Any"):
            filtered = [fragment for fragment in fragments if fragment.get("type") == fragment_type]
            if filtered:
                return filtered
        return fragments

    def fragment_index_for_atom(self, scene: dict, atom_index: int) -> Optional[int]:
        for fragment in scene.get("fragment_table", []):
            if atom_index in fragment.get("site_indices", []):
                return int(fragment["index"])
        atom = scene["draw_atoms"][atom_index]
        atom_cart = np.array(atom["cart"], dtype=float)
        fragments = scene.get("fragment_table", [])
        if not fragments:
            return atom_index
        distances = [
            (float(np.linalg.norm(np.array(fragment["center"], dtype=float) - atom_cart)), int(fragment["index"]))
            for fragment in fragments
        ]
        distances.sort(key=lambda item: item[0])
        return distances[0][1]

    def _display_fragment(self, scene: dict, display_index: int | None) -> Optional[dict[str, Any]]:
        if display_index is None:
            return None
        return next((fragment for fragment in scene.get("fragment_table", []) if int(fragment["index"]) == int(display_index)), None)

    def _pbc_distance(self, bundle: LoadedCrystal, frac_a, frac_b) -> float:
        return _pbc_distance_for_bundle(bundle, frac_a, frac_b)

    def map_display_fragment_to_topology(self, bundle: LoadedCrystal, display_fragment: dict | None) -> Optional[dict[str, Any]]:
        return map_display_fragment_to_topology_for_bundle(bundle, display_fragment)

    def resolve_topology_site(
        self,
        *,
        state: dict[str, Any],
        structure: str,
        explicit_site: Optional[int],
        species_keys: Optional[list[str]],
        click_data: Optional[dict[str, Any]],
    ) -> Optional[int]:
        """Resolve which fragment index gets the right-panel histogram +
        topology results.

        Display (which species the polyhedra overlay tiles) and analysis
        (which single fragment is in the right panel) are independent:
        an ``explicit_site`` from the "Analyze fragment" dropdown wins
        unconditionally, even when its formula is not in the currently
        tiled ``species_keys`` set. Only when no explicit site was given
        do we fall through to the click target / first-match defaults
        scoped by the tiled species.
        """
        scene = self.scene_for_state(state)
        fragments = scene.get("fragment_table", [])
        species_set = {str(key) for key in species_keys or [] if key}
        if explicit_site is not None:
            chosen = self._display_fragment(scene, explicit_site)
            if chosen is not None:
                return int(explicit_site)
        if click_data and click_data.get("points"):
            point = click_data["points"][0]
            custom = point.get("customdata")
            if custom:
                # Phase 4: customdata schema is
                # ``[kind, idx, label, elem, is_minor, fragment_label]``.
                # We read by index 1 when the first slot is a kind tag
                # ("atom"), and fall back to index 0 for backwards
                # compatibility with any frontend payload still on the
                # legacy schema (cached page from before redeploy).
                if isinstance(custom[0], str) and len(custom) > 1:
                    atom_index_raw = custom[1]
                else:
                    atom_index_raw = custom[0]
                try:
                    atom_index = int(atom_index_raw)
                except (TypeError, ValueError):
                    return None
                return self.fragment_index_for_atom(scene, atom_index)
        if species_set:
            atom_level = any(
                str(spec.get("level") or "molecule") == "atom"
                and str(spec.get("center_species") or "") in species_set
                for spec in (state.get("polyhedron_specs") or [])
                if isinstance(spec, dict)
            )
            if atom_level:
                candidates = [
                    fragment
                    for fragment in fragments
                    if species_set.intersection(
                        {str(elem) for elem in (fragment.get("elem_set") or [])}
                    )
                ]
            else:
                candidates = [
                    fragment
                    for fragment in fragments
                    if (fragment.get("formula") or fragment.get("species")) in species_set
                ]
            if not candidates:
                return None
        else:
            candidates = fragments
        if candidates:
            return int(candidates[0]["index"])
        return None

    def _topology_cache(self, bundle) -> OrderedDict:
        cache = getattr(bundle, "_topology_state_cache", None)
        if not isinstance(cache, OrderedDict):
            cache = OrderedDict(cache or {})
            bundle._topology_state_cache = cache
        if not hasattr(bundle, "_topology_state_cache_lock"):
            bundle._topology_state_cache_lock = threading.Lock()
        return cache

    def _store_topology_geometry(self, structure: str, cache_key: tuple, geometry: Optional[dict[str, Any]]) -> None:
        if geometry is None:
            return
        bundle = self.get_bundle(structure)
        cache = self._topology_cache(bundle)
        with bundle._topology_state_cache_lock:
            cache[cache_key] = geometry
            cache.move_to_end(cache_key)
            while len(cache) > _TOPOLOGY_CACHE_LIMIT:
                cache.popitem(last=False)

    def _topology_context(
        self,
        state: dict[str, Any],
        click_data: Optional[dict[str, Any]] = None,
        *,
        strict: bool = False,
        scene: Optional[dict[str, Any]] = None,
        scientific: bool = False,
    ) -> Optional[dict[str, Any]]:
        if not scientific and not state.get("topology_enabled", False):
            if strict:
                raise TopologyUnavailable(
                    "topology is disabled for this scene",
                    hint="POST /api/v2/state with topology_enabled=true, or include center_species and ligand_species in the topology request.",
                )
            return None
        structure = state["structure"]
        bundle = self.get_bundle(structure)
        scene = self.scene_for_state(state) if scene is None else scene
        effective_specs = self._effective_polyhedron_specs(state)
        if not effective_specs:
            if strict:
                raise TopologyUnavailable(
                    "no enabled polyhedron specs are registered for this scene",
                    hint="POST /api/v2/polyhedra first, or include center_species and ligand_species in the topology request.",
                )
            return None
        # Legacy code paths below still consume a single ``species_keys``
        # list (used to resolve the analysis anchor when the user clicks
        # in the viewer). Reconstruct it from the union of every active
        # spec's center species so a click on any rendered polyhedron
        # still snaps the analysis panel.
        species_keys = sorted({spec["center_species"] for spec in effective_specs})
        if not species_keys:
            if strict:
                raise TopologyUnavailable("no center species are available for topology analysis")
            return None
        site_index = self.resolve_topology_site(
            state=state,
            structure=structure,
            explicit_site=state.get("topology_site_index"),
            species_keys=species_keys,
            click_data=click_data,
        )
        if site_index is None:
            if strict:
                raise TopologyUnavailable(
                    "could not resolve a topology fragment for center_index",
                    hint="Use an index from GET /api/v2/scene/{name} topology_fragment_table.",
                )
            return None
        display_fragment = next(
            (
                fragment
                for fragment in (scene.get("fragment_table") or [])
                if int(fragment.get("index", -1)) == int(site_index)
            ),
            None,
        )
        analysis_spec = next(
            (
                spec
                for spec in effective_specs
                if display_fragment is not None
                and _fragment_matches_polyhedron_spec(display_fragment, spec)
            ),
            effective_specs[0],
        )
        # Memoize the (heavy) topology dict on the bundle keyed on the
        # state fields that actually influence GEOMETRY. Per-spec colour
        # is intentionally not in the key -- it only affects the
        # renderer's painter cache (``_background_dict_cache`` etc),
        # which is keyed independently on the per-spec colour tuple.
        # That way swapping a hull colour stays a cheap re-paint and
        # doesn't recompute coordination shells for every tile.
        cutoff = float(state.get("cutoff", 10.0))
        # Per-spec colours and ``instance_overrides`` stay OUT of the key;
        # they only affect the renderer's painter cache.
        cache_key = topology_geometry_cache_key(
            structure=structure,
            display_mode=state.get("display_mode"),
            show_hydrogen="hydrogens" in (state.get("display_options") or []),
            site_index=site_index,
            cutoff=cutoff,
            specs=effective_specs,
            transforms=state.get("transforms") or [],
        )
        return {
            "structure": structure,
            "bundle": bundle,
            "scene": scene,
            "effective_specs": effective_specs,
            "site_index": int(site_index),
            "cutoff": cutoff,
            "analysis_spec_id": analysis_spec["id"],
            "cache_key": cache_key,
        }

    def topology_for_state_sync(
        self,
        state: dict[str, Any],
        click_data: Optional[dict[str, Any]] = None,
        *,
        strict: bool = False,
        scientific: bool = False,
    ):
        context = self._topology_context(
            state, click_data=click_data, strict=strict, scientific=scientific
        )
        if context is None:
            return None
        bundle = context["bundle"]
        cache = self._topology_cache(bundle)
        with bundle._topology_state_cache_lock:
            cached_geometry = cache.get(context["cache_key"])
            if cached_geometry is not None:
                cache.move_to_end(context["cache_key"])
        if cached_geometry is None:
            if strict:
                raise TopologyUnavailable("topology analysis produced no geometry for the requested fragment")
            return None
        return self._attach_spec_colors(
            cached_geometry,
            context["effective_specs"],
            analysis_spec_id=context["analysis_spec_id"],
        )

    def topology_request(
        self,
        state: dict[str, Any],
        click_data: Optional[dict[str, Any]] = None,
    ) -> bool:
        context = self._topology_context(state, click_data=click_data, strict=False)
        if context is None:
            return False
        bundle = context["bundle"]
        cache = self._topology_cache(bundle)
        with bundle._topology_state_cache_lock:
            cached_geometry = cache.get(context["cache_key"])
        if cached_geometry is not None:
            return False
        worker = getattr(self, "_render_worker", None)
        if worker is None:
            return False
        return worker.request_topology(dict(state), context)

    def topology_for_state(
        self,
        state: dict[str, Any],
        click_data: Optional[dict[str, Any]] = None,
        *,
        strict: bool = False,
        scientific: bool = False,
    ):
        context = self._topology_context(
            state, click_data=click_data, strict=strict, scientific=scientific
        )
        if context is None:
            return None
        bundle = context["bundle"]
        cache = self._topology_cache(bundle)
        with bundle._topology_state_cache_lock:
            cached_geometry = cache.get(context["cache_key"])
            if cached_geometry is not None:
                cache.move_to_end(context["cache_key"])
        if cached_geometry is None:
            cached_geometry = self._compute_topology_geometry(
                bundle=context["bundle"],
                scene=context["scene"],
                effective_specs=context["effective_specs"],
                site_index=context["site_index"],
                cutoff=context["cutoff"],
            )
            self._store_topology_geometry(context["structure"], context["cache_key"], cached_geometry)
        if cached_geometry is None:
            if strict:
                raise TopologyUnavailable("topology analysis produced no geometry for the requested fragment")
            return None
        return self._attach_spec_colors(
            cached_geometry,
            context["effective_specs"],
            analysis_spec_id=context["analysis_spec_id"],
        )

    def _compute_topology_geometry(
        self,
        *,
        bundle,
        scene: dict[str, Any],
        effective_specs: list[dict[str, Any]],
        site_index: int,
        cutoff: float,
    ) -> Optional[dict[str, Any]]:
        return compute_topology_geometry(
            bundle=bundle,
            scene=scene,
            effective_specs=effective_specs,
            site_index=site_index,
            cutoff=cutoff,
        )

    @staticmethod
    def _spec_paint_key(effective_specs: list[dict[str, Any]]) -> tuple:
        """Hashable summary of every styling field that the renderer's
        painter cache (keyed on per-spec colour + per-instance overrides)
        actually reads. Geometry-affecting fields are intentionally
        absent here; they're already in the geometry-level cache key.
        """
        items: list = []
        for spec in effective_specs or []:
            spec_id = str(spec.get("id") or "")
            color = str(spec.get("color") or "#7C5CBF")
            opacity = float(spec.get("opacity", 0.55))
            edge_opacity = float(spec.get("edge_opacity", 0.90))
            edge_width = float(spec.get("edge_width", 3.0))
            flatshading = bool(spec.get("flatshading", True))
            overrides_raw = spec.get("instance_overrides") or {}
            overrides_tuple = tuple(
                (
                    str(label),
                    str((overrides_raw.get(label) or {}).get("color") or ""),
                    bool((overrides_raw.get(label) or {}).get("visible", True)),
                )
                for label in sorted(overrides_raw.keys())
            )
            items.append((
                spec_id, color, opacity, edge_opacity, edge_width,
                flatshading, overrides_tuple,
            ))
        return tuple(items)

    def _attach_spec_colors(
        self,
        cached_geometry: dict[str, Any],
        effective_specs: list[dict[str, Any]],
        *,
        analysis_spec_id: str | None = None,
    ) -> dict[str, Any]:
        """Re-stamp per-spec colours and per-fragment instance overrides
        onto a geometry payload pulled from the bundle cache.

        The geometry dict is shared across colour permutations; this
        method returns a thin wrapper around it. Critically, when the
        SAME paint key (colours + per-instance overrides) hits this
        function twice on the same geometry, we return the **same
        wrapper instance** so the renderer's painter caches
        (``_background_dict_cache`` / ``_foreground_dict_cache`` on
        ``topology_data``) survive across calls.

        Without this, every slider tweak that doesn't touch polyhedron
        colours (atom_scale, bond_radius, ...) was triggering a fresh
        ~150 ms re-tessellation of every hull-edge cylinder, because
        ``topology_data`` started painter caches from scratch on every
        call.
        """
        paint_key = self._spec_paint_key(effective_specs)
        wrapper_cache = cached_geometry.get("_paint_wrapper_cache")
        if wrapper_cache is None:
            wrapper_cache = {}
            cached_geometry["_paint_wrapper_cache"] = wrapper_cache
        cached_wrapper = wrapper_cache.get(paint_key)
        if cached_wrapper is not None:
            return cached_wrapper

        paint_by_id = {
            spec["id"]: {
                "color": spec.get("color", "#7C5CBF"),
                "opacity": float(spec.get("opacity", 0.55)),
                "edge_opacity": float(spec.get("edge_opacity", 0.90)),
                "edge_width": float(spec.get("edge_width", 3.0)),
                "flatshading": bool(spec.get("flatshading", True)),
            }
            for spec in effective_specs
        }
        overrides_by_id: dict[str, dict[str, dict[str, Any]]] = {
            spec["id"]: dict(spec.get("instance_overrides") or {}) for spec in effective_specs
        }
        entries_by_id = {
            str(entry.get("spec_id") or ""): entry
            for entry in cached_geometry.get("spec_results", []) or []
        }
        spec_results = []
        anchor_overlay = None
        for spec in effective_specs:
            entry = entries_by_id.get(str(spec.get("id") or ""))
            if entry is None:
                continue
            spec_id = entry.get("spec_id")
            recoloured = dict(entry)
            recoloured.update(paint_by_id.get(spec_id, {
                "color": "#7C5CBF",
                "opacity": 0.55,
                "edge_opacity": 0.90,
                "edge_width": 3.0,
                "flatshading": True,
            }))
            # The bundle cache owns ``entry``.  Always detach overlay dicts
            # before restamping anchor/paint metadata on the request wrapper.
            recoloured["overlays"] = [
                dict(overlay) for overlay in entry.get("overlays") or []
            ]
            spec_overrides = overrides_by_id.get(spec_id) or {}
            if spec_overrides:
                # Patch each overlay with its per-fragment override (if
                # any). The override key is the fragment label; we copy
                # the overlay dict so the cached geometry stays clean.
                new_overlays = []
                for overlay in recoloured["overlays"]:
                    label = str(overlay.get("center_label") or "")
                    override = spec_overrides.get(label)
                    if override:
                        patched = dict(overlay)
                        if "color" in override:
                            patched["color"] = override["color"]
                        if "visible" in override:
                            patched["visible"] = bool(override["visible"])
                        new_overlays.append(patched)
                    else:
                        new_overlays.append(overlay)
                recoloured["overlays"] = new_overlays
            if str(spec.get("id") or "") == str(analysis_spec_id or ""):
                overlays = recoloured.get("overlays") or []
                cached_center = cached_geometry.get("center_coords")
                cached_label = cached_geometry.get("center_label")
                anchor_overlay = next(
                    (
                        overlay
                        for overlay in overlays
                        if cached_center is not None
                        and overlay.get("center_coords") == cached_center
                    ),
                    None,
                )
                if anchor_overlay is None and cached_label is not None:
                    anchor_overlay = next(
                        (
                            overlay
                            for overlay in overlays
                            if overlay.get("center_label") == cached_label
                        ),
                        None,
                    )
                # A single overlay is only a safe fallback for atom-level
                # specs.  Molecule-level rows may be enabled for N/C6N2
                # while the user explicitly analyses an X0 ClO4 fragment;
                # rebinding that unmatched anchor to the lone N overlay is
                # the source of the historical ``B0 (N2)`` report.  Keep the
                # selected fragment's identity when no overlay agrees with
                # its centre.
                if (
                    anchor_overlay is None
                    and len(overlays) == 1
                    and str(spec.get("level") or "molecule") == "atom"
                ):
                    anchor_overlay = overlays[0]
            spec_results.append(recoloured)
        out = dict(cached_geometry)
        out["spec_results"] = spec_results
        if analysis_spec_id is not None:
            out["analysis_spec_id"] = analysis_spec_id
            # A sorted geometry key intentionally reuses the payload after a
            # polyhedron-spec reorder.  Rebuild the identity-bearing primary
            # fields from the current anchor's overlay so the analysis panel
            # and foreground renderer cannot keep the previous spec's shell.
            for entry in out["spec_results"]:
                overlays = entry.get("overlays") or []
                for overlay in overlays:
                    overlay["is_analysis_anchor"] = overlay is anchor_overlay
            if anchor_overlay is not None:
                for field in (
                    "center_coords",
                    "center_label",
                    "shell_coords",
                    "distances",
                    "hull",
                    "source_center_coords",
                    "source_shell_coords",
                    "source_hull",
                ):
                    if field in anchor_overlay:
                        out[field] = anchor_overlay[field]
        # Brand-new wrapper -> brand-new painter caches. The renderer
        # populates these on first paint and they are reused for every
        # subsequent build_figure call sharing the same paint key.
        out.pop("_background_dict_cache", None)
        out.pop("_foreground_dict_cache", None)
        # Don't carry the wrapper-cache forward through the wrapper itself;
        # it lives on the geometry dict only.
        out.pop("_paint_wrapper_cache", None)
        # Bound the wrapper cache so a user spamming colours doesn't
        # wedge memory. Painter caches are small (a few hundred KB per
        # entry) but bounded retention keeps things predictable.
        if len(wrapper_cache) >= 8:
            wrapper_cache.pop(next(iter(wrapper_cache)))
        wrapper_cache[paint_key] = out
        return out


    def query_topology(
        self,
        structure: str,
        center_index: int,
        cutoff: float = 10.0,
        scene_id: Optional[str] = None,
        *,
        center_species: Optional[str] = None,
        ligand_species: Optional[str] = None,
        level: str = "molecule",
        enforce_enclosure: bool = True,
        centroid_offset_frac: Optional[float] = DEFAULT_CENTROID_OFFSET_FRAC,
        disorder_policy: Optional[str] = None,
    ) -> dict[str, Any]:
        if cutoff <= 0 or cutoff > 1000:
            raise ApiError("cutoff must be in the range (0, 1000]", status_code=400)
        state = self.get_state(scene_id)
        if state["structure"] != structure:
            state = self.normalize_state({"structure": structure}, scene_id=scene_id)
        scene = self.scene_for_state(state)
        if self._display_fragment(scene, center_index) is None:
            raise ApiError(
                f"center_index {center_index} is not present in this scene's fragment table",
                hint="Use an index from GET /api/v2/scene/{name} topology_fragment_table.",
                status_code=400,
            )
        state["topology_site_index"] = center_index
        state["cutoff"] = cutoff
        level = str(level or "molecule")
        if level not in {"molecule", "atom"}:
            raise ApiError("level must be 'molecule' or 'atom'", status_code=400)
        # Explicit analysis is independent of the drawing switch. get_state
        # returns a request-local snapshot, so this never enables the overlay
        # or changes the registered rows on the scene.
        state["topology_enabled"] = True
        if level == "atom":
            display_fragment = self._display_fragment(scene, center_index)
            topology_fragment = self.map_display_fragment_to_topology(self.get_bundle(structure), display_fragment)
            if topology_fragment is None:
                raise TopologyUnavailable("could not map display fragment to topology fragment")
            try:
                return analyze_topology(
                    self.get_bundle(structure),
                    center_index=int(topology_fragment["index"]),
                    cutoff=cutoff,
                    display_center=display_fragment.get("center") if display_fragment else None,
                    display_label=display_fragment.get("label") if display_fragment else None,
                    display_type=display_fragment.get("type") if display_fragment else None,
                    ligand_species=[ligand_species] if ligand_species else None,
                    level="atom",
                    center_species=center_species,
                    enforce_enclosure=enforce_enclosure,
                    centroid_offset_frac=_coerce_centroid_offset_frac(centroid_offset_frac),
                    disorder_policy=disorder_policy,
                )
            except ValueError as exc:
                raise ApiError(str(exc), status_code=400) from exc
        if center_species is not None or ligand_species is not None:
            if not center_species:
                fragment = self._display_fragment(scene, center_index)
                center_species = (fragment or {}).get("formula") or (fragment or {}).get("species")
            state["polyhedron_specs"] = _normalize_polyhedron_specs(
                [
                    {
                        "id": "ephemeral_topology_request",
                        "name": str(center_species or "Topology"),
                        "center_species": center_species,
                        "ligand_species": ligand_species,
                        "enabled": True,
                        "enforce_enclosure": enforce_enclosure,
                        "centroid_offset_frac": centroid_offset_frac,
                        "disorder_policy": disorder_policy,
                    }
                ],
                fallback_color=state.get("topology_hull_color", "#7C5CBF"),
            )
        try:
            result = self.topology_for_state(state, strict=True)
        except ValueError as exc:
            raise ApiError(str(exc), status_code=400) from exc
        if result is None:
            raise TopologyUnavailable("topology analysis was unavailable for this request")
        return result

