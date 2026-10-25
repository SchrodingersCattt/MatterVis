"""State projection patches retain framing and share the camera command clock."""

from __future__ import annotations

import copy

import pytest

from mat_viewer.app import create_app
from mat_viewer.app.camera_helpers import StaleCameraWrite


CAMERA = {
    "eye": {"x": 1.4, "y": 1.2, "z": 1.1},
    "center": {"x": 0.1, "y": -0.2, "z": 0.05},
    "up": {"x": 0.0, "y": 0.0, "z": 1.0},
    "projection": {"type": "perspective"},
}


@pytest.fixture
def empty_viewer(tmp_path):
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    try:
        yield app
    finally:
        app.close_extensions()
        app.crystal_backend.close()


@pytest.fixture
def viewer(empty_viewer):
    empty_viewer.crystal_backend.patch_state({"camera": CAMERA}, broadcast=False)
    return empty_viewer


@pytest.mark.parametrize(
    ("patch", "expected"),
    [
        ({"projection": " OrThOgRaPhIc "}, "orthographic"),
        ({"camera": {"projection": {"type": " OrThOgRaPhIc "}}}, "orthographic"),
        (
            {"projection": "orthographic", "camera": {"projection": {"type": "perspective"}}},
            "orthographic",
        ),
        (
            {"projection": "invalid", "camera": {"projection": {"type": "orthographic"}}},
            "perspective",
        ),
        (
            {"projection": None, "camera": {"projection": {"type": "orthographic"}}},
            "orthographic",
        ),
    ],
)
def test_state_projection_precedence_and_framing(viewer, patch, expected):
    client = viewer.server.test_client()
    backend = viewer.crystal_backend
    before = backend.get_state()
    cursor = backend.latest_figure_seq()

    response = client.post("/api/v2/state", json=patch)

    assert response.status_code == 200
    state = client.get("/api/v2/state").get_json()
    assert state["projection"] == expected
    assert state["camera"] == {**CAMERA, "projection": {"type": expected}}
    if expected != before["projection"]:
        assert state["camera_revision"] == before["camera_revision"] + 1
        assert state["camera_version"] == state["camera_revision"]
        assert state["geometry_version"] == before["geometry_version"]
        updates = backend.figure_broadcasts_since(cursor)
        assert len(updates) == 1
        assert updates[0]["update_kind"] == "camera"
        assert updates[0]["payload"]["camera"] == state["camera"]


def test_partial_camera_projection_patch_merges_vector_components(viewer):
    backend = viewer.crystal_backend
    before = backend.get_state()
    patch = {"projection": "orthographic", "camera": {"eye": {"x": 2.1}}}
    original_patch = copy.deepcopy(patch)

    state = backend.normalize_state(patch)

    assert state["camera"] == {
        **CAMERA,
        "eye": {**CAMERA["eye"], "x": 2.1},
        "projection": {"type": "orthographic"},
    }
    state["camera"]["eye"]["x"] = 10.0
    assert patch == original_patch
    assert backend.get_state()["camera"] == before["camera"]


def test_nested_projection_patch_fences_an_older_camera_write(viewer):
    backend = viewer.crystal_backend
    before = backend.get_state()
    response = viewer.server.test_client().post(
        "/api/v2/state", json={"camera": {"projection": {"type": "orthographic"}}}
    )
    assert response.status_code == 200

    with pytest.raises(StaleCameraWrite):
        backend.patch_state(
            {"camera": CAMERA},
            expected_camera_revision=before["camera_revision"],
            expected_scene_generation=before["scene_generation"],
            _camera_fence=True,
        )
    assert backend.get_state()["projection"] == "orthographic"


def test_sparse_plotly_camera_patches_merge_without_legacy_conversion(empty_viewer):
    backend = empty_viewer.crystal_backend
    backend.update_scene(backend.active_scene_id(), {"camera": None})
    assert backend.get_state()["camera"] is None
    client = empty_viewer.server.test_client()
    assert client.post("/api/v2/state", json={"camera": {"up": {"z": 1.0}}}).status_code == 200

    response = client.post("/api/v2/state", json={"camera": {"eye": {"x": 2.1}}})

    assert response.status_code == 200
    assert response.get_json()["camera"] == {"up": {"z": 1.0}, "eye": {"x": 2.1}}


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("display_key", ["atom_scale", "minor_opacity"])
@pytest.mark.parametrize("requested_increment", [None, 1, 7])
def test_projection_with_display_patch_advances_camera_clock(
    viewer, nested, display_key, requested_increment
):
    backend = viewer.crystal_backend
    before = backend.get_state()
    patch = {display_key: before[display_key] + 0.1}
    if nested:
        patch["camera"] = {"projection": {"type": "orthographic"}}
    else:
        patch["projection"] = "orthographic"
    if requested_increment is not None:
        patch["camera_revision"] = before["camera_revision"] + requested_increment

    response = viewer.server.test_client().post("/api/v2/state", json=patch)

    assert response.status_code == 200
    state = response.get_json()
    expected_revision = before["camera_revision"] + (requested_increment or 1)
    assert state["camera_revision"] == state["camera_version"] == expected_revision
    assert state["display_version"] > before["display_version"]
    with pytest.raises(StaleCameraWrite):
        backend.patch_state(
            {"camera": CAMERA},
            expected_camera_revision=before["camera_revision"],
            expected_scene_generation=before["scene_generation"],
            _camera_fence=True,
        )
