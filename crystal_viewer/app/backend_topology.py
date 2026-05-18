from __future__ import annotations
# ruff: noqa: F401,F403,F405

from collections import OrderedDict

from .shared import *
from .normalizers import *
from .rightclick import _normalize_polyhedron_specs


_TOPOLOGY_CACHE_LIMIT = 8
_DISORDER_CENTER_DEDUPE_TOL = 0.75


def _pbc_distance_for_bundle(bundle: LoadedCrystal, frac_a, frac_b) -> float:
    return float(
        minimum_image_distance(
            np.array(frac_b, dtype=float),
            np.array(frac_a, dtype=float),
            np.array(bundle.M, dtype=float),
        )
    )


def map_display_fragment_to_topology_for_bundle(
    bundle: LoadedCrystal,
    display_fragment: dict | None,
) -> Optional[dict[str, Any]]:
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
        ranked.append(
            (
                _pbc_distance_for_bundle(
                    bundle,
                    display_frac,
                    fragment.get("frac_center", [0.0, 0.0, 0.0]),
                ),
                fragment,
            )
        )
    ranked.sort(key=lambda item: item[0])
    return ranked[0][1]


def compute_topology_geometry_payload(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    return compute_topology_geometry(
        bundle=payload["bundle"],
        scene=payload["scene"],
        effective_specs=payload["effective_specs"],
        site_index=int(payload["site_index"]),
        cutoff=float(payload["cutoff"]),
    )


def _fragment_minor_score(scene: dict[str, Any], fragment: dict[str, Any]) -> tuple[bool, int, int]:
    atoms = scene.get("draw_atoms") or []
    flags = []
    for site_idx in fragment.get("site_indices") or []:
        try:
            atom = atoms[int(site_idx)]
        except (TypeError, ValueError, IndexError):
            continue
        flags.append(bool(atom.get("is_minor", atom.get("_is_minor", False))))
    minor_count = sum(1 for flag in flags if flag)
    return (
        bool(flags) and minor_count == len(flags),
        minor_count,
        int(fragment.get("index", 0) or 0),
    )


def _dedupe_disorder_center_fragments(
    bundle,
    scene: dict[str, Any],
    fragments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse PART/orientation alternatives that share a molecular centre.

    Disorder alternatives can appear as separate display fragments with
    near-identical centres. Rendering all of them tiles overlapping
    polyhedra. Keep the best representative, preferring the major
    orientation, while leaving genuinely distinct molecules alone.
    """
    representatives: list[dict[str, Any]] = []
    for fragment in fragments:
        formula = fragment.get("formula") or fragment.get("species")
        frac = fragment.get("frac_center", [0.0, 0.0, 0.0])
        duplicate_at: int | None = None
        for index, representative in enumerate(representatives):
            rep_formula = representative.get("formula") or representative.get("species")
            if rep_formula != formula:
                continue
            distance = _pbc_distance_for_bundle(
                bundle,
                frac,
                representative.get("frac_center", [0.0, 0.0, 0.0]),
            )
            if distance <= _DISORDER_CENTER_DEDUPE_TOL:
                duplicate_at = index
                break
        if duplicate_at is None:
            representatives.append(fragment)
            continue
        current = representatives[duplicate_at]
        if _fragment_minor_score(scene, fragment) < _fragment_minor_score(scene, current):
            representatives[duplicate_at] = fragment
    return representatives


def _overlay_drawable_hull_size(overlay: dict[str, Any]) -> int:
    shell = overlay.get("shell_coords") or []
    hull = overlay.get("hull") or {}
    simplices = hull.get("simplices") or []
    return len(shell) if len(shell) >= 4 and len(simplices) > 0 else 0


def compute_topology_geometry(
    *,
    bundle,
    scene: dict[str, Any],
    effective_specs: list[dict[str, Any]],
    site_index: int,
    cutoff: float,
) -> Optional[dict[str, Any]]:
    display_fragment = next(
        (fragment for fragment in scene.get("fragment_table", []) if int(fragment["index"]) == int(site_index)),
        None,
    )
    topology_fragment = map_display_fragment_to_topology_for_bundle(bundle, display_fragment)
    if topology_fragment is None:
        return None

    center_to_spec_indices: dict[str, list[int]] = {}
    for index, spec in enumerate(effective_specs):
        center_to_spec_indices.setdefault(spec["center_species"], []).append(index)

    primary_display_index = int(display_fragment["index"]) if display_fragment else None
    primary_formula = (
        (display_fragment.get("formula") or display_fragment.get("species"))
        if display_fragment else None
    )
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

    spec_results: list[dict[str, Any]] = []
    legacy_extras: list[dict[str, Any]] = []
    warnings: list[str] = []
    for index, spec in enumerate(effective_specs):
        center_species = spec["center_species"]
        ligand = spec.get("ligand_species") or None
        ligand_arg = [ligand] if ligand else None
        enforce_enclosure = bool(spec.get("enforce_enclosure", True))
        centroid_offset_frac = float(spec.get("centroid_offset_frac", DEFAULT_CENTROID_OFFSET_FRAC))
        overlays: list[dict[str, Any]] = []
        candidate_fragments = [
            frag
            for frag in (scene.get("fragment_table") or [])
            if (frag.get("formula") or frag.get("species")) == center_species
        ]
        center_fragments = _dedupe_disorder_center_fragments(bundle, scene, candidate_fragments)
        for frag in center_fragments:
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
            mapped = map_display_fragment_to_topology_for_bundle(bundle, frag)
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
        drawable_count = sum(1 for overlay in overlays if _overlay_drawable_hull_size(overlay))
        if center_fragments and drawable_count == 0:
            max_shell = max((len(overlay.get("shell_coords") or []) for overlay in overlays), default=0)
            mode = "Gap + enclosure" if enforce_enclosure else "Gap only"
            warnings.append(
                f"{spec.get('name') or center_species}: no drawable polyhedron for "
                f"{center_species} -> {ligand or '(auto)'} ({mode}); "
                f"largest shell has {max_shell} ligand point(s), need at least 4 non-coplanar points."
            )
        elif len(center_fragments) < len(candidate_fragments):
            warnings.append(
                f"{spec.get('name') or center_species}: collapsed "
                f"{len(candidate_fragments) - len(center_fragments)} overlapping disorder/PART centre(s)."
            )

    primary = dict(primary)
    if legacy_extras:
        primary["extra_overlays"] = legacy_extras
    primary["spec_results"] = spec_results
    if warnings:
        primary["warnings"] = warnings
    primary["analysis_spec_id"] = analysis_spec["id"]
    return primary


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
    ) -> Optional[dict[str, Any]]:
        if not state.get("topology_enabled", False):
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
        return {
            "structure": structure,
            "bundle": bundle,
            "scene": scene,
            "effective_specs": effective_specs,
            "site_index": int(site_index),
            "cutoff": cutoff,
            "cache_key": cache_key,
        }

    def topology_for_state_sync(
        self,
        state: dict[str, Any],
        click_data: Optional[dict[str, Any]] = None,
        *,
        strict: bool = False,
    ):
        context = self._topology_context(state, click_data=click_data, strict=strict)
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
        return self._attach_spec_colors(cached_geometry, context["effective_specs"])

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
    ):
        context = self._topology_context(state, click_data=click_data, strict=strict)
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
        return self._attach_spec_colors(cached_geometry, context["effective_specs"])

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
            overrides_raw = spec.get("instance_overrides") or {}
            overrides_tuple = tuple(
                (
                    str(label),
                    str((overrides_raw.get(label) or {}).get("color") or ""),
                    bool((overrides_raw.get(label) or {}).get("visible", True)),
                )
                for label in sorted(overrides_raw.keys())
            )
            items.append((spec_id, color, overrides_tuple))
        return tuple(items)

    def _attach_spec_colors(
        self,
        cached_geometry: dict[str, Any],
        effective_specs: list[dict[str, Any]],
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

