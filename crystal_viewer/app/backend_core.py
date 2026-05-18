from __future__ import annotations
# ruff: noqa: F401,F403,F405

import atexit
from collections import OrderedDict

from .shared import *
from .normalizers import *
from .camera_helpers import *
from .style_helpers import *
from .rightclick import _normalize_polyhedron_specs
from .render_worker import AsyncRenderWorker


class _CoreBackendMixin:
    def __init__(self, preset_path: str, names: Optional[Iterable[str]] = None, root_dir: Optional[str] = None):
        self.root_dir = root_dir or WORKSPACE_DIR
        self.preset_path = preset_path
        self.preset = load_preset(preset_path) if os.path.exists(preset_path) else default_preset()
        self.server_started_at = time.time()
        self.catalog = get_default_catalog(root_dir=self.root_dir)
        self._lock = threading.Lock()
        self._bundle_lock = threading.Lock()
        default_names = [name for name in DEFAULT_CATALOG.keys() if name in self.catalog]
        requested_names = [name for name in (names or []) if name in self.catalog]
        self.structure_names = requested_names if requested_names else default_names
        if not self.structure_names:
            self.structure_names = list(self.catalog.keys())
        self.bundles: Dict[str, LoadedCrystal] = {}
        self.upload_manifest_path = os.path.join(self.root_dir, LOCAL_STATE_DIRNAME, "crystal_view_uploads.json")
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
                f"[crystal_viewer] dropped {len(removed_scene_ids)} stored scene(s) "
                f"referencing unknown structures: {removed_scene_ids}",
                file=sys.stderr,
            )
        self.scene_store.ensure(self.structure_names, default_state_factory=self.default_state)
        scene_store_after = json.dumps(
            _json_safe(self.scene_store.list()),
            sort_keys=True,
            separators=(",", ":"),
        )
        if removed_scene_ids or scene_store_after != scene_store_before:
            try:
                self.scene_store.save()
            except OSError as exc:  # pragma: no cover - disk-full / read-only mount
                print(f"[crystal_viewer] could not persist scene store: {exc}", file=sys.stderr)
        if self.scene_store.active_id:
            self.current_state = self.scene_state(self.scene_store.active_id)
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
        atexit.register(self.flush_scene_store)
        self._intent_lock = threading.Lock()
        self._intent_seq_by_client: dict[str, int] = {}

    def default_state(self, structure: str) -> dict[str, Any]:
        bundle = self.get_bundle(structure)
        scene = bundle.scene
        style = dict(DEFAULT_STYLE)
        style.update(scene.get("style", {}))
        preset_style = self.preset.get("style", {})
        entry_style = self.preset.get("structures", {}).get(structure, {}).get("style", {})
        style.update(preset_style)
        style.update(entry_style)
        if scene.get("has_minor") and "minor_wireframe" not in preset_style and "minor_wireframe" not in entry_style:
            style["minor_wireframe"] = True
        # Default selected polyhedron centres: every non-halide species in
        # the structure. That generalises the old "B-site default" without
        # baking ABX nomenclature into the UI, and gives the multi-species
        # tiling view "for free" -- e.g. DAP-4 ships with one polyhedron
        # around the NH4+ centre and one around each DABCO ring.
        species_present = self._species_summary(scene.get("fragment_table") or [])
        anion_only = {"Cl", "Br", "I", "F"}
        non_anion = [
            item for item in species_present
            if not (set(item["elements"]) and set(item["elements"]).issubset(anion_only | {"O"}))
        ]
        if non_anion:
            default_species = [item["formula"] for item in non_anion]
        elif species_present:
            default_species = [species_present[0]["formula"]]
        else:
            default_species = []
        return {
            "structure": structure,
            "atom_scale": float(style["atom_scale"]),
            "bond_radius": float(style["bond_radius"]),
            "minor_opacity": float(style["minor_opacity"]),
            "material": str(style.get("material", "mesh")),
            "style": str(style.get("style", "ball_stick")),
            "disorder": str(style.get("disorder", "outline_rings")),
            "ortep_mode": str(style.get("ortep_mode", "ortep_axes")),
            "axis_scale": float(style["axis_scale"]),
            "display_options": _display_options_from_style(style),
            "label_mode": str(style.get("label_mode", "unique_sites")),
            "display_mode": style.get("display_mode", scene.get("display_mode", "formula_unit")),
            "topology_species_keys": list(default_species),
            "topology_site_index": None,
            "topology_enabled": False,
            "topology_hull_color": str(style.get("topology_hull_color", "#7C5CBF")),
            # ``polyhedron_specs`` is the new (Phase 1) per-scene named-row
            # data model: each entry is {id, name, center_species,
            # ligand_species, color, enabled}. Empty list = fall back to the
            # legacy ``topology_species_keys`` + shared ``topology_hull_color``
            # behaviour (auto-derived neighbour types). See
            # ``agents/polyhedron_api.md`` for the API surface.
            "polyhedron_specs": [],
            # Phase 2: per-scene atom-group rules. Each entry is
            # {id, name, selector, color, color_light, visible, opacity,
            # material, style}. Selectors are ANDed across keys; the
            # supported keys are ``all``, ``elements`` (list),
            # ``is_minor``, and the Phase-4 additions ``labels``,
            # ``atom_indices``, ``fragment_labels``, ``fragment_indices``.
            # Multiple groups apply in list order with later-wins
            # semantics on overlapping atoms. Empty list = no overrides;
            # the legacy ``monochrome`` flag is still honoured when no
            # atom_groups are present. See ``agents/atom_groups_api.md``.
            "atom_groups": [],
            # Phase 4: per-scene bond-group rules. Each entry is
            # {id, name, selector, color, visible, opacity,
            # radius_scale}. Selectors support ``all``,
            # ``between_elements`` (unordered), ``labels`` (atom-pair
            # ids), and ``is_minor``. Empty list = render bonds as the
            # endpoint atoms dictate. See ``agents/bond_groups_api.md``.
            "bond_groups": [],
            # Phase 4: list of structure-mutation transforms. See
            # ``crystal_viewer.transforms`` and
            # ``agents/transforms_api.md`` for the schema. Empty list =
            # no transform; ``apply_transforms`` short-circuits.
            "transforms": [],
            # Manual 2D overlay placement overrides. Paper-anchored
            # entries move viewport components such as the compass; world-
            # anchored entries store a target plus pixel offset so labels
            # reproject when the camera changes.
            "overlay_overrides": [],
            "fast_rendering": bool(style.get("fast_rendering", False)),
            "camera": scene.get("camera"),
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

    def broadcast_figure(
        self,
        *,
        scene_id: Optional[str],
        figure: dict[str, Any],
        topology_data: Optional[dict[str, Any]] = None,
        state: Optional[dict[str, Any]] = None,
        reason: str = "render-ready",
    ) -> dict[str, Any]:
        if not self._figure_payload_has_scene3d(figure):
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
        with self._figure_broadcast_lock:
            self._figure_broadcast_seq += 1
            payload = {
                "type": "figure",
                "figure_seq": self._figure_broadcast_seq,
                "figure_version": self.version,
                "version": self.version,
                "scene_id": scene_id,
                "reason": reason,
                "figure": figure,
                "state": copy.deepcopy(state) if isinstance(state, dict) else None,
                "topology": copy.deepcopy(topology_data) if isinstance(topology_data, dict) else None,
            }
            self._figure_broadcasts.append(payload)
            self._figure_broadcasts = self._figure_broadcasts[-32:]
            return payload

    @staticmethod
    def _figure_state_cache_key(state: dict[str, Any]) -> str:
        key_state = {
            k: v
            for k, v in state.items()
            if k not in ("version", "server_started_at", "camera")
        }
        return json.dumps(_json_safe(key_state), sort_keys=True, separators=(",", ":"))

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
            return self._figure_state_cache_key(state) == self._figure_state_cache_key(current)
        except Exception:
            return False

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

    def broadcast_render_error(self, *, scene_id: Optional[str], error: str) -> dict[str, Any]:
        with self._figure_broadcast_lock:
            self._figure_broadcast_seq += 1
            payload = {
                "type": "render_error",
                "figure_seq": self._figure_broadcast_seq,
                "version": self.version,
                "scene_id": scene_id,
                "error": error,
            }
            self._figure_broadcasts.append(payload)
            self._figure_broadcasts = self._figure_broadcasts[-32:]
            return payload

    def figure_broadcasts_since(self, seq: int = 0) -> list[dict[str, Any]]:
        with self._figure_broadcast_lock:
            return [
                copy.deepcopy(payload)
                for payload in self._figure_broadcasts
                if int(payload.get("figure_seq", 0) or 0) > int(seq)
            ]

    def latest_figure_broadcast(self) -> Optional[dict[str, Any]]:
        with self._figure_broadcast_lock:
            if not self._figure_broadcasts:
                return None
            return copy.deepcopy(self._figure_broadcasts[-1])

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
            print(f"[crystal_viewer] could not persist scene store: {exc}", file=sys.stderr)

    def apply_intent(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("intent payload must be an object")
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
        scene_id = payload.get("scene_id") or data.get("scene_id") or self.scene_store.active_id
        details: dict[str, Any] = {}

        if intent_type in {"set_style", "set_display_options"}:
            state = self.patch_state(data, scene_id=scene_id, broadcast=False)
        elif intent_type in {"patch_state", "apply_transform"}:
            state = self.patch_state(data, scene_id=scene_id)
        elif intent_type == "set_camera":
            camera = data.get("camera", data)
            patch = {"camera": camera}
            if "camera_revision" in data:
                patch["camera_revision"] = data["camera_revision"]
            state = self.patch_state(patch, scene_id=scene_id, broadcast=False)
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
                details["scene"] = self.update_scene(str(scene_id), {"label": data.get("label", "")})
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
            key = {
                "crud_polyhedron": "polyhedron_specs",
                "crud_atom_group": "atom_groups",
                "crud_bond_group": "bond_groups",
            }[intent_type]
            state = self.patch_state({key: data.get(key, data.get("items", []))}, scene_id=scene_id)
        elif intent_type == "upload_complete":
            state = self.get_state(scene_id)
            self.pending_state = copy.deepcopy(state)
        else:
            raise ValueError(f"unknown intent type: {intent_type}")

        return {
            "ok": True,
            "type": intent_type,
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
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.server_started_at))

    def healthz(self) -> dict[str, Any]:
        return {
            "ok": True,
            "uptime_s": max(0.0, time.time() - self.server_started_at),
            "server_started_at": self.server_started_iso(),
            "scenes": len(self.scene_store.scenes),
            "structures": len(self.structure_names),
            "version": self.version,
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
        os.makedirs(os.path.dirname(os.path.abspath(self.upload_manifest_path)), exist_ok=True)
        with open(self.upload_manifest_path, "w", encoding="utf-8") as handle:
            json.dump(self.upload_manifest, handle, indent=2, ensure_ascii=False)

    def _restore_uploaded_bundles(self) -> None:
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
            try:
                bundle = build_loaded_crystal(
                    name=name,
                    cif_path=path,
                    title=str(record.get("title") or name),
                    preset=self.preset,
                    source="upload",
                )
            except Exception:
                uploads.pop(digest, None)
                changed = True
                continue
            self.bundles[bundle.name] = bundle
            self.structure_names.append(bundle.name)
        if changed:
            try:
                self._save_upload_manifest()
            except OSError:
                pass

    def list_structures(self) -> list[dict[str, Any]]:
        return [self.get_bundle(name).metadata() for name in self.structure_names]

    def structure_options(self) -> list[dict[str, str]]:
        return [
            {
                "label": "Upload CIF to begin" if name == PLACEHOLDER_STRUCTURE else name,
                "value": name,
            }
            for name in self.structure_names
        ]

    def scene_options(self) -> list[dict[str, Any]]:
        return self.scene_store.list()

    def scene_tabs(self) -> list[Any]:
        tabs = []
        for scene in self.scene_store.list():
            tabs.append(
                dcc.Tab(
                    label=scene["label"],
                    value=scene["id"],
                    id=f"scene-tab-{scene['id']}",
                )
            )
        return tabs

    def scene_close_buttons(self) -> list[Any]:
        buttons = []
        for scene in self.scene_store.list():
            buttons.append(
                html.Button(
                    html.Span("\u00d7", id=f"scene-tab-close-{scene['id']}"),
                    id={"type": "tab-close", "scene_id": scene["id"]},
                    className="tab-close-x",
                    n_clicks=0,
                    title=f"Close {scene['label']}",
                )
            )
        return buttons

    def scene_state(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        scene = self.scene_store.get(scene_id)
        defaults = self.default_state(scene.structure_name)
        return scene.state(defaults)

    def active_scene_id(self) -> Optional[str]:
        return self.scene_store.active_id

    def create_scene(
        self,
        *,
        structure: Optional[str] = None,
        label: Optional[str] = None,
        state: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        structure = structure or self.get_state().get("structure") or (self.structure_names[0] if self.structure_names else PLACEHOLDER_STRUCTURE)
        if structure not in self.structure_names:
            raise KeyError(structure)
        base_state = self.default_state(structure)
        if state:
            base_state.update(self.normalize_state(state))
        requested_label = label or structure
        scene = self.scene_store.add(
            label=requested_label,
            structure_name=structure,
            state_patch=base_state,
            camera=base_state.get("camera"),
            save=False,
        )
        self.current_state = self.scene_state(scene.id)
        self.pending_state = copy.deepcopy(self.current_state)
        self._bump_version()
        payload = scene.to_dict()
        payload["requested_label"] = str(requested_label)
        payload["label_renamed"] = payload["label"] != str(requested_label)
        self._request_scene_store_save()
        return payload

    def update_scene(self, scene_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        scene = self.scene_store.get(scene_id)
        if "label" in payload and len(payload) == 1:
            scene = self.scene_store.rename(scene_id, payload["label"], save=False)
        else:
            patch = dict(payload)
            if "state" in patch:
                state_patch = patch.pop("state") or {}
                state_patch = self.normalize_state(state_patch, scene_id=scene_id)
                patch.update(state_patch)
            scene = self.scene_store.patch_scene(scene_id, patch, save=False)
        if self.scene_store.active_id == scene_id:
            self.current_state = self.scene_state(scene_id)
            self.pending_state = copy.deepcopy(self.current_state)
        self._bump_version()
        self._request_scene_store_save()
        return scene.to_dict()

    def delete_scene(self, scene_id: str) -> dict[str, Any]:
        removed = self.scene_store.remove(scene_id, save=False)
        if self.scene_store.active_id:
            self.current_state = self.scene_state(self.scene_store.active_id)
        self.pending_state = copy.deepcopy(self.current_state)
        self._bump_version()
        self._request_scene_store_save()
        return removed.to_dict()

    def delete_other_scenes(self, keep_id: str) -> dict[str, Any]:
        """Close every scene except ``keep_id``.

        Returns a summary ``{"kept": scene_dict, "removed": [scene_dict,
        ...]}`` so the UI / REST caller can show a status banner. The
        scene store is mutated in place; we only bump the version once
        at the end to avoid invalidating ``_figure_cache`` N times in a
        row when the user batch-closes many tabs.
        """
        keep_id = str(keep_id)
        if keep_id not in self.scene_store.scenes:
            raise KeyError(f"Unknown scene id: {keep_id}")
        removed: list[dict[str, Any]] = []
        for scene_id in [sid for sid in list(self.scene_store.scenes.keys()) if sid != keep_id]:
            removed.append(self.scene_store.remove(scene_id, save=False).to_dict())
        self.scene_store.active_id = keep_id
        self.current_state = self.scene_state(keep_id)
        self.pending_state = copy.deepcopy(self.current_state)
        if removed:
            self._bump_version()
            self._request_scene_store_save()
        return {"kept": self.scene_store.get(keep_id).to_dict(), "removed": removed}

    def duplicate_scene(self, scene_id: str, label: Optional[str] = None) -> dict[str, Any]:
        scene = self.scene_store.duplicate(scene_id, label=label, save=False)
        self.current_state = self.scene_state(scene.id)
        self.pending_state = copy.deepcopy(self.current_state)
        self._bump_version()
        self._request_scene_store_save()
        return scene.to_dict()

    def reorder_scenes(self, order: Iterable[str]) -> list[str]:
        order = self.scene_store.reorder(order, save=False)
        self._bump_version()
        self._request_scene_store_save()
        return order

    def set_active_scene(self, scene_id: str, *, broadcast: bool = True) -> dict[str, Any]:
        # ``broadcast`` controls whether ``pending_state`` is armed for
        # the next ``sync_agent_state`` poll. The REST API agent path
        # (``/api/v1/scenes/.../activate``) wants this so the browser
        # UI picks up the change. Dash callbacks that originate *from*
        # the same UI must pass ``broadcast=False``: otherwise they
        # echo the change back to themselves on the next poll tick,
        # which (a) re-runs every per-control callback (refresh
        # topology species, refresh fragment options, ...) and (b)
        # triggers a full ``update_view`` for nothing -- doubling the
        # 1 MB-per-frame transfer cost on every click that carries a
        # ``scene-tabs.value`` Input.
        scene = self.scene_store.set_active(scene_id, save=False)
        self.current_state = self.scene_state(scene.id)
        if broadcast:
            self.pending_state = copy.deepcopy(self.current_state)
        self._bump_version()
        self._request_scene_store_save()
        return scene.to_dict()

    @staticmethod
    def _species_summary(fragments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Group fragments by their stoichiometric ``formula`` (e.g. ``C8N1``,
        ``ClO4``, ``N1``) and return one summary per distinct species,
        sorted by heavy-atom count then occurrence count.

        This is the species-checkbox source of truth: each entry carries a
        ``formula`` (the stable selector value), a count, and the elements
        present so the UI can colour-code or filter without re-deriving
        from raw fragments."""
        by_formula: dict[str, dict[str, Any]] = {}
        for frag in fragments:
            formula = frag.get("formula") or frag.get("species") or "?"
            entry = by_formula.get(formula)
            if entry is None:
                entry = {
                    "formula": formula,
                    "count": 0,
                    "heavy": int(frag.get("heavy_atom_count", 0) or 0),
                    "elements": list(frag.get("elem_set") or []),
                }
                by_formula[formula] = entry
            entry["count"] += 1
        return sorted(by_formula.values(), key=lambda item: (item["heavy"], -item["count"]))

    def species_options(self, structure: Optional[str] = None) -> list[dict[str, Any]]:
        """Checklist options for the species-based polyhedron selector.

        One entry per stoichiometrically distinct fragment present in the
        currently displayed scene. Each entry's ``value`` is the formula
        string (used as a stable group key) and the ``label`` shows the
        formula together with how many sites it covers, so the user sees
        e.g. ``C8N1 \u00d72`` for the DABCO rings of DAP-4.
        """
        target = structure or (self.structure_names[0] if self.structure_names else None)
        if target is None or target not in self.bundles:
            return []
        scene = self.get_bundle(target).scene
        return [
            {
                "label": f"{item['formula']} \u00d7{item['count']}",
                "value": item["formula"],
            }
            for item in self._species_summary(scene.get("fragment_table") or [])
        ]

    def element_options(self, state: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        """Distinct element symbols present in the active scene's
        ``draw_atoms``. Used by the Phase 3 atom-group editor's
        "by element" picker so the user can pick from real elements
        rather than typing free-form symbols.

        Returns a list of ``{"label": "O", "value": "O"}`` dicts in
        the order elements first appear in the scene (so e.g. for a
        perovskite the cations come first, then the anions, matching
        the user's mental model).
        """
        state = state or self.get_state()
        try:
            scene = self.scene_for_state(state)
        except Exception:
            return []
        seen: dict[str, None] = {}
        for atom in scene.get("draw_atoms") or []:
            elem = str(atom.get("elem") or "").strip()
            if elem and elem not in seen:
                seen[elem] = None
        return [{"label": elem, "value": elem} for elem in seen]

    def fragment_options(self, state: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        """Dropdown options for the right-panel "Analyze fragment" selector.

        One entry per fragment in the current scene. The ``value`` is the
        fragment index (matching what ``topology_site_index`` already
        used), the ``label`` is the human-readable id + formula. Crucially
        this list is *not* filtered by the species checkboxes -- the user
        can tile only ClO4 polyhedra and still ask the right panel to
        analyse a C6N2 fragment, which is the "decouple display from
        analysis" UX the user asked for.
        """
        state = state or self.get_state()
        try:
            scene = self.scene_for_state(state)
        except Exception:
            return []
        options: list[dict[str, Any]] = []
        for frag in scene.get("fragment_table") or []:
            label = frag.get("label") or f"#{frag['index']}"
            formula = frag.get("formula") or frag.get("species") or ""
            text = f"{label}  \u00b7  {formula}" if formula else str(label)
            options.append({"label": text, "value": int(frag["index"])})
        return options

    def _drop_placeholder(self) -> None:
        if PLACEHOLDER_STRUCTURE in self.structure_names and len(self.structure_names) == 1:
            self.structure_names = []
        self.bundles.pop(PLACEHOLDER_STRUCTURE, None)

    def get_bundle(self, name: str) -> LoadedCrystal:
        if name in self.bundles:
            return self.bundles[name]
        if name not in self.catalog:
            raise KeyError(name)

        entry = self.catalog[name]
        built = build_loaded_crystal(
            name=name,
            cif_path=entry["cif_path"],
            title=entry["title"],
            preset=self.preset,
            source="catalog",
        )

        with self._bundle_lock:
            existing = self.bundles.get(name)
            if existing is not None:
                return existing
            self.bundles[name] = built
            return built

    def get_scene_json(self, name: str, *, after_transforms: bool = False) -> dict[str, Any]:
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

    def normalize_state(self, patch: Optional[dict[str, Any]], scene_id: Optional[str] = None) -> dict[str, Any]:
        if scene_id is not None:
            state = self.scene_state(scene_id)
        else:
            state = copy.deepcopy(self.current_state)
        patch = patch or {}

        def _display_signature(value: dict[str, Any]) -> tuple[str, bool, bool]:
            return (
                str(value.get("display_mode", "")),
                "unit_cell_box" in (value.get("display_options") or []),
                bool(value.get("topology_enabled", False)),
            )
        if "scene_id" in patch and patch["scene_id"] in self.scene_store.scenes:
            scene_id = str(patch["scene_id"])
            state = self.scene_state(scene_id)
        if "structure" in patch and patch["structure"] in self.structure_names:
            structure = patch["structure"]
            defaults = self.default_state(structure)
            state.update(defaults)
            state["structure"] = structure
        if scene_id is not None:
            state["scene_id"] = scene_id
            scene = self.scene_store.get(scene_id)
            state["scene_label"] = scene.label
        display_signature_before = _display_signature(state)
        for key in ("atom_scale", "bond_radius", "minor_opacity", "axis_scale", "cutoff"):
            if key in patch and patch[key] is not None:
                state[key] = float(patch[key])
        for key in ("material", "style", "disorder", "ortep_mode", "label_mode"):
            if key in patch and patch[key] is not None:
                state[key] = str(patch[key])
        if state.get("style") == "ortep" and "display_mode" not in patch:
            state["display_mode"] = "asymmetric_unit"
        if "display_options" in patch and patch["display_options"] is not None:
            state["display_options"] = list(patch["display_options"])
        if "display_mode" in patch and patch["display_mode"] is not None:
            state["display_mode"] = str(patch["display_mode"])
            if "topology_site_index" not in patch:
                state["topology_site_index"] = None
        if "topology_species_keys" in patch:
            value = patch["topology_species_keys"]
            if value is None:
                state["topology_species_keys"] = []
            else:
                state["topology_species_keys"] = [str(item) for item in value if item is not None]
        # Legacy A/B/X selection: translate the type to the matching list of
        # species formulas in the active scene so existing /api/v1 callers (and
        # the example scripts shipped under scripts/) keep working without
        # learning the new species-checkbox vocabulary.
        if patch.get("topology_fragment_type"):
            requested_type = str(patch["topology_fragment_type"])
            structure = state.get("structure")
            if structure and structure in self.bundles:
                fragments = self.get_bundle(structure).scene.get("fragment_table") or []
                matched = {
                    f.get("formula") or f.get("species")
                    for f in fragments
                    if f.get("type") == requested_type
                }
                state["topology_species_keys"] = [k for k in matched if k]
        if patch.get("topology_show_all_sites") and not state.get("topology_species_keys"):
            structure = state.get("structure")
            if structure and structure in self.bundles:
                fragments = self.get_bundle(structure).scene.get("fragment_table") or []
                state["topology_species_keys"] = sorted(
                    {f.get("formula") or f.get("species") for f in fragments if f.get("formula") or f.get("species")}
                )
        if "topology_site_index" in patch:
            value = patch["topology_site_index"]
            state["topology_site_index"] = None if value in ("", None) else int(value)
        if "topology_enabled" in patch:
            state["topology_enabled"] = bool(patch["topology_enabled"])
        if "topology_hull_color" in patch and patch["topology_hull_color"]:
            state["topology_hull_color"] = str(patch["topology_hull_color"])
        if "polyhedron_specs" in patch:
            # Empty list is a valid override (= "drop all named specs and
            # fall back to legacy topology_species_keys"); ``None`` means
            # the same. Treat both uniformly.
            state["polyhedron_specs"] = _normalize_polyhedron_specs(
                patch.get("polyhedron_specs") or [],
                fallback_color=state.get("topology_hull_color", "#7C5CBF"),
            )
        if "atom_groups" in patch:
            # Same semantics as polyhedron_specs: empty list / None
            # both mean "drop all overrides; use legacy monochrome
            # flag (if any) and element palette".
            state["atom_groups"] = _normalize_atom_groups(patch.get("atom_groups") or [])
        if "bond_groups" in patch:
            state["bond_groups"] = _normalize_bond_groups(patch.get("bond_groups") or [])
        if "transforms" in patch:
            state["transforms"] = _normalize_transforms(patch.get("transforms") or [])
        # ``supercell`` is a v2 shorthand: ``{"a": Na, "b": Nb, "c": Nc}``
        # is rewritten to a single ``repeat`` transform appended to the
        # transforms list. Keeps the AI scripting path one-line for the
        # most common "show me a 2x2x2" request without forcing the
        # caller to construct a transform spec.
        if "supercell" in patch and patch["supercell"] is not None:
            sc = patch["supercell"]
            try:
                a = max(1, int(sc.get("a", 1) if isinstance(sc, dict) else sc[0]))
                b = max(1, int(sc.get("b", 1) if isinstance(sc, dict) else sc[1]))
                c = max(1, int(sc.get("c", 1) if isinstance(sc, dict) else sc[2]))
            except (TypeError, ValueError, KeyError, IndexError):
                a = b = c = 1
            existing = list(state.get("transforms") or [])
            # Always replace any existing repeat transform from a previous
            # supercell shorthand call instead of stacking; otherwise the AI
            # ends up with [repeat 2x2x2, repeat 3x3x3] and the user gets a
            # 6x6x6. ``{1,1,1}`` therefore acts as "clear the supercell".
            existing = [t for t in existing if t.get("kind") != "repeat"]
            if (a, b, c) != (1, 1, 1):
                existing_ids = {t["id"] for t in existing}
                normalized = _normalize_transform(
                    {"kind": "repeat", "params": {"a": a, "b": b, "c": c}, "name": f"Repeat {a}x{b}x{c}"},
                    existing_ids=existing_ids,
                )
                if normalized is not None:
                    existing.append(normalized)
            state["transforms"] = existing
        if "fast_rendering" in patch:
            state["fast_rendering"] = bool(patch["fast_rendering"])
        # ---- legacy migration: monochrome=True --> atom_group rule ----
        #
        # Old presets / agent scripts may still set ``monochrome=True``
        # on the display options (or via ``"monochrome"`` in
        # ``display_options``). Promote that to a single all-atoms
        # black ``atom_group`` so the renderer has a single source of
        # truth and the legacy flag becomes inert. Idempotent: we skip
        # if the user already has any explicit colour rule.
        wants_mono = False
        if "display_options" in patch:
            wants_mono = "monochrome" in (state.get("display_options") or [])
        existing_groups = list(state.get("atom_groups") or [])
        has_explicit_color_rule = any(g.get("color") for g in existing_groups)
        if wants_mono and not has_explicit_color_rule:
            existing_ids = {g["id"] for g in existing_groups}
            migrated = _legacy_monochrome_group(existing_ids)
            if migrated is not None:
                state["atom_groups"] = existing_groups + [migrated]
        display_signature_after = _display_signature(state)
        if (
            any(key in patch for key in ("display_mode", "display_options", "topology_enabled"))
            and display_signature_after != display_signature_before
        ):
            # Plotly cameras live in the normalized scene cube. Reusing one
            # after a display-signature change remaps the eye through a new
            # cube scale and makes the model look squished.
            state["camera"] = None
            if "camera_revision" not in patch:
                try:
                    state["camera_revision"] = int(state.get("camera_revision", 0) or 0) + 1
                except (TypeError, ValueError):
                    state["camera_revision"] = 1
        if "camera" in patch and patch["camera"] is not None:
            state["camera"] = patch["camera"]
        # ``camera_revision`` is the uirevision-bump counter written by
        # ``camera_action`` / ``align_camera``. ``normalize_state``
        # whitelists keys, so without an explicit pass-through the
        # bump silently drops on the floor and Plotly keeps clamping
        # the figure to whatever rotation the user drag-saved last.
        if "camera_revision" in patch and patch["camera_revision"] is not None:
            try:
                state["camera_revision"] = int(patch["camera_revision"])
            except (TypeError, ValueError):
                pass
        # Phase 4 (view tools): top-level ``projection`` is a v2 state
        # key that mirrors ``camera.projection.type``. Accept either
        # spelling so AI callers don't have to dig into the camera
        # dict; ``set_projection`` keeps the two in sync.
        if "projection" in patch and patch["projection"] is not None:
            state["projection"] = _coerce_projection(
                patch["projection"], fallback=str(state.get("projection", "perspective"))
            )
        elif isinstance(patch.get("camera"), dict):
            cam_proj = patch["camera"].get("projection")
            if isinstance(cam_proj, dict) and "type" in cam_proj:
                state["projection"] = _coerce_projection(
                    cam_proj["type"], fallback=str(state.get("projection", "perspective"))
                )
        return state

    def get_state(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        with self._lock:
            if scene_id is not None:
                state = copy.deepcopy(self.scene_state(scene_id))
            else:
                state = copy.deepcopy(self.current_state)
            state["server_started_at"] = self.server_started_iso()
            state["version"] = self.version
            return state

    def patch_state(
        self,
        patch: Optional[dict[str, Any]],
        scene_id: Optional[str] = None,
        *,
        broadcast: bool = True,
    ) -> dict[str, Any]:
        # ``broadcast`` controls whether ``pending_state`` is armed for
        # the next ``sync_agent_state`` poll. REST/WS callers want this
        # so the browser UI picks up the change. Dash callbacks that
        # originate *from* the same UI (``capture_camera`` in
        # particular) must pass ``broadcast=False``: otherwise the next
        # 5 s poll echoes that camera back into ``camera-state-store``,
        # ``update_view`` rebuilds with the stale-by-debounce camera
        # value, and the user sees the view "snap back" to where the
        # last ``relayoutData`` left it. The same logic applies to any
        # other UI-originated patch where the browser is already
        # authoritative for the field being changed.
        with self._lock:
            target_scene_id = scene_id or (patch or {}).get("scene_id") or self.scene_store.active_id
            self.current_state = self.normalize_state(patch, scene_id=target_scene_id)
            if target_scene_id:
                scene_payload = copy.deepcopy(self.current_state)
                scene_payload.pop("scene_id", None)
                scene_payload.pop("scene_label", None)
                self.scene_store.patch_scene(target_scene_id, scene_payload, save=False)
            if broadcast:
                self.pending_state = copy.deepcopy(self.current_state)
            self._bump_version()
            state = copy.deepcopy(self.current_state)
            state["version"] = self.version
            state["server_started_at"] = self.server_started_iso()
            self._request_scene_store_save()
            return state

    def pop_pending_state(self) -> Optional[dict[str, Any]]:
        with self._lock:
            pending = self.pending_state
            self.pending_state = None
            return copy.deepcopy(pending) if pending else None

    def record_state(self, patch: Optional[dict[str, Any]], scene_id: Optional[str] = None) -> None:
        with self._lock:
            target_scene_id = scene_id or (patch or {}).get("scene_id") or self.scene_store.active_id
            self.current_state = self.normalize_state(patch, scene_id=target_scene_id)
            if target_scene_id:
                scene_payload = copy.deepcopy(self.current_state)
                scene_payload.pop("scene_id", None)
                scene_payload.pop("scene_label", None)
                self.scene_store.patch_scene(target_scene_id, scene_payload, save=False)
            self._bump_version()
            self._request_scene_store_save()

    def show_hydrogen_for_state(self, state: Optional[dict[str, Any]] = None) -> bool:
        state = self.current_state if state is None else state
        return "hydrogens" in set(state.get("display_options", []))

    def scene_for_state(self, state: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        state = self.current_state if state is None else state
        bundle = self.get_bundle(state["structure"])
        # Phase 4: ``state["transforms"]`` is the structure-mutation
        # pipeline. ``build_bundle_scene`` short-circuits when the list
        # is empty so the no-transform path stays a single dict lookup.
        transforms = list(state.get("transforms") or [])
        scene = build_bundle_scene(
            bundle,
            display_mode=state.get("display_mode", "formula_unit"),
            show_hydrogen=self.show_hydrogen_for_state(state),
            preset=self.preset,
            transforms=transforms,
        )
        bundle.scene = scene
        bundle.fragment_table = scene.get("fragment_table", bundle.fragment_table)
        return scene

    def style_for_state(self, state: Optional[dict[str, Any]] = None, scene: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        state = self.current_state if state is None else state
        scene = self.scene_for_state(state) if scene is None else scene
        style = dict(scene.get("style", {}))
        style.update(
            style_from_controls(
                state["atom_scale"],
                state["bond_radius"],
                state["minor_opacity"],
                state["axis_scale"],
                state["display_options"],
                material=state.get("material"),
                render_style=state.get("style"),
                disorder=state.get("disorder"),
                ortep_mode=state.get("ortep_mode"),
            )
        )
        style["display_mode"] = state.get("display_mode", scene.get("display_mode", "formula_unit"))
        style["material"] = state.get("material", style.get("material", "mesh"))
        style["style"] = state.get("style", style.get("style", "ball_stick"))
        style["disorder"] = state.get("disorder", style.get("disorder", "outline_rings"))
        style["ortep_mode"] = state.get("ortep_mode", style.get("ortep_mode", "ortep_axes"))
        style["label_mode"] = state.get("label_mode", style.get("label_mode", "unique_sites"))
        style["fast_rendering"] = bool(state.get("fast_rendering", False)) or style["material"] == "flat"
        style["topology_enabled"] = bool(state.get("topology_enabled", False))
        style["topology_hull_color"] = str(state.get("topology_hull_color", "#7C5CBF"))
        # Phase 2: per-scene atom-group rules ride along on the style
        # dict so the renderer dispatcher can partition draw_atoms by
        # (effective_material, effective_style) without touching the
        # backend layer. Renderer reads ``style["atom_groups"]`` only
        # if the list is non-empty; the legacy ``monochrome`` flag is
        # otherwise honoured untouched.
        style["atom_groups"] = list(state.get("atom_groups") or [])
        # Phase 4: bond-group rules ride on the style dict so the
        # renderer's bond pipeline (``_bond_segments``) can decorate
        # each bond with ``_render_color`` / ``_render_visible`` /
        # ``_render_opacity_scale`` / ``_render_radius_scale``. The
        # tagging itself happens in ``figure_for_state`` (where the
        # scene is mutable); this entry is the single source of truth
        # for downstream callers.
        style["bond_groups"] = list(state.get("bond_groups") or [])
        # Phase 4 (view tools): persist the camera projection choice
        # onto the style dict so the renderer's
        # ``_plotly_camera_from_scene`` picks orthographic vs.
        # perspective without rebuilding the scene.
        style["projection"] = _coerce_projection(
            state.get("projection", style.get("projection", "perspective")),
            fallback=str(style.get("projection", "perspective")),
        )
        if isinstance(state.get("camera"), dict):
            style["camera"] = copy.deepcopy(state["camera"])
        # Plotly's ``layout.scene.uirevision`` makes the WebGL camera
        # state persist across redraws -- reusing the same revision
        # means a mouse-drag rotation survives a Labels toggle. The
        # flip side: when the user clicks Reset / down-a / down-b /
        # ... the layout's new camera is silently ignored unless the
        # revision changes. ``camera_revision`` (bumped by
        # ``camera_action`` and ``align_camera``) gives the renderer
        # exactly that signal: Reset triggers a fresh revision so
        # Plotly accepts the new camera, while pan/orbit updates that
        # flow through ``patch_state`` directly leave it untouched.
        #
        # We also bake ``display_mode`` / ``unit_cell_box`` /
        # ``topology_enabled`` into the uirevision: those toggles change
        # the *scene cube extent* (``unit_cell`` mode owns the box and
        # all polyhedra extras, the others don't — see ``_scene_ranges``).
        # If the same revision string covered an old state with a 25 Å
        # cube and a new state with a 12 Å cube, Plotly would silently
        # "preserve UI state" for the cube too, which is what made the
        # molecule look squashed after switching back to ``formula_unit``.
        layout_signature = "{mode}|box={box}|topo={topo}".format(
            mode=str(state.get("display_mode", scene.get("display_mode", ""))),
            box=int(bool("unit_cell_box" in (state.get("display_options") or []))),
            topo=int(bool(state.get("topology_enabled", False))),
        )
        style["uirevision"] = "{name}__{rev}__{sig}".format(
            name=scene.get("name", "scene"),
            rev=int(state.get("camera_revision", 0) or 0),
            sig=layout_signature,
        )
        # The interactive Dash app paints the corner compass via
        # ``compass_overlay.js`` into a sibling SVG layer. Skip baking
        # the same compass into ``layout.annotations`` / ``layout.shapes``
        # here so that JS does not have to keep stripping them on every
        # figure rebuild (each strip would cost a ``Plotly.relayout``
        # which freezes the gl3d render -- see commentary at the top
        # of ``crystal_viewer/assets/compass_overlay.js`` and the
        # ``axis_key_overlay`` docstring). Static export pipelines
        # (``cube.export_static``, ``scripts/``) build their own style
        # without this flag and keep the baked compass for kaleido.
        style["axis_key_via_svg_overlay"] = True
        return style

