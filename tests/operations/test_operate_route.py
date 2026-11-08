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


def test_preview_candidate_snapshot_exposes_sites_and_bonds(tmp_path):
    backend, client = _client(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        preview = client.post(
            "/api/v2/operate",
            json={
                "command": "build_supercell",
                "params": {"a": 2, "b": 1, "c": 1},
                "structure_id": "urea",
                "scene_id": scene_id,
                "expected_revision": 1,
                "preview": True,
            },
        )
        assert preview.status_code == 200
        body = preview.get_json()
        assert body["effect"] == "preview"
        assert body["candidate_snapshot_path"] == f"/api/v2/operate/candidates/{body['candidate_id']}"
        assert "sites" not in body
        assert "bonds" not in body
        snapshot = client.get(body["candidate_snapshot_path"])
        assert snapshot.status_code == 200
        payload = snapshot.get_json()
        source = backend.effect_session().snapshot(structure_id="urea")
        sites = payload["snapshot"]["sites"]
        bonds = payload["snapshot"]["bonds"]
        assert payload["preview"] is True
        assert payload["input"]["revision"] == 1
        assert payload["status"] == "preview"
        assert len(sites) == source.n_sites * 2
        assert len(sites[0]["cartesian"]) == 3
        assert "molecule_index" in sites[0]
        assert "image_shift" in sites[0]
        assert bonds
        assert {"left_site_id", "right_site_id", "distance_A"} <= set(bonds[0])
        assert backend.effect_session().current_ref("urea").revision == 1
        missing = client.get("/api/v2/operate/candidates/cand-missing")
        assert missing.status_code == 404
        cancelled = client.post("/api/v2/operate/cancel", json={"candidate_id": body["candidate_id"]})
        assert cancelled.status_code == 200
        denied = client.get(body["candidate_snapshot_path"])
        assert denied.status_code == 400
    finally:
        backend.close(wait=False)
