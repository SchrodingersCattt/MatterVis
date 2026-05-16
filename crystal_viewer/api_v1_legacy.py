from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .api_shared import *


def register_v1_routes(v1, backend, handlers: dict) -> None:
    _perf_payload = handlers["_perf_payload"]
    healthz_v2 = handlers["healthz_v2"]
    upload_cif = handlers["upload_cif"]
    structures = handlers["structures"]
    scene = handlers["scene"]
    screenshot = handlers["screenshot"]
    preset_save = handlers["preset_save"]
    preset_load = handlers["preset_load"]
    export_static = handlers["export_static"]
    @v1.get("/perf")
    def perf_get_v1():
        return _perf_payload()

    @v1.post("/perf/clear")
    def perf_clear_v1():
        perf_log.clear()
        return jsonify({"cleared": True})

    @v1.get("/state")
    def v1_get_state():
        return jsonify(backend.get_state())

    @v1.get("/healthz")
    def healthz_v1():
        return healthz_v2()

    # Fields that only exist in v2 (Phase 1/2 per-scene CRUD models).
    # We still accept them on POST /api/v1/state so old scripts that
    # POST a full snapshot back to v1 keep working, but we attach a
    # ``Deprecation`` header (RFC 8594) so callers can spot the
    # migration target without surprise behaviour changes. New code
    # should use the dedicated v2 CRUD endpoints for these fields.
    _V2_ONLY_STATE_FIELDS = {
        "polyhedron_specs": "/api/v2/polyhedra",
        "atom_groups": "/api/v2/atom_groups",
        "bond_groups": "/api/v2/bond_groups",
        "transforms": "/api/v2/transforms",
        "supercell": "/api/v2/transforms (POST kind=repeat)",
    }

    @v1.post("/state")
    def v1_post_state():
        payload = request.get_json(force=True, silent=True) or {}
        v2_only_used = [k for k in _V2_ONLY_STATE_FIELDS if k in payload]
        result = backend.patch_state(payload)
        response = jsonify(result)
        if v2_only_used:
            response.headers["Deprecation"] = "true"
            target_paths = ", ".join(_V2_ONLY_STATE_FIELDS[k] for k in v2_only_used)
            response.headers["Warning"] = (
                f'299 - "fields {v2_only_used!r} are v2-only; '
                f'use {target_paths} for CRUD with proper validation"'
            )
        return response

    @v1.get("/camera")
    def v1_get_camera():
        return jsonify({"camera": backend.get_camera()})

    @v1.post("/camera")
    def v1_post_camera():
        payload = request.get_json(force=True, silent=True) or {}
        return jsonify({"camera": backend.set_camera(payload.get("camera", payload))})

    @v1.post("/camera/action")
    def v1_post_camera_action():
        payload = request.get_json(force=True, silent=True) or {}
        action = payload.get("action")
        if not action:
            return jsonify({"error": "action is required"}), 400
        rest = {key: value for key, value in payload.items() if key != "action"}
        return jsonify({"camera": backend.camera_action(action, **rest)})

    @v1.post("/upload")
    def v1_upload_cif():
        return upload_cif()

    @v1.get("/structures")
    def v1_structures():
        return structures()

    @v1.get("/scene/<name>")
    def v1_scene(name: str):
        return scene(name)

    @v1.post("/topology")
    def v1_topology():
        payload = request.get_json(force=True, silent=True) or {}
        structure = payload.get("structure") or backend.get_state().get("structure")
        center_index = payload.get("center_index")
        cutoff = float(payload.get("cutoff", 10.0))
        if center_index is None:
            return jsonify({"error": "center_index is required"}), 400
        return jsonify(
            backend.query_topology(
                structure=structure,
                center_index=int(center_index),
                cutoff=cutoff,
                center_species=payload.get("center_species"),
                ligand_species=payload.get("ligand_species"),
                level=payload.get("level", "molecule"),
                enforce_enclosure=payload.get("enforce_enclosure", True),
                centroid_offset_frac=payload.get("centroid_offset_frac"),
            )
        )

    @v1.get("/screenshot")
    def v1_screenshot():
        return screenshot()

    @v1.post("/preset/save")
    def v1_preset_save():
        return preset_save()

    @v1.post("/preset/load")
    def v1_preset_load():
        return preset_load()

    @v1.post("/export")
    def v1_export_static():
        return export_static()
