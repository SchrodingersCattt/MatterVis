"""The operate route pins a structure and an expected revision."""

from __future__ import annotations

import json
from pathlib import Path

from flask import Flask

from mat_viewer.api import register_api
from mat_viewer.app import ViewerBackend

UREA = Path("docs/qa/urea-default.cif")


def _client(tmp_path: Path):
    local = tmp_path / ".local"
    local.mkdir(parents=True, exist_ok=True)
    (local / "catalog.local.json").write_text(
        json.dumps({"structures": {"urea": {"cif_path": str(UREA.resolve()), "title": "urea"}}}),
        encoding="utf-8",
    )
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path), names=["urea"])
    host = Flask("operate-route")
    register_api(type("Dash", (), {"server": host})(), backend)
    return backend, host.test_client()


def test_operate_route_rejects_missing_and_mismatched_targets(tmp_path):
    backend, client = _client(tmp_path)
    try:
        original = backend.active_scene_id()
        other = backend.create_scene(structure="urea", label="other")
        backend.set_active_scene(other["id"], broadcast=False)
        missing = client.post("/api/v2/operate", json={"command": "build_supercell", "params": {"a": 2, "b": 1, "c": 1}})
        assert missing.status_code == 400
        mismatch = client.post(
            "/api/v2/operate",
            json={
                "command": "build_supercell",
                "params": {"a": 2, "b": 1, "c": 1},
                "structure_id": "not-urea",
                "scene_id": original,
                "expected_revision": 1,
            },
        )
        assert mismatch.status_code == 409
        assert backend.effect_session().counters.commits == 0
        assert backend.active_scene_id() == other["id"]
        accepted = client.post(
            "/api/v2/operate",
            json={
                "command": "build_supercell",
                "params": {"a": 2, "b": 1, "c": 1},
                "structure_id": "urea",
                "scene_id": original,
                "expected_revision": 1,
            },
        )
        assert accepted.status_code == 200
        body = accepted.get_json()
        assert body["source_scene_id"] == original
        revision = body["output"]["revision"]
        stale = client.post(
            "/api/v2/operate",
            json={
                "command": "build_supercell",
                "params": {"a": 3, "b": 1, "c": 1},
                "structure_id": "urea",
                "scene_id": original,
                "expected_revision": 1,
            },
        )
        assert stale.status_code == 409
        assert backend.effect_session().current_ref("urea").revision == revision
    finally:
        backend.close(wait=False)
