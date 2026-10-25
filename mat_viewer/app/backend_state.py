from __future__ import annotations

import copy
from typing import Any, Optional

from ..config import current_config
from ..presets import deep_merge
from .camera_helpers import *
from .camera_helpers import _plotly_camera
from .normalizers import *
from .normalizers import _normalize_selection
from .reducer import LEGACY_STYLE_STATE_FIELDS
from .rightclick import _normalize_polyhedron_specs
from .selectors import show_hydrogen, viewport_signature
from .shared import *
from .style_helpers import *
from .view_updates import UpdateKind, classify_change, make_update, state_versions


class _StateBackendMixin:
    def normalize_state(
        self, patch: Optional[dict[str, Any]], scene_id: Optional[str] = None
    ) -> dict[str, Any]:
        if scene_id is not None:
            state = self.scene_state(scene_id)
        else:
            state = copy.deepcopy(self.current_state)
        patch = patch or {}

        if "render_revision" in patch and patch["render_revision"] is not None:
            try:
                state["render_revision"] = int(patch["render_revision"])
            except (TypeError, ValueError):
                pass

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
        for key in (
            "atom_scale",
            "bond_radius",
            "minor_opacity",
            "axis_scale",
            "cutoff",
        ):
            if key in patch and patch[key] is not None:
                state[key] = float(patch[key])
        for key in ("material", "style", "disorder", "ortep_mode", "label_mode"):
            if key in patch and patch[key] is not None:
                state[key] = str(patch[key])
        if "display_options" in patch and patch["display_options"] is not None:
            state["display_options"] = list(patch["display_options"])

        # Preserve old renderer/config fields without dedicated controls.
        for key in LEGACY_STYLE_STATE_FIELDS:
            if key in patch:
                state[key] = copy.deepcopy(patch[key])
        if "geometry_entities" in patch:
            entities = patch["geometry_entities"]
            if entities is not None and (not isinstance(entities, list) or any(not isinstance(item, dict) for item in entities)):
                raise ValueError("geometry_entities must be a list of mesh entities")
            state["geometry_entities"] = copy.deepcopy(entities or [])
        if "display_mode" in patch and patch["display_mode"] is not None:
            state["display_mode"] = str(patch["display_mode"])
            if "topology_site_index" not in patch:
                state["topology_site_index"] = None
        if "topology_species_keys" in patch:
            value = patch["topology_species_keys"]
            if value is None:
                state["topology_species_keys"] = []
            else:
                state["topology_species_keys"] = [
                    str(item) for item in value if item is not None
                ]
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
        if patch.get("topology_show_all_sites") and not state.get(
            "topology_species_keys"
        ):
            structure = state.get("structure")
            if structure and structure in self.bundles:
                fragments = self.get_bundle(structure).scene.get("fragment_table") or []
                state["topology_species_keys"] = sorted(
                    {
                        f.get("formula") or f.get("species")
                        for f in fragments
                        if f.get("formula") or f.get("species")
                    }
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
            state["atom_groups"] = _normalize_atom_groups(
                patch.get("atom_groups") or []
            )
        if "bond_groups" in patch:
            state["bond_groups"] = _normalize_bond_groups(
                patch.get("bond_groups") or []
            )
        if "atom_property_color" in patch:
            state["atom_property_color"] = _normalize_atom_property_color(
                patch.get("atom_property_color")
            )
        if "transforms" in patch:
            state["transforms"] = _normalize_transforms(patch.get("transforms") or [])
        if "selection" in patch:
            state["selection"] = _normalize_selection(patch.get("selection"))
        if "style_overrides" in patch:
            state["style_overrides"] = normalize_style_overrides(patch.get("style_overrides"))
        if "disorder_resolve" in patch:
            state["disorder_resolve"] = _normalize_disorder_resolve(
                patch.get("disorder_resolve"),
                state.get("disorder_resolve"),
            )
        if "disorder_replicas" in patch:
            replicas = patch.get("disorder_replicas") or []
            state["disorder_replicas"] = replicas if isinstance(replicas, list) else []
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
                    {
                        "kind": "repeat",
                        "params": {"a": a, "b": b, "c": c},
                        "name": f"Repeat {a}x{b}x{c}",
                    },
                    existing_ids=existing_ids,
                )
                if normalized is not None:
                    existing.append(normalized)
            state["transforms"] = existing
        if "fast_rendering" in patch:
            state["fast_rendering"] = bool(patch["fast_rendering"])
        if "bfdh_morphology" in patch:
            morphology = patch.get("bfdh_morphology")
            if isinstance(morphology, dict):
                state["bfdh_morphology"] = copy.deepcopy(morphology)
            else:
                state["bfdh_morphology"] = None
        if "bfdh_morphology_color" in patch and patch["bfdh_morphology_color"]:
            state["bfdh_morphology_color"] = str(patch["bfdh_morphology_color"])
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
        # Display and geometry changes keep the user's camera.  Only the
        # explicit camera commands below change the camera clock; resetting it
        # here made Labels/Hydrogens/scene-range changes snap the viewport.
        if "camera" in patch and patch["camera"] is not None:
            camera_patch = patch["camera"]
            camera = state.get("camera")
            if isinstance(camera_patch, dict) and isinstance(camera, dict):
                if "position" in camera_patch or "focal_point" in camera_patch:
                    # A legacy position/focal camera replaces the Plotly
                    # representation; mixing both makes ``eye`` win.
                    camera = {}
                elif ("position" in camera or "focal_point" in camera) and any(
                    isinstance(camera_patch.get(key), dict) for key in ("eye", "center", "up")
                ):
                    camera = _plotly_camera(camera) or {}
                state["camera"] = deep_merge(camera, camera_patch)
            else:
                state["camera"] = copy.deepcopy(camera_patch)
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
        # dict. Copy the coerced type onto an existing camera here.
        # ``set_projection`` also refits; calling it from ``patch_state``
        # would recurse and move the eye.
        projection_specified = False
        if "projection" in patch and patch["projection"] is not None:
            state["projection"] = _coerce_projection(
                patch["projection"],
                fallback=str(state.get("projection", "perspective")),
            )
            projection_specified = True
        elif isinstance(patch.get("camera"), dict):
            cam_proj = patch["camera"].get("projection")
            if isinstance(cam_proj, dict) and "type" in cam_proj:
                state["projection"] = _coerce_projection(
                    cam_proj["type"],
                    fallback=str(state.get("projection", "perspective")),
                )
                projection_specified = True
        if projection_specified and isinstance(state.get("camera"), dict):
            camera = dict(state["camera"])
            camera["projection"] = {"type": state["projection"]}
            state["camera"] = camera
        return state

    def get_state(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        with self._lock:
            if scene_id is not None:
                state = self.scene_state(scene_id)
            else:
                state = self.current_state
            versions = state_versions(state)
            state.setdefault("geometry_version", versions.geometry)
            state.setdefault("display_version", versions.display)
            state.setdefault("camera_version", versions.camera)
            state.setdefault("camera_revision", versions.camera)
            return self._state_snapshot(state, scene_id)

    def patch_state(
        self,
        patch: Optional[dict[str, Any]],
        scene_id: Optional[str] = None,
        *,
        broadcast: bool = True,
        expected_camera_revision: Any = None,
        expected_scene_generation: Any = None,
        _camera_fence: bool = False,
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
        rebuild_projection_image = False
        with self._lock:
            target_scene_id = (
                scene_id or (patch or {}).get("scene_id") or self.scene_store.active_id
            )
            active_scene_id = self.scene_store.active_id
            state_before = (
                self.scene_state(target_scene_id)
                if target_scene_id
                else copy.deepcopy(self.current_state)
            )
            if _camera_fence:
                # Compare inside the same lock that commits the camera. A
                # Reset/Fit that landed after the caller's earlier read must
                # not be overwritten by that stale drag.
                current_generation = self.scene_generation(target_scene_id)
                try:
                    revision_ok = int(expected_camera_revision) == int(
                        state_before.get("camera_revision", 0) or 0
                    )
                except (TypeError, ValueError):
                    revision_ok = False
                generation_ok = (
                    expected_scene_generation is not None
                    and str(expected_scene_generation) == str(current_generation)
                )
                if not revision_ok or not generation_ok:
                    raise StaleCameraWrite(
                        "camera write lost the scene generation or camera revision race"
                    )
            working_state = self.normalize_state(patch, scene_id=target_scene_id)
            change_kind = classify_change(state_before, working_state)
            projection_changed = state_before.get("projection") != working_state.get("projection")
            # Structure/display changes can remove displayed instances while
            # preserving the scene's persisted selection/style dictionaries.
            # Reconcile exact IDs before committing the transition so stale
            # browser targets cannot survive into a later redraw.
            # ``classify_change`` deliberately treats selection as a complete
            # frame because the browser needs a fresh outline.  That does not
            # mean the selected targets became stale: filtering them on every
            # click would discard caller-supplied geometry IDs that are not
            # materialised in the current Python scene.  Reconcile only when
            # an authoritative structure/display identity input changed.
            identity_inputs = (
                "structure",
                "geometry_entities",
                "display_mode",
                "display_options",
                "atom_groups",
                "bond_groups",
                "transforms",
                "disorder",
                "disorder_resolve",
                "disorder_replicas",
                "polyhedron_specs",
                "bfdh_morphology",
                "topology_enabled",
                "topology_species_keys",
                "topology_site_index",
                "cutoff",
            )
            identity_changed = any(
                state_before.get(key) != working_state.get(key)
                for key in identity_inputs
            )
            if change_kind == UpdateKind.GEOMETRY and identity_changed:
                working_state["target_revision"] = int(state_before.get("target_revision", 0)) + 1
                # Source ordinals belong to their structure. An atom:source:0
                # in a different upload must never inherit the old atom's paint.
                if state_before.get("structure") != working_state.get("structure"):
                    previous = working_state.get("style_overrides") or {}
                    removed = [key for key, value in previous.items() if value.get("kind") not in {"geometry", "geometry_entity"}]
                    working_state["style_overrides"] = {key: value for key, value in previous.items() if key not in removed}
                    working_state["selection"] = _normalize_selection({})
                    working_state["style_override_warnings"] = removed
                working_state = self._reconcile_semantic_targets(working_state)
            before_versions = state_versions(state_before)
            # Maintain independent clocks.  A display patch can reuse the
            # current geometry; a camera patch never invalidates either.
            geometry_version = before_versions.geometry
            display_version = before_versions.display
            camera_version = max(
                before_versions.camera,
                int(working_state.get("camera_revision", 0) or 0),
            )
            if change_kind == UpdateKind.GEOMETRY:
                geometry_version += 1
                display_version += 1
            elif change_kind in {
                UpdateKind.DISPLAY,
                UpdateKind.OVERLAY,
                UpdateKind.ANALYSIS,
            }:
                display_version += 1
            if change_kind == UpdateKind.CAMERA or projection_changed:
                # Browser drag persistence changes the camera value without
                # invalidating a command revision. Explicit camera commands
                # carry a revision/projection marker.  A caller that supplies
                # a revision has already selected the next monotonic value;
                # do not increment it a second time here.  This matters for
                # orbit/pan/zoom, which write the camera and revision in one
                # atomic patch. A projection change also advances this clock
                # when a simultaneous display/geometry edit takes precedence
                # in the delivery classification.
                if "camera_revision" in (patch or {}):
                    try:
                        requested = int(working_state.get("camera_revision", 0) or 0)
                    except (TypeError, ValueError):
                        requested = 0
                    camera_version = max(before_versions.camera + 1, requested)
                elif "projection" in (patch or {}) or projection_changed:
                    camera_version += 1
            working_state["geometry_version"] = geometry_version
            working_state["display_version"] = display_version
            working_state["camera_version"] = camera_version
            working_state["camera_revision"] = camera_version
            # Keep the in-memory command clock in step with the persisted
            # state.  A later command must not reuse a revision from before a
            # scene-scoped patch (for example after an orbit followed by a
            # pan).
            revisions = getattr(self, "_camera_revisions", None)
            if isinstance(revisions, dict) and target_scene_id:
                revisions[str(target_scene_id)] = camera_version
            self._last_update_kind_by_scene[str(target_scene_id)] = change_kind.value
            if target_scene_id:
                scene_payload = copy.deepcopy(working_state)
                scene_payload.pop("scene_id", None)
                scene_payload.pop("scene_label", None)
                self.scene_store.patch_scene(target_scene_id, scene_payload, save=False)
                self._bump_render_revision_if_changed(
                    target_scene_id, state_before, working_state
                )
            working_state["render_revision"] = self.render_revision(
                target_scene_id
            )
            if str(target_scene_id or "") == str(active_scene_id or ""):
                self.current_state = working_state
            if broadcast and str(target_scene_id or "") == str(active_scene_id or ""):
                self.pending_state = copy.deepcopy(working_state)
            elif not broadcast and str(target_scene_id or "") == str(active_scene_id or ""):
                # A UI-originated write is already authoritative in the browser.
                # If an agent/REST update is already queued, keep it queued but
                # refresh it with the latest camera/UI fields so the poll does
                # not lose the earlier agent value.
                if self.pending_state is not None:
                    self.pending_state = copy.deepcopy(working_state)
            self._bump_version()
            state = copy.deepcopy(working_state)
            state["version"] = self.version
            state["server_started_at"] = self.server_started_iso()
            state["render_revision"] = self.render_revision(target_scene_id)
            state["scene_generation"] = self.scene_generation(target_scene_id)
            self._request_scene_store_save()
            if broadcast and change_kind in {
                UpdateKind.CAMERA,
                UpdateKind.OVERLAY,
                UpdateKind.DISPLAY,
                UpdateKind.ANALYSIS,
            }:
                update_payload = {}
                if change_kind == UpdateKind.CAMERA:
                    update_payload["camera"] = copy.deepcopy(state.get("camera"))
                elif change_kind in {UpdateKind.OVERLAY, UpdateKind.DISPLAY}:
                    update_payload.update(
                        {
                            "display_options": list(state.get("display_options") or []),
                            "axis_scale": state.get("axis_scale"),
                            "minor_opacity": state.get("minor_opacity"),
                            "polyhedron_specs": copy.deepcopy(
                                state.get("polyhedron_specs") or []
                            ),
                        }
                    )
                self.broadcast_view_update(
                    make_update(
                        state,
                        # The transaction already classified this change.
                        # Reclassifying against the wire snapshot sees added
                        # envelope fields (e.g. scene_generation) as display
                        # changes and can publish a camera payload as DISPLAY.
                        operation=change_kind.value,
                        payload=update_payload,
                        reason="state-confirmed",
                    ),
                    state=state,
                )
                if change_kind != UpdateKind.CAMERA and projection_changed:
                    # Display/overlay/analysis delivery never applies a
                    # camera. Publish both owners of a mixed transaction
                    # with the same committed scene and version fences.
                    projection_camera = copy.deepcopy(state.get("camera"))
                    if projection_camera is None:
                        projection_camera = {"projection": {"type": state["projection"]}}
                    self.broadcast_view_update(
                        make_update(
                            state,
                            operation="camera",
                            payload={"camera": projection_camera},
                            reason="state-confirmed",
                        ),
                        state=state,
                    )
                    rebuild_projection_image = state.get("material") == "flat" and state.get("style") == "ortep"
        # Flat ORTEP rasterizes the camera basis. Queue its combined image
        # after releasing the backend lock; worker completion takes the locks
        # in the other order. Ordinary Plotly patches keep the fast path.
        if rebuild_projection_image:
            worker = getattr(self, "_render_worker", None)
            if worker is not None:
                worker.request_figure_build(state, force=True)
        return state

    def pop_pending_state(self) -> Optional[dict[str, Any]]:
        with self._lock:
            pending = self.pending_state
            self.pending_state = None
            return copy.deepcopy(pending) if pending else None

    def show_hydrogen_for_state(self, state: Optional[dict[str, Any]] = None) -> bool:
        state = self.current_state if state is None else state
        return show_hydrogen(state)

    def scene_for_state(self, state: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        state = self.current_state if state is None else state
        bundle = self.get_bundle(state["structure"])
        # Phase 4: ``state["transforms"]`` is the structure-mutation
        # pipeline. ``build_bundle_scene`` short-circuits when the list
        # is empty so the no-transform path stays a single dict lookup.
        transforms = list(state.get("transforms") or [])
        # Keep the historic ``backend_core.build_bundle_scene`` patch
        # point working for embedders and tests while the state methods
        # live in this smaller module.
        from . import backend_core as _backend_core_module

        scene_builder = getattr(
            _backend_core_module, "build_bundle_scene", build_bundle_scene
        )
        display_mode = state.get("display_mode", "formula_unit")
        # ``wrap_into_cell`` is display-only. Both flags stay at their
        # historical defaults unless the token is on in unit-cell scope,
        # so a strict cell does not also sprout bonded images outside it.
        wrap_into_cell = (
            display_mode == "unit_cell"
            and "wrap_into_cell" in set(state.get("display_options") or [])
        )
        scene = scene_builder(
            bundle,
            display_mode=display_mode,
            show_hydrogen=self.show_hydrogen_for_state(state),
            preset=self.preset,
            transforms=transforms,
            include_boundary_replicas=not wrap_into_cell,
            include_cross_boundary_bond_endpoints=not wrap_into_cell,
        )
        if "geometry_entities" in state:
            scene = dict(scene)
            scene["geometry_entities"] = copy.deepcopy(state["geometry_entities"])
        # Keep state-owned morphology available to semantic target
        # reconciliation and programmatic scene callers.  The style selector
        # also applies this field before rendering, but reconciliation runs
        # before that selector and would otherwise discard valid facet IDs.
        if state.get("bfdh_morphology") is not None:
            scene["bfdh_morphology"] = copy.deepcopy(state.get("bfdh_morphology"))
        scene_label = state.get("scene_label")
        if scene_label:
            scene["display_title"] = str(scene_label)
        bundle.scene = scene
        bundle.fragment_table = scene.get("fragment_table", bundle.fragment_table)
        property_spec = state.get("atom_property_color")
        if property_spec:
            from ..properties import (
                map_property_colors,
                property_metadata,
                resolve_source_property_context,
                rgba_to_hex,
            )

            source = getattr(bundle, "_property_structure_input", None)
            if source is None:
                from pathlib import Path

                from ..loader.structure_input import StructureFrame, StructureInput

                frame_index = int(bundle.frame_info.get("frame_index", 0))
                frame = StructureFrame(
                    frame_index,
                    bundle,
                    dict(bundle.frame_info),
                    dict(bundle.atom_arrays),
                )
                source = StructureInput(
                    Path(bundle.cif_path),
                    str(scene.get("input_format") or "cif"),
                    (frame,),
                    1,
                    getattr(bundle, "_property_manifest", None),
                )
            context = resolve_source_property_context(source, property_spec)
            frame_index = int(scene.get("frame_index", context.source_frame_indices[0]))
            reduced = context.frame(frame_index)
            colors = rgba_to_hex(
                map_property_colors(
                    reduced.values,
                    context.scale,
                    nan_color=context.spec.nan_color,
                )
            )
            draw_atoms = []
            for display_index, original in enumerate(scene.get("draw_atoms") or []):
                atom = dict(original)
                source_index = int(atom.get("_source_index", display_index))
                if not 0 <= source_index < len(colors):
                    raise ValueError(
                        f"display atom {display_index} maps outside property array"
                    )
                atom["_property_color"] = colors[source_index]
                atom["_property_value"] = float(reduced.values[source_index])
                draw_atoms.append(atom)
            scene = dict(scene)
            scene["draw_atoms"] = draw_atoms
            scene["atom_property_color"] = property_metadata(
                context.spec,
                reduced,
                context.scale,
                manifest_hash=context.manifest_hash,
            )
        return scene

    def atom_properties(
        self,
        state: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Return bounded field descriptors and the active public spec."""

        from ..properties import catalog_from_arrays, merge_catalogs

        state = self.get_state() if state is None else state
        bundle = self.get_bundle(state["structure"])
        catalogs = [catalog_from_arrays(bundle.atom_arrays)]
        manifest = getattr(bundle, "_property_manifest", None)
        source = getattr(bundle, "_property_structure_input", None)
        if manifest is None and source is not None:
            manifest = getattr(source, "property_manifest", None)
        if manifest is not None:
            catalogs.append(manifest.catalog())
        catalog = merge_catalogs(*catalogs)
        return {
            "fields": catalog.to_dict(),
            "active": copy.deepcopy(state.get("atom_property_color")),
        }

    def resolved_atom_property_range(
        self,
        state: Optional[dict[str, Any]] = None,
    ) -> tuple[float, float] | None:
        """Return the visible range for the active auto-scaled field.

        ``AtomPropertyColorSpec.value_range`` deliberately stays ``None`` in
        auto mode so the persisted state continues to mean *derive this from
        the selected source frames*.  The panel still needs to show the range
        that was derived, though.  Resolve just the property context here
        (without assembling geometry) so control refreshes can populate the
        two range inputs while retaining auto mode.
        """

        state = self.get_state() if state is None else state
        spec = state.get("atom_property_color") or {}
        if not spec or not spec.get("fields"):
            return None
        if spec.get("value_range") is not None:
            try:
                lower, upper = spec["value_range"]
                return float(lower), float(upper)
            except (TypeError, ValueError):
                return None

        try:
            bundle = self.get_bundle(state["structure"])
            source = getattr(bundle, "_property_structure_input", None)
            if source is None:
                from pathlib import Path

                from ..loader.structure_input import StructureFrame, StructureInput

                frame_index = int(bundle.frame_info.get("frame_index", 0))
                frame = StructureFrame(
                    frame_index,
                    bundle,
                    dict(bundle.frame_info),
                    dict(bundle.atom_arrays),
                )
                source = StructureInput(
                    Path(bundle.cif_path),
                    str(bundle.scene.get("input_format") or "cif"),
                    (frame,),
                    1,
                    getattr(bundle, "_property_manifest", None),
                )
            from ..properties import resolve_source_property_context

            context = resolve_source_property_context(source, spec)
        except (OSError, TypeError, ValueError, KeyError, IndexError):
            # The renderer reports malformed/missing fields through its normal
            # state validation path. A control refresh should remain usable if
            # an externally edited scene briefly contains such a spec.
            return None
        return tuple(float(value) for value in context.scale.value_range)


    def style_for_state(
        self,
        state: Optional[dict[str, Any]] = None,
        scene: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        state = self.current_state if state is None else state
        scene = self.scene_for_state(state) if scene is None else scene
        if state.get("bfdh_morphology") is not None:
            scene["bfdh_morphology"] = copy.deepcopy(state.get("bfdh_morphology"))
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
        style["display_mode"] = state.get(
            "display_mode", scene.get("display_mode", "formula_unit")
        )
        # An isolated cluster is explicitly non-periodic.  The display
        # options list is intentionally retained when switching scopes so a
        # user can turn Cell boundary off before returning to Unit cell, but
        # that stale token must never resurrect a lattice box (or its camera
        # extent) while the active scope is ``cluster``.
        if style["display_mode"] == "cluster":
            style["show_unit_cell"] = False
        style["material"] = state.get("material", style.get("material", "mesh"))
        style["style"] = state.get("style", style.get("style", "ball_stick"))
        style["disorder"] = state.get(
            "disorder", style.get("disorder", "opacity")
        )
        style["ortep_mode"] = state.get(
            "ortep_mode", style.get("ortep_mode", "ortep_axes")
        )
        style["label_mode"] = state.get(
            "label_mode", style.get("label_mode", "unique_sites")
        )
        style["fast_rendering"] = bool(state.get("fast_rendering", False))
        style["topology_enabled"] = bool(state.get("topology_enabled", False))
        style["topology_hull_color"] = str(state.get("topology_hull_color", "#7C5CBF"))
        # Pass through publication, isosurface, and axis-key style options.
        for key in LEGACY_STYLE_STATE_FIELDS:
            if key in state:
                style[key] = copy.deepcopy(state[key])
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
        style["atom_property_color"] = copy.deepcopy(
            scene.get("atom_property_color") or state.get("atom_property_color")
        )
        style["selection"] = copy.deepcopy(
            state.get("selection")
            or {"atom_labels": [], "active_label": None, "order": [], "targets": [], "active_target": None}
        )
        style["style_overrides"] = copy.deepcopy(state.get("style_overrides") or {})
        style["_target_revision"] = int(state.get("target_revision", 0))
        style["selection_highlight"] = current_config().colors.get(
            "selection_highlight", "#74808C"
        )
        # Phase 4 (view tools): persist the camera projection choice
        # onto the style dict so the renderer's
        # ``_plotly_camera_from_scene`` picks orthographic vs.
        # perspective without rebuilding the scene.
        style["projection"] = _coerce_projection(
            state.get("projection", style.get("projection", "perspective")),
            fallback=str(style.get("projection", "perspective")),
        )
        style["bfdh_morphology_color"] = str(
            state.get("bfdh_morphology_color", "#4f7cff")
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
        layout_signature = viewport_signature(state)
        style["uirevision"] = "{name}__{rev}__{sig}".format(
            name=scene.get("name", "scene"),
            rev=int(state.get("camera_revision", 0) or 0),
            sig=layout_signature,
        )
        # The interactive Dash app uses a separate SVG overlay for the
        # compass to avoid Plotly.relayout calls that interrupt gl3d's
        # render cycle during drag.
        style["axis_key_via_svg_overlay"] = True
        return style
