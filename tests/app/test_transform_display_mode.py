"""REST keeps its promotion default while callers can retain display mode.

The Dash transform buttons opt out of promotion. That UI choice must not
change the REST default or leave the persisted scene in a different mode
from the state returned to clients.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mat_viewer.app import create_app
from mat_viewer.scenes import SceneStore


@pytest.fixture
def transform_app(tmp_path: Path):
    app = create_app(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )
    try:
        yield app
    finally:
        app.close_extensions()
        app.crystal_backend.close(wait=False)


@pytest.mark.parametrize(
    ("option", "expected_mode"),
    [
        (None, "unit_cell"),
        ("true", "unit_cell"),
        ("false", "formula_unit"),
        ("0", "formula_unit"),
        ("no", "formula_unit"),
    ],
    ids=["default", "true", "false", "zero", "no"],
)
def test_rest_transform_display_mode_is_persisted(
    transform_app, option: str | None, expected_mode: str
):
    backend = transform_app.crystal_backend
    client = transform_app.server.test_client()
    scene_id = backend.active_scene_id()
    assert backend.get_state(scene_id)["display_mode"] == "formula_unit"

    response = client.post(
        "/api/v2/transforms",
        query_string={} if option is None else {"auto_promote": option},
        json={"kind": "repeat", "params": {"a": 2, "b": 1, "c": 1}},
    )

    assert response.status_code == 200
    created = response.get_json()
    assert client.get("/api/v2/state").get_json()["display_mode"] == expected_mode
    if expected_mode == "unit_cell":
        assert created["display_mode_auto_promoted"] == "formula_unit -> unit_cell"
        assert any("promoted" in warning for warning in created["warnings"])
    else:
        assert "display_mode_auto_promoted" not in created
        assert any(
            "formula_unit will trim" in warning and "unit_cell" in warning
            for warning in created["warnings"]
        )

    backend.flush_scene_store()
    restored = SceneStore.load(backend.scene_store.path).get(scene_id).state_patch
    assert restored["display_mode"] == expected_mode
    assert [item["id"] for item in restored["transforms"]] == [created["id"]]


@pytest.mark.parametrize("display_mode", ["unit_cell", "cluster"])
def test_rest_transform_retains_non_formula_display_mode(transform_app, display_mode):
    backend = transform_app.crystal_backend
    backend.patch_state({"display_mode": display_mode})
    client = transform_app.server.test_client()

    response = client.post(
        "/api/v2/transforms",
        json={"kind": "repeat", "params": {"a": 2, "b": 1, "c": 1}},
    )

    assert response.status_code == 200
    created = response.get_json()
    assert client.get("/api/v2/state").get_json()["display_mode"] == display_mode
    assert "display_mode_auto_promoted" not in created
    assert not any("formula_unit" in warning for warning in created.get("warnings", []))
