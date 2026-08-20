from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *
from ..config import current_config, delete_user_config, reload_config, write_user_config


def register_config_routes(v2, backend) -> dict:
    @v2.get("/config")
    def config_get():
        return jsonify(current_config().as_dict())

    @v2.get("/config/colors/elements")
    def config_get_element_colors():
        cfg = current_config()
        colors = cfg.colors.as_dict()
        return jsonify(
            {
                "elements": colors.get("elements", {}),
                "elements_light": colors.get("elements_light", {}),
            }
        )

    @v2.patch("/config")
    def config_patch():
        payload = request.get_json(force=True, silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({"error": "JSON object payload required"}), 400
        path = write_user_config(payload)
        cfg = reload_config(str(path))
        return jsonify({"path": str(path), "config": cfg.as_dict()})

    @v2.delete("/config")
    def config_delete():
        deleted = delete_user_config()
        cfg = reload_config()
        return jsonify({"deleted": deleted, "config": cfg.as_dict()})

    @v2.post("/config/reload")
    def config_reload():
        cfg = reload_config()
        return jsonify(cfg.as_dict())

    return locals()


__all__ = [name for name in globals() if not name.startswith("__")]
