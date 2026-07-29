from __future__ import annotations

from pathlib import Path

import numpy as np

from crystal_viewer.cube import CubeData
from crystal_viewer.render.figures import _element_legend_annotations
from crystal_viewer.render.traces_overlays import _label_traces
from crystal_viewer.structure.bonds import find_bonds


def _atom(label: str, element: str, x: float) -> dict:
    return {
        "label": label,
        "elem": element,
        "cart": np.array([x, 0.0, 0.0]),
        "frac": np.array([x / 10.0, 0.0, 0.0]),
        "occ": 1.0,
        "dg": ".",
        "da": ".",
        "_bond_partners": (),
        "_bond_lengths": {},
        "_has_bond_table": False,
    }


def test_bond_scale_changes_visible_mck_threshold():
    # MCK C-C cutoff = (0.76 + 0.76) * 1.25 = 1.90 Å.
    atoms = [_atom("C1", "C", 0.0), _atom("C2", "C", 1.95)]
    assert find_bonds(atoms, bond_scale=1.0) == []
    assert find_bonds(atoms, bond_scale=1.05) == [(0, 1)]


def test_explicit_pair_threshold_is_scaled():
    atoms = [_atom("C1", "C", 0.0), _atom("C2", "C", 1.55)]
    thresholds = {("C", "C"): 1.5}
    assert find_bonds(atoms, bond_scale=1.0, bond_thresholds=thresholds) == []
    assert find_bonds(atoms, bond_scale=1.1, bond_thresholds=thresholds) == [(0, 1)]


def test_bond_scale_must_be_positive():
    atoms = [_atom("C1", "C", 0.0), _atom("C2", "C", 1.4)]
    for invalid in (0.0, -1.0, float("nan")):
        try:
            find_bonds(atoms, bond_scale=invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected ValueError for bond_scale={invalid!r}")


def test_cube_wrapper_style_bond_scale_precedence(tmp_path, monkeypatch):
    from crystal_viewer.cube import build_cube_figure
    from crystal_viewer.loader import cube_adapter
    from crystal_viewer.render import figures

    cube_path = tmp_path / "minimal.cube"
    cube_path.write_text(
        "bond wrapper\nsynthetic\n"
        "    0 0.0 0.0 0.0\n"
        "    2 0.5 0.0 0.0\n"
        "    2 0.0 0.5 0.0\n"
        "    2 0.0 0.0 0.5\n"
        " 0 0 0 0 0 0 0 0\n"
    )
    cube = CubeData(
        title="bond wrapper",
        comment="synthetic",
        atoms=[],
        origin=np.zeros(3),
        axes=np.eye(3) * 0.5,
        values=np.zeros((2, 2, 2)),
        path=Path(cube_path),
    )
    bundle = type("Bundle", (), {"cube_data": cube})()
    captured = {}

    def fake_load(*_args, **kwargs):
        captured["loader_scale"] = kwargs["bond_scale"]
        return bundle

    monkeypatch.setattr(cube_adapter, "load_cube_file", fake_load)
    monkeypatch.setattr(
        "crystal_viewer.loader.core.build_bundle_scene",
        lambda *_args, **_kwargs: {"cube_data": cube},
    )
    monkeypatch.setattr(figures, "build_figure", lambda _scene, style: style)

    style = build_cube_figure(cube_path, bond_scale=0.9, style={"mck_bond_scale": 1.1})
    assert captured["loader_scale"] == 1.1
    assert style["mck_bond_scale"] == 1.1


def test_selective_labels_and_element_legend():
    scene = {
        "label_items": [
            {"text": "Sn1", "elem": "Sn", "label_cart": [0, 0, 0], "is_minor": False},
            {"text": "C1", "elem": "C", "label_cart": [1, 0, 0], "is_minor": False},
        ],
        "draw_atoms": [
            {"elem": "Sn", "color": "#777777"},
            {"elem": "C", "color": "#5E5E5E"},
        ],
    }
    traces = _label_traces(scene, {"label_selector": {"elements": ["Sn"]}})
    texts = [text for trace in traces for text in trace["text"]]
    assert texts == ["Sn1"]

    annotations = _element_legend_annotations(scene, {"show_element_legend": True})
    assert len(annotations) == 1
    assert "Sn" in annotations[0]["text"]
    assert "C" in annotations[0]["text"]