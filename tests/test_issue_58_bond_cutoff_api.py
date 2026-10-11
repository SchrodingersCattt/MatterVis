from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from mat_viewer.cli import _build_render_parser
from mat_viewer.loader import build_loaded_crystal
from mat_viewer.render.cli_controls import bond_thresholds_from_args


def test_issue_58_renderer_cli_exposes_pair_cutoffs() -> None:
    """Keep the caller-facing renderer policy contract from regressing."""

    parser = argparse.ArgumentParser()
    render = _build_render_parser(parser.add_subparsers())
    args = render.parse_args(
        [
            "structure.cif",
            "-o",
            "figure.png",
            "--bond-scale",
            "1.05",
            "--bond-threshold",
            "Zn,N=2.5",
        ]
    )
    assert args.bond_scale == 1.05
    assert bond_thresholds_from_args(args) == {("N", "Zn"): 2.5}


def test_public_loader_and_renderer_use_pair_cutoff_policy() -> None:
    """The public structure/render pipeline must carry cutoffs to geometry."""
    from mat_viewer.agent import load_structure, prepare_render

    source = (
        Path(__file__).resolve().parents[1]
        / "benchmarks/structures/molecular_water.cif"
    )
    loaded = load_structure(source, bond_thresholds={("H", "O"): 0.1})
    bundle = loaded.frames[0].bundle

    # The strict O-H cutoff removes both O-H edges while retaining the H-H
    # edge. This assertion exercises the public loader rather than the legacy
    # ``find_bonds`` helper directly.
    assert bundle.bond_thresholds == {("H", "O"): 0.1}
    assert bundle.molcrys_analysis.bond_pairs == [(1, 2)]

    plan = prepare_render(
        loaded,
        render_spec={"representation": "ball_stick", "show_cell": False},
    )
    bond_primitives = [
        primitive
        for primitive in plan.primitives
        if primitive.metadata.get("kind") == "bond"
    ]
    assert len(bond_primitives) == 1
    assert bond_primitives[0].metadata["atom_indices"] == [1, 2]


def test_batch_renderer_rejects_bond_threshold_instead_of_dropping_it() -> None:
    """Batch/auto dispatch must never silently ignore a chemistry option."""
    from mat_viewer.cli import _build_render_parser
    from mat_viewer.render.fast_cli import render_batch_if_selected

    parser = argparse.ArgumentParser()
    render = _build_render_parser(parser.add_subparsers())
    args = render.parse_args(
        [
            "structure.xyz",
            "-o",
            "figure.png",
            "--renderer",
            "batch",
            "--style",
            "ball",
            "--bond-threshold",
            "C,C=1.5",
        ]
    )
    with pytest.raises(ValueError, match=r"--bond-threshold"):
        render_batch_if_selected(args, install_command="")


def test_rest_policy_reload_rebuilds_direct_and_uploaded_bundles(
    monkeypatch, tmp_path
) -> None:
    """Reloading config must replace the cached MolCrysKit bond graph."""
    from types import SimpleNamespace

    from mat_viewer.app import backend_core
    from mat_viewer.app.backend import ViewerBackend

    backend = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
    )
    try:
        direct = build_loaded_crystal(
            name="direct-water",
            cif_path="benchmarks/structures/molecular_water.cif",
            source="direct",
        )
        uploaded = build_loaded_crystal(
            name="uploaded-water",
            cif_path="benchmarks/structures/molecular_water.cif",
            source="upload",
        )
        backend.bundles[direct.name] = direct
        backend.bundles[uploaded.name] = uploaded
        assert len(direct.molcrys_analysis.bond_pairs) == 3
        assert len(uploaded.molcrys_analysis.bond_pairs) == 3

        monkeypatch.setattr(
            backend_core,
            "current_config",
            lambda: SimpleNamespace(
                mck_overrides={
                    "bond_scale": 1.0,
                    "bond_thresholds": [
                        {"elements": ["H", "O"], "cutoff": 0.1}
                    ],
                }
            ),
        )
        backend.reload_bond_policy()

        # The existing bundle objects are updated in place, so any active
        # scene reference observes the new policy immediately.
        assert backend.bundles[direct.name] is direct
        assert backend.bundles[uploaded.name] is uploaded
        assert direct.molcrys_analysis.bond_pairs == [(1, 2)]
        assert uploaded.molcrys_analysis.bond_pairs == [(1, 2)]
    finally:
        backend.close(wait=False)
