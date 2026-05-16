from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *


def register_scene_routes(v2, backend) -> dict:
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

    @v2.post("/scenes/close_others")
    def scenes_close_others():
        payload = request.get_json(force=True, silent=True) or {}
        keep_id = payload.get("keep") or payload.get("keep_id") or backend.active_scene_id()
        if not keep_id:
            return jsonify({"error": "keep id is required", "type": "ValueError"}), 400
        return jsonify(backend.delete_other_scenes(keep_id))

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


    return locals()
