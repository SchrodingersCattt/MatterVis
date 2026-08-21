from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mat_viewer.cube import CubeData
from mat_viewer.render.figures import _element_legend_annotations
from mat_viewer.render.traces_overlays import _label_traces
from mat_viewer.structure.bonds import find_bonds


def _atom(label: str, element: str, x) -> dict:
    cart = np.asarray(x if np.ndim(x) else [x, 0.0, 0.0], dtype=float)
    return {
        "label": label,
        "elem": element,
        "cart": cart,
        "frac": cart / 10.0,
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


@pytest.mark.parametrize("value", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_pair_thresholds_are_rejected(value):
    atoms = [_atom("C1", "C", 0.0), _atom("C2", "C", 1.0)]
    with pytest.raises(ValueError, match="finite and positive"):
        find_bonds(atoms, bond_scale=1.0, bond_thresholds={("C", "C"): value})


def test_pair_threshold_keys_are_symmetric():
    atoms = [_atom("C1", "C", 0.0), _atom("N1", "N", 1.5)]
    assert find_bonds(atoms, bond_scale=1.0, bond_thresholds={("N", "C"): 1.6}) == [(0, 1)]


def test_threshold_above_legacy_five_angstrom_limit_is_supported():
    atoms = [_atom("C1", "C", 0.0), _atom("C2", "C", 5.5)]
    assert find_bonds(atoms, bond_scale=1.0, bond_thresholds={("C", "C"): 6.0}) == [(0, 1)]
    with pytest.raises(ValueError, match="candidate-search guard"):
        find_bonds(atoms, bond_scale=1.0, bond_thresholds={("C", "C"): 13.0})


def test_large_triclinic_row_vector_pbc_finds_boundary_bond():
    lattice = np.array([[10.0, 0.0, 0.0], [2.0, 10.0, 0.0], [0.0, 1.0, 10.0]])
    atoms = []
    for index in range(64):
        frac = np.array([0.3 + index * 0.001, 0.7, 0.7])
        atoms.append(_atom(f"C{index}", "C", frac @ lattice))
    atoms[0]["cart"] = np.array([0.01, 7.70, 7.70])
    atoms[1]["cart"] = np.array([9.99, 7.70, 7.70])
    import gemmi
    cell = gemmi.UnitCell(10.198, 10.198, 10.05, 84.3, 90.0, 78.7)
    bonds = find_bonds(atoms, M=lattice, cell=cell, bond_scale=1.0)
    assert (0, 1) in bonds


def test_bond_scale_must_be_positive():
    atoms = [_atom("C1", "C", 0.0), _atom("C2", "C", 1.4)]
    for invalid in (0.0, -1.0, float("nan")):
        with pytest.raises(ValueError, match="finite and positive"):
            find_bonds(atoms, bond_scale=invalid)


def test_cube_wrapper_style_bond_scale_precedence(tmp_path, monkeypatch):
    from mat_viewer.cube import build_cube_figure
    from mat_viewer.loader import cube_adapter
    from mat_viewer.render import figures

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
        "mat_viewer.loader.core.build_bundle_scene",
        lambda *_args, **_kwargs: {"cube_data": cube},
    )
    monkeypatch.setattr(figures, "build_figure", lambda _scene, style: style)

    style = build_cube_figure(cube_path, bond_scale=0.9, style={"mck_bond_scale": 1.1})
    assert captured["loader_scale"] == 1.1
    assert style["mck_bond_scale"] == 1.1


def test_scene_cache_key_separates_bond_scale_and_thresholds(tmp_path):
    from mat_viewer.loader.cube_adapter import load_cube_file
    from mat_viewer.loader.core import build_bundle_scene

    cube_path = tmp_path / "cache.cube"
    cube_path.write_text(
        "cache\nsynthetic\n"
        "    2 0.0 0.0 0.0\n"
        "    2 0.5 0.0 0.0\n"
        "    2 0.0 0.5 0.0\n"
        "    2 0.0 0.0 0.5\n"
        " 6 0.0 0.0 0.0 0 0 0 0\n"
        " 6 1.95 0.0 0.0 0 0 0 0\n"
        " 0 0 0 0 0 0 0 0\n"
    )
    bundle_a = load_cube_file(cube_path, bond_scale=1.0)
    bundle_b = load_cube_file(cube_path, bond_scale=1.1)
    build_bundle_scene(bundle_a)
    build_bundle_scene(bundle_b)
    assert bundle_a.scene_cache.keys() != bundle_b.scene_cache.keys()


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


def test_label_selector_label_only_empty_and_warm_cache():
    scene = {
        "label_items": [
            {"text": "Sn1", "elem": "Sn", "label_cart": [0, 0, 0], "is_minor": False},
            {"text": "C1", "elem": "C", "label_cart": [1, 0, 0], "is_minor": False},
        ],
    }
    style = {"label_selector": {"labels": ["C1"]}}
    first = _label_traces(scene, style)
    second = _label_traces(scene, style)
    assert [text for trace in first for text in trace["text"]] == ["C1"]
    assert [text for trace in second for text in trace["text"]] == ["C1"]
    assert len(_label_traces(scene, {"label_selector": {}})) == 1
    assert _label_traces(scene, {"label_selector": {"elements": ["Cl"]}}) == []
    with pytest.raises((TypeError, ValueError)):
        from mat_viewer.render.style.core import validate_style_schema
        validate_style_schema({"label_selector": "Sn"})