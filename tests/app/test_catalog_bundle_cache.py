from __future__ import annotations

from pathlib import Path

import pytest

from mat_viewer.app.backend import ViewerBackend
from mat_viewer.app.shared import default_preset, get_default_catalog


ROOT = Path(__file__).resolve().parents[2]


def _dap4_loader_args() -> dict:
    entry = get_default_catalog(root_dir=str(ROOT))["DAP-4"]
    return {
        "name": "DAP-4",
        "cif_path": entry["cif_path"],
        "title": entry["title"],
        "preset": default_preset(),
        "source": "catalog",
    }


def test_catalog_bundle_factory_returns_independent_copies(
    catalog_bundle_factory,
) -> None:
    first = catalog_bundle_factory(**_dap4_loader_args())
    original_title = first.scene["title"]
    first.scene["title"] = "mutated in one test"

    second = catalog_bundle_factory(**_dap4_loader_args())

    assert second is not first
    assert second.scene is not first.scene
    assert second.scene["title"] == original_title


@pytest.mark.real_catalog_load
def test_backend_can_use_uncached_catalog_loader(tmp_path: Path) -> None:
    with ViewerBackend(
        preset_path=str(tmp_path / "preset.json"),
        names=["DAP-4"],
        root_dir=str(ROOT),
    ) as backend:
        bundle = backend.get_bundle("DAP-4")

    assert bundle.name == "DAP-4"
    assert bundle.source == "catalog"
    assert bundle.raw_atoms
