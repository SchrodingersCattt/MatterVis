"""Structural operation routes.

These routes never reuse ``/state`` ``supercell`` or ``/transforms``.
Display repetition stays on those legacy resources.
"""

from __future__ import annotations

from .shared import *


def register_operate_routes(v2, backend) -> dict:
    @v2.get("/operate/capabilities")
    def operate_capabilities():
        return jsonify(backend.effect_session().capabilities())

    @v2.post("/operate")
    def operate_command():
        payload = request.get_json(force=True, silent=True) or {}
        command = payload.get("command")
        if not command:
            return jsonify({"error": "command is required"}), 400
        result = backend.operate(
            str(command),
            payload.get("params") or {},
            scene_id=_scene_id_from_request(),
            expected_revision=payload.get("expected_revision"),
            frame_ref=payload.get("frame_ref"),
            request_id=payload.get("request_id"),
            preview=bool(payload.get("preview", False)),
            property_policy=payload.get("property_policy"),
        )
        return jsonify(result)

    @v2.post("/operate/commit")
    def operate_commit():
        payload = request.get_json(force=True, silent=True) or {}
        candidate_id = payload.get("candidate_id")
        if not candidate_id:
            return jsonify({"error": "candidate_id is required"}), 400
        result = backend.commit_operation(
            str(candidate_id),
            expected_revision=int(payload["expected_revision"]),
            request_id=payload.get("request_id"),
            scene_id=_scene_id_from_request(),
        )
        return jsonify(result)

    @v2.post("/operate/cancel")
    def operate_cancel():
        payload = request.get_json(force=True, silent=True) or {}
        candidate_id = payload.get("candidate_id")
        if not candidate_id:
            return jsonify({"error": "candidate_id is required"}), 400
        candidate = backend.effect_session().cancel(str(candidate_id))
        return jsonify(candidate.to_dict())

    @v2.post("/operate/undo")
    def operate_undo():
        payload = request.get_json(force=True, silent=True) or {}
        return jsonify(
            backend.undo_structure(
                payload.get("structure_id"),
                scene_id=_scene_id_from_request(),
            )
        )

    @v2.post("/operate/redo")
    def operate_redo():
        payload = request.get_json(force=True, silent=True) or {}
        return jsonify(
            backend.redo_structure(
                payload.get("structure_id"),
                scene_id=_scene_id_from_request(),
            )
        )

    return {}
