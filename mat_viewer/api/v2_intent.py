from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *


def register_intent_routes(v2, backend) -> dict:
    @v2.post("/intent")
    def post_intent():
        payload = request.get_json(force=True, silent=True) or {}
        with perf_log.time_block(
            "http:intent",
            kind="http",
            intent_type=payload.get("type") if isinstance(payload, dict) else None,
        ):
            return jsonify(backend.apply_intent(payload))

    return locals()
