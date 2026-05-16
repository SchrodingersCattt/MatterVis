from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .api_shared import *


def register_overlay_routes(v2, backend) -> dict:
    def _polyhedra_scene_id() -> str | None:
        # Body and querystring both supported so simple curl-driven agents can
        # stay on either; matches the convention used by topology/screenshot.
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
                enforce_enclosure=payload.get("enforce_enclosure", True),
                centroid_offset_frac=payload.get("centroid_offset_frac"),
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
                auto_promote=str(request.args.get("auto_promote", "true")).lower() not in {"0", "false", "no"},
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


    return locals()
