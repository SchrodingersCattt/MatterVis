from __future__ import annotations

import gemmi
import numpy as np
import pytest

from mat_viewer.render.figures import build_figure, build_row_figure
from mat_viewer.render.selection import _atom_selection_trace, _bond_selection_trace
from mat_viewer.scene import build_scene_from_atoms, scene_ops, scene_style


def _scene():
    cell = gemmi.UnitCell(20.0, 20.0, 20.0, 90.0, 90.0, 90.0)
    matrix = np.eye(3) * 20.0
    atoms = [
        {"label": "C1", "elem": "C", "frac": np.array([0.5, 0.5, 0.5]), "cart": np.array([0.0, 0.0, 0.0]), "occ": 1.0, "dg": ".", "da": "."},
        {"label": "H2", "elem": "H", "frac": np.array([0.55, 0.5, 0.5]), "cart": np.array([1.09, 0.0, 0.0]), "occ": 1.0, "dg": ".", "da": "."},
    ]
    return build_scene_from_atoms(
        name="ch",
        title="CH",
        atoms=atoms,
        cell=cell,
        M=matrix,
        R=np.eye(3),
        show_hydrogen=True,
        preset={"style": {"show_hydrogen": True}},
        display_mode="cluster",
        ops=scene_ops(),
    )


def _roles(figure) -> set[str]:
    return {
        trace.meta.get("mv_role")
        for trace in figure.data
        if isinstance(trace.meta, dict) and trace.meta.get("mv_role")
    }


def test_interaction_markers_have_no_default_white_outline() -> None:
    scene = _scene()
    style = scene_style(scene, {"show_hydrogen": True})
    atom = _atom_selection_trace(scene, style).to_plotly_json()
    bond = _bond_selection_trace(scene, style).to_plotly_json()
    for trace in (atom, bond):
        assert trace["marker"]["line"]["width"] == 0
        assert trace["marker"]["line"]["color"] == "rgba(0,0,0,0)"


def test_static_figure_omits_interaction_only_traces() -> None:
    scene = _scene()
    style = scene_style(scene, {"show_hydrogen": True})
    interactive = build_figure(scene, style, include_interaction_traces=True)
    static = build_figure(scene, style, include_interaction_traces=False)
    assert {"atom_selection", "bond_selection"} <= _roles(interactive)
    assert "atom_selection" not in _roles(static)
    assert "bond_selection" not in _roles(static)
    assert "disorder_preview" not in _roles(static)


def test_static_row_figure_omits_atom_selection_trace() -> None:
    scene = _scene()
    style = scene_style(scene, {"show_hydrogen": True})
    interactive = build_row_figure([(scene, style)], include_interaction_traces=True)
    static = build_row_figure([(scene, style)], include_interaction_traces=False)
    assert "atom_selection" in _roles(interactive)
    assert "atom_selection" not in _roles(static)


def test_static_save_payload_strips_face_target_metadata_without_mutating_figure() -> None:
    from mat_viewer.render.api import _static_export_dict

    payload = {
        "data": [
            {
                "type": "mesh3d",
                "name": "hull",
                "meta": {
                    "mv_role": "polyhedron",
                    "face_ids": ["polyhedron:spec:a|source:x"],
                    "vertex_face_ids": ["polyhedron:spec:a|source:x"],
                    "facet_ids": ["facet:bfdh-morphology:1,0,0:0"],
                },
            }
        ]
    }
    static = _static_export_dict(payload)
    assert "face_ids" not in static["data"][0]["meta"]
    assert "vertex_face_ids" not in static["data"][0]["meta"]
    assert "facet_ids" not in static["data"][0]["meta"]
    assert payload["data"][0]["meta"]["face_ids"]
    interactive = _static_export_dict(payload, include_interaction_traces=True)
    assert interactive["data"][0]["meta"]["face_ids"]


def test_cli_static_export_disables_interaction_traces(monkeypatch, tmp_path) -> None:
    from mat_viewer.render import cli
    from mat_viewer.render.api import FigureResult

    captured = {}

    def fake_render(scene, style, **kwargs):
        captured.update(kwargs)
        return FigureResult(mpl_fig=object())

    args = type(
        "Args",
        (),
        {
            "publication_layout": False,
            "title": None,
            "subtitle": None,
            "width": 100,
            "height": 100,
            "scale": 1,
        },
    )()
    monkeypatch.setattr("mat_viewer.renderer.render", fake_render)
    monkeypatch.setattr(FigureResult, "save", lambda *a, **k: None)

    cli._save_static_output(object(), {}, {}, None, args, tmp_path / "x.png")

    assert captured["include_interaction_traces"] is False


def test_figure_result_save_filters_browser_pick_layers(monkeypatch, tmp_path) -> None:
    """Direct FigureResult exports match the CLI static path.

    ``render(...).save(...)`` is a public API and historically bypassed the
    CLI's ``include_interaction_traces=False`` option.  The transparent
    Scatter3d hit layers are useful only in a browser; retaining them in a
    static export can change WebGL depth compositing and makes screenshot
    output depend on Plotly's event traces.
    """
    from mat_viewer.render.api import FigureResult

    class _Figure:
        def to_dict(self):
            return {
                "data": [
                    {"type": "mesh3d", "name": "atoms", "meta": {"mv_role": "atom"}},
                    {"type": "scatter3d", "name": "pick", "meta": {"mv_role": "atom_selection"}},
                    {"type": "scatter3d", "name": "bond-pick", "meta": {"mv_role": "bond_selection"}},
                    {"type": "mesh3d", "name": "outline", "meta": {"mv_role": "selection"}},
                    {"type": "mesh3d", "name": "preview", "meta": {"mv_role": "disorder_preview"}},
                ],
                "layout": {},
            }

    captured = {}

    class _Go:
        def __init__(self, payload):
            self.payload = payload

        def write_image(self, path, **kwargs):
            captured["payload"] = self.to_dict()

        def to_dict(self):
            return self.payload

    monkeypatch.setattr("plotly.graph_objects.Figure", lambda payload: _Go(payload))
    FigureResult(plotly_fig=_Figure()).save(str(tmp_path / "out.png"))

    names = [item["name"] for item in captured["payload"]["data"]]
    assert names == ["atoms"]

    captured.clear()
    FigureResult(plotly_fig=_Figure()).save(
        str(tmp_path / "diagnostic.png"), include_interaction_traces=True
    )
    names = [item["name"] for item in captured["payload"]["data"]]
    assert names == ["atoms", "pick", "bond-pick", "outline", "preview"]


@pytest.mark.parametrize(
    ("background", "selection"),
    [("#FFFFFF", "#74808C"), ("#1B2026", "#AEBFD0")],
)
def test_light_and_dark_figure_payloads_keep_explicit_background_and_selection(
    background: str, selection: str
) -> None:
    scene = _scene()
    style = scene_style(
        scene,
        {
            "show_hydrogen": True,
            "background": background,
            "selection_highlight": selection,
            "selection": {"atom_labels": ["C1"], "targets": []},
        },
    )
    figure = build_figure(scene, style, include_interaction_traces=False)
    payload = figure.to_plotly_json()

    assert payload["layout"]["paper_bgcolor"] == background
    assert payload["layout"]["plot_bgcolor"] == background
    assert payload["layout"]["scene"]["bgcolor"] == background
    outline = next(
        trace
        for trace in payload["data"]
        if isinstance(trace.get("meta"), dict)
        and trace["meta"].get("mv_role") == "selection"
    )
    assert outline["color"] == selection
