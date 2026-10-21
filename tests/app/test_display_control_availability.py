from types import SimpleNamespace

from dash import no_update

from mat_viewer.app import ViewerBackend, callbacks_state
from mat_viewer.app.display_controls import (
    display_option_hint,
    display_option_items,
    reconcile_display_options,
    visible_display_options,
)


def test_cell_boundary_is_disabled_outside_its_render_scope():
    options = {item["value"]: item for item in display_option_items("formula_unit", ["axes"])}
    assert options["unit_cell_box"]["disabled"]
    assert options["wrap_into_cell"]["disabled"]
    assert "Unit cell" in display_option_hint("formula_unit")
    assert "wrap" in display_option_hint("formula_unit").lower()
    assert all(
        not item.get("disabled", False)
        for key, item in options.items()
        if key not in {"unit_cell_box", "wrap_into_cell"}
    )


def test_cell_boundary_is_available_in_unit_cell_scope():
    options = {item["value"]: item for item in display_option_items("unit_cell", [])}
    assert not options["unit_cell_box"]["disabled"]
    assert not options["wrap_into_cell"]["disabled"]
    assert display_option_hint("unit_cell") == ""


def test_wrap_into_cell_is_available_in_unit_cell_scope():
    options = {item["value"]: item for item in display_option_items("unit_cell", [])}
    assert not options["wrap_into_cell"]["disabled"]
    assert options["wrap_into_cell"]["label"] == "Wrap into cell"


def test_previously_selected_scope_options_are_hidden_until_unit_cell():
    """A checked Cell boundary or wrap stays stored, and is unchecked while disabled.

    Unchecking happens in Unit cell. Outside that scope the controls are
    disabled, so the checklist does not leave them looking checked.
    """
    selected = ["unit_cell_box", "wrap_into_cell"]
    options = {item["value"]: item for item in display_option_items("cluster", selected)}
    assert options["unit_cell_box"]["disabled"]
    assert options["wrap_into_cell"]["disabled"]
    assert visible_display_options("cluster", selected) == []
    assert reconcile_display_options("cluster", [], "unit_cell", selected) == selected


def test_disabled_cell_boundary_is_not_shown_checked():
    selected = ["labels", "unit_cell_box", "wrap_into_cell"]
    options = {item["value"]: item for item in display_option_items("cluster", selected)}
    assert options["unit_cell_box"]["disabled"]
    assert options["wrap_into_cell"]["disabled"]
    assert visible_display_options("cluster", selected) == ["labels"]
    assert reconcile_display_options("cluster", ["labels"], "unit_cell", selected) == selected


def test_returning_to_unit_cell_restores_a_hidden_boundary_preference():
    restored = reconcile_display_options(
        "unit_cell",
        ["labels"],
        "formula_unit",
        ["labels", "unit_cell_box", "wrap_into_cell"],
    )
    assert restored == ["labels", "unit_cell_box", "wrap_into_cell"]
    assert visible_display_options("unit_cell", restored) == restored


def test_explicit_cell_boundary_outside_unit_cell_is_stored():
    stored = reconcile_display_options(
        "formula_unit",
        ["labels", "unit_cell_box", "wrap_into_cell"],
        "formula_unit",
        ["labels"],
    )
    assert stored == ["labels", "unit_cell_box", "wrap_into_cell"]
    assert visible_display_options("formula_unit", stored) == ["labels"]


def test_unchecking_cell_boundary_in_unit_cell_clears_it():
    cleared = reconcile_display_options(
        "unit_cell",
        ["labels"],
        "unit_cell",
        ["labels", "unit_cell_box", "wrap_into_cell"],
    )
    assert "unit_cell_box" not in cleared
    assert "wrap_into_cell" not in cleared


class _CallbackRegistry:
    def __init__(self):
        self.callbacks = {}

    def callback(self, *args, **kwargs):
        def register(fn):
            self.callbacks[fn.__name__] = fn
            return fn

        return register


def test_cell_boundary_checkbox_returns_when_scope_returns(tmp_path, monkeypatch):
    """Unit cell (box on) → Formula unit (unchecked, preference kept) → Unit cell (checked)."""
    backend = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )
    try:
        backend.patch_state(
            {
                "display_mode": "unit_cell",
                "display_options": ["labels", "unit_cell_box", "wrap_into_cell"],
            },
            broadcast=False,
        )
        registry = _CallbackRegistry()
        callbacks_state.register_state_callbacks(registry, backend)

        def capture(mode, options):
            monkeypatch.setattr(
                callbacks_state,
                "callback_context",
                SimpleNamespace(triggered=[{"prop_id": "display-mode-selector.value"}]),
            )
            state = backend.get_state()
            return registry.callbacks["capture_state"](
                state["scene_id"],
                mode,
                options,
                state["atom_scale"],
                state["bond_radius"],
                state["minor_opacity"],
                state["material"],
                state["style"],
                state["disorder"],
                state["ortep_mode"],
                state["axis_scale"],
                state["topology_site_index"],
                ["enabled"] if state.get("topology_enabled") else [],
                [],
                "auto",
                None,
                "viridis",
                "auto",
                None,
                None,
                None,
                "#BDBDBD",
                [],
            )

        items, _hint, visible = registry.callbacks["update_display_availability"](
            "formula_unit", ["labels", "unit_cell_box", "wrap_into_cell"]
        )
        by_value = {item["value"]: item for item in items}
        assert by_value["unit_cell_box"]["disabled"]
        assert by_value["wrap_into_cell"]["disabled"]
        assert visible == ["labels"]
        _stored, checklist = capture("formula_unit", visible)
        stored = backend.get_state()
        assert stored["display_mode"] == "formula_unit"
        assert "unit_cell_box" in stored["display_options"]
        assert "wrap_into_cell" in stored["display_options"]
        assert checklist is no_update

        _items, _hint, still_hidden = registry.callbacks["update_display_availability"](
            "unit_cell", ["labels"]
        )
        assert still_hidden is no_update
        _stored, checklist = capture("unit_cell", ["labels"])
        stored = backend.get_state()
        assert stored["display_mode"] == "unit_cell"
        assert "unit_cell_box" in stored["display_options"]
        assert "wrap_into_cell" in stored["display_options"]
        assert checklist == ["labels", "unit_cell_box", "wrap_into_cell"]
    finally:
        backend.close()
