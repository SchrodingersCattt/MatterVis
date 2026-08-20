from __future__ import annotations

from pathlib import Path


def test_plotly_static_export_real_write_probe_reports_failure(
    monkeypatch,
) -> None:
    import plotly.graph_objects as go

    from crystal_viewer.render.export import plotly_static_export_available

    def fail(*args, **kwargs):
        raise RuntimeError("browser starts but cannot render")

    monkeypatch.setattr(go.Figure, "write_image", fail)

    available, reason = plotly_static_export_available(real_write_probe=True)

    assert not available
    assert "write probe failed" in str(reason)
    assert "browser starts but cannot render" in str(reason)


def test_animation_disables_style_fallback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from crystal_viewer.render import cli

    calls = []

    def fake_static(
        bundle,
        scene,
        style,
        topology_data,
        args,
        output_path,
        *,
        allow_style_fallback=True,
    ):
        calls.append(allow_style_fallback)
        raise RuntimeError("Chrome unavailable")

    monkeypatch.setattr(cli, "_save_static_output", fake_static)
    args = type("Args", (), {"fps": 5.0})()
    output = tmp_path / "fallback.gif"
    prepared = [
        (object(), {}, {}, None),
        (object(), {}, {}, None),
    ]

    import pytest
    from crystal_viewer.render.animation import save_prepared_from_cli

    with pytest.raises(RuntimeError, match="Chrome unavailable"):
        save_prepared_from_cli(prepared, args, output)

    assert calls == [False]
    assert not output.exists()
