from __future__ import annotations

import io
import tempfile
import time

from mat_viewer.app.factory import create_app


def _app(tmp_path):
    return create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))


def _identity(client, subject: str):
    headers = {"X-User-Sub": subject}
    workspace = client.get("/api/workspaces", headers=headers).get_json()["workspaces"][0]
    project = client.get(
        f"/api/workspaces/{workspace['id']}/projects", headers=headers
    ).get_json()["projects"][0]
    return headers, workspace, project


def test_workspace_isolation_and_project_api_key(tmp_path):
    app = _app(tmp_path)
    try:
        client = app.server.test_client()
        alice_headers, workspace, project = _identity(client, "alice")
        bob_headers, _, _ = _identity(client, "bob")

        denied = client.get(
            f"/api/workspaces/{workspace['id']}/projects/{project['id']}/structures",
            headers=bob_headers,
        )
        assert denied.status_code == 403

        created = client.post(
            f"/api/workspaces/{workspace['id']}/projects/{project['id']}/api-keys",
            headers=alice_headers,
            json={"name": "agent", "scopes": ["structures:read"]},
        )
        assert created.status_code == 201
        key = created.get_json()["key"]
        allowed = client.get(
            f"/api/workspaces/{workspace['id']}/projects/{project['id']}/structures",
            headers={"X-API-Key": key},
        )
        assert allowed.status_code == 200
    finally:
        app.close_extensions()


def test_scene_revision_uses_if_match(tmp_path):
    app = _app(tmp_path)
    try:
        client = app.server.test_client()
        headers, workspace, project = _identity(client, "scene-user")
        # A scene needs a structure asset; a tiny fake asset is enough for the
        # repository contract and avoids making this test depend on a parser.
        service = app.saas_service
        asset = service.repository.create_asset(
            project_id=project["id"], filename="x.cif", input_format="cif",
            digest="a" * 64, storage_key="projects/x/source/x.cif",
        )
        scene = client.post(
            f"/api/workspaces/{workspace['id']}/projects/{project['id']}/scenes",
            headers=headers,
            json={"structure_id": asset["id"], "label": "initial", "state": {"style": "ball"}},
        ).get_json()
        response = client.patch(
            f"/api/workspaces/{workspace['id']}/projects/{project['id']}/scenes/{scene['id']}",
            headers={**headers, "If-Match": '"revision-999"'},
            json={"state": {"style": "wireframe"}},
        )
        assert response.status_code == 409
    finally:
        app.close_extensions()


def test_upload_returns_idempotent_job_and_completes(tmp_path):
    app = _app(tmp_path)
    try:
        client = app.server.test_client()
        headers, workspace, project = _identity(client, "upload-user")
        data = b"""data_test
_cell_length_a 10
_cell_length_b 10
_cell_length_c 10
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_space_group_name_H-M_alt 'P 1'
loop_
_space_group_symop_operation_xyz
'x, y, z'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
C1 C 0 0 0 1
"""
        path = f"/api/workspaces/{workspace['id']}/projects/{project['id']}/structures"
        first = client.post(
            path, headers={**headers, "Idempotency-Key": "upload-1"},
                data={"file": (io.BytesIO(data), "test.cif")}, content_type="multipart/form-data",
        )
        assert first.status_code == 202
        payload = first.get_json()
        assert payload["job"]["id"]
        second = client.post(
            path, headers={**headers, "Idempotency-Key": "upload-1"},
            data={"file": (io.BytesIO(data), "test.cif")}, content_type="multipart/form-data",
        )
        assert second.status_code == 202
        assert second.get_json()["structure"]["id"] == payload["structure"]["id"]
        for _ in range(40):
            status = client.get(
                f"/api/jobs/{payload['job']['id']}?project_id={project['id']}", headers=headers
            ).get_json()
            if status["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.025)
        assert status["status"] == "succeeded"
    finally:
        app.close_extensions()


def test_http_preset_cannot_escape_local_state(tmp_path):
    app = _app(tmp_path)
    try:
        client = app.server.test_client()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=True) as target:
            response = client.post(
                "/api/v2/preset/save",
                json={"path": target.name, "allow_external": True},
            )
            assert response.status_code == 400
    finally:
        app.close_extensions()
