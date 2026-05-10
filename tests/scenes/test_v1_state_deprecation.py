"""POST /api/v1/state must accept v2-only fields with a Deprecation
header.

Background
----------
Phase 1 added ``polyhedron_specs`` and Phase 2 added ``atom_groups``
to the per-scene state dict. Both have dedicated CRUD endpoints
under ``/api/v2/polyhedra`` and ``/api/v2/atom_groups`` with proper
validation.

We can't outright reject these fields on ``POST /api/v1/state`` --
old scripts that round-trip a full state snapshot to v1 would break
overnight. Instead the response carries a ``Deprecation: true``
header (RFC 8594) plus a ``Warning`` header pointing at the v2
endpoint, so clients can spot the migration target without surprise
behaviour changes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from crystal_viewer.app import WORKSPACE_DIR, create_app


@pytest.fixture
def client(tmp_path: Path):
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=WORKSPACE_DIR)
    return app.server.test_client()


def test_v1_state_accepts_polyhedron_specs_with_deprecation_header(client):
    response = client.post(
        "/api/v1/state",
        json={"polyhedron_specs": [{"center_species": "ClO4", "color": "#ff0000"}]},
    )
    assert response.status_code == 200
    assert response.headers.get("Deprecation") == "true"
    warning = response.headers.get("Warning") or ""
    assert "polyhedron_specs" in warning
    assert "/api/v2/" in warning


def test_v1_state_accepts_atom_groups_with_deprecation_header(client):
    response = client.post(
        "/api/v1/state",
        json={"atom_groups": [{"selector": {"all": True}, "color": "#000000"}]},
    )
    assert response.status_code == 200
    assert response.headers.get("Deprecation") == "true"
    warning = response.headers.get("Warning") or ""
    assert "atom_groups" in warning


def test_v1_state_no_deprecation_header_on_unaffected_post(client):
    response = client.post("/api/v1/state", json={"atom_scale": 1.2})
    assert response.status_code == 200
    assert response.headers.get("Deprecation") is None
