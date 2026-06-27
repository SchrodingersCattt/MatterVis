from __future__ import annotations

import time
from typing import Any

from dash import ALL, Input, Output, State, callback_context, no_update

from .. import perf_log
from ..topology import DEFAULT_CENTROID_OFFSET_FRAC
from .backend import ViewerBackend
from .editor_tables import (
    _ATOM_GROUP_INHERIT,
    _ATOM_GROUP_KIND_ALL,
    _ATOM_GROUP_KIND_MAJOR,
    _ATOM_GROUP_KIND_MINOR,
    _BOND_GROUP_KIND_ALL,
    _BOND_GROUP_KIND_MAJOR,
    _BOND_GROUP_KIND_MINOR,
    _atom_groups_table_rows,
    _bond_groups_table_rows,
    _polyhedra_table_rows,
)
from .editor_transforms import _seed_text_to_selector, _transforms_table_rows
from .normalizers import _AUTO_LIGAND_VALUE
from .status_helpers import surface_callback_error


# Editor callbacks used to swallow backend exceptions silently
# (``except Exception: return no_update, no_update``), which made
# the UI look dead whenever ``add_transform`` / ``add_polyhedron``
# / ``patch_state`` raised (e.g. the MAX_ATOMS_AFTER_TRANSFORM cap
# or an MCK shape rejection). ``surface_editor_error`` routes the
# exception text into the hidden ``#status`` Div via
# ``ctx.set_props`` -- the existing ``mirror_legacy_status`` callback
# styles that string into the visible banner, so the user gets a
# real explanation instead of "click does nothing". The perf log
# records the original exception type + message for the Server log
# panel.
#
# Lifted to module scope so unit tests can drive it directly without
# spinning up a Dash callback context. The module-private alias
# ``_surface_error`` keeps the historical name available inside
# ``register_editor_callbacks`` (where it is referenced ~15 times)
# without each call site having to import the new name.
def surface_editor_error(prefix: str, exc: BaseException) -> None:
    surface_callback_error(prefix, exc, callback_ctx=callback_context)


