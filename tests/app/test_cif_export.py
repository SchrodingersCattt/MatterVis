"""Regression coverage for Operations → Current structure CIF export."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
from _layout_helpers import layout_ids

from mat_viewer.app import callbacks_view, create_app
from mat_viewer.loader.cif_export import loaded_crystal_to_cif

WATER_CIF = "benchmarks/structures/molecular_water.cif"


def test_current_cif_api_returns_download_and_json_payload(tmp_path) -> None:
    app = create_app(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
        cif_paths=[WATER_CIF],
    )
    try:
        client = app.server.test_client()
        response = client.get("/api/v2/export/cif")
        assert response.status_code == 200
        assert response.mimetype == "chemical/x-cif"
        assert response.headers["Content-Disposition"] == 'attachment; filename="molecular_water.cif"'
        assert response.data.startswith(b"data_crystal\n")

        payload = client.get("/api/v2/export/cif?json=1").get_json()
        assert payload["filename"] == "molecular_water.cif"
        assert payload["structure"] == "molecular_water"
        assert payload["content"].startswith("data_crystal\n")
    finally:
        app.close_extensions()
        app.crystal_backend.close()


def test_operations_panel_exposes_cif_download_controls(tmp_path) -> None:
    app = create_app(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
    )
    try:
        ids = layout_ids(app.layout())
        assert {"export-cif-btn", "cif-download", "status-banner"} <= ids
        assert any(
            "export-cif-btn" in str(input_spec)
            for callback in app.callback_map.values()
            for input_spec in callback.get("inputs", [])
        )
    finally:
        app.close_extensions()
        app.crystal_backend.close()


def test_raw_atom_fallback_produces_valid_cif() -> None:
    bundle = SimpleNamespace(
        M=np.diag([5.0, 6.0, 7.0]),
        cell=SimpleNamespace(a=5.0, b=6.0, c=7.0, alpha=90.0, beta=90.0, gamma=90.0),
        raw_atoms=[
            {
                "label": "C1",
                "elem": "C",
                "frac": np.array([0.1, 0.2, 0.3]),
                "occ": 1.0,
                "dg": ".",
                "da": ".",
            }
        ],
        crystal=None,
    )
    text = loaded_crystal_to_cif(bundle)
    assert "_cell_length_a 5.000000" in text
    assert "C1 C 0.10000000 0.20000000 0.30000000" in text


def test_save_preset_failure_is_reported_in_status_banner(tmp_path, monkeypatch) -> None:
    app = create_app(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
    )
    try:
        callback = next(
            spec["callback"].__wrapped__
            for spec in app.callback_map.values()
            if spec.get("callback") is not None
            and spec["callback"].__name__ == "save_or_export"
        )
        monkeypatch.setattr(
            callbacks_view,
            "callback_context",
            SimpleNamespace(
                triggered=[{"prop_id": "save-preset-btn.n_clicks"}],
            ),
        )
        monkeypatch.setattr(
            app.crystal_backend,
            "save_preset",
            lambda: (_ for _ in ()).throw(OSError("disk full")),
        )
        message, class_name, *_ = callback(1, 0, 0)
        assert message == "Save preset failed: disk full"
        assert class_name == "status-banner status-banner--error"
    finally:
        app.close_extensions()
        app.crystal_backend.close()

