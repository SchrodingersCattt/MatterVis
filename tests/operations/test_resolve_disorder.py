from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mat_viewer.operations import disorder
from mat_viewer.operations.disorder import resolve_disorder


def test_resolve_disorder_returns_ordered_replica_summaries():
    cif_path = Path(__file__).resolve().parents[2] / "scripts" / "data" / "DAP-4-user-disorder.cif"
    bundle = SimpleNamespace(cif_path=str(cif_path), raw_atoms=[])

    replicas = resolve_disorder(bundle, method="optimal", count=1)

    assert replicas
    first = replicas[0]
    assert first["method"] == "optimal"
    assert first["kept_count"] == len(first["kept_indices"])
    assert all(isinstance(idx, int) for idx in first["kept_indices"])


@pytest.mark.parametrize("value,expected", [(0, 1), (-3, 1), (5, 5), (1000, 128)])
def test_resolve_count_is_bounded(value, expected):
    assert disorder._coerce_count(value, method="enumerate") == expected


def test_resolve_count_optimal_is_always_one():
    assert disorder._coerce_count(1000, method="optimal") == 1


def test_random_seed_zero_is_preserved_and_invalid_seed_is_unseeded():
    assert disorder._coerce_seed(0) == 0
    assert disorder._coerce_seed("") is None
    assert disorder._coerce_seed("not-an-int") is None


def test_unordered_resolve_reports_no_disorder_for_ordered_cif():
    cif_path = Path(__file__).resolve().parents[2] / "benchmarks" / "structures" / "molecular_water.cif"
    bundle = SimpleNamespace(cif_path=str(cif_path), raw_atoms=[])

    assert resolve_disorder(bundle, method="enumerate", count=5) == []


def test_unseeded_random_resolve_is_not_cached(monkeypatch):
    calls = []

    def fake_generate(path, *, method, count, seed):
        calls.append((path, method, count, seed))
        return ((len(calls),),)

    monkeypatch.setattr(disorder, "_generate_mck_replicas", fake_generate)
    disorder._cached_mck_replicas.cache_clear()

    first = disorder._mck_replicas("fixture.cif", method="random", count=5, seed=None)
    second = disorder._mck_replicas("fixture.cif", method="random", count=5, seed=None)
    assert first != second
    assert len(calls) == 2

    seeded_a = disorder._mck_replicas("fixture.cif", method="random", count=5, seed=7)
    seeded_b = disorder._mck_replicas("fixture.cif", method="random", count=5, seed=7)
    assert seeded_a == seeded_b
    assert len(calls) == 3
