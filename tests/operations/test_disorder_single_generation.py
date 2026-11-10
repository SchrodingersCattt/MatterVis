"""Unseeded disorder keeps the crystal from the same generation as the summary."""

from __future__ import annotations

import warnings
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from mat_viewer.effects import EffectSession
from mat_viewer.operations import disorder
from mat_viewer.structure.cif_parse import load_cif

CIF = Path("scripts/data/DAP-4-user-disorder.cif")


def test_unseeded_random_generation_is_applied_without_a_second_draw(monkeypatch):
    calls = []
    original = disorder._call_mck_replicas

    def counted(cif_path, *, method, count, seed):
        calls.append(method)
        return original(cif_path, method=method, count=count, seed=seed)

    monkeypatch.setattr(disorder, "_call_mck_replicas", counted)
    bundle = SimpleNamespace(cif_path=str(CIF), raw_atoms=[])
    summaries, retained = disorder.resolve_disorder_with_crystals(
        bundle,
        method="random",
        count=1,
        seed=None,
    )
    assert summaries
    assert calls.count("random") == 1
    candidate_id = summaries[0]["candidate_id"]
    crystal = retained[candidate_id]["crystal"]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        source = load_cif(str(CIF)).crystal
    session = EffectSession()
    ref = session.register(source, structure_id="disorder")
    committed = session.operate(
        "commit_disorder_candidate",
        {"crystal": crystal, "kept_indices": retained[candidate_id]["kept_indices"]},
        structure_id=ref.id,
        expected_revision=1,
    )
    saved = session.checkout(committed.output_ref)
    original_positions = np.vstack([np.asarray(molecule.positions, dtype=float) for molecule in crystal.molecules])
    saved_positions = np.vstack([np.asarray(molecule.positions, dtype=float) for molecule in saved.molecules])
    assert saved_positions.shape == original_positions.shape
    assert np.allclose(saved_positions, original_positions)
    assert calls.count("random") == 1
