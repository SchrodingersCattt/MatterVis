"""Regressions for the exploratory canvas QA on the orthographic viewer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mat_viewer.app import ViewerBackend
from mat_viewer.render.screen_projection import camera_shows_scene
from mat_viewer.render.viewport_spec import resolve_viewport


NACL = Path("benchmarks/structures/ionic_nacl.cif")
FRAMED_ZOOM = {
    "eye": {"x": 0.5, "y": 0.5, "z": 0.5},
    "center": {"x": 0.0, "y": 0.2, "z": 0.0},
    "up": {"x": 0.0, "y": 0.0, "z": 1.0},
    "projection": {"type": "perspective"},
}
SPECK = {
    "eye": {"x": 0.02, "y": 0.01, "z": 0.01},
    "center": {"x": 0.0, "y": 1.4, "z": 0.0},
    "up": {"x": 0.0, "y": 0.0, "z": 1.0},
    "projection": {"type": "orthographic"},
}
# A look-at far from the cell. The near "speck" eye above still frames
# several unit-cell centres, so it cannot stand in for a missed scene.
FAR = {
    "eye": {"x": 0.0, "y": 0.0, "z": 50.0},
    "center": {"x": 20.0, "y": 20.0, "z": 20.0},
    "up": {"x": 0.0, "y": 0.0, "z": 1.0},
    "projection": {"type": "orthographic"},
}


def _backend(tmp_path: Path) -> ViewerBackend:
    local = tmp_path / ".local"
    local.mkdir(parents=True, exist_ok=True)
    (local / "catalog.local.json").write_text(
        json.dumps(
            {
                "structures": {
                    "ionic_nacl": {
                        "cif_path": str(NACL.resolve()),
                        "title": "ionic_nacl",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return ViewerBackend(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
    )


def _viewport(backend: ViewerBackend, scene_id: str):
    state = backend.get_state(scene_id)
    scene = backend.scene_for_state(state)
    style = backend.style_for_state(state, scene=scene)
    viewport = resolve_viewport(scene, style)
    points = [atom["cart"] for atom in scene.get("draw_atoms") or []]
    return state, scene, style, viewport, points


def test_orthographic_figure_keeps_projection_for_position_camera(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state(
            {"display_mode": "unit_cell", "projection": "orthographic"},
            scene_id=scene_id,
            broadcast=False,
        )
        state = backend.get_state(scene_id)
        # Inorganic unit-cell defaults leave camera fitting to the viewport
        # resolver; the persisted scene camera is intentionally unset.
        assert state["camera"] is None
        fig, _ = backend.figure_for_state(state)
        rendered = fig.layout.scene.camera.to_plotly_json()
        assert rendered["projection"]["type"] == "orthographic"
        _state, _scene, _style, viewport, points = _viewport(backend, scene_id)
        assert camera_shows_scene(
            rendered,
            points,
            ranges=viewport.ranges,
            aspectratio=viewport.aspectratio,
            projection="orthographic",
        )
    finally:
        backend.close()


def test_display_scope_change_installs_the_default_camera(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state(
            {"camera": SPECK, "projection": "orthographic"},
            scene_id=scene_id,
            broadcast=False,
        )
        before = backend.get_state(scene_id)
        state = backend.refit_scene_camera(scene_id, force=True)
        assert state["projection"] == "orthographic"
        assert state["camera"]["center"] != SPECK["center"]
        assert state["camera"]["projection"]["type"] == "orthographic"
        assert int(state["camera_revision"]) == int(before["camera_revision"]) + 1
    finally:
        backend.close()


def test_speck_camera_is_not_treated_as_framed(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state(
            {
                "display_mode": "unit_cell",
                "projection": "orthographic",
                "camera": FAR,
            },
            scene_id=scene_id,
            broadcast=False,
        )
        _state, _scene, _style, viewport, points = _viewport(backend, scene_id)
        assert not camera_shows_scene(
            FAR,
            points,
            ranges=viewport.ranges,
            aspectratio=viewport.aspectratio,
            projection="orthographic",
        )
        fitted, replaced = backend.camera_that_frames(
            backend.get_state(scene_id),
            FAR,
            scene=_scene,
            style=_style,
        )
        assert replaced is True
        assert camera_shows_scene(
            fitted,
            points,
            ranges=viewport.ranges,
            aspectratio=viewport.aspectratio,
            projection="orthographic",
        )
        backend.patch_state(
            {"camera": FRAMED_ZOOM, "projection": "perspective"},
            scene_id=scene_id,
            broadcast=False,
        )
        state, scene, style, viewport, points = _viewport(backend, scene_id)
        assert camera_shows_scene(
            FRAMED_ZOOM,
            points,
            ranges=viewport.ranges,
            aspectratio=viewport.aspectratio,
            projection="perspective",
        )
        _fitted, replaced = backend.camera_that_frames(
            state, FRAMED_ZOOM, scene=scene, style=style
        )
        assert replaced is False
    finally:
        backend.close()


def test_ortep_style_does_not_change_display_scope_or_leave_a_caption(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state(
            {"display_mode": "unit_cell", "style": "ball_stick", "material": "mesh"},
            scene_id=scene_id,
            broadcast=False,
        )
        backend.patch_state(
            {"style": "ortep", "material": "flat"},
            scene_id=scene_id,
            broadcast=False,
        )
        state = backend.get_state(scene_id)
        assert state["display_mode"] == "unit_cell"
        assert state["style"] == "ortep"
        backend.patch_state(
            {"style": "ball_stick", "material": "mesh"},
            scene_id=scene_id,
            broadcast=False,
        )
        fig, _ = backend.figure_for_state(backend.get_state(scene_id))
        texts = [
            str(getattr(item, "text", "") or "")
            for item in (fig.layout.annotations or [])
        ]
        assert all("2D ORTEP Publication Mode" not in text for text in texts)
    finally:
        backend.close()


def test_cluster_figure_has_no_periodic_cell(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state({"display_mode": "cluster"}, scene_id=scene_id, broadcast=False)
        fig, _ = backend.figure_for_state(backend.get_state(scene_id))
        names = [str(getattr(trace, "name", "") or "") for trace in fig.data]
        assert "unit-cell-box" not in names
        roles = []
        for trace in fig.data:
            meta = getattr(trace, "meta", None)
            if isinstance(meta, dict):
                roles.append(meta.get("mv_role"))
        assert "unit_cell" not in roles
    finally:
        backend.close()


def test_cluster_scope_suppresses_a_stale_cell_boundary_option(tmp_path: Path):
    """Switching to Isolated cluster must hide a previously checked cell box.

    The control state deliberately keeps ``unit_cell_box`` so switching back
    to Unit cell can restore the user's choice.  Rendering must nevertheless
    treat cluster as non-periodic and omit the unit-cell trace while that
    stale token is present.
    """
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state(
            {
                "display_mode": "cluster",
                "display_options": ["labels", "unit_cell_box"],
            },
            scene_id=scene_id,
            broadcast=False,
        )
        state = backend.get_state(scene_id)
        style = backend.style_for_state(state)
        assert state["display_options"] == ["labels", "unit_cell_box"]
        assert style["display_mode"] == "cluster"
        assert style["show_unit_cell"] is False
        fig, _ = backend.figure_for_state(state)
        names = [str(getattr(trace, "name", "") or "") for trace in fig.data]
        assert "unit-cell-box" not in names
    finally:
        backend.close()


def _home_atoms(atoms: list) -> list:
    return [
        atom
        for atom in atoms
        if not atom.get("_is_boundary_replica")
        and not atom.get("_is_framework_context_replica")
        and not atom.get("_is_bonded_image_replica")
    ]


def test_repeat_counts_differ_and_clear_removes_them(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state({"display_mode": "unit_cell"}, scene_id=scene_id, broadcast=False)
        base_scene = backend.scene_for_state(backend.get_state(scene_id))
        base_atoms = list(base_scene.get("draw_atoms") or [])
        home = _home_atoms(base_atoms)
        home_ids = {id(atom) for atom in home}
        home_index = {
            index for index, atom in enumerate(base_atoms) if id(atom) in home_ids
        }
        home_bonds = [
            bond
            for bond in (base_scene.get("bonds") or [])
            if bond["i"] in home_index and bond["j"] in home_index
        ]
        backend.add_transform("repeat", {"a": 2, "b": 2, "c": 2}, scene_id=scene_id)
        two_scene = backend.scene_for_state(backend.get_state(scene_id))
        two_atoms = list(two_scene.get("draw_atoms") or [])
        from mat_viewer.scene.core import source_image_identity

        identities = [
            source_image_identity(atom, two_scene["_canonical_source_atoms"], index)
            for index, atom in enumerate(two_atoms)
        ]
        assert len(two_atoms) == len(home) * 8
        assert len(set(identities)) == len(identities)
        # This CIF is a one-dimensional Na–Cl contact.  Four chains in a
        # 2×2×2 repeat each keep two in-cell contacts plus the junction
        # between the repeated cells.
        repeated_bonds = list(two_scene.get("bonds") or [])
        assert len(home_bonds) == 1
        assert len(repeated_bonds) == 4 * 3
        import numpy as np

        for bond in repeated_bonds:
            pair = {two_atoms[bond["i"]]["elem"], two_atoms[bond["j"]]["elem"]}
            assert pair == {"Na", "Cl"}
            length = float(
                np.linalg.norm(np.asarray(bond["end"]) - np.asarray(bond["start"]))
            )
            assert length == pytest.approx(2.82, abs=1e-2)
        fig, _ = backend.figure_for_state(backend.get_state(scene_id))
        box = next(trace for trace in fig.data if getattr(trace, "name", "") == "unit-cell-box")
        # The outline is the supercell, not the home cell the repeats grew out of.
        assert max(box.x) > float(np_max_cell(base_scene)) * 1.5
        backend.commit_patch({"transforms": []}, scene_id=scene_id, broadcast=False)
        backend.add_transform("repeat", {"a": 3, "b": 3, "c": 3}, scene_id=scene_id)
        three = len(backend.scene_for_state(backend.get_state(scene_id)).get("draw_atoms") or [])
        assert three == len(home) * 27
        assert len(two_atoms) != three
        backend.commit_patch({"transforms": []}, scene_id=scene_id, broadcast=False)
        cleared = backend.scene_for_state(backend.get_state(scene_id))
        assert backend.get_state(scene_id)["transforms"] == []
        assert len(cleared.get("draw_atoms") or []) == len(base_atoms)
    finally:
        backend.close()


def np_max_cell(scene: dict) -> float:
    import numpy as np

    matrix = np.asarray(scene["M"], dtype=float)
    return float(np.linalg.norm(matrix, axis=1).max())


def test_resolve_disorder_on_a_scene_without_occupancy_is_empty(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        replicas = backend.resolve_disorder(scene_id, method="enumerate", count=5, seed=1)
        assert replicas == []
    finally:
        backend.close()


def test_empty_rename_is_rejected_and_a_name_updates_the_title(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        with pytest.raises(ValueError, match="cannot be empty"):
            backend.scene_store.rename(scene_id, "   ")
        backend.apply_intent(
            {
                "type": "crud_scene",
                "scene_id": scene_id,
                "payload": {"action": "rename", "label": "Bench salt"},
            }
        )
        state = backend.get_state(scene_id)
        fig, _ = backend.figure_for_state(state)
        cached, _ = backend.figure_for_state(backend.get_state(scene_id))
        assert "Bench salt" in str(fig.layout.title.text)
        assert "Bench salt" in str(cached.layout.title.text)
    finally:
        backend.close()


def test_click_rect_selects_the_nearest_atom_from_a_focal_camera(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        state = backend.get_state(scene_id)
        scene = backend.scene_for_state(state)
        style = backend.style_for_state(state, scene=scene)
        from mat_viewer.render.screen_projection import project_gl3d_pixels
        from mat_viewer.render.viewport import _plotly_camera_from_scene

        viewport = resolve_viewport(scene, style)
        camera = _plotly_camera_from_scene(scene, style)
        points = [atom["cart"] for atom in scene["draw_atoms"]]
        xy = project_gl3d_pixels(
            points,
            camera=camera,
            ranges=viewport.ranges,
            aspectratio=viewport.aspectratio,
            width=1000,
            height=800,
            projection=state.get("projection"),
        )
        index = 0
        x, y = float(xy[index, 0]), float(xy[index, 1])
        selection = backend.select_box(
            [x - 10, y - 10, x + 10, y + 10],
            [1000, 800],
            scene_id=scene_id,
        )
        assert scene["draw_atoms"][index]["label"] in selection["atom_labels"]
        assert selection["parts"][0]["label"] == selection["atom_labels"][0]
        assert selection["parts"][0]["elem"]
        preview = backend.select_box(
            [x - 10, y - 10, x + 10, y + 10],
            [1000, 800],
            scene_id=scene_id,
            commit=False,
        )
        assert preview["atom_labels"] == selection["atom_labels"]
        assert backend.get_state(scene_id)["selection"]["atom_labels"] == selection["atom_labels"]
    finally:
        backend.close()


def test_flat_ball_omits_bonds_and_mesh_can_return(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state(
            {"material": "flat", "style": "ball"},
            scene_id=scene_id,
            broadcast=False,
        )
        flat, _ = backend.figure_for_state(backend.get_state(scene_id))
        roles = []
        for trace in flat.data:
            meta = getattr(trace, "meta", None)
            if isinstance(meta, dict) and meta.get("mv_role"):
                roles.append(meta.get("mv_role"))
        assert "bond" not in roles
        assert "atom" in roles
        backend.patch_state(
            {"material": "mesh", "style": "ball_stick"},
            scene_id=scene_id,
            broadcast=False,
        )
        state = backend.get_state(scene_id)
        assert state["material"] == "mesh"
        assert state["style"] == "ball_stick"
        mesh, _ = backend.figure_for_state(state)
        mesh_types = {str(getattr(trace, "type", "") or "") for trace in mesh.data}
        assert "mesh3d" in mesh_types
    finally:
        backend.close()
