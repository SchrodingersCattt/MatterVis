"""Stable tenant-scoped API for hosted MatterVis."""

from __future__ import annotations

import json
import time
import uuid

from flask import Blueprint, jsonify, request, send_file

from ..saas import AuthContext, SaaSService
from ..saas.repository import RepositoryError
from ..saas.jobs import LocalEventBus

try:
    from flask_sock import Sock
except Exception:  # pragma: no cover - optional web dependency
    Sock = None


def _api_version() -> None:
    requested = request.headers.get("X-API-Version", "1").strip()
    if requested not in {"", "1"}:
        raise RepositoryError(f"unsupported API contract version: {requested}", status_code=406)


def register_saas_routes(server, service: SaaSService) -> None:
    api = Blueprint("mattervis_saas_api", __name__, url_prefix="/api")

    @api.before_request
    def _saas_request_guards():
        request.environ["mattervis_request_id"] = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        if request.method in {"POST", "PATCH", "PUT", "DELETE"} and service.config.auth_mode == "oidc":
            if not request.headers.get("X-API-Key"):
                csrf_cookie = request.cookies.get("csrf_token")
                csrf_header = request.headers.get("X-CSRF-Token")
                if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
                    return jsonify({"error": {"code": "csrf_required", "message": "X-CSRF-Token is required"}}), 403

    @api.after_request
    def _saas_response_headers(response):
        response.headers["X-Request-ID"] = request.environ.get("mattervis_request_id", "")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    def auth() -> AuthContext:
        _api_version()
        return service.authenticate(request)

    @api.get("/healthz")
    def healthz():
        return jsonify({"ok": True, "service": "mattervis-saas", "api_version": 1})

    @api.get("/auth/csrf")
    def csrf_token():
        token = uuid.uuid4().hex
        response = jsonify({"csrf_token": token})
        response.set_cookie("csrf_token", token, secure=request.is_secure, httponly=False, samesite="Lax")
        return response

    @api.get("/me")
    def me():
        return jsonify(service.identity(auth()))

    @api.get("/workspaces")
    def workspaces():
        context = auth()
        return jsonify({"workspaces": service.list_workspaces(context)})

    @api.get("/workspaces/<workspace_id>/projects")
    def projects(workspace_id: str):
        context = auth()
        return jsonify({"projects": service.list_projects(context, workspace_id)})

    @api.post("/workspaces/<workspace_id>/projects")
    def create_project(workspace_id: str):
        context = auth()
        payload = request.get_json(silent=True) or {}
        return jsonify(service.create_project(context, workspace_id, payload.get("name", ""))), 201

    @api.get("/workspaces/<workspace_id>/projects/<project_id>/structures")
    def structures(workspace_id: str, project_id: str):
        context = auth()
        _check_workspace(service, context, workspace_id, project_id)
        return jsonify({"structures": service.list_assets(context, project_id)})

    @api.post("/workspaces/<workspace_id>/projects/<project_id>/structures")
    def upload_structure(workspace_id: str, project_id: str):
        context = auth()
        _check_workspace(service, context, workspace_id, project_id, "structures:write")
        uploaded = request.files.get("file")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": {"code": "missing_file", "message": "multipart field 'file' is required"}}), 400
        content = uploaded.read(service.config.max_upload_bytes + 1)
        input_format = request.form.get("input_format") or request.args.get("input_format")
        result = service.upload_asset(
            context, project_id, uploaded.filename, content, input_format,
            request.headers.get("Idempotency-Key"),
        )
        return jsonify(result), 202

    @api.get("/workspaces/<workspace_id>/projects/<project_id>/scenes")
    def scenes(workspace_id: str, project_id: str):
        context = auth()
        _check_workspace(service, context, workspace_id, project_id, "scenes:read")
        return jsonify({"scenes": service.list_scenes(context, project_id)})

    @api.post("/workspaces/<workspace_id>/projects/<project_id>/scenes")
    def create_scene(workspace_id: str, project_id: str):
        context = auth()
        _check_workspace(service, context, workspace_id, project_id, "scenes:write")
        payload = request.get_json(silent=True) or {}
        scene = service.create_scene(
            context, project_id, str(payload.get("structure_id") or ""),
            str(payload.get("label") or "Scene"), payload.get("state") or {},
        )
        return jsonify(scene), 201

    @api.patch("/workspaces/<workspace_id>/projects/<project_id>/scenes/<scene_id>")
    def patch_scene(workspace_id: str, project_id: str, scene_id: str):
        context = auth()
        _check_workspace(service, context, workspace_id, project_id, "scenes:write")
        payload = request.get_json(silent=True) or {}
        expected = _if_match_revision()
        result = service.patch_scene(context, project_id, scene_id, payload, expected)
        response = jsonify(result)
        response.headers["ETag"] = f'"revision-{result["revision"]}"'
        return response

    @api.post("/workspaces/<workspace_id>/projects/<project_id>/render-jobs")
    def render_job(workspace_id: str, project_id: str):
        return _create_compute_job(service, workspace_id, project_id, "render")

    @api.post("/workspaces/<workspace_id>/projects/<project_id>/analysis-jobs")
    def analysis_job(workspace_id: str, project_id: str):
        return _create_compute_job(service, workspace_id, project_id, "analysis")

    @api.post("/workspaces/<workspace_id>/projects/<project_id>/export-jobs")
    def export_job(workspace_id: str, project_id: str):
        return _create_compute_job(service, workspace_id, project_id, "export")

    @api.get("/jobs/<job_id>")
    def job(job_id: str):
        context = auth()
        project_id = request.args.get("project_id") or context.project_id
        if not project_id:
            raise RepositoryError("project_id query parameter is required for user jobs", status_code=400)
        return jsonify(service.get_job(context, project_id, job_id))

    @api.get("/jobs/<job_id>/artifacts")
    def job_artifacts(job_id: str):
        context = auth()
        project_id = request.args.get("project_id") or context.project_id
        if not project_id:
            raise RepositoryError("project_id query parameter is required for user jobs", status_code=400)
        return jsonify({"artifacts": service.list_job_artifacts(context, project_id, job_id, request.host_url)})

    @api.get("/artifacts/<artifact_id>/download")
    def artifact_download(artifact_id: str):
        expires = request.args.get("expires")
        signature = request.args.get("signature")
        if expires and signature:
            try:
                signed_context = AuthContext(subject="signed-url", auth_type="signed-url", scopes=frozenset({"artifacts:read"}))
                stream, metadata = service.artifact_download(
                    signed_context, artifact_id, expires=int(expires), signature=signature
                )
            except ValueError as exc:
                raise RepositoryError(str(exc), status_code=400) from exc
        else:
            context = auth()
            project_id = request.args.get("project_id") or context.project_id
            if not project_id:
                raise RepositoryError("project_id query parameter is required", status_code=400)
            context = AuthContext(
                subject=context.subject, workspace_id=context.workspace_id, project_id=project_id,
                scopes=context.scopes, auth_type=context.auth_type, key_id=context.key_id,
            )
            stream, metadata = service.artifact_download(context, artifact_id)
        return send_file(stream, mimetype=metadata["content_type"], as_attachment=True, download_name=metadata["filename"])

    @api.post("/workspaces/<workspace_id>/projects/<project_id>/api-keys")
    def create_api_key(workspace_id: str, project_id: str):
        context = auth()
        _check_workspace(service, context, workspace_id, project_id)
        payload = request.get_json(silent=True) or {}
        raw, metadata = service.create_api_key(context, project_id, str(payload.get("name") or "API key"), list(payload.get("scopes") or []))
        return jsonify({"key": raw, "metadata": metadata}), 201

    @api.delete("/workspaces/<workspace_id>/projects/<project_id>/api-keys/<key_id>")
    def revoke_api_key(workspace_id: str, project_id: str, key_id: str):
        context = auth()
        _check_workspace(service, context, workspace_id, project_id)
        service.revoke_api_key(context, project_id, key_id)
        return jsonify({"ok": True})

    server.register_blueprint(api)
    _register_saas_ws(server, service)


