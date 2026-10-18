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
        if "targets" in payload:
            selection = backend.set_target_selection(
                payload.get("targets") or [],
                scene_id=_scene_id_from_request(),
                additive=not bool(payload.get("replace", True)),
                toggle=bool(payload.get("toggle", False)),
            )
            return jsonify({"selection": selection})
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
        if "targets" in payload:
            backend.set_target_selection(
                payload.get("targets") or [],
                scene_id=scene_id,
                additive=bool(payload.get("additive", True)),
                toggle=bool(payload.get("toggle", False)),
            )
        if payload.get("add"):
            backend.add_to_selection(payload.get("add") or [], scene_id=scene_id)
        if payload.get("remove"):
            backend.remove_from_selection(payload.get("remove") or [], scene_id=scene_id)
        return jsonify({"selection": backend.get_selection(scene_id)})

    @v2.get("/selection/targets")
    def selection_targets_get():
        return jsonify({"selection": backend.get_selection(scene_id=_scene_id_from_request())})

    @v2.post("/selection/targets")
    def selection_targets_set():
        payload = request.get_json(force=True, silent=True) or {}
        selection = backend.set_target_selection(
            payload.get("targets") or payload.get("target") or [],
            scene_id=_scene_id_from_request(),
            additive=bool(payload.get("additive", False)),
            toggle=bool(payload.get("toggle", False)),
        )
        return jsonify({"selection": selection})

    @v2.get("/style_overrides")
    def style_overrides_get():
        state = backend.get_state(scene_id=_scene_id_from_request())
        return jsonify({"style_overrides": state.get("style_overrides") or {}})

    @v2.patch("/style_overrides")
    def style_overrides_patch():
        payload = request.get_json(force=True, silent=True) or {}
        scene_id = _scene_id_from_request()
        target_id = payload.get("target_id") or payload.get("id")
        kind = payload.get("kind")
        if not target_id:
            return jsonify({"error": "target_id is required"}), 400
        if not payload.get("reset") and not isinstance(kind, str):
            return jsonify({"error": "kind is required for style overrides"}), 400
        if payload.get("reset"):
            field = payload.get("field")
            if field is not None:
                allowed_fields = {
                    "color", "edge_color", "opacity", "edge_opacity", "visible",
                    "radius", "thickness", "edge_width", "material", "representation",
                    "flatshading",
                }
                if str(field) not in allowed_fields:
                    return jsonify({"error": f"unsupported style field: {field!r}"}), 400
            overrides = backend.reset_style_override(
                str(target_id), scene_id=scene_id, field=field
            )
        else:
            patch = payload.get("style") or payload.get("override") or {}
            if not isinstance(patch, dict):
                return jsonify({"error": "style must be an object"}), 400
            try:
                overrides = backend.set_style_override(
                    str(target_id), str(kind), patch, scene_id=scene_id
                )
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
        return jsonify({"style_overrides": overrides})

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
            commit=bool(payload.get("commit", True)),
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
