from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *


def register_state_routes(v2, backend) -> dict:
    @v2.get("/state")
    def get_state():
        return jsonify(backend.get_state(request.args.get("scene_id")))

    @v2.get("/healthz")
    def healthz_v2():
        return jsonify(backend.healthz())

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
        broadcast = bool(payload.get("broadcast", True))
        rest = {key: value for key, value in payload.items() if key not in ("action", "scene_id", "broadcast")}
        return jsonify({"camera": backend.camera_action(action, scene_id=_scene_id_from_request(), broadcast=broadcast, **rest)})

    @v2.post("/upload")
    def upload_structure():
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
        meta = bundle.metadata()
        if getattr(bundle, "_upload_existing", False):
            meta["existing"] = True
        return jsonify(meta)

    @v2.get("/structures")
    def structures():
        return jsonify({"structures": backend.list_structures()})

    @v2.get("/scene/<name>")
    def scene(name: str):
        after_transforms = str(request.args.get("after_transforms", "")).lower() in {"1", "true", "yes", "on"}
        return jsonify(backend.get_scene_json(name, after_transforms=after_transforms))

    @v2.post("/topology")
    def topology():
        payload = request.get_json(force=True, silent=True) or {}
        state = backend.get_state(_scene_id_from_request())
        structure = payload.get("structure") or state.get("structure")
        center_index = payload.get("center_index")
        if center_index is None:
            return jsonify({"error": "center_index is required"}), 400
        try:
            cutoff = float(payload.get("cutoff", 10.0))
        except (TypeError, ValueError):
            return jsonify({"error": "cutoff must be numeric", "type": "ValueError"}), 400
        return jsonify(
            backend.query_topology(
                structure=structure,
                center_index=int(center_index),
                cutoff=cutoff,
                scene_id=_scene_id_from_request(),
                center_species=payload.get("center_species"),
                ligand_species=payload.get("ligand_species"),
                level=payload.get("level", "molecule"),
                enforce_enclosure=payload.get("enforce_enclosure", True),
                centroid_offset_frac=payload.get("centroid_offset_frac"),
            )
        )


    return locals()
