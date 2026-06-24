from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *


def register_export_routes(v2, backend) -> dict:
    @v2.get("/screenshot")
    def screenshot():
        wants_json_errors = "application/json" in (request.headers.get("Accept") or "")
        at_version = request.args.get("at_version")
        if at_version not in (None, ""):
            try:
                timeout = float(request.args.get("timeout", 30.0))
                ready = backend.wait_for_version(int(at_version), timeout=timeout)
            except (TypeError, ValueError) as exc:
                return _error_response(exc, 400, hint="at_version must be an integer and timeout must be numeric")
            if not ready:
                return _error_response(
                    TimeoutError(f"timed out waiting for state version {at_version}"),
                    504,
                    hint="Retry after polling GET /api/v2/state for the current version.",
                )
        def _int_arg(name: str):
            raw = request.args.get(name)
            return None if raw in (None, "") else int(raw)

        def _float_arg(name: str, default: float):
            raw = request.args.get(name)
            return default if raw in (None, "") else float(raw)

        try:
            png = backend.render_current_png(
                scene_id=request.args.get("scene_id"),
                raise_errors=wants_json_errors,
                width=_int_arg("width"),
                height=_int_arg("height"),
                scale=_float_arg("scale", 2.0),
                fast=str(request.args.get("fast", "")).lower() in {"1", "true", "yes", "on"},
            )
        except (TypeError, ValueError) as exc:
            return _error_response(exc, 400, hint="width and height must be integers; scale must be numeric")
        except Exception as exc:
            return _error_response(exc, 503, hint="Plotly/Kaleido image export failed")
        return Response(png, mimetype="image/png")

    @v2.post("/preset/save")
    def preset_save():
        payload = request.get_json(force=True, silent=True) or {}
        path = payload.get("path")
        allow_external = bool(payload.get("allow_external")) or str(request.args.get("allow_external", "")).lower() in {"1", "true", "yes"}
        try:
            return jsonify(backend.save_preset(path=path, allow_external=allow_external))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @v2.post("/preset/load")
    def preset_load():
        payload = request.get_json(force=True, silent=True) or {}
        path = payload.get("path")
        allow_external = bool(payload.get("allow_external")) or str(request.args.get("allow_external", "")).lower() in {"1", "true", "yes"}
        try:
            return jsonify(backend.load_preset_from_path(path, allow_external=allow_external))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @v2.post("/export")
    def export_static():
        payload = request.get_json(force=True, silent=True) or {}
        return jsonify(backend.export_static(output_path=payload.get("output_path")))

    # ----- polyhedron specs (Phase 1) -----------------------------------
    #
    # Per-scene named-row data model for coordination polymers. Each spec
    # = {id, name, center_species, ligand_species, color, enabled,
    # packing-shell knobs}. Empty list (DELETE-all or never-set) falls back to the
    # legacy ``topology_species_keys`` + shared ``topology_hull_color``
    # behaviour. See ``agents/polyhedron_api.md``.

    return locals()