def _register_saas_ws(server, service: SaaSService) -> None:
    if Sock is None:
        return
    sock = Sock(server)

    @sock.route("/api/ws/<project_id>")
    def project_ws(socket, project_id: str):
        context = service.authenticate(request)
        service._project(context, project_id, "jobs:read")
        socket.send(json.dumps({"type": "ready", "project_id": project_id}, ensure_ascii=False))
        with service.events.subscribe(project_id) as queue:
            while True:
                event = LocalEventBus.receive(queue, timeout=0.5)
                if event is not None:
                    socket.send(json.dumps(event, ensure_ascii=False))
                # A short receive lets clients close cleanly when the
                # flask-sock implementation supports timed reads. Older
                # versions simply use the event queue timeout above.
                try:
                    message = socket.receive(timeout=0.01)
                except TypeError:
                    message = None
                if message:
                    try:
                        payload = json.loads(message)
                    except json.JSONDecodeError:
                        payload = {}
                    if isinstance(payload, dict) and payload.get("type") == "ping":
                        socket.send(json.dumps({"type": "pong", "ts": time.time()}))


def _if_match_revision() -> int | None:
    value = request.headers.get("If-Match")
    if not value:
        return None
    value = value.strip().strip('"')
    if value.startswith("revision-"):
        value = value[len("revision-"):]
    try:
        return int(value)
    except ValueError as exc:
        raise RepositoryError("If-Match must be a scene revision", status_code=400) from exc


def _check_workspace(service: SaaSService, context: AuthContext, workspace_id: str,
                     project_id: str, scope: str | None = None) -> None:
    project = service._project(context, project_id, scope)
    if project.get("workspace_id") != workspace_id:
        raise RepositoryError("project does not belong to workspace", status_code=404)


def _create_compute_job(service: SaaSService, workspace_id: str, project_id: str, kind: str):
    context = service.authenticate(request)
    _check_workspace(service, context, workspace_id, project_id, "jobs:write")
    payload = request.get_json(silent=True) or {}
    job = service.submit_job(context, project_id, kind, payload, request.headers.get("Idempotency-Key"))
    return jsonify({"job": job}), 202
