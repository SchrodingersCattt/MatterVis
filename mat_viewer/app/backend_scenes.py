from __future__ import annotations

# ruff: noqa: F401,F403,F405
import copy
from functools import wraps

from ..scenes.core import Scene
from .shared import *
from .view_updates import committed_render_kind


def _scene_transaction(method):
    """Serialize native lifecycle changes with extension commits."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            before = dict(self._scene_generations)
            result = method(self, *args, **kwargs)
            host = getattr(self, "_extension_structures", None)
            if host is not None:
                from ..extensions.structures import SceneEvent, SceneHandle
                for scene_id, generation in before.items():
                    if scene_id not in self._scene_generations:
                        host.writer.emit(SceneEvent("closed", SceneHandle(scene_id, generation)))
            return result
    return wrapped


class _SceneBackendMixin:
    def _record_scene_close(
        self,
        removed: list[dict[str, Any]],
        *,
        order: list[str],
        active_id: str | None,
    ) -> None:
        """Keep a bounded in-memory snapshot for the visible Undo action."""
        if not removed:
            return
        stack = getattr(self, "_scene_close_undo", None)
        if stack is None:
            stack = self._scene_close_undo = []
        stack.append(
            {
                "removed": copy.deepcopy(removed),
                "order": list(order),
                "active_id": active_id,
            }
        )
        del stack[:-8]

    @_scene_transaction
    def undo_scene_close(self) -> dict[str, Any]:
        """Restore the most recently closed scene set, if one exists."""
        stack = getattr(self, "_scene_close_undo", None) or []
        if not stack:
            return {"restored": [], "active_id": self.active_scene_id()}
        snapshot = stack.pop()
        restored: list[str] = []
        for payload in snapshot.get("removed") or []:
            scene_id = str(payload.get("id") or "")
            if not scene_id or scene_id in self.scene_store.scenes:
                continue
            scene = Scene.from_dict(payload)
            self.scene_store.scenes[scene.id] = scene
            restored.append(scene.id)
            self._render_revisions[str(scene.id)] = 0
            # Restoring a persisted id creates a new in-memory scene
            # generation.  Late frames from the pre-close instance must not
            # become valid for the restored scene merely because the id is
            # the same.
            self._scene_generation_counter += 1
            self._scene_generations[str(scene.id)] = self._scene_generation_counter
            restored_state = self.scene_state(scene.id)
            self._camera_revisions[str(scene.id)] = int(
                restored_state.get("camera_revision", 0) or 0
            )

        # Preserve any scenes created after the close by appending them after
        # the original order instead of dropping a newer upload or duplicate.
        original_order = [
            str(scene_id)
            for scene_id in snapshot.get("order") or []
            if str(scene_id) in self.scene_store.scenes
        ]
        trailing = [
            str(scene_id)
            for scene_id in self.scene_store.order
            if str(scene_id) not in original_order
        ]
        self.scene_store.order = original_order + trailing
        wanted_active = snapshot.get("active_id")
        if wanted_active in self.scene_store.scenes:
            self.scene_store.active_id = str(wanted_active)
        elif self.scene_store.active_id not in self.scene_store.scenes:
            self.scene_store.active_id = (
                self.scene_store.order[0] if self.scene_store.order else None
            )
        if self.scene_store.active_id:
            self.current_state = self.scene_state(self.scene_store.active_id)
            self.pending_state = self._state_snapshot(
                self.current_state, self.scene_store.active_id
            )
        self._bump_version()
        self._request_scene_store_save()
        if restored:
            self._publish_scene_catalog(focus=False)
        return {"restored": restored, "active_id": self.scene_store.active_id}

    def scene_catalog_seq(self) -> int:
        return int(getattr(self, "_scene_catalog_seq", 0) or 0)

    def scene_catalog(self, *, focus: bool = False) -> dict[str, Any]:
        """Slim scene list carried by the existing snapshot and journal."""
        scenes = [
            {
                "id": item.get("id"),
                "label": item.get("label"),
                "structure_name": item.get("structure_name"),
            }
            for item in self.scene_store.list()
        ]
        return {
            "scene_catalog_seq": self.scene_catalog_seq(),
            "scenes": scenes,
            "active_id": self.scene_store.active_id,
            "focus": bool(focus),
            "version": int(getattr(self, "version", 0) or 0),
        }

    def _publish_scene_catalog(self, *, focus: bool = False) -> dict[str, Any] | None:
        """Append one scene-list envelope to the figure journal.

        The WebSocket snapshot carries the same catalog. ``focus`` is set
        only for an explicit active-scene request; creating or editing a
        scene leaves the browser's selected tab alone.
        """
        lock = getattr(self, "_figure_broadcast_lock", None)
        if lock is None:
            return None
        self._scene_catalog_seq = self.scene_catalog_seq() + 1
        payload = self.scene_catalog(focus=focus)
        payload["type"] = "scene_catalog"
        with lock:
            self._figure_broadcast_seq = int(getattr(self, "_figure_broadcast_seq", 0) or 0) + 1
            payload["figure_seq"] = self._figure_broadcast_seq
            broadcasts = getattr(self, "_figure_broadcasts", None)
            if broadcasts is None:
                broadcasts = self._figure_broadcasts = []
            broadcasts.append(copy.deepcopy(payload))
            del broadcasts[:-32]
        return payload

    def scene_catalog_selection(self, event: dict[str, Any], browser_scene: Any) -> tuple[str, Any]:
        """Choose how an open page applies one scene-catalog message.

        Returns ``("stale"|"empty"|"keep"|"select", scene_id)``. ``keep``
        leaves the browser's selected tab in place.
        """
        options = self.scene_options()
        if not options:
            return "empty", None
        try:
            event_seq = int((event or {}).get("catalog_seq") or 0)
        except (TypeError, ValueError):
            event_seq = 0
        current_seq = self.scene_catalog_seq()
        if event_seq and current_seq and event_seq < current_seq:
            return "stale", None
        ids = {str(item["id"]) for item in options}
        if bool((event or {}).get("focus")) or str(browser_scene or "") not in ids:
            return "select", self.active_scene_id() or options[0]["id"]
        return "keep", None

    def _schedule_scene_render(self, scene_id: str, before: dict[str, Any], after: dict[str, Any]) -> None:
        kind = committed_render_kind(before, after)
        if kind is None:
            return
        scheduler = getattr(self, "_schedule_committed_render", None)
        if scheduler is None:
            return
        try:
            state = self.get_state(scene_id)
        except (KeyError, TypeError, ValueError):
            state = after
        scheduler(state, kind)

    def scene_options(self) -> list[dict[str, Any]]:
        return self.scene_store.list()

    def _scene_close_control(self, scene: dict[str, Any]):
        """Per-tab close button. The string id and the pattern id stay stable."""
        return html.Button(
            html.Span(
                "\u00d7",
                id=f"scene-tab-close-{scene['id']}",
                **{"aria-hidden": "true"},
            ),
            id={"type": "tab-close", "scene_id": scene["id"]},
            className="tab-close-x",
            n_clicks=0,
            title=f"Close {scene['label']}",
            **{"aria-label": f"Close {scene['label']} tab"},
        )

    def scene_tabs(self) -> list[Any]:
        from .dash_tabs import tab_label_accepts_components

        component_label = tab_label_accepts_components()
        tabs = []
        for scene in self.scene_store.list():
            if component_label:
                label: Any = html.Span(
                    [
                        html.Span(
                            scene["label"],
                            className="scene-tab-text",
                            title=str(scene["label"]),
                        ),
                        self._scene_close_control(scene),
                    ],
                    id=f"scene-tab-label-{scene['id']}",
                    className="scene-tab-label",
                    title=str(scene["label"]),
                )
            else:
                # Dash rejects a component in this prop and the layout
                # callback would replace the viewer with the error screen.
                label = str(scene["label"])
            tabs.append(
                dcc.Tab(
                    label=label,
                    value=scene["id"],
                    id=f"scene-tab-{scene['id']}",
                )
            )
        return tabs

    def scene_tab_close_row(self) -> list[Any]:
        """Close controls that cannot live inside ``dcc.Tab.label``.

        When the installed Dash accepts a component label, the close
        control is inside the tab and this row is empty. Otherwise the
        tab label is the scene name and the same close ids render here.
        """
        from .dash_tabs import tab_label_accepts_components

        if tab_label_accepts_components():
            return []
        row = []
        for scene in self.scene_store.list():
            row.append(
                html.Span(
                    [
                        html.Span(
                            scene["label"],
                            className="scene-tab-text",
                            title=str(scene["label"]),
                        ),
                        self._scene_close_control(scene),
                    ],
                    className="scene-tab-label",
                    title=str(scene["label"]),
                )
            )
        return row

    def scene_close_buttons(self) -> list[Any]:
        """Chip row with a select button and a close button for each scene.

        The live layout uses ``scene_tabs`` and ``scene_tab_close_row``.
        Chips stay available for callers that select a scene by writing
        ``scene-tabs.value`` from the browser.
        """
        active = self.active_scene_id()
        chips = []
        for scene in self.scene_store.list():
            selected = scene["id"] == active
            chips.append(
                html.Div(
                    [
                        html.Button(
                            scene["label"],
                            id={"type": "tab-select", "scene_id": scene["id"]},
                            className="tab-select",
                            n_clicks=0,
                            title=scene["label"],
                        ),
                        html.Button(
                            html.Span(
                                "\u00d7",
                                id=f"scene-tab-close-{scene['id']}",
                            ),
                            id={"type": "tab-close", "scene_id": scene["id"]},
                            className="tab-close-x",
                            n_clicks=0,
                            title=f"Close {scene['label']}",
                        ),
                    ],
                    className="scene-tab-chip"
                    + (" scene-tab-chip--selected" if selected else ""),
                    title=scene["label"],
                )
            )
        return chips

    def scene_state(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        scene = self.scene_store.get(scene_id)
        defaults = self.default_state(scene.structure_name)
        return scene.state(defaults)

    @_scene_transaction
    def replace_scene_state_with_defaults(
        self,
        scene_id: str,
        *,
        structure: Optional[str] = None,
    ) -> dict[str, Any]:
        """Replace a pending scene's placeholder state with real defaults.

        Native uploads register a scene before the CIF worker has parsed the
        structure, so the scene initially carries the empty upload bundle's
        ``formula_unit`` state.  Merging the new defaults through
        :meth:`scene_state` is insufficient because the placeholder patch
        wins. Replace the patch as one transaction so inorganic uploads do
        not inherit the placeholder presentation.
        """
        scene = self.scene_store.get(scene_id)
        before = self.scene_state(scene_id)
        target = str(structure or scene.structure_name)
        if target != scene.structure_name:
            scene.structure_name = target
        defaults = self.default_state(target)
        scene.state_patch = copy.deepcopy(defaults)
        scene.camera = copy.deepcopy(defaults.get("camera"))
        scene.updated_at = time.time()
        after = self.scene_state(scene_id)
        self._bump_render_revision_if_changed(scene_id, before, after)
        if self.scene_store.active_id == scene_id:
            self.current_state = after
            self.pending_state = self._state_snapshot(after, scene_id)
        self._bump_version()
        self._request_scene_store_save()
        self._schedule_scene_render(scene_id, before, after)
        return after

    def active_scene_id(self) -> Optional[str]:
        return self.scene_store.active_id

    @_scene_transaction
    def create_scene(
        self,
        *,
        structure: Optional[str] = None,
        label: Optional[str] = None,
        state: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        structure = (
            structure
            or self.get_state().get("structure")
            or (
                self.structure_names[0]
                if self.structure_names
                else PLACEHOLDER_STRUCTURE
            )
        )
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
        self._render_revisions[str(scene.id)] = 0
        self._scene_generation_counter += 1
        self._scene_generations[str(scene.id)] = self._scene_generation_counter
        self._camera_revisions[str(scene.id)] = int(base_state.get("camera_revision", 0) or 0)
        self.current_state = self.scene_state(scene.id)
        # ``pending_state`` is a derived broadcast snapshot. Keep it detached
        # from the canonical scene state so poll-driven UI sync cannot alias
        # later in-process mutations.
        self.pending_state = self._state_snapshot(self.current_state, scene.id)
        self._bump_version()
        payload = scene.to_dict()
        payload["requested_label"] = str(requested_label)
        payload["label_renamed"] = payload["label"] != str(requested_label)
        self._request_scene_store_save()
        self._publish_scene_catalog(focus=False)
        return payload

    @_scene_transaction
    def update_scene(self, scene_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        scene = self.scene_store.get(scene_id)
        state_before = self.scene_state(scene_id)
        if "label" in payload and len(payload) == 1:
            scene = self.scene_store.rename(scene_id, payload["label"], save=False)
            # Scene labels are rendered into the Plotly title. Keep the
            # display clock monotonic for a rename so the resulting full
            # frame cannot be deduplicated against the previous title.
            next_display_version = int(state_before.get("display_version", 0) or 0) + 1
            scene.state_patch["display_version"] = next_display_version
            state_after = self.scene_state(scene_id)
        else:
            patch = dict(payload)
            if "state" in patch:
                state_patch = patch.pop("state") or {}
                state_patch = self.normalize_state(state_patch, scene_id=scene_id)
                patch.update(state_patch)
            scene = self.scene_store.patch_scene(scene_id, patch, save=False)
            state_after = self.scene_state(scene_id)
        self._bump_render_revision_if_changed(
            scene_id, state_before, state_after
        )
        if self.scene_store.active_id == scene_id:
            self.current_state = state_after
            # Poll clients consume ``pending_state`` asynchronously; take a
            # full snapshot instead of sharing the canonical active-scene dict.
            self.pending_state = self._state_snapshot(self.current_state, scene_id)
        self._bump_version()
        self._request_scene_store_save()
        if state_before.get("scene_label") != state_after.get("scene_label"):
            self._publish_scene_catalog(focus=False)
        self._schedule_scene_render(scene_id, state_before, state_after)
        return scene.to_dict()

    @_scene_transaction
    def delete_scene(self, scene_id: str) -> dict[str, Any]:
        order_before = list(self.scene_store.order)
        active_before = self.scene_store.active_id
        removed = self.scene_store.remove(scene_id, save=False)
        self._record_scene_close(
            [removed.to_dict()], order=order_before, active_id=active_before
        )
        self._render_revisions.pop(str(scene_id), None)
        self._scene_generations.pop(str(scene_id), None)
        self._camera_revisions.pop(str(scene_id), None)
        if self.scene_store.active_id:
            self.current_state = self.scene_state(self.scene_store.active_id)
        self.pending_state = self._state_snapshot(
            self.current_state, self.scene_store.active_id
        )
        self._bump_version()
        self._request_scene_store_save()
        self._publish_scene_catalog(focus=False)
        return removed.to_dict()

    @_scene_transaction
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
        order_before = list(self.scene_store.order)
        active_before = self.scene_store.active_id
        removed: list[dict[str, Any]] = []
        for scene_id in [
            sid for sid in list(self.scene_store.scenes.keys()) if sid != keep_id
        ]:
            removed.append(self.scene_store.remove(scene_id, save=False).to_dict())
            self._render_revisions.pop(str(scene_id), None)
            self._scene_generations.pop(str(scene_id), None)
            self._camera_revisions.pop(str(scene_id), None)
        self.scene_store.active_id = keep_id
        self.current_state = self.scene_state(keep_id)
        self.pending_state = self._state_snapshot(self.current_state, keep_id)
        self._record_scene_close(
            removed, order=order_before, active_id=active_before
        )
        if removed:
            self._bump_version()
            self._request_scene_store_save()
            self._publish_scene_catalog(focus=False)
        return {"kept": self.scene_store.get(keep_id).to_dict(), "removed": removed}

    @_scene_transaction
    def duplicate_scene(
        self, scene_id: str, label: Optional[str] = None
    ) -> dict[str, Any]:
        state_before = self.scene_state(scene_id)
        scene = self.scene_store.duplicate(scene_id, label=label, save=False)
        self._render_revisions[str(scene.id)] = 0
        self._scene_generation_counter += 1
        self._scene_generations[str(scene.id)] = self._scene_generation_counter
        state_after = self.scene_state(scene.id)
        self._bump_render_revision_if_changed(scene.id, state_before, state_after)
        self._camera_revisions[str(scene.id)] = int(state_after.get("camera_revision", 0) or 0)
        self.current_state = state_after
        self.pending_state = self._state_snapshot(self.current_state, scene.id)
        self._bump_version()
        self._request_scene_store_save()
        self._publish_scene_catalog(focus=False)
        # The copy's tab label arrives with the catalog. The canvas title
        # arrives only with a figure, and a duplicate used to leave that
        # frame unscheduled at render revision 0.
        self._schedule_scene_render(scene.id, state_before, state_after)
        return scene.to_dict()

    @_scene_transaction
    def reorder_scenes(self, order: Iterable[str]) -> list[str]:
        order = self.scene_store.reorder(order, save=False)
        self._bump_version()
        self._request_scene_store_save()
        self._publish_scene_catalog(focus=False)
        return order

    @_scene_transaction
    def set_active_scene(
        self,
        scene_id: str,
        *,
        broadcast: bool = True,
        focus_clients: bool = False,
    ) -> dict[str, Any]:
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
            self.pending_state = self._state_snapshot(self.current_state, scene.id)
        self._bump_version()
        self._request_scene_store_save()
        # ``broadcast`` arms the control poll. ``focus_clients`` is the
        # separate explicit request that moves the open tab strip.
        if focus_clients:
            self._publish_scene_catalog(focus=True)
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
        return sorted(
            by_formula.values(), key=lambda item: (item["heavy"], -item["count"])
        )

    def species_options(self, structure: Optional[str] = None) -> list[dict[str, Any]]:
        """Checklist options for the species-based polyhedron selector.

        One entry per stoichiometrically distinct fragment present in the
        currently displayed scene. Each entry's ``value`` is the formula
        string (used as a stable group key) and the ``label`` shows the
        formula together with how many sites it covers, so the user sees
        e.g. ``C8N1 \u00d72`` for the DABCO rings of DAP-4.
        """
        target = structure or (
            self.structure_names[0] if self.structure_names else None
        )
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

    def element_options(
        self, state: Optional[dict[str, Any]] = None
    ) -> list[dict[str, Any]]:
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

    def fragment_options(
        self, state: Optional[dict[str, Any]] = None
    ) -> list[dict[str, Any]]:
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

