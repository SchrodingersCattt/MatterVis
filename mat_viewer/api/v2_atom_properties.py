from __future__ import annotations
# ruff: noqa: F403,F405

from .shared import *


def register_atom_property_routes(v2, backend) -> dict:
    @v2.get("/atom-properties")
    def atom_properties():
        scene_id = request.args.get("scene_id")
        state = backend.get_state(scene_id)
        return jsonify(backend.atom_properties(state))

    return locals()
