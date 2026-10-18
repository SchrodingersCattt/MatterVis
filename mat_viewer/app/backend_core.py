from __future__ import annotations

# ruff: noqa: F401
import atexit
from collections import OrderedDict

from ..cache_keys import figure_state_cache_key
from ..config import current_config
from ..structure.bonds import inorganic_solid_profile
from ..topology.analysis import _MAX_REGISTERED_CN
from .camera_helpers import *
from .normalizers import *
from .reducer import LEGACY_STYLE_STATE_FIELDS
from .render_worker import AsyncRenderWorker
from .rightclick import _normalize_polyhedron_specs
from .selectors import show_hydrogen, viewport_signature
from .shared import *
from .style_helpers import *
from .view_updates import (
    UpdateKind,
    classify_change,
    display_state_key,
    geometry_state_key,
    make_update,
    state_versions,
    update_applies,
)
from .wire import json_safe_wire

# Maximum number of distinct figure cache entries.  A bounded LRU
# (least-recently-used) eviction policy kicks in when the cache
# exceeds this limit; the oldest entry is dropped.  16 entries
# comfortably covers typical tab-switch + display-mode + slider
# patterns without unbounded memory growth.
FIGURE_CACHE_MAX = 16
# Ionic and extended inorganic lattices are read more clearly with a thin
# coordination stroke.  Keep the value in state construction so callers can
# still override it through a preset or an explicit state patch.
INORGANIC_DEFAULT_BOND_RADIUS = 0.12


