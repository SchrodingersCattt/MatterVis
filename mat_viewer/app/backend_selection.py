from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *
from .normalizers import _normalize_selection
from ..render.picking import (
    atom_instance_id,
    atom_instance_id_aliases,
    bond_instance_id,
    facet_instance_id,
    normalize_target,
)
from ..render.style.resolver import (
    merge_style_override,
    merge_style_overrides_for_targets,
    normalize_style_overrides,
    validate_style_override_patch,
)


def _selection_part(atom: dict[str, Any]) -> dict[str, Any]:
    part = atom.get("part") or atom.get("disorder_group") or atom.get("disorder_assembly")
    entry: dict[str, Any] = {
        "label": str(atom.get("label") or ""),
        "is_minor": bool(atom.get("is_minor")),
        "elem": str(atom.get("elem") or ""),
    }
    if part not in (None, ""):
        entry["part"] = str(part)
    return entry


def _selection_target(atom: dict[str, Any], display_index: int) -> dict[str, Any]:
    """Return the semantic target for one displayed atom.

    Coordinate picking must use the same exact identity as the Plotly
    selection trace.  Labels alone are not enough when periodic replicas or
    disorder copies share a label, so the target carries the stable instance
    id plus the legacy display metadata used by the browser inspector.
    """

    value: dict[str, Any] = {
        "kind": "atom",
        "id": atom_instance_id(atom, display_index),
        "label": str(atom.get("label") or ""),
        "element": str(atom.get("elem") or ""),
    }
    source_index = atom.get("_source_index")
    if source_index not in (None, ""):
        try:
            value["source_index"] = int(source_index)
        except (TypeError, ValueError):
            value["source_index"] = source_index
    image_shift = atom.get("_image_shift", atom.get("_pbc_image"))
    if image_shift not in (None, ""):
        try:
            value["image_shift"] = [int(item) for item in image_shift]
        except (TypeError, ValueError):
            value["image_shift"] = image_shift
    # ``normalize_target`` keeps the public target contract stable and strips
    # any accidental loader-only fields before the payload reaches JSON.
    return normalize_target(value) or {"kind": "atom", "id": value["id"]}


