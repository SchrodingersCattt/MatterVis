from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from crystal_viewer.operations.disorder import resolve_disorder


def test_resolve_disorder_returns_ordered_replica_summaries():
    cif_path = Path(__file__).resolve().parents[2] / "scripts" / "data" / "DAP-4.cif"
    bundle = SimpleNamespace(cif_path=str(cif_path), raw_atoms=[])

    replicas = resolve_disorder(bundle, method="optimal", count=1)

    assert replicas
    first = replicas[0]
    assert first["method"] == "optimal"
    assert first["kept_count"] == len(first["kept_indices"])
    assert all(isinstance(idx, int) for idx in first["kept_indices"])
