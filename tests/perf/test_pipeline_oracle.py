from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import numpy as np

from mat_viewer.perf import bench_pipeline
from mat_viewer.perf.oracle import SCHEMA, build_oracle_signature


def _atom(index: int, *, minor: bool = False) -> dict:
    atom = {
        "elem": "C" if index == 0 else "O",
        "label": f"A{index}",
        "frac": np.array([0.1 + index * 0.2, 0.2, 0.3]),
        "cart": np.array([1.0 + index, 2.0, 3.0]),
        "occ": 0.5 if minor else 1.0,
        "da": "A" if minor else ".",
        "dg": "2" if minor else ".",
        "_symop_index": index,
        "_source_index": index,
        "_image_shift": (0, 0, 0),
        "is_minor": minor,
    }
    if minor:
        atom["_is_minor"] = True
    return atom


def _bundle():
    atoms = [_atom(0), _atom(1, minor=True)]
    analysis = SimpleNamespace(
        mol_indices=[[0, 1]],
        bond_pairs=[(0, 1)],
        species_map={"CO_1": [0]},
        per_fu={"CO_1": 1},
    )
    scene = {
        "display_mode": "unit_cell",
        "draw_atoms": copy.deepcopy(atoms),
        "bonds": [{"i": 0, "j": 1, "start": atoms[0]["cart"], "end": atoms[1]["cart"], "is_minor": True}],
        "fragment_table": [{"label": "A0", "formula": "CO", "cluster_size": 2, "heavy_atom_count": 2}],
    }
    return SimpleNamespace(
        raw_atoms=atoms,
        formula_unit_atoms=copy.deepcopy(atoms),
        molcrys_analysis=analysis,
        scene=scene,
    )


def test_peak_rss_is_optional_on_platforms_without_resource(monkeypatch):
    monkeypatch.setattr(bench_pipeline, "_resource", None)

    assert bench_pipeline._peak_rss_mib() is None


def test_oracle_signature_is_stable_and_json_safe():
    bundle = _bundle()

    first = build_oracle_signature(bundle)
    second = build_oracle_signature(bundle)

    assert first == second
    assert first["schema"] == SCHEMA
    assert first["counts"]["raw_atoms"] == 2
    assert first["counts"]["major_atoms"] == 1
    assert first["counts"]["minor_atoms"] == 1
    json.dumps(first)


def test_oracle_reports_only_changed_section_digest():
    baseline_bundle = _bundle()
    changed_bundle = _bundle()
    changed_bundle.raw_atoms[1]["occ"] = 0.4

    baseline = build_oracle_signature(baseline_bundle)
    changed = build_oracle_signature(changed_bundle)

    assert baseline["section_digests"]["raw_atoms"] != changed["section_digests"]["raw_atoms"]
    assert baseline["section_digests"]["analysis"] == changed["section_digests"]["analysis"]
    assert baseline["overall_digest"] != changed["overall_digest"]


def test_oracle_canonicalizes_atom_and_molecule_order_and_selection_schema():
    bundle = _bundle()
    figure = {
        "data": [
            {
                "name": "atom-selection",
                "meta": {"mv_role": "atom_selection"},
                "customdata": [["atom", 0, "A0", "C", 0, ""]],
            }
        ]
    }
    baseline = build_oracle_signature(bundle, figure=figure)
    reordered = _bundle()
    reordered.molcrys_analysis.mol_indices = [[1, 0]]
    reordered.raw_atoms.reverse()

    changed = build_oracle_signature(reordered, figure=figure)

    assert baseline["section_digests"]["analysis"] == changed["section_digests"]["analysis"]
    assert baseline["section_digests"]["figure"] == changed["section_digests"]["figure"]
    assert baseline["counts"]["major_atoms"] == 1


def test_pipeline_report_exposes_cold_warm_and_manifest(monkeypatch, tmp_path):
    cif = tmp_path / "small.cif"
    cif.write_text("data_small\n")
    bundle = _bundle()
    bundle.scene_cache = {("formula_unit", False): bundle.scene}
    bundle.fragment_table_cache = {("scene", "formula_unit", False): ([{"formula": "stale"}], ["?"])}
    calls = []

    def fake_loader(**_kwargs):
        return bundle

    def fake_scene(bundle, *, display_mode, **_kwargs):
        calls.append(display_mode)
        return bundle.scene

    monkeypatch.setattr(bench_pipeline, "build_loaded_crystal", fake_loader)
    monkeypatch.setattr(bench_pipeline, "build_bundle_scene", fake_scene)
    monkeypatch.setattr(bench_pipeline, "_package_provenance", lambda _name: {"version": "test", "direct_url": None})

    report = bench_pipeline.build_pipeline_report(cif, include_unit_cell=False, include_figure=False)

    assert report["schema"] == bench_pipeline.SCHEMA
    assert report["fixture"]["sha256"]
    assert report["scenes"]["formula_unit"]["timing"].keys() == {"cold", "warm"}
    assert calls == ["formula_unit", "formula_unit"]
    assert bundle.fragment_table_cache == {}
    json.dumps(report)