class _SelectionBackendMixin:
    def expand_selection_scope(
        self,
        target: Mapping[str, Any] | Any,
        scope: str | None = None,
        scene_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Expand one semantic target into an explicit target list.

        The browser can safely persist the returned list instead of relying
        on a future redraw to reinterpret a broad selector.  Atom scopes use
        loader identity fields (source, symmetry operation, molecule and
        disorder part); hidden/group-filtered atoms are excluded by the same
        visibility decoration used by the renderer.  Non-atom targets remain
        exact unless their renderer exposes an explicit family list.
        """
        normalized = normalize_target(target)
        if normalized is None:
            raise ValueError("target must contain a supported kind and id")
        wanted_scope = str(scope or "instance").strip().lower()
        allowed = {"instance", "source", "symmetry", "molecule", "part", "spec", "facet_family"}
        if wanted_scope not in allowed:
            raise ValueError(f"unsupported selection scope: {wanted_scope!r}")

        expanded: list[dict[str, Any]] = []
        kind = str(normalized.get("kind") or "")
        # A broad scope only has meaning for the target kinds whose identity
        # metadata can be expanded.  Reject mismatches before mutating state;
        # otherwise an atom + ``spec`` request falls through the atom matcher,
        # produces an empty list, and silently clears the current selection.
        allowed_scopes = {
            "atom": {"instance", "source", "symmetry", "molecule", "part"},
            "polyhedron": {"instance", "spec"},
            "facet": {"instance", "facet_family"},
            "morphology": {"instance"},
            "bond": {"instance"},
            "molecule": {"instance"},
            "part": {"instance"},
            "geometry": {"instance"},
            "geometry_entity": {"instance"},
        }
        if wanted_scope not in allowed_scopes.get(kind, {"instance"}):
            raise ValueError(
                f"scope {wanted_scope!r} is not supported for target kind {kind!r}"
            )
        if wanted_scope == "instance" or kind != "atom":
            expanded = [normalized]
        else:
            state = self.get_state(scene_id)
            scene = self.scene_for_state(state)
            atoms = list(scene.get("draw_atoms") or [])
            rows = list(enumerate(atoms))
            groups = state.get("atom_groups") or []
            if groups:
                from ..style.atom_groups import tag_atoms_with_groups
                from ..render.style.core import _atom_render_visible

                tagged = tag_atoms_with_groups(atoms, list(groups))
                rows = [
                    (index, atom)
                    for index, atom in enumerate(tagged)
                    if _atom_render_visible(atom)
                ]

            def atom_source(atom: Mapping[str, Any]) -> Any:
                return atom.get("_source_index", atom.get("source_index"))

            def atom_image(atom: Mapping[str, Any]) -> Any:
                return (
                    atom.get("_image_shift")
                    or atom.get("_pbc_image")
                    or atom.get("_display_copy_id")
                    or atom.get("image_shift")
                )

            def atom_symmetry(atom: Mapping[str, Any]) -> Any:
                return atom.get("_symop_index", atom.get("symmetry_operation_index", 0))

            reference = None
            for index, atom in rows:
                if atom_instance_id(atom, index) == normalized.get("id"):
                    reference = atom
                    break
            if reference is None:
                source = normalized.get("source_index")
                image = normalized.get("image_shift")
                for _index, atom in rows:
                    if source not in (None, "") and str(atom_source(atom)) != str(source):
                        continue
                    if image not in (None, "", (), [], [0, 0, 0]) and str(atom_image(atom)) != str(image):
                        continue
                    reference = atom
                    break
            if reference is not None:
                source = atom_source(reference)
                symop = atom_symmetry(reference)
                molecule = reference.get("_source_molecule_index", reference.get("source_molecule_index"))
                if molecule in (None, ""):
                    molecule = reference.get("_molecule_index", reference.get("molecule_index"))
                part = reference.get("part") or reference.get("disorder_group") or reference.get("disorder_assembly") or ""
                for index, atom in rows:
                    match = (
                        wanted_scope == "source" and str(atom_source(atom)) == str(source)
                    ) or (
                        wanted_scope == "symmetry"
                        and str(atom_source(atom)) == str(source)
                        and str(atom_symmetry(atom)) == str(symop)
                    ) or (
                        wanted_scope == "molecule"
                        and str(atom.get("_source_molecule_index", atom.get("source_molecule_index", atom.get("_molecule_index", atom.get("molecule_index", ""))))) == str(molecule)
                    ) or (
                        wanted_scope == "part"
                        and str(atom.get("part") or atom.get("disorder_group") or atom.get("disorder_assembly") or "") == str(part)
                    )
                    if match:
                        value = _selection_target(atom, index)
                        fragment_labels = scene.get("atom_fragment_labels") or []
                        if index < len(fragment_labels) and fragment_labels[index] not in (None, ""):
                            value["fragment_label"] = str(fragment_labels[index])
                        expanded.append(value)
            # A valid target that is hidden or no longer present has no
            # broader visible scope. Return an empty explicit list instead of
            # re-inserting the stale target into the persistent selection.
            if not expanded:
                expanded = []

        # Morphology facets carry their Miller family in the semantic
        # payload. Expand a family without relying on Plotly triangle
        # ordinals, which are free to change when facets are regrouped by
        # colour/opacity.
        if kind == "facet" and wanted_scope == "facet_family":
            state = self.get_state(scene_id)
            scene = self.scene_for_state(state)
            morphology = scene.get("bfdh_morphology") or {}
            wanted_miller = normalized.get("miller")
            expanded = []
            for index, facet in enumerate(morphology.get("facets") or []):
                miller = facet.get("miller", [0, 0, 0]) if isinstance(facet, Mapping) else [0, 0, 0]
                if wanted_miller is not None and str(miller) != str(wanted_miller):
                    continue
                expanded.append({
                    "kind": "facet",
                    "id": facet_instance_id(miller, index, morphology_id="bfdh-morphology"),
                    "parent_id": "morphology:bfdh-morphology",
                    "miller": miller,
                })
            if not expanded:
                expanded = [normalized]

        # Polyhedron geometry is cached by the backend and may not exist yet
        # when a user asks for a broad scope. If it is available, enumerate
        # the visible overlays for the requested spec; otherwise preserve the
        # exact target so a deferred topology build can resolve it later.
        if kind == "polyhedron" and wanted_scope == "spec":
            state = self.get_state(scene_id)
            try:
                geometry = self.topology_for_state(state, strict=False)
            except Exception:
                geometry = None
            spec_id = str(normalized.get("spec_id") or "")
            if not spec_id:
                token = str(normalized.get("id") or "")
                if "spec:" in token:
                    spec_id = token.split("spec:", 1)[1].split("|", 1)[0]
            values: list[dict[str, Any]] = []
            for entry in (geometry or {}).get("spec_results") or []:
                if spec_id and str(entry.get("spec_id") or "") != spec_id:
                    continue
                for overlay in entry.get("overlays") or []:
                    if not overlay.get("visible", True):
                        continue
                    overlay_id = overlay.get("instance_id")
                    if not overlay_id:
                        continue
                    values.append({
                        "kind": "polyhedron",
                        "id": str(overlay_id),
                        "spec_id": str(entry.get("spec_id") or spec_id),
                        "fragment_label": str(overlay.get("center_label") or ""),
                        "source_index": overlay.get("center_source_index"),
                        "image_shift": overlay.get("center_image_shift", overlay.get("center_image", "")),
                    })
            if values:
                expanded = values

        # Commit the concrete list only after the expansion succeeds.  This
        # keeps malformed/bare targets from clearing an existing selection.
        selection = self.set_target_selection(expanded, scene_id=scene_id)
        return {
            "scope": wanted_scope,
            "target": normalized,
            "targets": expanded,
            "count": len(expanded),
            "selection": selection,
        }

    def _reconcile_semantic_targets(self, state: dict[str, Any]) -> dict[str, Any]:
        """Drop selection/paint targets that no longer exist in a scene.

        Target IDs are intentionally stable across redraws, but a structure or
        display-scope change can remove an atom image, bond, or caller geometry
        entirely.  Retaining those IDs makes a later style patch appear to
        succeed while silently applying to an unrelated object if IDs are
        reused.  Reconcile only the inexpensive scene-owned identities here;
        topology overlays are generated lazily and remain untouched until the
        topology painter can resolve them.
        """
        try:
            scene = self.scene_for_state(state)
        except Exception:
            # An upload may still be parsing.  Keep the previous state rather
            # than clearing valid targets because a temporary scene failure is
            # not evidence that the targets are stale.
            return state

        atoms = list(scene.get("draw_atoms") or [])
        bonds = list(scene.get("bonds") or [])
        atom_ids = {
            atom_instance_id(atom, index) for index, atom in enumerate(atoms)
        }
        # A legacy symmetry alias can be a valid canonical ID for a different
        # atom (for example a real periodic image).  Do not reinterpret that
        # canonical target as the transformed atom, and do not guess when two
        # transformed atoms share the same old alias.
        legacy_alias_targets: dict[str, set[str]] = {}
        for index, atom in enumerate(atoms):
            aliases = atom_instance_id_aliases(atom, index)
            for alias in aliases[1:]:
                if alias in atom_ids:
                    continue
                legacy_alias_targets.setdefault(alias, set()).add(aliases[0])
        legacy_atom_ids = {
            alias: next(iter(targets))
            for alias, targets in legacy_alias_targets.items()
            if len(targets) == 1
        }
        valid: dict[str, set[str]] = {
            "atom": atom_ids,
            "bond": {
                bond_instance_id(bond, atoms, index)
                for index, bond in enumerate(bonds)
            },
            "facet": set(),
            "morphology": set(),
        }
        # ``scene_for_state`` does not materialise caller-owned geometry for
        # every backend (for example when the mesh is supplied by a higher
        # level renderer).  An absent list therefore means that geometry
        # identity is unknown, not that the list is empty.  Only filter these
        # targets when the scene explicitly supplies an authoritative list;
        # otherwise unrelated display changes must preserve the selection and
        # its style overrides.
        geometry_entities = scene.get("geometry_entities")
        geometry_id_sets_authoritative = geometry_entities is not None
        if geometry_id_sets_authoritative:
            valid["geometry"] = set()
            valid["geometry_entity"] = set()
            for entity in geometry_entities or []:
                if not isinstance(entity, dict) or entity.get("id") in (None, ""):
                    continue
                entity_id = str(entity["id"])
                valid["geometry"].add(entity_id)
                valid["geometry_entity"].add(entity_id)
        morphology = scene.get("bfdh_morphology") or {}
        if isinstance(morphology, dict):
            for index, facet in enumerate(morphology.get("facets") or []):
                if not isinstance(facet, dict):
                    continue
                valid["facet"].add(
                    facet_instance_id(
                        facet.get("miller", [0, 0, 0]),
                        index,
                        morphology_id="bfdh-morphology",
                    )
                )
            if morphology.get("enabled"):
                valid["morphology"].add("bfdh-morphology")

        selection = _normalize_selection(state.get("selection"))
        targets = []
        for target in selection.get("targets") or []:
            kind = str(target.get("kind") or "").lower()
            entity_id = str(target.get("id") or "")
            canonical_id = legacy_atom_ids.get(entity_id, entity_id) if kind == "atom" else entity_id
            if canonical_id != entity_id:
                target = dict(target)
                target["id"] = canonical_id
                entity_id = canonical_id
            # Polyhedron IDs include source/image provenance and are resolved
            # by topology geometry after this scene pass. Keep them while a
            # topology layer is active so a deferred analysis can resolve a
            # valid pick; disabling topology is authoritative and drops the
            # stale target immediately.
            if kind == "polyhedron" and state.get("topology_enabled"):
                targets.append(target)
            elif kind in {"molecule", "part"}:
                targets.append(target)
            elif kind in {"geometry", "geometry_entity"} and not geometry_id_sets_authoritative:
                targets.append(target)
            elif entity_id and entity_id in valid.get(kind, set()):
                targets.append(target)
        target_keys = {(item.get("kind"), item.get("id")) for item in targets}
        active = selection.get("active_target")
        if not isinstance(active, dict) or (active.get("kind"), active.get("id")) not in target_keys:
            active = targets[-1] if targets else None
        labels = [
            str(atom.get("label") or "")
            for atom in atoms
            if str(atom.get("label") or "")
        ]
        label_set = set(labels)
        selection["targets"] = targets
        selection["active_target"] = active
        selection["atom_labels"] = [label for label in selection.get("atom_labels", []) if label in label_set]
        selection["order"] = [label for label in selection.get("order", []) if label in set(selection["atom_labels"])]
        if selection.get("active_label") not in selection["atom_labels"]:
            selection["active_label"] = selection["order"][-1] if selection["order"] else None
        state = dict(state)
        state["selection"] = selection

        overrides = normalize_style_overrides(state.get("style_overrides"))
        # Migrate old symmetry IDs to their canonical form when the displayed
        # atoms give us enough provenance.  Keep a canonical edit if both
        # forms exist; otherwise move the legacy value so it remains live
        # after subsequent state round-trips.
        for legacy_id, canonical_id in legacy_atom_ids.items():
            if legacy_id not in overrides:
                continue
            if canonical_id not in overrides:
                overrides[canonical_id] = overrides[legacy_id]
            overrides.pop(legacy_id, None)
        for target_id, override in list(overrides.items()):
            kind = str(override.get("kind") or "geometry").lower()
            if kind in {"molecule", "part"} or (
                kind == "polyhedron" and state.get("topology_enabled")
            ) or (
                kind in {"geometry", "geometry_entity"}
                and not geometry_id_sets_authoritative
            ):
                continue
            if target_id not in valid.get(kind, set()):
                overrides.pop(target_id, None)
        state["style_overrides"] = overrides
        return state

    def get_selection(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        return copy.deepcopy(_normalize_selection(self.get_state(scene_id).get("selection")))

    def set_selection(self, atom_labels: Iterable[Any], scene_id: Optional[str] = None) -> dict[str, Any]:
        labels = [str(label) for label in atom_labels if str(label).strip()]
        selection = _normalize_selection({"atom_labels": labels, "order": labels})
        self.commit_patch({"selection": selection}, scene_id=scene_id)
        return self.get_selection(scene_id)

    def set_target_selection(
        self,
        targets: Iterable[Any] | Any,
        scene_id: Optional[str] = None,
        *,
        additive: bool = False,
        toggle: bool = False,
    ) -> dict[str, Any]:
        """Select exact semantic targets while preserving atom-label clients.

        ``targets`` are canonical ``{kind, id}`` payloads emitted by Plotly
        interaction traces.  Atom labels are mirrored only as a compatibility
        projection for legacy selection actions; they are never used as the
        identity of a non-atom target.
        """

        if isinstance(targets, dict):
            values = [targets]
        else:
            values = list(targets or [])
        normalized = []
        seen: set[tuple[str, str]] = set()
        for value in values:
            target = normalize_target(value)
            if target is None:
                continue
            key = (target["kind"], target["id"])
            if key not in seen:
                normalized.append(target)
                seen.add(key)
        current = self.get_selection(scene_id)
        existing = list(current.get("targets") or []) if additive or toggle else []
        by_key = {(item.get("kind"), item.get("id")): item for item in existing}
        for target in normalized:
            key = (target["kind"], target["id"])
            if toggle and key in by_key:
                by_key.pop(key, None)
            else:
                by_key[key] = target
        final_targets = list(by_key.values())
        labels = [str(item.get("label")) for item in final_targets if item.get("kind") == "atom" and item.get("label")]
        selection = _normalize_selection({
            "atom_labels": labels,
            "order": labels,
            "targets": final_targets,
            # Derive the active target from the resulting set.  In toggle
            # mode ``normalized[-1]`` may be the target that was just
            # removed; passing it to the normalizer would add it back to
            # ``targets`` as a side effect.
            "active_target": final_targets[-1] if final_targets else None,
        })
        self.patch_state({"selection": selection}, scene_id=scene_id)
        return self.get_selection(scene_id)

    def clear_target_selection(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        return self.clear_selection(scene_id=scene_id)

    def set_style_override(
        self,
        target_id: str,
        kind: str,
        patch: dict[str, Any],
        scene_id: Optional[str] = None,
    ) -> dict[str, dict[str, Any]]:
        validate_style_override_patch(kind, patch)
        state = self.get_state(scene_id)
        overrides = merge_style_override(state.get("style_overrides"), str(target_id), str(kind), patch)
        self.patch_state({"style_overrides": overrides}, scene_id=scene_id)
        return copy.deepcopy(self.get_state(scene_id).get("style_overrides") or {})

    def set_style_overrides(
        self,
        targets: Iterable[Mapping[str, Any]],
        patch: dict[str, Any],
        scene_id: Optional[str] = None,
    ) -> dict[str, dict[str, Any]]:
        """Apply one paint patch to every selected target atomically.

        The resolver validates the intersection of fields supported by all
        target kinds before mutating state, so a mixed atom/polyhedron pick
        cannot partially apply a radius or representation change.
        """

        state = self.get_state(scene_id)
        overrides = merge_style_overrides_for_targets(
            state.get("style_overrides"), targets, patch
        )
        self.patch_state({"style_overrides": overrides}, scene_id=scene_id)
        return copy.deepcopy(self.get_state(scene_id).get("style_overrides") or {})

    def reset_style_override(
        self,
        target_id: str,
        scene_id: Optional[str] = None,
        *,
        field: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        state = self.get_state(scene_id)
        overrides = normalize_style_overrides(state.get("style_overrides"))
        key = str(target_id)
        if field and key in overrides:
            overrides[key].pop(str(field), None)
            if set(overrides[key]) <= {"kind"}:
                overrides.pop(key, None)
        else:
            overrides.pop(key, None)
        self.patch_state({"style_overrides": overrides}, scene_id=scene_id)
        return copy.deepcopy(self.get_state(scene_id).get("style_overrides") or {})

    def add_to_selection(self, atom_labels: Iterable[Any], scene_id: Optional[str] = None) -> dict[str, Any]:
        current = self.get_selection(scene_id)
        order = list(current.get("order") or current.get("atom_labels") or [])
        labels = list(current.get("atom_labels") or [])
        seen = set(labels)
        for raw in atom_labels:
            label = str(raw).strip()
            if label and label not in seen:
                labels.append(label)
                order.append(label)
                seen.add(label)
        selection = _normalize_selection({"atom_labels": labels, "order": order, "active_label": order[-1] if order else None})
        self.commit_patch({"selection": selection}, scene_id=scene_id)
        return self.get_selection(scene_id)

    def remove_from_selection(self, atom_labels: Iterable[Any], scene_id: Optional[str] = None) -> dict[str, Any]:
        remove = {str(label).strip() for label in atom_labels if str(label).strip()}
        current = self.get_selection(scene_id)
        labels = [label for label in current.get("atom_labels", []) if label not in remove]
        order = [label for label in current.get("order", []) if label in labels]
        selection = _normalize_selection({"atom_labels": labels, "order": order})
        self.commit_patch({"selection": selection}, scene_id=scene_id)
        return self.get_selection(scene_id)

    def clear_selection(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        # Clear exact semantic targets as well as the legacy label projection.
        # Omitting ``targets`` happened to work while the normalizer rebuilt
        # the whole selection object, but left this contract dependent on that
        # implementation detail and made atom-only outlines linger after a
        # clear event in older state payloads.
        self.patch_state(
            {
                "selection": {
                    "atom_labels": [],
                    "active_label": None,
                    "order": [],
                    "targets": [],
                    "active_target": None,
                }
            },
            scene_id=scene_id,
        )
        return self.get_selection(scene_id)

    def toggle_selection(self, atom_labels: Iterable[Any], scene_id: Optional[str] = None) -> dict[str, Any]:
        current = self.get_selection(scene_id)
        selected = set(current.get("atom_labels") or [])
        labels = [str(label).strip() for label in atom_labels if str(label).strip()]
        if all(label in selected for label in labels):
            return self.remove_from_selection(labels, scene_id=scene_id)
        return self.add_to_selection(labels, scene_id=scene_id)

    def _labels_for_scene(self, scene_id: Optional[str] = None) -> list[str]:
        scene = self.scene_for_state(self.get_state(scene_id))
        labels: list[str] = []
        seen: set[str] = set()
        for atom in scene.get("draw_atoms") or []:
            label = str(atom.get("label") or "").strip()
            if label and label not in seen:
                labels.append(label)
                seen.add(label)
        return labels

    def select_all(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        return self.set_selection(self._labels_for_scene(scene_id), scene_id=scene_id)

    def invert_selection(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        current = set(self.get_selection(scene_id).get("atom_labels") or [])
        return self.set_selection(
            [label for label in self._labels_for_scene(scene_id) if label not in current],
            scene_id=scene_id,
        )

    def select_element(self, element: str, scene_id: Optional[str] = None) -> dict[str, Any]:
        scene = self.scene_for_state(self.get_state(scene_id))
        labels = []
        for atom in scene.get("draw_atoms") or []:
            if str(atom.get("elem") or "") == str(element):
                labels.append(str(atom.get("label") or ""))
        return self.set_selection(labels, scene_id=scene_id)

    def select_box(
        self,
        rect_pixels: Iterable[Any],
        viewport_size: Iterable[Any],
        *,
        additive: bool = False,
        scene_id: Optional[str] = None,
        commit: bool = True,
    ) -> dict[str, Any]:
        try:
            x0, y0, x1, y1 = [float(v) for v in rect_pixels]
            width, height = [max(1.0, float(v)) for v in viewport_size]
        except (TypeError, ValueError):
            return self.get_selection(scene_id)
        lo_x, hi_x = sorted((x0, x1))
        lo_y, hi_y = sorted((y0, y1))
        state = self.get_state(scene_id)
        scene = self.scene_for_state(state)
        # Box/coordinate picking must follow the same visibility contract as
        # the Plotly interaction trace.  ``draw_atoms`` is the complete
        # structural list and therefore still contains atoms hidden by an
        # ``atom_groups`` rule (or a zero-opacity override).  Projecting that
        # list would make a click on an empty pixel select an invisible atom.
        # Keep the original display index alongside each visible atom so
        # stable IDs continue to match the customdata emitted by the figure.
        atoms = list(scene.get("draw_atoms") or [])
        atom_rows = list(enumerate(atoms))
        atom_groups = state.get("atom_groups") or []
        if atom_groups:
            from ..style.atom_groups import tag_atoms_with_groups
            from ..render.style.core import _atom_render_visible

            tagged = tag_atoms_with_groups(atoms, list(atom_groups))
            atom_rows = [
                (index, atom)
                for index, atom in enumerate(tagged)
                if _atom_render_visible(atom)
            ]
            atoms = [atom for _index, atom in atom_rows]
        if lo_x <= 0.0 and lo_y <= 0.0 and hi_x >= width and hi_y >= height:
            # Keep the fast full-cell path, but include exact atom targets so
            # this API has the same semantic contract as a small coordinate
            # pick.  ``select_all`` intentionally remains label-only for its
            # legacy keyboard/API callers.
            parts = [_selection_part(atom) for atom in atoms]
            picked = [part["label"] for part in parts if part["label"]]
            targets = [_selection_target(atom, index) for index, atom in atom_rows]
            selection = _normalize_selection(
                {
                    "atom_labels": picked,
                    "order": picked,
                    "parts": parts,
                    "targets": targets,
                    "active_target": targets[-1] if targets else None,
                }
            )
            if additive:
                current = self.get_selection(scene_id)
                merged_labels = list(current.get("atom_labels") or [])
                merged_parts = list(current.get("parts") or [])
                for part in parts:
                    label = part["label"]
                    if label and label not in merged_labels:
                        merged_labels.append(label)
                        merged_parts.append(part)
                by_key: dict[tuple[str, str], dict[str, Any]] = {}
                for target in list(current.get("targets") or []) + targets:
                    normalized = normalize_target(target)
                    if normalized is not None:
                        by_key[(normalized["kind"], normalized["id"])] = normalized
                merged_targets = list(by_key.values())
                selection = _normalize_selection(
                    {
                        "atom_labels": merged_labels,
                        "order": merged_labels,
                        "parts": merged_parts,
                        "targets": merged_targets,
                        "active_target": targets[-1] if targets else current.get("active_target"),
                    }
                )
            if not commit:
                return selection
            self.commit_patch({"selection": selection}, scene_id=scene_id)
            return self.get_selection(scene_id)
        if not atoms:
            return self.get_selection(scene_id)
        coords = np.asarray([atom.get("cart", [0.0, 0.0, 0.0]) for atom in atoms], dtype=float)
        try:
            from mat_viewer.render.screen_projection import project_gl3d_pixels
            from mat_viewer.render.viewport import _plotly_camera_from_scene
            from mat_viewer.render.viewport_spec import resolve_viewport

            style = self.style_for_state(state, scene)
            viewport = resolve_viewport(scene, style)
            # Stored cameras are often position/focal. Projecting those
            # with the default eye picks the wrong atom.
            camera = _plotly_camera_from_scene(scene, style)
            projection = state.get("projection")
            xy = project_gl3d_pixels(
                coords,
                camera=camera,
                ranges=viewport.ranges,
                aspectratio=viewport.aspectratio,
                width=width,
                height=height,
                projection=str(projection) if projection else None,
            )
        except Exception:
            return self.get_selection(scene_id)
        center_x = 0.5 * (lo_x + hi_x)
        center_y = 0.5 * (lo_y + hi_y)
        hits: list[tuple[float, int, dict[str, Any]]] = []
        for row_index, (atom, sx, sy) in enumerate(zip(atoms, xy[:, 0], xy[:, 1])):
            if not (np.isfinite(sx) and np.isfinite(sy)):
                continue
            if lo_x <= float(sx) <= hi_x and lo_y <= float(sy) <= hi_y:
                dist = (float(sx) - center_x) ** 2 + (float(sy) - center_y) ** 2
                display_index = atom_rows[row_index][0]
                hits.append((dist, display_index, atom))
        click = (hi_x - lo_x) <= 48.0 and (hi_y - lo_y) <= 48.0
        if click and hits:
            hits = [min(hits, key=lambda item: item[0])]
        parts = [_selection_part(atom) for _dist, _index, atom in hits]
        picked = [part["label"] for part in parts if part["label"]]
        targets = [_selection_target(atom, index) for _dist, index, atom in hits]
        selection = _normalize_selection(
            {
                "atom_labels": picked,
                "order": picked,
                "parts": parts,
                "targets": targets,
                "active_target": targets[-1] if targets else None,
            }
        )
        if additive:
            current = self.get_selection(scene_id)
            merged_labels = list(current.get("atom_labels") or [])
            merged_parts = list(current.get("parts") or [])
            seen = set(merged_labels)
            for part in parts:
                label = part["label"]
                if label and label not in seen:
                    merged_labels.append(label)
                    merged_parts.append(part)
                    seen.add(label)
            by_key: dict[tuple[str, str], dict[str, Any]] = {}
            for target in list(current.get("targets") or []) + targets:
                normalized = normalize_target(target)
                if normalized is not None:
                    by_key[(normalized["kind"], normalized["id"])] = normalized
            merged_targets = list(by_key.values())
            selection = _normalize_selection(
                {
                    "atom_labels": merged_labels,
                    "order": merged_labels,
                    "parts": merged_parts,
                    "targets": merged_targets,
                    "active_target": targets[-1] if targets else current.get("active_target"),
                }
            )
        if not commit:
            return selection
        self.commit_patch({"selection": selection}, scene_id=scene_id)
        return self.get_selection(scene_id)

    def select_fragment(self, fragment_label: str, scene_id: Optional[str] = None) -> dict[str, Any]:
        state = self.get_state(scene_id)
        scene = self.scene_for_state(state)
        fragment_labels = scene.get("atom_fragment_labels") or []
        labels = [
            str(atom.get("label") or "")
            for idx, atom in enumerate(scene.get("draw_atoms") or [])
            if idx < len(fragment_labels) and str(fragment_labels[idx]) == str(fragment_label)
        ]
        return self.set_selection(labels, scene_id=scene_id)

    def promote_selection_to_atom_group(
        self,
        name: str | None = None,
        color: str | None = None,
        scene_id: Optional[str] = None,
    ) -> str | None:
        labels = self.get_selection(scene_id).get("atom_labels") or []
        if not labels:
            return None
        group = self.add_atom_group(
            selector={"labels": labels},
            name=name or "selection",
            color=color or "#FFD24A",
            visible=True,
            scene_id=scene_id,
        )
        self.clear_selection(scene_id)
        return group.get("id")

    def hide_selection(self, scene_id: Optional[str] = None) -> str | None:
        labels = self.get_selection(scene_id).get("atom_labels") or []
        if not labels:
            return None
        group = self.add_atom_group(
            selector={"labels": labels},
            name="hide selection",
            color="#888888",
            visible=False,
            scene_id=scene_id,
        )
        self.clear_selection(scene_id)
        return group.get("id")

    def focus_selection_camera(self, scene_id: Optional[str] = None) -> dict[str, Any] | None:
        selected = set(self.get_selection(scene_id).get("atom_labels") or [])
        if not selected:
            return None
        state = self.get_state(scene_id)
        scene = self.scene_for_state(state)
        coords = [
            np.asarray(atom.get("cart"), dtype=float)
            for atom in scene.get("draw_atoms") or []
            if str(atom.get("label") or "") in selected
        ]
        if not coords:
            return None
        center = np.asarray(coords, dtype=float).mean(axis=0)
        camera = self.get_camera(scene_id)
        camera["center"] = {"x": float(center[0]), "y": float(center[1]), "z": float(center[2])}
        return self.set_camera(camera, scene_id=scene_id)
