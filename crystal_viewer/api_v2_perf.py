from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .api_shared import *


def register_perf_routes(v2, backend) -> dict:
    @v2.get("/perf")
    def perf_get_v2():
        return _perf_payload()

    @v2.post("/perf/clear")
    def perf_clear_v2():
        perf_log.clear()
        return jsonify({"cleared": True})

    return locals()
