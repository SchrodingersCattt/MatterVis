"""Analysis panel callbacks: polyhedra config + analysis status.

The polyhedra table moves from the left sidebar into the right
"Analysis" tab.  Callbacks are extracted from
``callbacks_editors.py`` with the same dispatch pattern and perf
optimizations.
"""
from __future__ import annotations

import time
from typing import Any

from dash import ALL, Input, Output, State, callback_context, no_update

from .. import perf_log
from ..topology import DEFAULT_CENTROID_OFFSET_FRAC
from .editor_tables import _polyhedra_table_rows
from .normalizers import _AUTO_LIGAND_VALUE
from .shared import _POLY_SHELL_MODE_ENCLOSURE, _POLY_SHELL_MODE_GAP
from .status_helpers import surface_callback_error


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


def register_analysis_callbacks(app, backend):
    _surface_error = surface_callback_error

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
            if triggered_label == "poly-row-level":
                return _rebuild(), backend.get_state()
            return no_update, backend.get_state()

        return no_update, no_update

    @app.callback(
        Output("bfdh-results-container", "children"),
        Input("bfdh-run-btn", "n_clicks"),
        Input("scene-tabs", "value"),
        State("bfdh-max-index", "value"),
        State("bfdh-top-n", "value"),
        prevent_initial_call=True,
    )
    def run_bfdh_analysis(n_clicks, active_scene_id, max_index, top_n):
        triggered = getattr(callback_context, "triggered_id", None)
        if triggered == "scene-tabs":
            return ""
        if not n_clicks:
            return no_update

        scene_id = active_scene_id or backend.active_scene_id()
        try:
            max_idx = int(max_index) if max_index is not None else 2
            n = int(top_n) if top_n is not None else 10
        except (TypeError, ValueError):
            max_idx, n = 2, 10

        t0 = time.perf_counter()
        result = backend.run_bfdh_analysis(scene_id=scene_id, max_index=max_idx, top_n=n)
        perf_log.record(
            "bfdh:run_analysis",
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            kind="event",
            info={
                "scene_id": scene_id,
                "max_index": max_idx,
                "top_n": n,
                "facet_count": len(result.get("wulff_facets") or []),
            },
        )
        if result["status"] == "error":
            _surface_error("BFDH Analysis", Exception("\n".join(result["warnings"])))
            return html.Div("Analysis failed.", style={"color": "#A00"})

        facets = result.get("facets") or []
        if not facets:
            return html.Div("No facets found.", style={"color": "#777"})

        wulff_facets = result.get("wulff_facets") or []
        if wulff_facets:
            backend.patch_state({
                "bfdh_morphology": {
                    "facets": wulff_facets,
                    "enabled": True,
                    "scale": 1.0,
                    "opacity": 0.3
                }
            }, scene_id=scene_id, broadcast=False)

        from dash import html
        rows = []
        # Header
        rows.append(
            html.Tr([
                html.Th("hkl", style={"textAlign": "left", "padding": "2px 4px", "borderBottom": "1px solid #ccc"}),
                html.Th("d_hkl (Å)", style={"textAlign": "right", "padding": "2px 4px", "borderBottom": "1px solid #ccc"}),
                html.Th("Importance", style={"textAlign": "right", "padding": "2px 4px", "borderBottom": "1px solid #ccc"}),
                html.Th("Mult", style={"textAlign": "right", "padding": "2px 4px", "borderBottom": "1px solid #ccc"}),
            ])
        )
        for f in facets:
            hkl_str = f"({f['miller_index'][0]}, {f['miller_index'][1]}, {f['miller_index'][2]})"
            rows.append(
                html.Tr([
                    html.Td(hkl_str, style={"padding": "2px 4px"}),
                    html.Td(f"{f['d_hkl']:.3f}", style={"textAlign": "right", "padding": "2px 4px"}),
                    html.Td(f"{f['relative_morphological_importance']:.3f}", style={"textAlign": "right", "padding": "2px 4px"}),
                    html.Td(str(f.get("multiplicity", 1)), style={"textAlign": "right", "padding": "2px 4px"}),
                ])
            )

        return html.Table(
            rows,
            style={"width": "100%", "borderCollapse": "collapse", "marginTop": "4px"}
        )

    @app.callback(
        Output("bfdh-morphology-enabled", "value"),
        Input("bfdh-morphology-enabled", "value"),
        Input("bfdh-morphology-scale", "value"),
        Input("bfdh-morphology-opacity", "value"),
        Input("bfdh-morphology-color", "value"),
        State("scene-tabs", "value"),
        prevent_initial_call=True,
    )
    def update_bfdh_morphology_controls(enabled_val, scale, opacity, color, active_scene_id):
        scene_id = active_scene_id or backend.active_scene_id()
        state = backend.get_state(scene_id)
        morph = state.get("bfdh_morphology")
        patch: dict[str, Any] = {}
        if morph:
            morph = dict(morph)
            morph["enabled"] = bool(enabled_val and "enabled" in enabled_val)
            morph["scale"] = float(scale)
            morph["opacity"] = float(opacity)
            patch["bfdh_morphology"] = morph
        if color:
            from .normalizers import _coerce_hex_color

            patch["bfdh_morphology_color"] = _coerce_hex_color(
                color,
                str(state.get("bfdh_morphology_color", "#4f7cff")),
            )
        if patch:
            backend.patch_state(patch, scene_id=scene_id, broadcast=False)
        return no_update
