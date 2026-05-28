from __future__ import annotations

from crystal_viewer.app.rightclick import _dispatch_rightclick_action


class FakeBackend:
    def __init__(self):
        self.calls = []

    def set_selection(self, labels, scene_id=None):
        self.calls.append(("set", list(labels), scene_id))

    def add_to_selection(self, labels, scene_id=None):
        self.calls.append(("add", list(labels), scene_id))

    def select_fragment(self, fragment_label, scene_id=None):
        self.calls.append(("fragment", fragment_label, scene_id))

    def select_element(self, element, scene_id=None):
        self.calls.append(("element", element, scene_id))

    def clear_selection(self, scene_id=None):
        self.calls.append(("clear", scene_id))


def test_rightclick_dispatches_selection_actions():
    backend = FakeBackend()

    _dispatch_rightclick_action(backend, "scene_a", "select", "atom", {"label": "Cl1"}, {})
    _dispatch_rightclick_action(backend, "scene_a", "select_add", "atom", {"label": "O1"}, {})
    _dispatch_rightclick_action(backend, "scene_a", "select_fragment", "atom", {"fragment_label": "frag-1"}, {})
    _dispatch_rightclick_action(backend, "scene_a", "select_element", "atom", {"element": "Cl"}, {})
    _dispatch_rightclick_action(backend, "scene_a", "select_clear", "_global", {}, {})

    assert backend.calls == [
        ("set", ["Cl1"], "scene_a"),
        ("add", ["O1"], "scene_a"),
        ("fragment", "frag-1", "scene_a"),
        ("element", "Cl", "scene_a"),
        ("clear", "scene_a"),
    ]
