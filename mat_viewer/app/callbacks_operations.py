"""Operation panel callbacks: display transforms + disorder resolve.

The transforms table moves from the left sidebar into the right
"Operation" tab, alongside the existing disorder resolve section.
"""
from __future__ import annotations

import time
from typing import Any

from dash import ALL, Input, Output, State, callback_context, no_update

from .. import perf_log
from .editor_transforms import _seed_text_to_selector, _transforms_table_rows
from .status_helpers import surface_callback_error


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


def register_operations_callbacks(app, backend):
    _surface_error = surface_callback_error

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
                backend.add_transform(
                    "repeat",
                    {"a": n, "b": n, "c": n},
                    name=f"Repeat {n}x{n}x{n}",
                    scene_id=scene_id,
                )
            except Exception as exc:
                _surface_error(f"Repeat {n}x{n}x{n}", exc)
                return _rebuild(), no_update
            return _rebuild(), backend.get_state()

        if triggered == "transforms-clear-repeat":
            try:
                transforms = list(backend.list_transforms(scene_id=scene_id))
                for t in transforms:
                    if t.get("kind") == "repeat":
                        backend.remove_transform(t["id"], scene_id=scene_id)
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
            return no_update, backend.get_state()

        return no_update, no_update
