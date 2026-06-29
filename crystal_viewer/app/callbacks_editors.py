"""Editor callbacks: atom-groups and bond-groups tables (left sidebar).

Polyhedron config moved to ``callbacks_analysis.py`` (right Analysis tab).
Display transforms moved to ``callbacks_operations.py`` (right Operation tab).
"""
from __future__ import annotations

import time

from dash import ALL, Input, Output, State, callback_context, no_update

from .. import perf_log
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
)
from .status_helpers import surface_callback_error


def surface_editor_error(prefix, exc):
    surface_callback_error(prefix, exc, callback_ctx=callback_context)


def _build_atom_groups(
    *,
    color_ids,
    visibles,
    colors,
    kinds,
    elements_lists,
    opacities,
    materials,
    styles,
):
    new_groups = []
    for index, id_dict in enumerate(color_ids):
        group_id = id_dict.get("group_id")
        kind_value = kinds[index] if index < len(kinds) else _ATOM_GROUP_KIND_ALL
        if kind_value == _ATOM_GROUP_KIND_ALL:
            selector = {"all": True}
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
    color_ids,
    visibles,
    colors,
    kinds,
    elements_lists,
    opacities,
    radius_scales,
):
    new_groups = []
    for index, id_dict in enumerate(color_ids):
        group_id = id_dict.get("group_id")
        kind_value = kinds[index] if index < len(kinds) else _BOND_GROUP_KIND_ALL
        if kind_value == _BOND_GROUP_KIND_ALL:
            selector = {"all": True}
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


def register_editor_callbacks(app, backend):
    _surface_error = surface_editor_error

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
            if triggered.get("type") == "ag-row-kind":
                return _rebuild(), backend.get_state()
            return no_update, backend.get_state()

        return no_update, no_update

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
            if triggered.get("type") == "bg-row-kind":
                return _rebuild(), backend.get_state()
            return no_update, backend.get_state()

        return no_update, no_update
