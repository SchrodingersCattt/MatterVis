from __future__ import annotations

from pathlib import Path

from crystal_viewer.app import WORKSPACE_DIR, create_app


def _client(tmp_path: Path):
    app = create_app(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=WORKSPACE_DIR,
    )
    return app.server.test_client()


def test_topology_rejects_invalid_center_index(tmp_path: Path):
    client = _client(tmp_path)

    response = client.post(
        "/api/v2/topology",
        json={"structure": "DAP-4", "center_index": 9999, "cutoff": 10.0},
    )

    assert response.status_code == 400
    body = response.get_json()
    assert "center_index" in body["error"]
    assert "hint" in body


def test_topology_rejects_invalid_cutoff(tmp_path: Path):
    client = _client(tmp_path)

    response = client.post(
        "/api/v2/topology",
        json={"structure": "DAP-4", "center_index": 0, "cutoff": 0.0},
    )

    assert response.status_code == 400
    assert "cutoff" in response.get_json()["error"]


def test_topology_reports_missing_specs_as_precondition(tmp_path: Path):
    client = _client(tmp_path)
    client.post("/api/v2/state", json={"topology_enabled": True})

    response = client.post(
        "/api/v2/topology",
        json={"structure": "DAP-4", "center_index": 0, "cutoff": 10.0},
    )

    assert response.status_code == 409
    body = response.get_json()
    assert "polyhedra" in body["hint"]


def test_topology_atom_level_one_shot_query(tmp_path: Path):
    client = _client(tmp_path)
    scene = client.get("/api/v2/scene/DAP-4").get_json()
    cl_frag = next(
        frag for frag in scene["fragment_table"]
        if (frag.get("formula") or frag.get("species")) == "ClO4"
    )

    response = client.post(
        "/api/v2/topology",
        json={
            "structure": "DAP-4",
            "center_index": cl_frag["index"],
            "center_species": "Cl",
            "ligand_species": "O",
            "level": "atom",
            "cutoff": 2.2,
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["analysis_level"] == "atom"
    assert body["coordination_number"] == 4
    assert body["coordination_polyhedron_label"]
