from __future__ import annotations

import json
import time

from flask import Blueprint, Response, jsonify, request

from . import perf_log

try:
    from flask_sock import Sock
except Exception:  # pragma: no cover - optional dependency
    Sock = None


def register_api(dash_app, backend) -> None:
    server = dash_app.server

    def _scene_id_from_request() -> str | None:
        payload = request.get_json(silent=True) if request.method not in ("GET", "HEAD") else None
        return request.args.get("scene_id") or ((payload or {}).get("scene_id") if isinstance(payload, dict) else None)

    v2 = Blueprint("crystal_viewer_api_v2", __name__, url_prefix="/api/v2")

    @v2.get("/scenes")
    def scenes_list():
        return jsonify({"active_id": backend.active_scene_id(), "scenes": backend.scene_options()})

    @v2.post("/scenes")
    def scenes_create():
        payload = request.get_json(force=True, silent=True) or {}
        return jsonify(
            backend.create_scene(
                structure=payload.get("structure") or payload.get("structure_name"),
                label=payload.get("label"),
                state=payload.get("state"),
            )
        )

    @v2.patch("/scenes/<scene_id>")
    def scenes_patch(scene_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        return jsonify(backend.update_scene(scene_id, payload))

    @v2.delete("/scenes/<scene_id>")
    def scenes_delete(scene_id: str):
        return jsonify(backend.delete_scene(scene_id))

    @v2.post("/scenes/<scene_id>/duplicate")
    def scenes_duplicate(scene_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        return jsonify(backend.duplicate_scene(scene_id, label=payload.get("label")))

    @v2.post("/scenes/reorder")
    def scenes_reorder():
        payload = request.get_json(force=True, silent=True) or {}
        return jsonify({"order": backend.reorder_scenes(payload.get("order") or [])})

    @v2.get("/scenes/active")
    def scenes_active_get():
        scene_id = backend.active_scene_id()
        return jsonify({"active_id": scene_id, "state": backend.get_state(scene_id) if scene_id else None})

    @v2.post("/scenes/active")
    def scenes_active_post():
        payload = request.get_json(force=True, silent=True) or {}
        scene_id = payload.get("scene_id") or payload.get("id")
        if not scene_id:
            return jsonify({"error": "scene_id is required"}), 400
        return jsonify(backend.set_active_scene(scene_id))

    @v2.get("/state")
    def get_state():
        return jsonify(backend.get_state(request.args.get("scene_id")))

    @v2.post("/state")
    def post_state():
        payload = request.get_json(force=True, silent=True) or {}
        return jsonify(backend.patch_state(payload, scene_id=_scene_id_from_request()))

    @v2.get("/camera")
    def get_camera():
        return jsonify({"camera": backend.get_camera(scene_id=request.args.get("scene_id"))})

    @v2.post("/camera")
    def post_camera():
        payload = request.get_json(force=True, silent=True) or {}
        camera = payload.get("camera", payload)
        return jsonify({"camera": backend.set_camera(camera, scene_id=_scene_id_from_request())})

    @v2.post("/camera/action")
    def post_camera_action():
        payload = request.get_json(force=True, silent=True) or {}
        action = payload.get("action")
        if not action:
            return jsonify({"error": "action is required"}), 400
        rest = {key: value for key, value in payload.items() if key not in ("action", "scene_id")}
        return jsonify({"camera": backend.camera_action(action, scene_id=_scene_id_from_request(), **rest)})

    @v2.post("/upload")
    def upload_cif():
        if "file" not in request.files:
            return jsonify({"error": "missing multipart file field 'file'"}), 400
        file = request.files["file"]
        if not file.filename:
            return jsonify({"error": "empty filename"}), 400
        with perf_log.time_block(
            "http:upload",
            kind="http",
            filename=file.filename,
        ):
            content = file.read()
            try:
                with perf_log.time_block(
                    "upload:parse_and_register",
                    kind="event",
                    filename=file.filename,
                    bytes=len(content),
                ):
                    bundle = backend.add_uploaded_file_bytes(content, file.filename)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
        return jsonify(bundle.metadata())

    @v2.get("/structures")
    def structures():
        return jsonify({"structures": backend.list_structures()})

    @v2.get("/scene/<name>")
    def scene(name: str):
        return jsonify(backend.get_scene_json(name))

    @v2.post("/topology")
    def topology():
        payload = request.get_json(force=True, silent=True) or {}
        state = backend.get_state(_scene_id_from_request())
        structure = payload.get("structure") or state.get("structure")
        center_index = payload.get("center_index")
        cutoff = float(payload.get("cutoff", 10.0))
        if center_index is None:
            return jsonify({"error": "center_index is required"}), 400
        return jsonify(backend.query_topology(structure=structure, center_index=int(center_index), cutoff=cutoff, scene_id=_scene_id_from_request()))

    @v2.get("/screenshot")
    def screenshot():
        png = backend.render_current_png(scene_id=request.args.get("scene_id"))
        return Response(png, mimetype="image/png")

    @v2.post("/preset/save")
    def preset_save():
        payload = request.get_json(force=True, silent=True) or {}
        path = payload.get("path")
        try:
            return jsonify(backend.save_preset(path=path))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @v2.post("/preset/load")
    def preset_load():
        payload = request.get_json(force=True, silent=True) or {}
        path = payload.get("path")
        try:
            return jsonify(backend.load_preset_from_path(path))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @v2.post("/export")
    def export_static():
        payload = request.get_json(force=True, silent=True) or {}
        return jsonify(backend.export_static(output_path=payload.get("output_path")))

    # ----- polyhedron specs (Phase 1) -----------------------------------
    #
    # Per-scene named-row data model for coordination polymers. Each spec
    # = {id, name, center_species, ligand_species (None=auto), color,
    # enabled}. Empty list (DELETE-all or never-set) falls back to the
    # legacy ``topology_species_keys`` + shared ``topology_hull_color``
    # behaviour. See ``agents/polyhedron_api.md``.

    def _polyhedra_scene_id() -> str | None:
        # Body and querystring both supported so simple curl-driven
        # agents can stay on either; matches the convention used by
        # ``/topology`` and ``/screenshot``.
        return _scene_id_from_request()

    @v2.get("/polyhedra")
    def polyhedra_list():
        scene_id = request.args.get("scene_id")
        return jsonify({"specs": backend.list_polyhedron_specs(scene_id=scene_id)})

    @v2.post("/polyhedra")
    def polyhedra_create():
        payload = request.get_json(force=True, silent=True) or {}
        center_species = payload.get("center_species")
        if not center_species:
            return jsonify({"error": "center_species is required"}), 400
        try:
            spec = backend.add_polyhedron_spec(
                center_species=center_species,
                ligand_species=payload.get("ligand_species"),
                name=payload.get("name"),
                color=payload.get("color"),
                enabled=bool(payload.get("enabled", True)),
                scene_id=_polyhedra_scene_id(),
                spec_id=payload.get("id"),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(spec)

    @v2.patch("/polyhedra/<spec_id>")
    def polyhedra_update(spec_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        try:
            spec = backend.update_polyhedron_spec(
                spec_id=spec_id,
                patch=payload,
                scene_id=_polyhedra_scene_id(),
            )
        except KeyError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(spec)

    @v2.delete("/polyhedra/<spec_id>")
    def polyhedra_delete(spec_id: str):
        ok = backend.remove_polyhedron_spec(
            spec_id=spec_id,
            scene_id=request.args.get("scene_id"),
        )
        if not ok:
            return jsonify({"error": f"unknown polyhedron spec id: {spec_id!r}"}), 404
        return jsonify({"deleted": spec_id})

    @v2.post("/polyhedra/reorder")
    def polyhedra_reorder():
        payload = request.get_json(force=True, silent=True) or {}
        ordered = payload.get("order")
        if not isinstance(ordered, list):
            return jsonify({"error": "'order' must be a list of spec ids"}), 400
        try:
            specs = backend.reorder_polyhedron_specs(
                ordered_ids=ordered,
                scene_id=_polyhedra_scene_id(),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"specs": specs})

    # ----- atom_groups (Phase 2) ----------------------------------------
    #
    # Per-scene atom-style override table. Each row pins a selector
    # (``{"all": true}`` or ``{"elements": [...]}`` or
    # ``{"is_minor": bool}``) plus optional color / color_light /
    # visible / opacity / material / style overrides. Rules apply in
    # list order; later rows win on overlapping atoms. Empty list
    # falls back to the legacy ``monochrome`` flag + element palette.
    # See ``agents/atom_groups_api.md``.

    @v2.get("/atom_groups")
    def atom_groups_list():
        scene_id = request.args.get("scene_id")
        return jsonify({"groups": backend.list_atom_groups(scene_id=scene_id)})

    @v2.post("/atom_groups")
    def atom_groups_create():
        payload = request.get_json(force=True, silent=True) or {}
        selector = payload.get("selector")
        if not isinstance(selector, dict):
            return jsonify({"error": "'selector' (dict) is required"}), 400
        try:
            group = backend.add_atom_group(
                selector=selector,
                name=payload.get("name"),
                color=payload.get("color"),
                color_light=payload.get("color_light"),
                visible=bool(payload.get("visible", True)),
                opacity=payload.get("opacity"),
                material=payload.get("material"),
                style=payload.get("style"),
                scene_id=_scene_id_from_request(),
                group_id=payload.get("id"),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(group)

    @v2.patch("/atom_groups/<group_id>")
    def atom_groups_update(group_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        try:
            group = backend.update_atom_group(
                group_id=group_id,
                patch=payload,
                scene_id=_scene_id_from_request(),
            )
        except KeyError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(group)

    @v2.delete("/atom_groups/<group_id>")
    def atom_groups_delete(group_id: str):
        ok = backend.remove_atom_group(group_id=group_id, scene_id=request.args.get("scene_id"))
        if not ok:
            return jsonify({"error": f"unknown atom_group id: {group_id!r}"}), 404
        return jsonify({"deleted": group_id})

    @v2.post("/atom_groups/reorder")
    def atom_groups_reorder():
        payload = request.get_json(force=True, silent=True) or {}
        ordered = payload.get("order")
        if not isinstance(ordered, list):
            return jsonify({"error": "'order' must be a list of group ids"}), 400
        try:
            groups = backend.reorder_atom_groups(
                ordered_ids=ordered,
                scene_id=_scene_id_from_request(),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"groups": groups})

    # ----- bond_groups (Phase 4) ---------------------------------------
    #
    # Per-scene bond-style override table. See agents/bond_groups_api.md.

    @v2.get("/bond_groups")
    def bond_groups_list():
        return jsonify({"groups": backend.list_bond_groups(scene_id=request.args.get("scene_id"))})

    @v2.post("/bond_groups")
    def bond_groups_create():
        payload = request.get_json(force=True, silent=True) or {}
        selector = payload.get("selector")
        if not isinstance(selector, dict):
            return jsonify({"error": "'selector' (dict) is required"}), 400
        try:
            group = backend.add_bond_group(
                selector=selector,
                name=payload.get("name"),
                color=payload.get("color"),
                visible=bool(payload.get("visible", True)),
                opacity=payload.get("opacity"),
                radius_scale=payload.get("radius_scale"),
                scene_id=_scene_id_from_request(),
                group_id=payload.get("id"),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(group)

    @v2.patch("/bond_groups/<group_id>")
    def bond_groups_update(group_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        try:
            group = backend.update_bond_group(
                group_id=group_id,
                patch=payload,
                scene_id=_scene_id_from_request(),
            )
        except KeyError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(group)

    @v2.delete("/bond_groups/<group_id>")
    def bond_groups_delete(group_id: str):
        ok = backend.remove_bond_group(group_id=group_id, scene_id=request.args.get("scene_id"))
        if not ok:
            return jsonify({"error": f"unknown bond_group id: {group_id!r}"}), 404
        return jsonify({"deleted": group_id})

    @v2.post("/bond_groups/reorder")
    def bond_groups_reorder():
        payload = request.get_json(force=True, silent=True) or {}
        ordered = payload.get("order")
        if not isinstance(ordered, list):
            return jsonify({"error": "'order' must be a list of group ids"}), 400
        try:
            groups = backend.reorder_bond_groups(
                ordered_ids=ordered,
                scene_id=_scene_id_from_request(),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"groups": groups})

    # ----- transforms (Phase 4) ----------------------------------------
    #
    # Per-scene structure-mutation pipeline. Each entry is a transform
    # spec dict {id, name, kind, params, enabled}. Order matters --
    # later transforms see the results of earlier ones. See
    # agents/transforms_api.md for the parameter schema per kind.

    @v2.get("/transforms")
    def transforms_list():
        return jsonify({"transforms": backend.list_transforms(scene_id=request.args.get("scene_id"))})

    @v2.post("/transforms")
    def transforms_create():
        payload = request.get_json(force=True, silent=True) or {}
        kind = payload.get("kind")
        if not kind:
            return jsonify({"error": "'kind' is required"}), 400
        try:
            transform = backend.add_transform(
                kind=kind,
                params=payload.get("params") or {},
                name=payload.get("name"),
                enabled=bool(payload.get("enabled", True)),
                scene_id=_scene_id_from_request(),
                transform_id=payload.get("id"),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(transform)

    @v2.patch("/transforms/<transform_id>")
    def transforms_update(transform_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        try:
            transform = backend.update_transform(
                transform_id=transform_id,
                patch=payload,
                scene_id=_scene_id_from_request(),
            )
        except KeyError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(transform)

    @v2.delete("/transforms/<transform_id>")
    def transforms_delete(transform_id: str):
        ok = backend.remove_transform(
            transform_id=transform_id,
            scene_id=request.args.get("scene_id"),
        )
        if not ok:
            return jsonify({"error": f"unknown transform id: {transform_id!r}"}), 404
        return jsonify({"deleted": transform_id})

    @v2.post("/transforms/reorder")
    def transforms_reorder():
        payload = request.get_json(force=True, silent=True) or {}
        ordered = payload.get("order")
        if not isinstance(ordered, list):
            return jsonify({"error": "'order' must be a list of transform ids"}), 400
        try:
            transforms = backend.reorder_transforms(
                ordered_ids=ordered,
                scene_id=_scene_id_from_request(),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"transforms": transforms})

    # ----- polyhedron instance overrides (Phase 4) ---------------------
    #
    # POST/DELETE on a single fragment_label inside a spec's
    # instance_overrides map. The full PATCH endpoint above also
    # accepts ``instance_overrides`` to set the entire map at once.

    @v2.post("/polyhedra/<spec_id>/instance_overrides/<fragment_label>")
    def polyhedra_instance_override_set(spec_id: str, fragment_label: str):
        payload = request.get_json(force=True, silent=True) or {}
        try:
            spec = backend.set_polyhedron_instance_override(
                spec_id=spec_id,
                fragment_label=fragment_label,
                override=payload,
                scene_id=_polyhedra_scene_id(),
            )
        except KeyError as exc:
            return jsonify({"error": str(exc)}), 404
        return jsonify(spec)

    @v2.delete("/polyhedra/<spec_id>/instance_overrides/<fragment_label>")
    def polyhedra_instance_override_clear(spec_id: str, fragment_label: str):
        try:
            spec = backend.clear_polyhedron_instance_override(
                spec_id=spec_id,
                fragment_label=fragment_label,
                scene_id=request.args.get("scene_id"),
            )
        except KeyError as exc:
            return jsonify({"error": str(exc)}), 404
        return jsonify(spec)

    # ----- perf log (cross-version) -------------------------------------
    #
    # ``/perf`` tails the in-memory ring buffer of perf-log events
    # written by ``crystal_viewer.perf_log``. It is the missing
    # "what did the server actually do, and how long did it take"
    # signal that the dev server's ``POST /_dash-update-component``
    # log lines deliberately omit. The UI side panel polls this
    # endpoint with ``since`` so it only ships new events.
    def _perf_payload():
        try:
            since = int(request.args.get("since", "0") or 0)
        except ValueError:
            since = 0
        try:
            limit = int(request.args.get("limit", "200") or 200)
        except ValueError:
            limit = 200
        events = perf_log.recent(limit=limit, since_seq=since)
        return jsonify(
            {
                "events": events,
                "latest_seq": perf_log.latest_seq(),
                "log_path": perf_log.log_path(),
            }
        )

    @v2.get("/perf")
    def perf_get_v2():
        return _perf_payload()

    @v2.post("/perf/clear")
    def perf_clear_v2():
        perf_log.clear()
        return jsonify({"cleared": True})

    server.register_blueprint(v2)

    v1 = Blueprint("crystal_viewer_api_v1", __name__, url_prefix="/api/v1")

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
        "polyhedron_search_supercell": "/api/v2/state",
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
        return jsonify(backend.query_topology(structure=structure, center_index=int(center_index), cutoff=cutoff))

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

    server.register_blueprint(v1)

    if Sock is None:
        return

    sock = Sock(server)

    @sock.route("/api/v2/ws")
    def ws_state(socket):
        last_version = -1
        while True:
            snapshot = backend.websocket_snapshot()
            version = snapshot["version"]
            if version != last_version:
                socket.send(json.dumps(snapshot, ensure_ascii=False))
                last_version = version
            try:
                message = socket.receive(timeout=0.5)
            except TypeError:
                message = None
                time.sleep(0.5)
            if message:
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    payload = {"type": "raw", "message": message}
                handle_ws_message(backend, payload)

    sock.route("/api/v1/ws")(ws_state)


def handle_ws_message(backend, payload: dict) -> None:
    """Dispatch one WebSocket message envelope.

    Extracted so the dispatch logic is unit-testable without spinning
    a real socket. Currently honours one envelope type:

    - ``{"type": "set_state", "payload": {...}, "scene_id": "..."}``
      patches state on a specific scene; ``scene_id`` may also live
      inside ``payload`` (legacy shape) or be omitted entirely (active
      scene). Returns the new state dict for the targeted scene so
      tests can assert on the result; production callers ignore the
      return value.
    """
    if not isinstance(payload, dict) or payload.get("type") != "set_state":
        return None
    inner = payload.get("payload", {}) or {}
    scene_id = (
        payload.get("scene_id")
        or (inner.get("scene_id") if isinstance(inner, dict) else None)
    )
    return backend.patch_state(inner, scene_id=scene_id)
