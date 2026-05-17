from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import Blueprint, register_error_handler
from .v1_legacy import register_v1_routes
from .v2_export import register_export_routes
from .v2_overlays import register_overlay_routes
from .v2_perf import register_perf_routes
from .v2_scenes import register_scene_routes
from .v2_state import register_state_routes
from .ws import handle_ws_message, register_ws_routes


def register_api(dash_app, backend) -> None:
    server = dash_app.server
    register_error_handler(server)

    v2 = Blueprint("crystal_viewer_api_v2", __name__, url_prefix="/api/v2")
    handlers: dict = {}
    for register in (
        register_scene_routes,
        register_state_routes,
        register_export_routes,
        register_overlay_routes,
        register_perf_routes,
    ):
        handlers.update(register(v2, backend))
    server.register_blueprint(v2)

    v1 = Blueprint("crystal_viewer_api_v1", __name__, url_prefix="/api/v1")
    register_v1_routes(v1, backend, handlers)
    server.register_blueprint(v1)

    register_ws_routes(server, backend)


__all__ = ["handle_ws_message", "register_api"]
