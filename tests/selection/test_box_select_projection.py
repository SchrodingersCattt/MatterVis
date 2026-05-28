from __future__ import annotations

from crystal_viewer.app import create_app


def test_select_box_full_view_selects_visible_atoms():
    backend = create_app().crystal_backend
    scene = backend.scene_for_state(backend.get_state())
    expected = {str(atom["label"]) for atom in scene["draw_atoms"]}

    selection = backend.select_box([0, 0, 800, 600], [800, 600])

    assert set(selection["atom_labels"]) == expected
