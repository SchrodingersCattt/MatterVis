"""Regression coverage for analysis/operations polish, the status bar, and the server log."""

from pathlib import Path

from _layout_helpers import find_component, layout_ids, walk_layout
from dash import html

from mat_viewer.app import create_app
from mat_viewer.app.editor_tables import render_perf_log
from mat_viewer.app.panel_status import (
    atom_list_model,
    render_atom_list,
    status_bar_parts,
)
from mat_viewer.app.perf_log_view import group_perf_log_entries, perf_event_level
from mat_viewer.render.topology import topology_histogram_figure

ASSETS = Path(__file__).parents[2] / "frontend" / "assets"


def test_empty_histogram_is_a_caption_not_a_blank_titled_plot():
    payload = topology_histogram_figure(None).to_plotly_json()
    layout = payload["layout"]
    title = layout.get("title") or {}
    assert not (title.get("text") or "").strip()
    annotations = layout.get("annotations") or []
    assert any("fragment" in (item.get("text") or "").lower() for item in annotations)
    assert layout["xaxis"]["visible"] is False
    assert layout["yaxis"]["visible"] is False


def test_histogram_bars_keep_axis_titles_without_a_second_heading():
    payload = topology_histogram_figure(
        {"all_distances": [1.2, 1.8], "distances": [1.2]}
    ).to_plotly_json()
    layout = payload["layout"]
    assert not (layout.get("title") or {}).get("text")
    assert layout["xaxis"]["title"]["text"] == "Neighbor rank"
    assert layout["yaxis"]["title"]["text"] == "Distance (Å)"
    assert payload["data"][0]["y"] == [1.2, 1.8]


def test_status_bar_names_scope_and_counts():
    label, scope, counts = status_bar_parts(
        {"scene_label": "DAP-4", "display_mode": "unit_cell"},
        {"draw_atoms": [{}, {}], "bonds": [{}]},
    )
    assert label == "DAP-4"
    assert scope == "Unit cell"
    assert counts == "2 atoms · 1 bonds"


def test_atom_list_samples_the_displayed_atoms():
    scene = {
        "draw_atoms": [
            {"label": f"C{index}", "elem": "C", "occupancy": 1.0 if index else 0.5}
            for index in range(20)
        ]
    }
    model = atom_list_model(scene, limit=12)
    assert model["total"] == 20
    assert len(model["rows"]) == 12
    assert model["rows"][0]["label"] == "C0"
    assert model["rows"][0]["occupancy"] == 0.5


def test_perf_log_ranks_errors_warnings_and_fast_callbacks():
    assert perf_event_level({"label": "callback:editor_error", "kind": "cb"}) == "error"
    assert perf_event_level({"label": "figure_for_state", "kind": "event", "ms": 800}) == "warn"
    assert perf_event_level({"label": "cache:mesh", "kind": "cache", "ms": 1}) == "debug"
    assert perf_event_level({"label": "upload:create_scene", "kind": "event", "ms": 120}) == "info"


def test_perf_log_groups_consecutive_repeats_and_hides_debug_by_default():
    entries = [
        {"label": "callback:update_view", "kind": "cb", "ms": 4, "seq": 3},
        {"label": "callback:update_view", "kind": "cb", "ms": 900, "seq": 2},
        {"label": "cache:mesh", "kind": "cache", "ms": 1, "seq": 1},
    ]
    groups = group_perf_log_entries(entries, minimum_level="info")
    assert len(groups) == 1
    assert groups[0]["label"] == "callback:update_view"
    assert groups[0]["level"] == "warn"
    assert len(groups[0]["entries"]) == 2
    everything = group_perf_log_entries(entries, minimum_level="debug")
    assert [group["label"] for group in everything] == ["callback:update_view", "cache:mesh"]


def test_render_perf_log_collapses_a_repeated_label():
    entries = [
        {"label": "callback:update_view", "kind": "cb", "ms": 4, "iso": "2026-01-01T00:00:01.000"},
        {"label": "callback:update_view", "kind": "cb", "ms": 900, "iso": "2026-01-01T00:00:02.000"},
    ]
    nodes = render_perf_log(entries, minimum_level="info")
    assert len(nodes) == 1
    assert isinstance(nodes[0], html.Details)
    assert nodes[0].open is False


def test_atom_list_renderer_marks_a_truncated_sample():
    scene = {"draw_atoms": [{"label": f"O{i}", "elem": "O"} for i in range(15)]}
    nodes = render_atom_list(scene, limit=12)
    texts = []
    for node in walk_layout(html.Div(nodes)):
        children = getattr(node, "children", None)
        if isinstance(children, str):
            texts.append(children)
    assert any(text.startswith("Showing 12 of 15") for text in texts)


def test_workbench_layout_includes_status_bar_atom_list_and_log_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("MATTERVIS_PREWARM", "0")
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    try:
        root = app.layout()
        ids = set(layout_ids(root))
        assert {"mv-status-bar", "mv-status-busy", "atom-list", "perf-log-level"} <= ids
        results = find_component(root, "topology-results")
        assert results is not None
        assert getattr(results, "_type", None) != "Pre" or results.__class__.__name__ != "Pre"
        assert results.__class__.__name__ == "Div"
        histogram = find_component(root, "topology-histogram")
        assert histogram.style["height"] == "220px"
        level = find_component(root, "perf-log-level")
        assert level.value == "info"
        busy = find_component(root, "mv-status-busy")
        assert "is-hidden" in busy.className
    finally:
        app.close_extensions()
        app.crystal_backend.close()


def test_panel_assets_define_status_loading_and_log_grouping():
    workbench = (ASSETS / "workbench.css").read_text(encoding="utf-8")
    panels = (ASSETS / "panel_resize.css").read_text(encoding="utf-8")
    status = (ASSETS / "status_bar.js").read_text(encoding="utf-8")
    analysis = (ASSETS / "analysis_panel.js").read_text(encoding="utf-8")

    assert ".mv-status-bar" in workbench
    assert ".mv-status-spinner" in workbench
    assert ".rc-slider-mark-text:first-child" in workbench
    assert "margin: 0" in workbench
    assert "overflow: visible" in panels
    assert ".analysis-number-input" in panels
    assert ".perf-log-group" in panels
    assert ".perf-log-level--warn" in panels
    assert "agent-state-poll." in status
    assert "mv-status-busy" in status
    assert "topology-histogram" in analysis
    assert "analysis-tab-content--hidden" in analysis
