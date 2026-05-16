from __future__ import annotations
# ruff: noqa: F401,F403,F405

import json
import time

from flask import Blueprint, Response, jsonify, request
from werkzeug.exceptions import HTTPException

from .. import perf_log

try:
    from flask_sock import Sock
except Exception:  # pragma: no cover - optional dependency
    Sock = None


def _error_payload(exc: Exception, *, hint: str | None = None) -> dict:
    payload = {"error": str(exc), "type": type(exc).__name__}
    if hint:
        payload["hint"] = hint
    return payload


def _error_response(exc: Exception, status: int = 500, *, hint: str | None = None):
    return jsonify(_error_payload(exc, hint=hint)), status


def register_error_handler(server) -> None:
    @server.errorhandler(Exception)
    def _api_error_handler(exc: Exception):
        if not request.path.startswith("/api/"):
            raise exc
        if isinstance(exc, HTTPException):
            return jsonify({"error": exc.description, "type": type(exc).__name__}), exc.code
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int):
            return _error_response(exc, status_code, hint=getattr(exc, "hint", None))
        if isinstance(exc, KeyError):
            return _error_response(exc, 404)
        if isinstance(exc, (TypeError, ValueError)):
            return _error_response(exc, 400)
        return _error_response(exc, 500)


def _scene_id_from_request() -> str | None:
    payload = request.get_json(silent=True) if request.method not in ("GET", "HEAD") else None
    return request.args.get("scene_id") or ((payload or {}).get("scene_id") if isinstance(payload, dict) else None)


__all__ = [name for name in globals() if not name.startswith("__")]
