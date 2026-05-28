from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *


def register_selection_routes(v2, backend) -> dict:
    @v2.get("/selection")
    def selection_get():
        return jsonify({"selection": backend.get_selection(scene_id=request.args.get("scene_id"))})

    @v2.post("/selection")
    def selection_set():
        payload = request.get_json(force=True, silent=True) or {}
        labels = payload.get("atom_labels") or payload.get("labels") or []
        replace = bool(payload.get("replace", True))
        if replace:
            selection = backend.set_selection(labels, scene_id=_scene_id_from_request())
        else:
            selection = backend.add_to_selection(labels, scene_id=_scene_id_from_request())
        return jsonify({"selection": selection})

    @v2.patch("/selection")
    def selection_patch():
        payload = request.get_json(force=True, silent=True) or {}
        scene_id = _scene_id_from_request()
        if payload.get("add"):
            backend.add_to_selection(payload.get("add") or [], scene_id=scene_id)
        if payload.get("remove"):
            backend.remove_from_selection(payload.get("remove") or [], scene_id=scene_id)
        return jsonify({"selection": backend.get_selection(scene_id)})

    @v2.delete("/selection")
    def selection_clear():
        return jsonify({"selection": backend.clear_selection(scene_id=request.args.get("scene_id"))})

    @v2.post("/selection/by_fragment")
    def selection_by_fragment():
        payload = request.get_json(force=True, silent=True) or {}
        fragment_label = payload.get("fragment_label")
        if not fragment_label:
            return jsonify({"error": "fragment_label is required"}), 400
        return jsonify({"selection": backend.select_fragment(str(fragment_label), scene_id=_scene_id_from_request())})

    @v2.post("/selection/by_element")
    def selection_by_element():
        payload = request.get_json(force=True, silent=True) or {}
        element = payload.get("element")
        if not element:
            return jsonify({"error": "element is required"}), 400
        return jsonify({"selection": backend.select_element(str(element), scene_id=_scene_id_from_request())})

    @v2.post("/selection/all")
    def selection_all():
        return jsonify({"selection": backend.select_all(scene_id=_scene_id_from_request())})

    @v2.post("/selection/invert")
    def selection_invert():
        return jsonify({"selection": backend.invert_selection(scene_id=_scene_id_from_request())})

    @v2.post("/selection/by_box")
    def selection_by_box():
        payload = request.get_json(force=True, silent=True) or {}
        selection = backend.select_box(
            payload.get("rect_pixels") or [],
            payload.get("viewport_size") or payload.get("viewport") or [],
            additive=bool(payload.get("additive", False)),
            scene_id=_scene_id_from_request(),
        )
        return jsonify({"selection": selection})

    @v2.post("/selection/promote")
    def selection_promote():
        payload = request.get_json(force=True, silent=True) or {}
        group_id = backend.promote_selection_to_atom_group(
            name=payload.get("name"),
            color=payload.get("color"),
            scene_id=_scene_id_from_request(),
        )
        if group_id is None:
            return jsonify({"error": "selection is empty"}), 400
        return jsonify({"group_id": group_id, "selection": backend.get_selection(_scene_id_from_request())})

    return locals()


__all__ = [name for name in globals() if not name.startswith("__")]
