"""Empty/error analysis must render a message rather than an unbound html local."""

from types import SimpleNamespace

import pytest

from mat_viewer.app import callbacks_analysis, create_app


@pytest.mark.parametrize("status,expected", [
    ("error", "Analysis failed."), ("ok", "No facets found."),
])
def test_bfdh_non_result_message(tmp_path, monkeypatch, status, expected):
    monkeypatch.setenv("MATTERVIS_PREWARM", "0")
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    monkeypatch.setattr(callbacks_analysis, "callback_context",
                        SimpleNamespace(triggered_id="bfdh-run-btn"))
    monkeypatch.setattr(app.crystal_backend, "run_bfdh_analysis",
                        lambda **kwargs: {"status": status, "warnings": ["test"], "facets": []})
    try:
        callback = app.callback_map["bfdh-results-container.children"]["callback"].__wrapped__
        result = callback(1, None, 2, 10)
        assert result.children == expected
    finally:
        app.close_extensions()
        app.crystal_backend.close()


@pytest.mark.parametrize("max_index", [0, -1])
def test_bfdh_invalid_max_index_is_shown_in_results(tmp_path, monkeypatch, max_index):
    monkeypatch.setenv("MATTERVIS_PREWARM", "0")
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    monkeypatch.setattr(
        callbacks_analysis,
        "callback_context",
        SimpleNamespace(triggered_id="bfdh-run-btn"),
    )
    called = []
    monkeypatch.setattr(
        app.crystal_backend,
        "run_bfdh_analysis",
        lambda **kwargs: called.append(kwargs),
    )
    try:
        callback = app.callback_map["bfdh-results-container.children"]["callback"].__wrapped__
        result = callback(1, None, max_index, 10)
        assert "at least 1" in result.children
        assert called == []
    finally:
        app.close_extensions()
        app.crystal_backend.close()
