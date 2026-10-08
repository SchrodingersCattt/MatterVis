from mat_viewer.app.display_controls import display_option_hint, display_option_items


def test_cell_boundary_is_disabled_outside_its_render_scope():
    options = {item["value"]: item for item in display_option_items("formula_unit", ["axes"])}
    assert options["unit_cell_box"]["disabled"]
    assert "Unit cell" in display_option_hint("formula_unit")
    assert all(not item.get("disabled", False) for key, item in options.items() if key != "unit_cell_box")


def test_cell_boundary_is_available_in_unit_cell_scope():
    options = {item["value"]: item for item in display_option_items("unit_cell", [])}
    assert not options["unit_cell_box"]["disabled"]
    assert display_option_hint("unit_cell") == ""


def test_previously_selected_value_can_always_be_unchecked():
    selected = ["unit_cell_box"]
    options = {item["value"]: item for item in display_option_items("cluster", selected)}
    assert not options["unit_cell_box"]["disabled"]
    assert selected == ["unit_cell_box"]