def _build_polyhedron_specs(
    *,
    color_ids: list[dict[str, Any]],
    colors,
    centers,
    ligands,
    enableds,
    shell_modes,
    centroid_offsets,
    levels,
    center_kinds,
    hard_cutoffs,
    fallback_maxes,
    existing: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    new_specs: list[dict[str, Any]] = []
    for index, id_dict in enumerate(color_ids):
        spec_id = id_dict.get("spec_id")
        base = existing.get(spec_id, {})
        ligand_value = ligands[index] if index < len(ligands) else _AUTO_LIGAND_VALUE
        if ligand_value == _AUTO_LIGAND_VALUE:
            ligand_value = None
        new_specs.append(
            {
                "id": spec_id,
                "name": base.get("name") or "",
                "color": colors[index] if index < len(colors) else base.get("color"),
                "center_species": centers[index] if index < len(centers) else base.get("center_species"),
                "ligand_species": ligand_value,
                "enabled": "yes" in (enableds[index] if index < len(enableds) else []),
                "enforce_enclosure": (
                    (shell_modes[index] if index < len(shell_modes) else _POLY_SHELL_MODE_ENCLOSURE)
                    != _POLY_SHELL_MODE_GAP
                ),
                "centroid_offset_frac": (
                    centroid_offsets[index]
                    if index < len(centroid_offsets) and centroid_offsets[index] is not None
                    else base.get("centroid_offset_frac", DEFAULT_CENTROID_OFFSET_FRAC)
                ),
                "level": (
                    levels[index] if index < len(levels) and levels[index] else base.get("level")
                ),
                "center_kind": (
                    center_kinds[index]
                    if index < len(center_kinds) and center_kinds[index]
                    else base.get("center_kind")
                ),
                "hard_cutoff": (
                    hard_cutoffs[index]
                    if index < len(hard_cutoffs)
                    else base.get("hard_cutoff")
                ),
                "fallback_max": (
                    fallback_maxes[index]
                    if index < len(fallback_maxes)
                    else base.get("fallback_max")
                ),
                "instance_overrides": base.get("instance_overrides", {}),
            }
        )
    return new_specs


def _build_atom_groups(
    *,
    color_ids: list[dict[str, Any]],
    visibles,
    colors,
    kinds,
    elements_lists,
    opacities,
    materials,
    styles,
) -> list[dict[str, Any]]:
    new_groups: list[dict[str, Any]] = []
    for index, id_dict in enumerate(color_ids):
        group_id = id_dict.get("group_id")
        kind_value = kinds[index] if index < len(kinds) else _ATOM_GROUP_KIND_ALL
        if kind_value == _ATOM_GROUP_KIND_ALL:
            selector: dict[str, Any] = {"all": True}
        elif kind_value == _ATOM_GROUP_KIND_MINOR:
            selector = {"is_minor": True}
        elif kind_value == _ATOM_GROUP_KIND_MAJOR:
            selector = {"is_minor": False}
        else:
            selector = {
                "elements": list(elements_lists[index]) if index < len(elements_lists) and elements_lists[index] else []
            }
        opacity_value = opacities[index] if index < len(opacities) else 1.0
        opacity_payload = None if opacity_value is None or float(opacity_value) >= 0.999 else float(opacity_value)
        material_value = materials[index] if index < len(materials) else _ATOM_GROUP_INHERIT
        style_value = styles[index] if index < len(styles) else _ATOM_GROUP_INHERIT
        new_groups.append(
            {
                "id": group_id,
                "selector": selector,
                "color": colors[index] if index < len(colors) else None,
                "visible": "yes" in (visibles[index] if index < len(visibles) else ["yes"]),
                "opacity": opacity_payload,
                "material": None if material_value == _ATOM_GROUP_INHERIT else material_value,
                "style": None if style_value == _ATOM_GROUP_INHERIT else style_value,
            }
        )
    return new_groups


def _build_bond_groups(
    *,
    color_ids: list[dict[str, Any]],
    visibles,
    colors,
    kinds,
    elements_lists,
    opacities,
    radius_scales,
) -> list[dict[str, Any]]:
    new_groups: list[dict[str, Any]] = []
    for index, id_dict in enumerate(color_ids):
        group_id = id_dict.get("group_id")
        kind_value = kinds[index] if index < len(kinds) else _BOND_GROUP_KIND_ALL
        if kind_value == _BOND_GROUP_KIND_ALL:
            selector: dict[str, Any] = {"all": True}
        elif kind_value == _BOND_GROUP_KIND_MINOR:
            selector = {"is_minor": True}
        elif kind_value == _BOND_GROUP_KIND_MAJOR:
            selector = {"is_minor": False}
        else:
            elements = [str(e) for e in (elements_lists[index] if index < len(elements_lists) else []) if e]
            selector = {"between_elements": elements} if elements else {"all": True}
        new_groups.append(
            {
                "id": group_id,
                "selector": selector,
                "color": colors[index] if index < len(colors) else None,
                "visible": "yes" in (visibles[index] if index < len(visibles) else []),
                "opacity": float(opacities[index]) if index < len(opacities) and opacities[index] is not None else None,
                "radius_scale": float(radius_scales[index]) if index < len(radius_scales) and radius_scales[index] is not None else None,
                "enabled": True,
            }
        )
    return new_groups


def _build_transforms(
    *,
    enabled_ids: list[dict[str, Any]],
    enableds,
    param_a,
    param_b,
    param_c,
    param_seeds,
    param_radius,
    param_hops,
    param_maxhops,
    param_cutoff,
    param_ops,
    param_miller0,
    param_miller1,
    param_miller2,
    param_layers,
    param_vacuum,
    existing: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    new_transforms: list[dict[str, Any]] = []
    for index, id_dict in enumerate(enabled_ids):
        transform_id = id_dict.get("transform_id")
        base = existing.get(transform_id)
        if base is None:
            continue
        kind = base.get("kind") or "repeat"
        params: dict[str, Any] = {}
        if kind == "repeat":
            params = {
                "a": int(param_a[index]) if index < len(param_a) and param_a[index] is not None else int(base["params"].get("a", 1) or 1),
                "b": int(param_b[index]) if index < len(param_b) and param_b[index] is not None else int(base["params"].get("b", 1) or 1),
                "c": int(param_c[index]) if index < len(param_c) and param_c[index] is not None else int(base["params"].get("c", 1) or 1),
            }
        elif kind in ("grow_radius", "grow_bonds", "complete_fragment", "complete_polyhedron", "by_symmetry"):
            seeds_text = param_seeds[index] if index < len(param_seeds) else None
            seeds = _seed_text_to_selector(seeds_text) if seeds_text is not None else base["params"].get("seeds") or {}
            params["seeds"] = seeds
            if kind == "grow_radius":
                params["radius"] = float(param_radius[index]) if index < len(param_radius) and param_radius[index] is not None else float(base["params"].get("radius", 0.0) or 0.0)
            elif kind == "grow_bonds":
                params["hops"] = int(param_hops[index]) if index < len(param_hops) and param_hops[index] is not None else int(base["params"].get("hops", 1) or 1)
            elif kind == "complete_fragment":
                params["max_hops"] = int(param_maxhops[index]) if index < len(param_maxhops) and param_maxhops[index] is not None else int(base["params"].get("max_hops", 32) or 32)
            elif kind == "complete_polyhedron":
                params["cutoff"] = float(param_cutoff[index]) if index < len(param_cutoff) and param_cutoff[index] is not None else float(base["params"].get("cutoff", 4.0) or 4.0)
            elif kind == "by_symmetry":
                ops_text = param_ops[index] if index < len(param_ops) else None
                if ops_text:
                    try:
                        import json as _json
                        params["ops"] = _json.loads(ops_text)
                    except (ValueError, TypeError):
                        params["ops"] = base["params"].get("ops") or []
                else:
                    params["ops"] = base["params"].get("ops") or []
        elif kind == "slab":
            miller = [
                int(param_miller0[index]) if index < len(param_miller0) and param_miller0[index] is not None else (base["params"].get("miller") or [0, 0, 1])[0],
                int(param_miller1[index]) if index < len(param_miller1) and param_miller1[index] is not None else (base["params"].get("miller") or [0, 0, 1])[1],
                int(param_miller2[index]) if index < len(param_miller2) and param_miller2[index] is not None else (base["params"].get("miller") or [0, 0, 1])[2],
            ]
            layers_val = param_layers[index] if index < len(param_layers) and param_layers[index] is not None else base["params"].get("layers")
            vacuum_val = param_vacuum[index] if index < len(param_vacuum) and param_vacuum[index] is not None else base["params"].get("vacuum", 10.0)
            params = {
                "miller": miller,
                "layers": int(layers_val) if layers_val is not None else None,
                "vacuum": float(vacuum_val or 10.0),
            }
        new_transforms.append(
            {
                "id": transform_id,
                "name": base.get("name") or "",
                "kind": kind,
                "params": params,
                "enabled": "yes" in (enableds[index] if index < len(enableds) else []),
            }
        )
    return new_transforms


def register_editor_callbacks(app, backend):
    _surface_error = surface_editor_error

    @app.callback(
        Output("polyhedra-rows-container", "children", allow_duplicate=True),
        Output("agent-state-store", "data", allow_duplicate=True),
        Input("polyhedra-add-btn", "n_clicks"),
        Input("scene-tabs", "value"),
        Input({"type": "poly-row-color", "spec_id": ALL}, "value"),
        Input({"type": "poly-row-center", "spec_id": ALL}, "value"),
        Input({"type": "poly-row-ligand", "spec_id": ALL}, "value"),
        Input({"type": "poly-row-enabled", "spec_id": ALL}, "value"),
        Input({"type": "poly-row-shell-mode", "spec_id": ALL}, "value"),
        Input({"type": "poly-row-centroid-offset", "spec_id": ALL}, "value"),
        Input({"type": "poly-row-level", "spec_id": ALL}, "value"),
        Input({"type": "poly-row-center-kind", "spec_id": ALL}, "value"),
        Input({"type": "poly-row-hard-cutoff", "spec_id": ALL}, "value"),
        Input({"type": "poly-row-fallback-max", "spec_id": ALL}, "value"),
        Input({"type": "poly-row-delete", "spec_id": ALL}, "n_clicks"),
        State({"type": "poly-row-color", "spec_id": ALL}, "id"),
        prevent_initial_call=True,
    )
    def manage_polyhedra(
        add_clicks,
        active_scene_id,
        colors,
        centers,
        ligands,
        enableds,
        shell_modes,
        centroid_offsets,
        levels,
        center_kinds,
        hard_cutoffs,
        fallback_maxes,
        deletes,
        color_ids,
    ):
        # The second Output (``agent-state-store.data``) is the
        # critical perf fix for the inline-edit path: without it, an
        # in-row colour / centre / ligand / enabled change has to
        # wait for the 5 s ``agent-state-poll`` to round-trip via
        # ``sync_agent_state`` before ``update_view`` re-renders the
        # figure. Pushing the new state directly here cuts the
        # perceived latency from ~2.5 s (avg) to "the next frame".
        # ``broadcast=False`` on patch_state below stops the same
        # change from echoing back through the poll path on the next
        # tick.
        cb_start = time.monotonic()
        triggered = getattr(callback_context, "triggered_id", None)
        scene_id = active_scene_id or backend.active_scene_id()
        triggered_label = (
            triggered.get("type") if isinstance(triggered, dict) else triggered
        )
        species_options = backend.species_options(
            backend.get_state(scene_id).get("structure")
        )

        def _rebuild():
            specs = backend.list_polyhedron_specs(scene_id=scene_id)
            return _polyhedra_table_rows(specs, species_options)

        if triggered == "scene-tabs":
            return _rebuild(), no_update

        if triggered == "polyhedra-add-btn":
            if not species_options:
                return _rebuild(), no_update
            center_species = str(species_options[0]["value"])
            ligand_species = next(
                (str(option["value"]) for option in species_options if str(option["value"]) != center_species),
                None,
            )
            try:
                backend.add_polyhedron_spec(
                    center_species=center_species,
                    ligand_species=ligand_species,
                    enabled=True,
                    scene_id=scene_id,
                )
            except Exception as exc:
                _surface_error("Add polyhedron", exc)
                return no_update, no_update
            return _rebuild(), backend.get_state()

        if isinstance(triggered, dict) and triggered.get("type") == "poly-row-delete":
            spec_id = triggered.get("spec_id")
            if not spec_id:
                return no_update, no_update
            backend.remove_polyhedron_spec(spec_id, scene_id=scene_id)
            return _rebuild(), backend.get_state()

        if isinstance(triggered, dict) and triggered.get("type", "").startswith("poly-row-"):
            # Inline edit. Reconstruct the full spec list from the
            # current ALL-input values and persist it. We rely on
            # ``color_ids`` (one id-dict per row) to give us the spec_id
            # ordering that matches the value lists.
            if not color_ids:
                return no_update, no_update
            existing = {
                spec["id"]: spec
                for spec in backend.list_polyhedron_specs(scene_id=scene_id)
            }
            new_specs = _build_polyhedron_specs(
                color_ids=color_ids,
                colors=colors,
                centers=centers,
                ligands=ligands,
                enableds=enableds,
                shell_modes=shell_modes,
                centroid_offsets=centroid_offsets,
                levels=levels,
                center_kinds=center_kinds,
                hard_cutoffs=hard_cutoffs,
                fallback_maxes=fallback_maxes,
                existing=existing,
            )
            try:
                backend.patch_state({"polyhedron_specs": new_specs}, scene_id=scene_id, broadcast=False)
            except Exception as exc:
                _surface_error("Update polyhedron", exc)
                return _rebuild(), no_update
            perf_log.record(
                "callback:manage_polyhedra",
                duration_ms=(time.monotonic() - cb_start) * 1000.0,
                kind="cb",
                info={
                    "trigger": triggered_label,
                    "n_specs": len(new_specs),
                    "scene_id": scene_id,
                },
            )
            # Level toggles flip the disabled state of center_kind /
            # hard_cutoff inside the row, so they must trigger a rebuild.
            # Other inline edits keep ``no_update`` for children to avoid
            # mid-edit React tear-down.
            if triggered_label == "poly-row-level":
                return _rebuild(), backend.get_state()
            return no_update, backend.get_state()

        return no_update, no_update

    # ------------------------------------------------------------------
    # Phase 3 UI: Atom-groups table.
    #
    # Same pattern as the polyhedra callback, plus three quick-preset
    # buttons (Monochrome / Hide H / Clear all) that translate to
    # backend CRUD calls.
    # ------------------------------------------------------------------
    @app.callback(
        Output("atom-groups-rows-container", "children", allow_duplicate=True),
        Output("agent-state-store", "data", allow_duplicate=True),
        Input("atom-groups-add-btn", "n_clicks"),
        Input("atom-groups-preset-mono", "n_clicks"),
        Input("atom-groups-clear-btn", "n_clicks"),
        Input("scene-tabs", "value"),
        Input({"type": "ag-row-visible", "group_id": ALL}, "value"),
        Input({"type": "ag-row-color", "group_id": ALL}, "value"),
        Input({"type": "ag-row-kind", "group_id": ALL}, "value"),
        Input({"type": "ag-row-elements", "group_id": ALL}, "value"),
        Input({"type": "ag-row-opacity", "group_id": ALL}, "value"),
        Input({"type": "ag-row-material", "group_id": ALL}, "value"),
        Input({"type": "ag-row-style", "group_id": ALL}, "value"),
        Input({"type": "ag-row-delete", "group_id": ALL}, "n_clicks"),
        State({"type": "ag-row-color", "group_id": ALL}, "id"),
        prevent_initial_call=True,
    )
    def manage_atom_groups(
        add_clicks,
        mono_clicks,
        clear_clicks,
        active_scene_id,
        visibles,
        colors,
        kinds,
        elements_lists,
        opacities,
        materials,
        styles,
        deletes,
        color_ids,
    ):
        # Same perf rationale as ``manage_polyhedra``: the second
        # Output pushes the new state straight into ``agent-state-store``
        # so ``update_view`` re-renders on the next frame instead of
        # waiting for the 5 s ``agent-state-poll``. Without it, an
        # opacity / colour / visibility change has a 0-5 s perceived
        # latency.
        cb_start = time.monotonic()
        triggered = getattr(callback_context, "triggered_id", None)
        scene_id = active_scene_id or backend.active_scene_id()
        triggered_label = (
            triggered.get("type") if isinstance(triggered, dict) else triggered
        )

        def _rebuild():
            groups = backend.list_atom_groups(scene_id=scene_id)
            return _atom_groups_table_rows(
                groups, backend.element_options(backend.get_state(scene_id))
            )

        if triggered == "scene-tabs":
            return _rebuild(), no_update

        if triggered == "atom-groups-add-btn":
            try:
                backend.add_atom_group(selector={"all": True}, color="#888888", scene_id=scene_id)
            except Exception as exc:
                _surface_error("Add atom group", exc)
                return no_update, no_update
            return _rebuild(), backend.get_state()

        if triggered == "atom-groups-preset-mono":
            backend.add_atom_group(
                selector={"all": True},
                color="#000000",
                name="monochrome",
                scene_id=scene_id,
            )
            return _rebuild(), backend.get_state()

        if triggered == "atom-groups-clear-btn":
            for group in list(backend.list_atom_groups(scene_id=scene_id)):
                backend.remove_atom_group(group["id"], scene_id=scene_id)
            return _rebuild(), backend.get_state()

        if isinstance(triggered, dict) and triggered.get("type") == "ag-row-delete":
            group_id = triggered.get("group_id")
            if not group_id:
                return no_update, no_update
            backend.remove_atom_group(group_id, scene_id=scene_id)
            return _rebuild(), backend.get_state()

        if isinstance(triggered, dict) and triggered.get("type", "").startswith("ag-row-"):
            if not color_ids:
                return no_update, no_update
            new_groups = _build_atom_groups(
                color_ids=color_ids,
                visibles=visibles,
                colors=colors,
                kinds=kinds,
                elements_lists=elements_lists,
                opacities=opacities,
                materials=materials,
                styles=styles,
            )
            try:
                backend.patch_state({"atom_groups": new_groups}, scene_id=scene_id, broadcast=False)
            except Exception as exc:
                _surface_error("Update atom group", exc)
                return _rebuild(), no_update
            perf_log.record(
                "callback:manage_atom_groups",
                duration_ms=(time.monotonic() - cb_start) * 1000.0,
                kind="cb",
                info={
                    "trigger": triggered_label,
                    "n_groups": len(new_groups),
                    "scene_id": scene_id,
                },
            )
            # Special case: switching kind from "all" -> "by element"
            # needs to reveal the elements multi-select that's
            # display:none in the existing DOM. Rebuild children to
            # update the visibility toggle.
            if triggered.get("type") == "ag-row-kind":
                return _rebuild(), backend.get_state()
            return no_update, backend.get_state()

        return no_update, no_update

    # ------------------------------------------------------------------
    # Phase 4 UI: Bond-groups table.
    #
    # Same dispatch pattern as ``manage_atom_groups`` -- pattern-matched
    # row inputs, single ALL callback, ``agent-state-store`` second
    # Output for instant re-render. Only difference: bond-specific
    # selectors (between elements / minor / major) and per-bond
    # ``radius_scale``.
    # ------------------------------------------------------------------
    @app.callback(
        Output("bond-groups-rows-container", "children", allow_duplicate=True),
        Output("agent-state-store", "data", allow_duplicate=True),
        Input("bond-groups-add-btn", "n_clicks"),
        Input("scene-tabs", "value"),
        Input({"type": "bg-row-visible", "group_id": ALL}, "value"),
        Input({"type": "bg-row-color", "group_id": ALL}, "value"),
        Input({"type": "bg-row-kind", "group_id": ALL}, "value"),
        Input({"type": "bg-row-elements", "group_id": ALL}, "value"),
        Input({"type": "bg-row-opacity", "group_id": ALL}, "value"),
        Input({"type": "bg-row-radius", "group_id": ALL}, "value"),
        Input({"type": "bg-row-delete", "group_id": ALL}, "n_clicks"),
        State({"type": "bg-row-color", "group_id": ALL}, "id"),
        prevent_initial_call=True,
    )
    def manage_bond_groups(
        add_clicks,
        active_scene_id,
        visibles,
        colors,
        kinds,
        elements_lists,
        opacities,
        radius_scales,
        deletes,
        color_ids,
    ):
        cb_start = time.monotonic()
        triggered = getattr(callback_context, "triggered_id", None)
        scene_id = active_scene_id or backend.active_scene_id()
        triggered_label = (
            triggered.get("type") if isinstance(triggered, dict) else triggered
        )

        def _rebuild():
            groups = backend.list_bond_groups(scene_id=scene_id)
            return _bond_groups_table_rows(
                groups, backend.element_options(backend.get_state(scene_id))
            )

        if triggered == "scene-tabs":
            return _rebuild(), no_update

        if triggered == "bond-groups-add-btn":
            try:
                backend.add_bond_group(selector={"all": True}, scene_id=scene_id)
            except Exception as exc:
                _surface_error("Add bond group", exc)
                return no_update, no_update
            return _rebuild(), backend.get_state()

        if isinstance(triggered, dict) and triggered.get("type") == "bg-row-delete":
            group_id = triggered.get("group_id")
            if not group_id:
                return no_update, no_update
            backend.remove_bond_group(group_id, scene_id=scene_id)
            return _rebuild(), backend.get_state()

        if isinstance(triggered, dict) and triggered.get("type", "").startswith("bg-row-"):
            if not color_ids:
                return no_update, no_update
            new_groups = _build_bond_groups(
                color_ids=color_ids,
                visibles=visibles,
                colors=colors,
                kinds=kinds,
                elements_lists=elements_lists,
                opacities=opacities,
                radius_scales=radius_scales,
            )
            try:
                backend.patch_state({"bond_groups": new_groups}, scene_id=scene_id, broadcast=False)
            except Exception as exc:
                _surface_error("Update bond group", exc)
                return _rebuild(), no_update
            perf_log.record(
                "callback:manage_bond_groups",
                duration_ms=(time.monotonic() - cb_start) * 1000.0,
                kind="cb",
                info={
                    "trigger": triggered_label,
                    "n_groups": len(new_groups),
                    "scene_id": scene_id,
                },
            )
            # ``bg-row-kind`` toggles between visible/hidden elements
            # multi-select, so rebuild children to reveal it.
            if triggered.get("type") == "bg-row-kind":
                return _rebuild(), backend.get_state()
            return no_update, backend.get_state()

        return no_update, no_update

    # ------------------------------------------------------------------
    # Phase 4 UI: Transforms pipeline.
    #
    # The ``transforms-rows-container`` shows one row per transform spec
    # in ``state["transforms"]``, in pipeline order. Mutations:
    #
    #   - ``transforms-add-btn`` + ``transforms-kind-select`` -> append a
    #     new transform of the selected kind (sane defaults).
    #   - ``transforms-preset-2x`` / ``-3x`` / ``-clear-repeat`` /
    #     ``-clear-btn`` -> quick presets for the most common cases.
    #   - ``trf-row-delete`` / ``-up`` / ``-down`` -> remove / reorder.
    #   - ``trf-row-enabled`` and any ``trf-param-*`` -> patch the
    #     transform via ``update_transform`` (kind-aware).
    #
    # Because per-row parameter widgets vary by kind, the dispatch reads
    # ``State`` lists for every possible widget type and only consumes
    # the ones the row's kind cares about. Empty / missing values fall
    # back to the spec's defaults.
    # ------------------------------------------------------------------
    @app.callback(
        Output("transforms-rows-container", "children", allow_duplicate=True),
        Output("agent-state-store", "data", allow_duplicate=True),
        Input("transforms-add-btn", "n_clicks"),
        Input("transforms-preset-2x", "n_clicks"),
        Input("transforms-preset-3x", "n_clicks"),
        Input("transforms-clear-repeat", "n_clicks"),
        Input("transforms-clear-btn", "n_clicks"),
        Input("scene-tabs", "value"),
        Input({"type": "trf-row-enabled", "transform_id": ALL}, "value"),
        Input({"type": "trf-row-delete", "transform_id": ALL}, "n_clicks"),
        Input({"type": "trf-row-up", "transform_id": ALL}, "n_clicks"),
        Input({"type": "trf-row-down", "transform_id": ALL}, "n_clicks"),
        Input({"type": "trf-param-a", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-b", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-c", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-seeds", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-radius", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-hops", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-maxhops", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-cutoff", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-ops", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-miller-0", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-miller-1", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-miller-2", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-layers", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-vacuum", "transform_id": ALL}, "value"),
        State("transforms-kind-select", "value"),
        State({"type": "trf-row-enabled", "transform_id": ALL}, "id"),
        prevent_initial_call=True,
    )
    def manage_transforms(
        add_clicks,
        preset_2x_clicks,
        preset_3x_clicks,
        clear_repeat_clicks,
        clear_all_clicks,
        active_scene_id,
        enableds,
        deletes,
        ups,
        downs,
        param_a,
        param_b,
        param_c,
        param_seeds,
        param_radius,
        param_hops,
        param_maxhops,
        param_cutoff,
        param_ops,
        param_miller0,
        param_miller1,
        param_miller2,
        param_layers,
        param_vacuum,
        kind_select,
        enabled_ids,
    ):
        cb_start = time.monotonic()
        triggered = getattr(callback_context, "triggered_id", None)
        scene_id = active_scene_id or backend.active_scene_id()
        triggered_label = (
            triggered.get("type") if isinstance(triggered, dict) else triggered
        )

        def _rebuild():
            return _transforms_table_rows(backend.list_transforms(scene_id=scene_id))

        if triggered == "scene-tabs":
            return _rebuild(), no_update

        # Quick presets ------------------------------------------------
        if triggered == "transforms-preset-2x" or triggered == "transforms-preset-3x":
            n = 2 if triggered == "transforms-preset-2x" else 3
            try:
                backend.patch_state(
                    {"supercell": {"a": n, "b": n, "c": n}},
                    scene_id=scene_id,
                )
            except Exception as exc:
                _surface_error(f"Repeat {n}x{n}x{n}", exc)
                return _rebuild(), no_update
            return _rebuild(), backend.get_state()

        if triggered == "transforms-clear-repeat":
            try:
                backend.patch_state(
                    {"supercell": {"a": 1, "b": 1, "c": 1}},
                    scene_id=scene_id,
                )
            except Exception as exc:
                _surface_error("Clear repeat", exc)
                return _rebuild(), no_update
            return _rebuild(), backend.get_state()

        if triggered == "transforms-clear-btn":
            try:
                backend.patch_state({"transforms": []}, scene_id=scene_id)
            except Exception as exc:
                _surface_error("Clear all transforms", exc)
                return _rebuild(), no_update
            return _rebuild(), backend.get_state()

        if triggered == "transforms-add-btn":
            kind = kind_select or "repeat"
            # Defaults are intentionally chosen to be no-ops or
            # near-no-ops so an "Add" click never blows past the
            # MAX_ATOMS_AFTER_TRANSFORM cap when the pipeline already
            # carries a supercell. ``repeat 1x1x1`` is a harmless
            # placeholder the user can edit in-place; the legacy
            # 2x2x2 default fired the cap for any structure already
            # multiplied >= 2x in the previous transform.
            defaults_by_kind = {
                "repeat": {"a": 1, "b": 1, "c": 1},
                "grow_radius": {"seeds": {"all": True}, "radius": 4.0},
                "grow_bonds": {"seeds": {"all": True}, "hops": 1},
                "complete_fragment": {"seeds": {"all": True}, "max_hops": 32},
                "complete_polyhedron": {"seeds": {"all": True}, "cutoff": 4.0},
                "by_symmetry": {"seeds": {"all": True}, "ops": []},
                "slab": {"miller": [0, 0, 1], "layers": 3, "vacuum": 10.0},
            }
            try:
                backend.add_transform(
                    kind=kind,
                    params=defaults_by_kind.get(kind, {}),
                    scene_id=scene_id,
                )
            except Exception as exc:
                _surface_error(f"Add {kind} transform", exc)
                return _rebuild(), no_update
            return _rebuild(), backend.get_state()

        if isinstance(triggered, dict) and triggered.get("type") == "trf-row-delete":
            transform_id = triggered.get("transform_id")
            if not transform_id:
                return no_update, no_update
            backend.remove_transform(transform_id, scene_id=scene_id)
            return _rebuild(), backend.get_state()

        if isinstance(triggered, dict) and triggered.get("type") in ("trf-row-up", "trf-row-down"):
            transform_id = triggered.get("transform_id")
            transforms = list(backend.list_transforms(scene_id=scene_id))
            ids = [t["id"] for t in transforms]
            if transform_id not in ids:
                return no_update, no_update
            i = ids.index(transform_id)
            j = i - 1 if triggered.get("type") == "trf-row-up" else i + 1
            if j < 0 or j >= len(ids):
                return no_update, no_update
            ids[i], ids[j] = ids[j], ids[i]
            try:
                backend.reorder_transforms(ids, scene_id=scene_id)
            except Exception as exc:
                _surface_error("Reorder transforms", exc)
                return _rebuild(), no_update
            return _rebuild(), backend.get_state()

        # Inline edit (enabled toggle or any param change) -------------
        if isinstance(triggered, dict) and (
            triggered.get("type") == "trf-row-enabled"
            or triggered.get("type", "").startswith("trf-param-")
        ):
            if not enabled_ids:
                return no_update, no_update
            existing = {t["id"]: t for t in backend.list_transforms(scene_id=scene_id)}
            new_transforms = _build_transforms(
                enabled_ids=enabled_ids,
                enableds=enableds,
                param_a=param_a,
                param_b=param_b,
                param_c=param_c,
                param_seeds=param_seeds,
                param_radius=param_radius,
                param_hops=param_hops,
                param_maxhops=param_maxhops,
                param_cutoff=param_cutoff,
                param_ops=param_ops,
                param_miller0=param_miller0,
                param_miller1=param_miller1,
                param_miller2=param_miller2,
                param_layers=param_layers,
                param_vacuum=param_vacuum,
                existing=existing,
            )
            try:
                backend.patch_state({"transforms": new_transforms}, scene_id=scene_id, broadcast=False)
            except Exception as exc:
                _surface_error("Update transform", exc)
                return _rebuild(), no_update
            perf_log.record(
                "callback:manage_transforms",
                duration_ms=(time.monotonic() - cb_start) * 1000.0,
                kind="cb",
                info={
                    "trigger": triggered_label,
                    "n_transforms": len(new_transforms),
                    "scene_id": scene_id,
                },
            )
            # An enabled toggle doesn't change the parameter widgets,
            # but ``patch_state`` may have mutated the transform list
            # ordering / id set; safe to push state without rebuilding.
            return no_update, backend.get_state()

        return no_update, no_update

    # ------------------------------------------------------------------
    # Phase 4 UI: right-click context menu + keyboard shortcuts.
    #
    # Wiring overview:
    #   1. ``assets/right_click_menu.js`` listens for native
    #      ``contextmenu`` on ``#crystal-graph`` and writes a payload
    #      ``{kind, payload, x, y, ts}`` into
    #      ``dcc.Store(id="rightclick-target")`` via
    #      ``dash_clientside.set_props``.
    #   2. ``assets/keyboard_shortcuts.js`` writes the same store but
    #      with an extra ``action`` field (e.g. ``"supercell_2x"``,
    #      ``"hide"``, ``"grow_bonds"``) so a single dispatch callback
    #      can handle keyboard actions.
    #   3. ``sync_rightclick_fallback`` mirrors the hidden text-input
    #      fallback into the store for the rare case set_props isn't
    #      bootstrapped.
    #   4. ``render_rightclick_menu`` rebuilds the popover children
    #      based on the picked-target kind and positions it.
    #   5. ``apply_rightclick_action`` dispatches both popover button
    #      clicks (Hide / Grow / Analyze / Promote) and keyboard
    #      shortcut actions to backend mutations.
    #   6. ``apply_rightclick_color`` handles the inline colour picker
    #      that lives inside the popover.
    #   7. ``toggle_kbd_help`` shows/hides the keyboard-help overlay
    #      (close button only -- the JS handles the ``?`` toggle).
    # ------------------------------------------------------------------
