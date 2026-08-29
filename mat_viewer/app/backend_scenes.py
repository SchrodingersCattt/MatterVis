from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *


class _SceneBackendMixin:
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
        return payload

    def update_scene(self, scene_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        scene = self.scene_store.get(scene_id)
        state_before = self.scene_state(scene_id)
        if "label" in payload and len(payload) == 1:
            scene = self.scene_store.rename(scene_id, payload["label"], save=False)
        else:
            patch = dict(payload)
            if "state" in patch:
                state_patch = patch.pop("state") or {}
                state_patch = self.normalize_state(state_patch, scene_id=scene_id)
                patch.update(state_patch)
            scene = self.scene_store.patch_scene(scene_id, patch, save=False)
        self._bump_render_revision_if_changed(
            scene_id, state_before, self.scene_state(scene_id)
        )
        if self.scene_store.active_id == scene_id:
            self.current_state = self.scene_state(scene_id)
            # Poll clients consume ``pending_state`` asynchronously; take a
            # full snapshot instead of sharing the canonical active-scene dict.
            self.pending_state = self._state_snapshot(self.current_state, scene_id)
        self._bump_version()
        self._request_scene_store_save()
        return scene.to_dict()

    def delete_scene(self, scene_id: str) -> dict[str, Any]:
        removed = self.scene_store.remove(scene_id, save=False)
        self._render_revisions.pop(str(scene_id), None)
        if self.scene_store.active_id:
            self.current_state = self.scene_state(self.scene_store.active_id)
        self.pending_state = self._state_snapshot(
            self.current_state, self.scene_store.active_id
        )
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
        for scene_id in [
            sid for sid in list(self.scene_store.scenes.keys()) if sid != keep_id
        ]:
            removed.append(self.scene_store.remove(scene_id, save=False).to_dict())
            self._render_revisions.pop(str(scene_id), None)
        self.scene_store.active_id = keep_id
        self.current_state = self.scene_state(keep_id)
        self.pending_state = self._state_snapshot(self.current_state, keep_id)
        if removed:
            self._bump_version()
            self._request_scene_store_save()
        return {"kept": self.scene_store.get(keep_id).to_dict(), "removed": removed}

    def duplicate_scene(
        self, scene_id: str, label: Optional[str] = None
    ) -> dict[str, Any]:
        scene = self.scene_store.duplicate(scene_id, label=label, save=False)
        self._render_revisions[str(scene.id)] = 0
        self.current_state = self.scene_state(scene.id)
        self.pending_state = self._state_snapshot(self.current_state, scene.id)
        self._bump_version()
        self._request_scene_store_save()
        return scene.to_dict()

    def reorder_scenes(self, order: Iterable[str]) -> list[str]:
        order = self.scene_store.reorder(order, save=False)
        self._bump_version()
        self._request_scene_store_save()
        return order

    def set_active_scene(
        self, scene_id: str, *, broadcast: bool = True
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

