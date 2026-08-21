"""Helpers shared by command-line static export paths."""

from __future__ import annotations

from importlib.metadata import version
import os
import tempfile


def _plotly_write_probe() -> tuple[bool, str | None]:
    probe_path = None
    try:
        import plotly.graph_objects as go

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            probe_path = handle.name
        go.Figure(go.Scatter(x=[0, 1], y=[0, 1])).write_image(
            probe_path,
            width=64,
            height=64,
            scale=1,
        )
        if os.path.getsize(probe_path) <= 0:
            return False, "Plotly/Kaleido write probe produced an empty PNG"
        return True, None
    except Exception as exc:
        return False, f"Plotly/Kaleido write probe failed: {type(exc).__name__}: {exc}"
    finally:
        if probe_path:
            try:
                os.unlink(probe_path)
            except OSError:
                pass


def plotly_static_export_available(
    *, real_write_probe: bool = True
) -> tuple[bool, str | None]:
    """Check that Plotly/Kaleido can perform a real static image write."""
    try:
        major = int(version("kaleido").split(".", 1)[0])
    except Exception:
        return False, "Kaleido is not installed"
    if major < 1:
        return _plotly_write_probe() if real_write_probe else (True, None)
    try:
        from choreographer.browsers.chromium import Chromium

        browser = Chromium.find_browser(skip_local=False)
    except Exception as exc:
        return False, f"browser detection failed: {exc}"
    if browser:
        if not real_write_probe:
            return True, None
        return _plotly_write_probe()
    return False, "Kaleido 1+ could not find Chrome or Chromium"
