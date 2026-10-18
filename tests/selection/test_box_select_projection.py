from __future__ import annotations

import numpy as np

from mat_viewer.app import create_app
import mat_viewer.render.viewport_spec as viewport_spec
from mat_viewer.render.screen_projection import project_gl3d_pixels
from mat_viewer.render.picking import atom_instance_id
from mat_viewer.render.viewport_spec import ViewportSpec


def _backend():
    app = create_app()
    return app.crystal_backend


def _pixels(backend, width, height):
    from mat_viewer.render.viewport import _plotly_camera_from_scene

    state = backend.get_state()
    scene = backend.scene_for_state(state)
    style = backend.style_for_state(state, scene)
    viewport = viewport_spec.resolve_viewport(scene, style)
    coords = np.asarray([atom.get("cart", [0.0, 0.0, 0.0]) for atom in scene["draw_atoms"]], dtype=float)
    # The viewer paints a position/focal camera as an eye. Picks use that
    # same eye, so the expected pixels have to as well.
    pixels = project_gl3d_pixels(
        coords,
        camera=_plotly_camera_from_scene(scene, style),
        ranges=viewport.ranges,
        aspectratio=viewport.aspectratio,
        width=width,
        height=height,
        projection=str(state.get("projection") or "perspective"),
    )
    return scene, pixels


def _isolated(pixels, pad):
    for index, (x, y) in enumerate(pixels):
        if not np.isfinite([x, y]).all():
            continue
        hits = [
            other
            for other, (sx, sy) in enumerate(pixels)
            if np.isfinite([sx, sy]).all() and abs(sx - x) <= pad and abs(sy - y) <= pad
        ]
        if hits == [index]:
            return index
    return None


def test_select_box_full_view_selects_visible_atoms():
    backend = _backend()
    try:
        scene = backend.scene_for_state(backend.get_state())
        expected = {str(atom["label"]) for atom in scene["draw_atoms"]}
        selection = backend.select_box([0, 0, 800, 600], [800, 600])
        assert set(selection["atom_labels"]) == expected
        assert {
            target["id"]
            for target in selection["targets"]
            if target["kind"] == "atom"
        } == {
            atom_instance_id(atom, index)
            for index, atom in enumerate(scene["draw_atoms"])
        }
    finally:
        backend.close()


def test_select_box_skips_atoms_hidden_by_atom_group_and_preserves_ids():
    backend = _backend()
    try:
        scene = backend.scene_for_state(backend.get_state())
        atoms = list(scene["draw_atoms"])
        assert len(atoms) >= 2
        hidden = str(atoms[0]["label"])
        backend.patch_state(
            {
                "atom_groups": [
                    {
                        "id": "qa-hidden",
                        "selector": {"labels": [hidden]},
                        "visible": False,
                        "enabled": True,
                    }
                ]
            }
        )
        selection = backend.select_box([0, 0, 800, 600], [800, 600])
        labels = set(selection["atom_labels"])
        assert hidden not in labels
        assert labels == {
            str(atom["label"])
            for atom in atoms[1:]
        }
        target_ids = {
            target["id"]
            for target in selection["targets"]
            if target["kind"] == "atom"
        }
        assert atom_instance_id(atoms[0], 0) not in target_ids
        assert target_ids == {
            atom_instance_id(atom, index)
            for index, atom in enumerate(atoms[1:], start=1)
        }
    finally:
        backend.close()


def test_select_box_follows_gl3d_pixels_for_zoom_pan_and_aspect():
    backend = _backend()
    try:
        camera = dict(backend.get_camera())
        camera["eye"] = {"x": 0.45, "y": 0.2, "z": 0.55}
        camera["center"] = {"x": 0.2, "y": -0.15, "z": 0.05}
        camera["up"] = {"x": 0.0, "y": 0.0, "z": 1.0}
        camera["projection"] = {"type": "perspective"}
        backend.set_projection("perspective")
        backend.set_camera(camera)
        width, height = 640, 480
        scene, pixels = _pixels(backend, width, height)
        index = _isolated(pixels, 3.0)
        assert index is not None
        x, y = pixels[index]
        selection = backend.select_box([x - 3, y - 3, x + 3, y + 3], [width, height])
        assert selection["atom_labels"] == [str(scene["draw_atoms"][index]["label"])]
        target = selection["targets"][0]
        assert target["kind"] == "atom"
        assert target["id"] == atom_instance_id(scene["draw_atoms"][index], index)
        assert target["label"] == str(scene["draw_atoms"][index]["label"])
        assert target["element"] == str(scene["draw_atoms"][index]["elem"])

        backend.set_projection("orthographic")
        scene, pixels = _pixels(backend, 900, 500)
        index = _isolated(pixels, 3.0)
        assert index is not None
        x, y = pixels[index]
        selection = backend.select_box([x - 3, y - 3, x + 3, y + 3], [900, 500])
        assert selection["atom_labels"] == [str(scene["draw_atoms"][index]["label"])]
    finally:
        backend.close()


def test_select_box_uses_non_cubic_layout_aspect(monkeypatch):
    backend = _backend()
    try:
        real = viewport_spec.resolve_viewport

        def non_cubic(scene, style, topology_data=None):
            spec = real(scene, style, topology_data=topology_data)
            return ViewportSpec(
                ranges=((0.0, 12.0), (0.0, 4.0), (0.0, 2.0)),
                aspectmode="manual",
                aspectratio={"x": 1.0, "y": 0.35, "z": 0.2},
                cube_scale=spec.cube_scale,
                signature=spec.signature,
            )

        monkeypatch.setattr("mat_viewer.render.viewport_spec.resolve_viewport", non_cubic)
        width, height = 800, 600
        scene, pixels = _pixels(backend, width, height)
        index = _isolated(pixels, 2.0)
        assert index is not None
        x, y = pixels[index]
        selection = backend.select_box([x - 2, y - 2, x + 2, y + 2], [width, height])
        assert selection["atom_labels"] == [str(scene["draw_atoms"][index]["label"])]
    finally:
        backend.close()
