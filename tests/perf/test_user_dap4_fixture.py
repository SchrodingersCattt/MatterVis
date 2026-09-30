"""Smoke coverage for the owner-supplied disorder DAP-4 fixture."""

from pathlib import Path
import warnings

from mat_viewer.loader import build_loaded_crystal


def test_user_dap4_disorder_fixture_loads_with_expected_source_counts():
    fixture = Path(__file__).parents[2] / "scripts" / "data" / "DAP-4-user-disorder.cif"
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Structure contains disorder.*")
        bundle = build_loaded_crystal(
            name="DAP-4-user-disorder",
            cif_path=str(fixture),
            title="DAP-4-user-disorder",
            source="owner-fixture",
        )
    assert len(bundle.raw_atoms) == 368
    assert len(bundle.formula_unit_atoms) == 46
