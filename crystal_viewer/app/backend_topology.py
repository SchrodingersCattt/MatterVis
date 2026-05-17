from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *
from .normalizers import *
from .rightclick import _normalize_polyhedron_specs


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
        return float(
            minimum_image_distance(
                np.array(frac_b, dtype=float),
                np.array(frac_a, dtype=float),
                np.array(bundle.M, dtype=float),
            )
        )

    def map_display_fragment_to_topology(self, bundle: LoadedCrystal, display_fragment: dict | None) -> Optional[dict[str, Any]]:
        if display_fragment is None:
            return None
        source_molecule_index = display_fragment.get("source_molecule_index")
        if source_molecule_index is not None:
            matched = next(
                (
                    fragment
                    for fragment in bundle.topology_fragment_table
                    if fragment.get("source_molecule_index") == source_molecule_index
                ),
                None,
            )
            if matched is not None:
                return matched
        # Prefer matching by stoichiometric formula (the species-checkbox
        # identity); fall back to A/B/X type for older payloads where the
        # formula field hasn't been populated yet.
        display_formula = display_fragment.get("formula") or display_fragment.get("species")
        candidates = [
            fragment
            for fragment in bundle.topology_fragment_table
            if (fragment.get("formula") or fragment.get("species")) == display_formula
        ]
        if not candidates:
            candidates = [
                fragment
                for fragment in bundle.topology_fragment_table
                if fragment.get("type") == display_fragment.get("type")
            ]
        if not candidates:
            candidates = list(bundle.topology_fragment_table)
        if not candidates:
            return None
        display_frac = np.array(display_fragment.get("frac_center", [0.0, 0.0, 0.0]), dtype=float)
        ranked = []
        for fragment in candidates:
            ranked.append((self._pbc_distance(bundle, display_frac, fragment.get("frac_center", [0.0, 0.0, 0.0])), fragment))
        ranked.sort(key=lambda item: item[0])
        return ranked[0][1]

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

    # ---- polyhedron_specs CRUD ---------------------------------------
    #
    # All methods operate on the active scene's state by default;
    # callers may pass ``scene_id`` to target a specific tab. They
    # always return the persisted list of specs (post-normalisation)
    # and emit a broadcast so every connected client picks up the
    # change. Wraps ``patch_state`` so the existing version bump,
    # autosave, and pending-state machinery just works.


    def _effective_polyhedron_specs(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Resolve the per-render list of explicit named polyhedron specs.

        MatterVis no longer synthesises auto-ligand specs from
        ``topology_species_keys``; molecule-level packing shells are delegated
        to MolCrysKit and require explicit centre/ligand formulas.
        """
        explicit = list(state.get("polyhedron_specs") or [])
        if explicit:
            return [dict(spec) for spec in explicit if spec.get("enabled", True)]
        return []

    def topology_for_state(
        self,
        state: dict[str, Any],
        click_data: Optional[dict[str, Any]] = None,
        *,
        strict: bool = False,
    ):
        if not state.get("topology_enabled", False):
            if strict:
                raise TopologyUnavailable(
                    "topology is disabled for this scene",
                    hint="POST /api/v2/state with topology_enabled=true, or include center_species and ligand_species in the topology request.",
                )
            return None
        structure = state["structure"]
        bundle = self.get_bundle(structure)
        scene = self.scene_for_state(state)
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
        # Memoize the (heavy) topology dict on the bundle keyed on the
        # state fields that actually influence GEOMETRY. Per-spec colour
        # is intentionally not in the key -- it only affects the
        # renderer's painter cache (``_background_dict_cache`` etc),
        # which is keyed independently on the per-spec colour tuple.
        # That way swapping a hull colour stays a cheap re-paint and
        # doesn't recompute coordination shells for every tile.
        cutoff = float(state.get("cutoff", 10.0))
        spec_geometry_key = frozenset(
            (
                spec["center_species"],
                spec.get("ligand_species") or None,
                bool(spec.get("enforce_enclosure", True)),
                float(spec.get("centroid_offset_frac", DEFAULT_CENTROID_OFFSET_FRAC)),
            )
            for spec in effective_specs
        )
        # Phase 4: ``transforms`` change which fragments exist and must be
        # in the geometry cache key. Per-spec colours and
        # ``instance_overrides`` stay OUT of the key (they only affect
        # the renderer's painter cache; see ``_attach_spec_colors``).
        from ..transforms import transforms_cache_key

        transforms_key = transforms_cache_key(state.get("transforms") or [])
        cache_key = (
            structure,
            state.get("display_mode"),
            bool("hydrogens" in (state.get("display_options") or [])),
            int(site_index),
            cutoff,
            spec_geometry_key,
            transforms_key,
        )
        cache = getattr(bundle, "_topology_state_cache", None)
        if cache is None:
            cache = {}
            bundle._topology_state_cache = cache
        cached_geometry = cache.get(cache_key)
        if cached_geometry is None:
            cached_geometry = self._compute_topology_geometry(
                bundle=bundle,
                scene=scene,
                effective_specs=effective_specs,
                site_index=site_index,
                cutoff=cutoff,
            )
            cache[cache_key] = cached_geometry
        if cached_geometry is None:
            if strict:
                raise TopologyUnavailable("topology analysis produced no geometry for the requested fragment")
            return None
        # Re-attach the per-render colour overrides on every call. The
        # geometry payload is shared across colour permutations; we only
        # ever copy a small list of dicts, never the heavy hull arrays.
        return self._attach_spec_colors(cached_geometry, effective_specs)

    def _compute_topology_geometry(
        self,
        *,
        bundle,
        scene: dict[str, Any],
        effective_specs: list[dict[str, Any]],
        site_index: int,
        cutoff: float,
    ) -> Optional[dict[str, Any]]:
        display_fragment = self._display_fragment(scene, site_index)
        topology_fragment = self.map_display_fragment_to_topology(bundle, display_fragment)
        if topology_fragment is None:
            return None

        # Group enabled specs by (center_species -> [spec_index_in_specs, ...])
        # so each fragment in the scene knows which spec(s) own it.
        # Multiple specs may share a centre species but request different
        # ligand restrictions (e.g. "Pb -> Cl" red vs "Pb -> Br" blue in
        # mixed-halide perovskites); the same fragment then participates
        # in both spec_results.
        center_to_spec_indices: dict[str, list[int]] = {}
        for index, spec in enumerate(effective_specs):
            center_to_spec_indices.setdefault(spec["center_species"], []).append(index)

        primary_display_index = int(display_fragment["index"]) if display_fragment else None
        primary_formula = (
            (display_fragment.get("formula") or display_fragment.get("species"))
            if display_fragment else None
        )
        # Pick the spec that "owns" the analysis anchor. Preference goes
        # to a spec whose center species matches the clicked fragment;
        # if none match, fall back to the first enabled spec so the
        # right-hand histogram still has data to render.
        analysis_spec_index = 0
        if primary_formula and primary_formula in center_to_spec_indices:
            analysis_spec_index = center_to_spec_indices[primary_formula][0]
        analysis_spec = effective_specs[analysis_spec_index]
        analysis_ligand = analysis_spec.get("ligand_species") or None
        analysis_enforce_enclosure = bool(analysis_spec.get("enforce_enclosure", True))
        analysis_centroid_offset_frac = float(
            analysis_spec.get("centroid_offset_frac", DEFAULT_CENTROID_OFFSET_FRAC)
        )

        primary = analyze_topology(
            bundle,
            center_index=int(topology_fragment["index"]),
            cutoff=cutoff,
            display_center=display_fragment.get("center") if display_fragment else None,
            display_label=display_fragment.get("label") if display_fragment else None,
            display_type=display_fragment.get("type") if display_fragment else None,
            ligand_species=[analysis_ligand] if analysis_ligand else None,
            enforce_enclosure=analysis_enforce_enclosure,
            centroid_offset_frac=analysis_centroid_offset_frac,
        )

        # Build per-spec overlay lists. For each fragment whose formula
        # matches a spec's center species, run the lighter
        # ``extract_coordination_shell`` (skips planarity / prism /
        # shape-classification passes -- those only matter for the
        # analysis anchor).
        # The same fragment may appear in multiple specs if those specs
        # share its centre species but differ in ligand selection; the
        # cache hit on (center_index, cutoff, ligand_species) makes the
        # repeat call cheap.
        spec_results: list[dict[str, Any]] = []
        legacy_extras: list[dict[str, Any]] = []
        for index, spec in enumerate(effective_specs):
            center_species = spec["center_species"]
            ligand = spec.get("ligand_species") or None
            ligand_arg = [ligand] if ligand else None
            enforce_enclosure = bool(spec.get("enforce_enclosure", True))
            centroid_offset_frac = float(spec.get("centroid_offset_frac", DEFAULT_CENTROID_OFFSET_FRAC))
            overlays: list[dict[str, Any]] = []
            for frag in scene.get("fragment_table") or []:
                formula_key = frag.get("formula") or frag.get("species")
                if formula_key != center_species:
                    continue
                is_anchor = (
                    index == analysis_spec_index
                    and primary_display_index is not None
                    and int(frag["index"]) == primary_display_index
                )
                if is_anchor:
                    overlays.append(
                        {
                            "center_coords": primary["center_coords"],
                            "center_label": primary.get("center_label"),
                            "shell_coords": primary["shell_coords"],
                            "distances": primary["distances"],
                            "hull": primary.get("hull"),
                            "is_analysis_anchor": True,
                        }
                    )
                    continue
                mapped = self.map_display_fragment_to_topology(bundle, frag)
                if mapped is None:
                    continue
                try:
                    extra = extract_coordination_shell(
                        bundle,
                        center_index=int(mapped["index"]),
                        cutoff=cutoff,
                        display_center=frag.get("center"),
                        display_label=frag.get("label"),
                        display_type=frag.get("type"),
                        ligand_species=ligand_arg,
                        enforce_enclosure=enforce_enclosure,
                        centroid_offset_frac=centroid_offset_frac,
                    )
                except Exception:
                    continue
                if not extra.get("shell_coords"):
                    # Empty shell would render as nothing anyway; skip
                    # the entry so renderer caches stay tidy.
                    continue
                overlay = {
                    "center_coords": extra.get("center_coords"),
                    "center_label": extra.get("center_label"),
                    "shell_coords": extra.get("shell_coords"),
                    "distances": extra.get("distances"),
                    "hull": extra.get("hull"),
                    "is_analysis_anchor": False,
                }
                overlays.append(overlay)
                legacy_extras.append(
                    {
                        "center_coords": overlay["center_coords"],
                        "center_label": overlay["center_label"],
                        "shell_coords": overlay["shell_coords"],
                        "distances": overlay["distances"],
                        "hull": overlay.get("hull"),
                    }
                )
            spec_results.append(
                {
                    "spec_id": spec["id"],
                    "name": spec["name"],
                    "center_species": center_species,
                    "ligand_species": ligand,
                    "enforce_enclosure": enforce_enclosure,
                    "centroid_offset_frac": centroid_offset_frac,
                    "overlays": overlays,
                }
            )

        primary = dict(primary)
        if legacy_extras:
            primary["extra_overlays"] = legacy_extras
        primary["spec_results"] = spec_results
        primary["analysis_spec_id"] = analysis_spec["id"]
        return primary

    def _attach_spec_colors(
        self,
        cached_geometry: dict[str, Any],
        effective_specs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Re-stamp per-spec colours and per-fragment instance overrides
        onto a geometry payload pulled from the bundle cache. The
        geometry dict is shared across colour changes; we copy a small
        wrapper so the renderer's painter cache (keyed on the colour
        tuple) doesn't get polluted by stale values."""
        color_by_id = {spec["id"]: spec.get("color", "#7C5CBF") for spec in effective_specs}
        overrides_by_id: dict[str, dict[str, dict[str, Any]]] = {
            spec["id"]: dict(spec.get("instance_overrides") or {}) for spec in effective_specs
        }
        spec_results = []
        for entry in cached_geometry.get("spec_results", []) or []:
            spec_id = entry.get("spec_id")
            recoloured = dict(entry)
            recoloured["color"] = color_by_id.get(spec_id, "#7C5CBF")
            spec_overrides = overrides_by_id.get(spec_id) or {}
            if spec_overrides:
                # Patch each overlay with its per-fragment override (if
                # any). The override key is the fragment label; we copy
                # the overlay dict so the cached geometry stays clean.
                new_overlays = []
                for overlay in entry.get("overlays") or []:
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
            spec_results.append(recoloured)
        out = dict(cached_geometry)
        out["spec_results"] = spec_results
        # Drop any painter caches the renderer attached to a sibling
        # colour permutation -- the new wrapper starts clean.
        out.pop("_background_dict_cache", None)
        out.pop("_foreground_dict_cache", None)
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
                )
            except ValueError as exc:
                raise ApiError(str(exc), status_code=400) from exc
        if center_species is not None or ligand_species is not None:
            if not center_species:
                fragment = self._display_fragment(scene, center_index)
                center_species = (fragment or {}).get("formula") or (fragment or {}).get("species")
            state["topology_enabled"] = True
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