class _CoreBackendMixin:
    def __init__(
        self,
        preset_path: str,
        names: Optional[Iterable[str]] = None,
        root_dir: Optional[str] = None,
    ):
        self.root_dir = root_dir or WORKSPACE_DIR
        self.preset_path = preset_path
        self.preset = (
            load_preset(preset_path)
            if os.path.exists(preset_path)
            else default_preset()
        )
        self.server_started_at = time.time()
        self.catalog = get_default_catalog(root_dir=self.root_dir)
        self._lock = threading.Lock()
        # Upload requests reserve their digest/name before the background
        # parser starts.  Keeping these maps on the backend makes concurrent
        # multipart requests idempotent and gives the browser a durable way to
        # observe parser failures instead of waiting forever on a blank tab.
        self._upload_jobs: dict[str, dict[str, Any]] = {}
        self._upload_reservations: dict[str, dict[str, Any]] = {}
        self._upload_reserved_names: set[str] = set()
        self._upload_completion_events: dict[str, threading.Event] = {}
        self._bundle_lock = threading.Lock()
        default_names = [
            name for name in DEFAULT_CATALOG.keys() if name in self.catalog
        ]
        requested_names = [name for name in (names or []) if name in self.catalog]
        self.structure_names = requested_names if requested_names else default_names
        if not self.structure_names:
            self.structure_names = list(self.catalog.keys())
        self.bundles: Dict[str, LoadedCrystal] = {}
        self.upload_manifest_path = os.path.join(
            self.root_dir, LOCAL_STATE_DIRNAME, "crystal_view_uploads.json"
        )
        self.upload_manifest = self._load_upload_manifest()
        self._restore_uploaded_bundles()
        if not self.structure_names:
            placeholder = build_empty_bundle(name=PLACEHOLDER_STRUCTURE)
            self.bundles[placeholder.name] = placeholder
            self.structure_names = [placeholder.name]
        first_name = self.structure_names[0]
        self.current_state = self.default_state(first_name)
        self.scene_store = SceneStore.load(SceneStore.default_path(self.root_dir))
        # Persisted scenes can outlive the catalog (uploads land in
        # ``tempfile.gettempdir()`` and get GC'd; ``--cif`` may have
        # been dropped). Without prune, ``scene_state(active_id)``
        # below dereferences an unknown ``structure_name`` and crashes
        # the entire app at startup with a blank page.
        scene_store_before = json.dumps(
            _json_safe(self.scene_store.list()),
            sort_keys=True,
            separators=(",", ":"),
        )
        removed_scene_ids = self.scene_store.prune(self.structure_names)
        if removed_scene_ids:
            print(
                f"[mat_viewer] dropped {len(removed_scene_ids)} stored scene(s) "
                f"referencing unknown structures: {removed_scene_ids}",
                file=sys.stderr,
            )
        self.scene_store.ensure(
            self.structure_names, default_state_factory=self.default_state
        )
        scene_store_after = json.dumps(
            _json_safe(self.scene_store.list()),
            sort_keys=True,
            separators=(",", ":"),
        )
        if removed_scene_ids or scene_store_after != scene_store_before:
            try:
                self.scene_store.save()
            except OSError as exc:  # pragma: no cover - disk-full / read-only mount
                print(
                    f"[mat_viewer] could not persist scene store: {exc}",
                    file=sys.stderr,
                )
        if self.scene_store.active_id:
            self.current_state = self.scene_state(self.scene_store.active_id)
        self._render_revisions: dict[str, int] = {
            str(scene_id): 0 for scene_id in self.scene_store.scenes
        }
        # Delivery identity is separate from the persisted scene id. A
        # generation changes whenever a scene is created in this server
        # lifetime, so a late frame cannot become valid for a replacement
        # scene that happens to reuse an id.
        self._scene_generation_counter = 0
        self._scene_generations: dict[str, int] = {}
        for scene_id in self.scene_store.order:
            self._scene_generation_counter += 1
            self._scene_generations[str(scene_id)] = self._scene_generation_counter
        self._camera_revisions: dict[str, int] = {
            str(scene_id): int(self.scene_state(scene_id).get("camera_revision", 0) or 0)
            for scene_id in self.scene_store.scenes
        }
        self._last_update_kind_by_scene: dict[str, str] = {}
        self.pending_state: Optional[dict[str, Any]] = None
        self._first_figure_ready = threading.Event()
        self.version = 0
        self._figure_cache_lock = threading.Lock()
        self._figure_cache: OrderedDict[str, tuple[Any, Any]] = OrderedDict()
        self._figure_broadcast_lock = threading.Lock()
        self._figure_broadcast_seq = 0
        self._figure_broadcasts: list[dict[str, Any]] = []
        self._render_worker = AsyncRenderWorker(self)
        self._persist_event = threading.Event()
        self._persist_stop = threading.Event()
        self._persist_thread = threading.Thread(
            target=self._persist_scene_store_loop,
            name="mattervis-scene-store-persist",
            daemon=True,
        )
        self._persist_thread.start()
        self._close_lock = threading.Lock()
        self._closed = False
        self._atexit_flush_callback = self.flush_scene_store
        atexit.register(self._atexit_flush_callback)
        self._intent_lock = threading.Lock()
        self._intent_seq_by_client: dict[str, int] = {}
        self.refit_offscreen_cameras()

    def default_state(
        self,
        structure: str,
        *,
        preset: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Build a fresh state from a preset source.

        ``preset`` is intentionally injectable for the Display Defaults
        action.  Normal scene creation keeps using the loaded preset (which
        may contain user customisations), while that action passes the
        shipped/config-backed :func:`default_preset` so it cannot
        accidentally restore the very customised preset the user is trying
        to clear.
        """
        bundle = self.get_bundle(structure)
        scene = bundle.scene
        effective_preset = self.preset if preset is None else preset
        style = dict(DEFAULT_STYLE)
        # A bundle's scene style was materialised when it was loaded and may
        # already contain values from the user's persisted preset.  When an
        # explicit preset source is supplied (the Restore Defaults action),
        # skip that baked style so it cannot leak custom values back in.
        if preset is None:
            style.update(scene.get("style", {}))
        preset_style = effective_preset.get("style", {})
        entry_style = (
            effective_preset.get("structures", {}).get(structure, {}).get("style", {})
        )
        style.update(preset_style)
        style.update(entry_style)
        # Default selected polyhedron centres: every non-halide species in
        # the structure. That generalises the old "B-site default" without
        # baking ABX nomenclature into the UI, and gives the multi-species
        # tiling view "for free" -- e.g. DAP-4 ships with one polyhedron
        # around the NH4+ centre and one around each DABCO ring.
        species_present = self._species_summary(scene.get("fragment_table") or [])
        anion_only = {"Cl", "Br", "I", "F"}
        non_anion = [
            item
            for item in species_present
            if not (
                set(item["elements"])
                and set(item["elements"]).issubset(anion_only | {"O"})
            )
        ]
        if non_anion:
            default_species = [item["formula"] for item in non_anion]
        elif species_present:
            default_species = [species_present[0]["formula"]]
        else:
            default_species = []
        # Ionic and extended inorganic cells (NaCl, CsCl, CaF2, rutile, and
        # perovskites) are lattice views rather than isolated molecules.  The
        # chemistry profile is conservative about carbon-containing frameworks
        # so MOFs and molecular salts keep their existing defaults.
        # Scene metadata is normally stamped by the loader. Recompute the
        # conservative inorganic profile from canonical raw atoms when a
        # legacy/cache scene predates those fields; otherwise such structures
        # silently fall back to the molecular formula-unit presentation.
        raw_profile = (
            inorganic_solid_profile(bundle.raw_atoms)
            if "is_inorganic_solid" not in scene
            else {}
        )
        ionic = bool(scene.get("is_ionic_solid", False))
        inorganic = bool(
            scene.get("is_inorganic_solid", False)
            or raw_profile.get("is_inorganic_solid", False)
        )
        inorganic_cations = list(
            scene.get("inorganic_cation_elements")
            or raw_profile.get("inorganic_cation_elements", ())
        )
        inorganic_anions = list(
            scene.get("inorganic_anion_elements")
            or raw_profile.get("inorganic_anion_elements", ())
        )
        lattice_default = ionic or inorganic
        default_display_mode = style.get(
            "display_mode", scene.get("display_mode", "formula_unit")
        )
        if (
            lattice_default
            and "display_mode" not in entry_style
            and str(style.get("display_mode", "formula_unit")) == "formula_unit"
        ):
            default_display_mode = "unit_cell"
        default_style = str(style.get("style", "ball_stick"))
        if (
            lattice_default
            and "style" not in entry_style
            and str(style.get("style", "ball_stick")) == "ball_stick"
        ):
            default_style = "ball"
        display_options = _display_options_from_style(style)
        if lattice_default and "unit_cell_box" not in display_options:
            display_options.append("unit_cell_box")
        if (
            lattice_default
            and "bond_radius" not in entry_style
            and float(style.get("bond_radius", DEFAULT_STYLE["bond_radius"]))
            == float(DEFAULT_STYLE["bond_radius"])
        ):
            style["bond_radius"] = INORGANIC_DEFAULT_BOND_RADIUS
        # ``bundle.scene`` is built once at load time in formula-unit mode so
        # the canonical cache always has a compact molecular view available.
        # Do not carry that camera into a lattice default: it is fitted to the
        # formula-unit bounds and can make a unit-cell scene look like a
        # clipped vertical rod (especially for layered hydroxides such as
        # brucite). Leaving the camera unset lets ``scene_for_state`` supply
        # the camera generated from the requested unit-cell scene; an
        # explicit user camera is still persisted by the normal state path.
        default_camera = None if lattice_default else scene.get("camera")
        ionic_specs = []
        if lattice_default:
            cations = list(
                scene.get("ionic_cation_elements")
                or inorganic_cations
                or []
            )
            anions = list(
                scene.get("ionic_anion_elements")
                or inorganic_anions
                or []
            )
            for index, cation in enumerate(cations):
                if not anions:
                    break
                ligand = anions[0]
                ionic_specs.append(
                    {
                        "id": "ionic-coordination" if index == 0 else f"ionic-coordination-{index + 1}",
                        "name": f"{cation}–{ligand} coordination",
                        "center_species": cation,
                        "ligand_species": ligand,
                        "level": "atom",
                        "enabled": True,
                        # Keep the default shell inside the registered
                        # polyhedron catalogue. Small periodic cells can
                        # expose hundreds of image neighbours; allowing the
                        # enclosure heuristic to consume all of them makes
                        # shape classification combinatorial.
                        "fallback_max": _MAX_REGISTERED_CN,
                    }
                )
        return {
            "structure": structure,
            "atom_scale": float(style["atom_scale"]),
            "bond_radius": float(style["bond_radius"]),
            "minor_opacity": float(style["minor_opacity"]),
            "material": str(style.get("material", "mesh")),
            "style": default_style,
            "disorder": str(style.get("disorder", "opacity")),
            "ortep_mode": str(style.get("ortep_mode", "ortep_axes")),
            "axis_scale": float(style["axis_scale"]),
            "display_options": display_options,
            "label_mode": str(style.get("label_mode", "unique_sites")),
            "display_mode": default_display_mode,
            "topology_species_keys": list(default_species),
            "topology_site_index": None,
            "topology_enabled": bool(ionic_specs),
            "topology_hull_color": str(style.get("topology_hull_color", "#7C5CBF")),
            # ``polyhedron_specs`` is the new (Phase 1) per-scene named-row
            # data model: each entry is {id, name, center_species,
            # ligand_species, color, enabled}. Empty list = fall back to the
            # legacy ``topology_species_keys`` + shared ``topology_hull_color``
            # behaviour (auto-derived neighbour types). See
            # ``docs/agents/polyhedron_api.md`` for the API surface.
            "polyhedron_specs": ionic_specs,
            # Phase 2: per-scene atom-group rules. Each entry is
            # {id, name, selector, color, color_light, visible, opacity,
            # material, style}. Selectors are ANDed across keys; the
            # supported keys are ``all``, ``elements`` (list),
            # ``is_minor``, and the Phase-4 additions ``labels``,
            # ``atom_indices``, ``fragment_labels``, ``fragment_indices``.
            # Multiple groups apply in list order with later-wins
            # semantics on overlapping atoms. Empty list = no overrides;
            # the legacy ``monochrome`` flag is still honoured when no
            # atom_groups are present. See ``docs/agents/atom_groups_api.md``.
            "atom_groups": [],
            # Phase 4: per-scene bond-group rules. Each entry is
            # {id, name, selector, color, visible, opacity,
            # radius_scale}. Selectors support ``all``,
            # ``between_elements`` (unordered), ``labels`` (atom-pair
            # ids), and ``is_minor``. Empty list = render bonds as the
            # endpoint atoms dictate. See ``docs/agents/bond_groups_api.md``.
            "bond_groups": [],
            # Optional continuous per-atom colour field.  The stored shape is
            # the public AtomPropertyColorSpec serialized to JSON.
            "atom_property_color": None,
            # Phase 4: list of structure-mutation transforms. See
            # ``mat_viewer.transforms`` and
            # ``docs/agents/transforms_api.md`` for the schema. Empty list =
            # no transform; ``apply_transforms`` short-circuits.
            "transforms": [],
            "disorder_resolve": {"method": "enumerate", "count": 5, "seed": None},
            "disorder_replicas": [],
            # Manual 2D overlay placement overrides. Paper-anchored
            # entries move viewport components such as the compass; world-
            # anchored entries store a target plus pixel offset so labels
            # reproject when the camera changes.
            "overlay_overrides": [],
            "bfdh_morphology": None,
            "bfdh_morphology_color": "#4f7cff",
            "selection": {"atom_labels": [], "active_label": None, "order": []},
            "fast_rendering": bool(style.get("fast_rendering", False)),
            "camera": default_camera,
            "geometry_version": 0,
            "display_version": 0,
            "camera_version": 0,
            "camera_revision": 0,
            # Phase 4: camera projection mode mirrored onto state so a
            # caller can inspect / set it via REST without diffing the
            # Plotly camera dict. ``style_for_state`` propagates this
            # to ``style["projection"]`` so the renderer picks it up.
            "projection": _coerce_projection(
                style.get("projection", "perspective"),
                fallback="perspective",
            ),
            "cutoff": 10.0,
        }


    def restore_display_defaults(
        self,
        scene_id: Optional[str] = None,
        *,
        broadcast: bool = True,
    ) -> dict[str, Any]:
        """Restore shipped/config display defaults for one active scene.

        Scene identity and structural operations remain attached to the
        current tab.  The loaded preset is deliberately ignored: it is a
        persisted user customisation, whereas the button promises the
        effective shipped/config defaults.  Returning the committed state
        lets Dash update all controls in one transaction.
        """
        current = self.get_state(scene_id)
        target_scene_id = scene_id or current.get("scene_id") or self.active_scene_id()
        structure = str(current.get("structure") or "")
        defaults = self.default_state(structure, preset=default_preset())

        # Operations and source identity belong to the scene, not to its
        # display skin.  Keep those durable fields while replacing every
        # display/editor value (including custom atom/bond/polyhedron rules).
        preserved = {
            key: copy.deepcopy(current.get(key))
            for key in (
                "scene_id",
                "scene_label",
                "structure",
                "transforms",
                "disorder_resolve",
                "disorder_replicas",
                "vector_overlays",
                "cell_overlays",
                "overlay_overrides",
                "selection",
                "bfdh_morphology",
                "bfdh_morphology_color",
            )
            if key in current
        }
        replacement = {**defaults, **preserved}
        replacement["structure"] = structure

        # Defaults from the source scene are valid for ordinary scenes.  For
        # a transformed tab, ask the scene builder for its effective camera
        # after preserving the transform operation rather than retaining a
        # zoomed/rotated camera from the customised state.
        candidate = self.normalize_state(replacement, scene_id=target_scene_id)
        try:
            candidate_scene = self.scene_for_state(candidate)
            if isinstance(candidate_scene.get("camera"), dict):
                candidate["camera"] = copy.deepcopy(candidate_scene["camera"])
        except Exception:
            # Camera fitting is a convenience; a malformed optional
            # transform must not prevent the display controls from resetting.
            candidate["camera"] = copy.deepcopy(defaults.get("camera"))
        return self.patch_state(candidate, scene_id=target_scene_id, broadcast=broadcast)


    def _bump_version(self):
        # ``_figure_cache`` is keyed on the *content* of the state dict
        # (scene_id, structure, every render-affecting field), so two
        # different cached figures never collide even though the global
        # version counter has moved on. Earlier revisions cleared the
        # whole cache on every bump, which made every tab switch and
        # every slider tick force a full 400+ ms ``build_figure``. We
        # only clear when the underlying structure catalog itself
        # changes (see ``_invalidate_figure_cache``).
        self.version += 1

    def _invalidate_figure_cache(self) -> None:
        """Drop every cached figure.

        Call this when a *bundle* underlying a cached scene mutates
        (new upload, structure deleted, preset reload) -- ``cache_key``
        won't change but the cached fig now refers to stale geometry.
        """
        with self._figure_cache_lock:
            self._figure_cache.clear()

    def reload_bond_policy(self) -> None:
        """Rebuild loaded bundles after a global MCK policy update."""
        from .bond_policy import reload_bond_policy

        reload_bond_policy(self, current_config())

    def broadcast_figure(
        self,
        *,
        scene_id: Optional[str],
        figure: dict[str, Any],
        topology_data: Optional[dict[str, Any]] = None,
        state: Optional[dict[str, Any]] = None,
        reason: str = "render-ready",
    ) -> dict[str, Any]:
        # Retain image-backed flat/ORTEP for HTTP fallback in the same journal;
        # the WS 3D fast lane may continue to ignore it.
        flat_ortep = (
            isinstance(state, dict)
            and isinstance(figure, dict)
            and state.get("material") == "flat"
            and state.get("style") == "ortep"
            and bool((figure.get("layout") or {}).get("images"))
        )
        if not self._figure_payload_has_scene3d(figure) and not flat_ortep:
            return {
                "type": "figure_ignored",
                "scene_id": scene_id,
                "reason": "missing-3d-scene",
            }
        if not self._figure_state_matches_current(scene_id, state):
            return {
                "type": "figure_ignored",
                "scene_id": scene_id,
                "reason": "stale-state",
            }
        if not self._figure_revision_matches_current(scene_id, state):
            return {
                "type": "figure_ignored",
                "scene_id": scene_id,
                "reason": "stale-render-revision",
            }
        # ``topology_data`` contains renderer-only painter caches with tuple
        # keys, and Plotly can expose NaN sentinels in trace arrays.  A frame
        # is also delivered through Dash's strict JSON encoder, so sanitize
        # once at the shared boundary before journaling it for WS/HTTP.
        figure = json_safe_wire(figure)
        topology_data = json_safe_wire(topology_data, drop_private=True)
        with self._figure_broadcast_lock:
            self._figure_broadcast_seq += 1
            payload = {
                "type": "figure",
                "figure_seq": self._figure_broadcast_seq,
                "figure_version": self.version,
                "version": self.version,
                "scene_id": scene_id,
                "scene_generation": self.scene_generation(scene_id),
                "render_revision": self.render_revision(scene_id),
                "reason": reason,
                "figure": figure,
                "state": json_safe_wire(copy.deepcopy(state))
                if isinstance(state, dict)
                else None,
                "topology": topology_data if isinstance(topology_data, dict) else None,
            }
            versions = state_versions(state or (self.get_state(scene_id) if scene_id in self.scene_store.scenes else {}))
            payload.update(versions.to_dict())
            payload["update_kind"] = UpdateKind.GEOMETRY.value
            self._figure_broadcasts.append(payload)
            self._figure_broadcasts = self._figure_broadcasts[-32:]
            return payload

    def broadcast_view_update(
        self,
        update: Any,
        *,
        state: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Publish a camera/display/overlay patch through the same journal.

        The browser's single submitter consumes this envelope; no figure is
        attached, so an Axes or camera action can never accidentally trigger a
        full Plotly frame.
        """
        payload = update.to_dict() if hasattr(update, "to_dict") else dict(update)
        payload = json_safe_wire(payload)
        scene_id = payload.get("scene_id")
        current = state or self.get_state(scene_id)
        if not update_applies(payload, current):
            return {"type": "view_update_ignored", "reason": "stale-update", **payload}
        with self._figure_broadcast_lock:
            self._figure_broadcast_seq += 1
            payload["figure_seq"] = self._figure_broadcast_seq
            payload["version"] = self.version
            payload["state"] = json_safe_wire(copy.deepcopy(current))
            payload["scene_generation"] = self.scene_generation(scene_id)
            self._figure_broadcasts.append(payload)
            self._figure_broadcasts = self._figure_broadcasts[-32:]
        return payload

    @staticmethod
    def _geometry_cache_key(state: dict[str, Any]) -> str:
        return geometry_state_key(state)

    @staticmethod
    def _display_state_key(state: dict[str, Any]) -> str:
        return display_state_key(state)

    @staticmethod
    def geometry_cache_key(state: dict[str, Any]) -> str:
        return geometry_state_key(state)

    @staticmethod
    def final_display_state_key(state: dict[str, Any]) -> str:
        return display_state_key(state)

    @staticmethod
    def _figure_state_cache_key(state: dict[str, Any]) -> str:
        return figure_state_cache_key(state)

    def _figure_state_matches_current(
        self,
        scene_id: Optional[str],
        state: Optional[dict[str, Any]],
    ) -> bool:
        if not isinstance(state, dict) or not scene_id:
            return True
        try:
            current = self.get_state(scene_id)
        except Exception:
            return False
        try:
            return self._figure_state_cache_key(state) == self._figure_state_cache_key(
                current
            )
        except Exception:
            return False

    def render_revision(self, scene_id: Optional[str] = None) -> int:
        target = scene_id or self.scene_store.active_id
        return int(self._render_revisions.get(str(target), 0)) if target else 0

    def scene_generation(self, scene_id: Optional[str] = None) -> int:
        target = scene_id or self.scene_store.active_id
        return int(self._scene_generations.get(str(target), 0)) if target else 0

    def _state_snapshot(
        self,
        state: dict[str, Any],
        scene_id: Optional[str] = None,
    ) -> dict[str, Any]:
        snapshot = copy.deepcopy(state)
        target = scene_id or snapshot.get("scene_id") or self.scene_store.active_id
        snapshot["server_started_at"] = self.server_started_iso()
        snapshot["version"] = self.version
        snapshot["render_revision"] = self.render_revision(target)
        snapshot["scene_generation"] = self.scene_generation(target)
        return snapshot

    def _stamp_figure_render_metadata(
        self,
        figure: Any,
        state: dict[str, Any],
    ) -> Any:
        render_meta = {
            "scene_id": state.get("scene_id"),
            "scene_generation": self.scene_generation(state.get("scene_id")),
            "camera_revision": int(state.get("camera_revision", 0) or 0),
            "render_revision": int(
                state.get(
                    "render_revision", self.render_revision(state.get("scene_id"))
                )
                or 0
            ),
            "server_started_at": state.get("server_started_at")
            or self.server_started_iso(),
        }
        render_meta.update(state_versions(state).to_dict())
        if isinstance(figure, dict):
            layout = figure.setdefault("layout", {})
            meta = layout.get("meta")
            meta = dict(meta) if isinstance(meta, dict) else {}
            meta["mattervis_render"] = render_meta
            layout["meta"] = meta
            return figure
        try:
            meta = figure.layout.meta
            meta = dict(meta) if isinstance(meta, dict) else {}
            meta["mattervis_render"] = render_meta
            figure.update_layout(meta=meta)
        except Exception:
            pass
        return figure

    def _figure_revision_matches_current(
        self,
        scene_id: Optional[str],
        state: Optional[dict[str, Any]],
    ) -> bool:
        if not isinstance(state, dict) or not scene_id:
            return True
        try:
            if "scene_generation" in state and int(state.get("scene_generation", 0) or 0) != self.scene_generation(scene_id):
                return False
            return (
                int(state.get("render_revision", -1)) == self.render_revision(scene_id)
                and int(state.get("camera_revision", 0) or 0)
                == int(self.get_state(scene_id).get("camera_revision", 0) or 0)
            )
        except (KeyError, TypeError, ValueError):
            return False

    def _bump_render_revision_if_changed(
        self,
        scene_id: Optional[str],
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> None:
        if not scene_id:
            return
        before_state = dict(before)
        after_state = dict(after)
        scene = self.scene_store.get(scene_id)
        # ``before`` is captured before a rename mutates the store. Preserve
        # that old label so a title change advances the render revision and
        # the browser receives a fresh canvas figure.
        before_state.setdefault("scene_id", str(scene_id))
        before_state.setdefault("scene_label", scene.label)
        after_state.update({"scene_id": str(scene_id), "scene_label": scene.label})
        geometry_changed = self._geometry_cache_key(before_state) != self._geometry_cache_key(after_state)
        display_changed = self._display_state_key(before_state) != self._display_state_key(after_state)
        if geometry_changed or display_changed:
            key = str(scene_id)
            self._render_revisions[key] = self._render_revisions.get(key, 0) + 1

    @staticmethod
    def _figure_payload_has_scene3d(figure: Any) -> bool:
        if not isinstance(figure, dict):
            return False
        layout = figure.get("layout")
        if not isinstance(layout, dict) or not isinstance(layout.get("scene"), dict):
            return False
        data = figure.get("data") or []
        if not isinstance(data, list):
            return False
        return any(
            isinstance(trace, dict)
            and str(trace.get("type") or "").lower() in {"mesh3d", "scatter3d", "cone"}
            for trace in data
        )

    def broadcast_render_error(
        self, *, scene_id: Optional[str], error: str
    ) -> dict[str, Any]:
        state = self.get_state(scene_id)
        with self._figure_broadcast_lock:
            self._figure_broadcast_seq += 1
            payload = {
                "type": "render_error",
                "figure_seq": self._figure_broadcast_seq,
                "version": self.version,
                "scene_id": scene_id,
                "scene_generation": self.scene_generation(scene_id),
                "error": error,
            }
            payload.update(state_versions(state).to_dict())
            self._figure_broadcasts.append(payload)
            self._figure_broadcasts = self._figure_broadcasts[-32:]
            return payload

    def figure_broadcasts_since(self, seq: int = 0) -> list[dict[str, Any]]:
        with self._figure_broadcast_lock:
            candidates = [copy.deepcopy(payload) for payload in self._figure_broadcasts
                          if int(payload.get("figure_seq", 0) or 0) > int(seq)]
        return [
            payload for payload in candidates
            if self._figure_revision_matches_current(
                payload.get("scene_id"), payload.get("state")
            )
            and self._figure_state_matches_current(
                payload.get("scene_id"), payload.get("state")
            )
        ]

    def latest_figure_broadcast(self, scene_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        with self._figure_broadcast_lock:
            candidates = [copy.deepcopy(payload) for payload in reversed(self._figure_broadcasts)
                          if payload.get("type") == "figure"
                          and (scene_id is None or payload.get("scene_id") == scene_id)]
        for payload in candidates:
            if self._figure_revision_matches_current(
                payload.get("scene_id"), payload.get("state")
            ) and self._figure_state_matches_current(
                payload.get("scene_id"), payload.get("state")
            ):
                return payload
        return None

    def latest_figure_seq(self) -> int:
        with self._figure_broadcast_lock:
            return self._figure_broadcast_seq

    def _request_scene_store_save(self) -> None:
        try:
            self.scene_store.mark_dirty()
        except Exception:
            pass
        self._persist_event.set()

    def _persist_scene_store_loop(self) -> None:
        while not self._persist_stop.is_set():
            self._persist_event.wait(timeout=1.0)
            if self._persist_stop.is_set():
                break
            if not self._persist_event.is_set():
                continue
            self._persist_event.clear()
            time.sleep(0.5)
            if self._persist_event.is_set():
                continue
            self.flush_scene_store()

    def flush_scene_store(self) -> None:
        if not getattr(self, "scene_store", None):
            return
        try:
            if self.scene_store.is_dirty():
                self.scene_store.save()
        except OSError as exc:  # pragma: no cover - disk-full / read-only mount
            print(f"[mat_viewer] could not persist scene store: {exc}", file=sys.stderr)

    def apply_intent(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("intent payload must be an object")
        base_version = payload.get("base_version")
        if base_version is not None:
            try:
                expected_version = int(base_version)
            except (TypeError, ValueError) as exc:
                raise ApiError("base_version must be an integer", status_code=409) from exc
            if expected_version != int(self.version):
                raise ApiError(
                    f"stale intent base_version {expected_version} != {self.version}",
                    status_code=409,
                )
        client_id = str(payload.get("client_id") or "default")
        if "client_seq" in payload and payload["client_seq"] is not None:
            client_seq = int(payload["client_seq"])
            with self._intent_lock:
                previous = self._intent_seq_by_client.get(client_id, -1)
                if client_seq <= previous:
                    raise ApiError(
                        f"out-of-order intent for client {client_id}: {client_seq} <= {previous}",
                        status_code=409,
                    )
                self._intent_seq_by_client[client_id] = client_seq
        intent_type = str(payload.get("type") or "")
        data = payload.get("payload") or {}
        if not isinstance(data, dict):
            raise ValueError("intent payload.payload must be an object")
        scene_id = (
            payload.get("scene_id")
            or data.get("scene_id")
            or self.scene_store.active_id
        )
        details: dict[str, Any] = {}

        # Phase 3 bridge: translate the wire envelope once, then use the pure
        # reducer to calculate the transition's invalidation facts.  The
        # existing normalizer/patch path remains responsible for compatibility
        # coercion and persistence during this migration phase.
        operation, invalidations = self._prepare_operation(payload, scene_id=scene_id)

        if intent_type in {"set_style", "set_display_options"}:
            state, invalidations = self.apply_operation(
                operation, scene_id=scene_id, broadcast=False
            )
        elif intent_type in {"patch_state", "apply_transform"}:
            state, invalidations = self.apply_operation(operation, scene_id=scene_id)
        elif intent_type == "set_camera":
            state, invalidations = self.apply_operation(
                operation, scene_id=scene_id, broadcast=False
            )
        elif intent_type == "set_active_scene":
            target = data.get("scene_id") or scene_id
            self.set_active_scene(str(target), broadcast=True)
            state = self.get_state(str(target))
        elif intent_type == "crud_scene":
            action = str(data.get("action") or "")
            if action == "duplicate":
                scene = self.duplicate_scene(str(scene_id))
                details["scene"] = scene
                state = self.get_state(scene["id"])
            elif action == "rename":
                details["scene"] = self.update_scene(
                    str(scene_id), {"label": data.get("label", "")}
                )
                state = self.get_state(str(scene_id))
            elif action == "delete":
                details["removed"] = self.delete_scene(str(scene_id))
                state = self.get_state()
            elif action == "delete_others":
                details.update(self.delete_other_scenes(str(scene_id)))
                state = self.get_state(str(scene_id))
            elif action == "reorder":
                self.reorder_scenes(data.get("order") or [])
                state = self.get_state(scene_id)
            else:
                raise ValueError(f"unknown crud_scene action: {action}")
        elif intent_type in {"crud_polyhedron", "crud_atom_group", "crud_bond_group"}:
            state, invalidations = self.apply_operation(operation, scene_id=scene_id)
        elif intent_type == "upload_complete":
            state = self.get_state(scene_id)
            self.pending_state = copy.deepcopy(state)
        else:
            raise ValueError(f"unknown intent type: {intent_type}")

        return {
            "ok": True,
            "type": intent_type,
            "operation": operation.name,
            "invalidations": sorted(item.value for item in invalidations),
            "version": self.version,
            "state": state,
            **details,
        }

    def wait_for_version(self, version: int, *, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while self.version < int(version):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        return True

    def server_started_iso(self) -> str:
        return time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.localtime(self.server_started_at)
        )

    def healthz(self) -> dict[str, Any]:
        from .build_info import asset_sha, build_sha

        return {
            "ok": True,
            "uptime_s": max(0.0, time.time() - self.server_started_at),
            "server_started_at": self.server_started_iso(),
            "scenes": len(self.scene_store.scenes),
            "structures": len(self.structure_names),
            "version": self.version,
            "build_sha": build_sha(),
            "asset_sha": asset_sha(),
        }

    def _load_upload_manifest(self) -> dict[str, Any]:
        if not os.path.exists(self.upload_manifest_path):
            return {"version": 1, "uploads": {}}
        try:
            with open(self.upload_manifest_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return {"version": 1, "uploads": {}}
        if not isinstance(payload, dict):
            return {"version": 1, "uploads": {}}
        payload.setdefault("version", 1)
        payload.setdefault("uploads", {})
        return payload

    def _save_upload_manifest(self) -> None:
        os.makedirs(
            os.path.dirname(os.path.abspath(self.upload_manifest_path)), exist_ok=True
        )
        with open(self.upload_manifest_path, "w", encoding="utf-8") as handle:
            json.dump(self.upload_manifest, handle, indent=2, ensure_ascii=False)

    def _restore_uploaded_bundles(self) -> None:
        """Register previously-uploaded structures into ``self.catalog``
        for lazy loading via :meth:`get_bundle`."""
        uploads = self.upload_manifest.get("uploads") or {}
        changed = False
        for digest, record in list(uploads.items()):
            if not isinstance(record, dict):
                uploads.pop(digest, None)
                changed = True
                continue
            name = str(record.get("name") or "")
            path = str(record.get("path") or "")
            if not name or not path or not os.path.exists(path):
                uploads.pop(digest, None)
                changed = True
                continue
            if name in self.structure_names:
                continue
            # Register into catalog for lazy loading via get_bundle().
            self.catalog[name] = {
                "cif_path": path,
                "title": str(record.get("title") or name),
                "source": "upload",
            }
            self.structure_names.append(name)
        if changed:
            try:
                self._save_upload_manifest()
            except OSError:
                pass

    def list_structures(self) -> list[dict[str, Any]]:
        result = []
        for name in self.structure_names:
            if name in self.bundles:
                result.append(self.bundles[name].metadata())
            else:
                entry = self.catalog.get(name, {})
                result.append(
                    {
                        "name": name,
                        "title": entry.get("title", name),
                        "source": entry.get("source", "catalog"),
                        "loaded": False,
                    }
                )
        return result

    def structure_options(self) -> list[dict[str, str]]:
        return [
            {
                "label": "Upload CIF to begin"
                if name == PLACEHOLDER_STRUCTURE
                else name,
                "value": name,
            }
            for name in self.structure_names
        ]

    def _drop_placeholder(self) -> None:
        # The placeholder is a bootstrap scene as well as a catalog entry.
        # Removing only the bundle left a stale blank tab behind after the
        # first upload (and made later tab labels appear to be lost).  Drop it
        # whenever a real structure is present, while preserving it when the
        # viewer is genuinely empty.
        has_real_structure = any(
            name != PLACEHOLDER_STRUCTURE for name in self.structure_names
        )
        if not has_real_structure:
            return
        self.structure_names = [
            name for name in self.structure_names if name != PLACEHOLDER_STRUCTURE
        ]
        self.bundles.pop(PLACEHOLDER_STRUCTURE, None)
        for scene_id, scene in list(self.scene_store.scenes.items()):
            if scene.structure_name == PLACEHOLDER_STRUCTURE:
                self.scene_store.remove(scene_id, save=False)
                self._render_revisions.pop(str(scene_id), None)
        if self.scene_store.active_id:
            self.current_state = self.scene_state(self.scene_store.active_id)
        self._request_scene_store_save()

    def get_bundle(self, name: str) -> LoadedCrystal:
        if name in self.bundles:
            return self.bundles[name]
        if name not in self.catalog:
            raise KeyError(name)

        entry = self.catalog[name]
        source = entry.get("source", "catalog")
        built = build_loaded_crystal(
            name=name,
            cif_path=entry["cif_path"],
            title=entry.get("title") or entry.get("name", name),
            preset=self.preset,
            source=source,
        )

        with self._bundle_lock:
            existing = self.bundles.get(name)
            if existing is not None:
                return existing
            self.bundles[name] = built
            return built

    def get_scene_json(
        self, name: str, *, after_transforms: bool = False
    ) -> dict[str, Any]:
        state = self.get_state()
        if state["structure"] != name:
            state = self.normalize_state({"structure": name})
        if not after_transforms:
            state = dict(state)
            state["transforms"] = []
        bundle = self.get_bundle(name)
        scene = self.scene_for_state(state)
        return {
            "name": bundle.name,
            "title": bundle.title,
            "scene": scene_json(scene),
            "fragment_table": copy.deepcopy(scene.get("fragment_table", [])),
            "topology_fragment_table": copy.deepcopy(bundle.topology_fragment_table),
            "summary": _structure_summary(scene),
        }
