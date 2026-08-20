"""Helpers shared by command-line static export paths."""

from __future__ import annotations

from importlib.metadata import version
import os
from pathlib import Path
import tempfile
from typing import Any

from ..scene import scene_style
from .api import render


def plotly_static_export_available(
    *, real_write_probe: bool = True
) -> tuple[bool, str | None]:
    """Check that Plotly/Kaleido can perform a real static image write."""
    try:
        major = int(version("kaleido").split(".", 1)[0])
    except Exception:
        return False, "Kaleido is not installed"
    if major < 1:
        return True, None
    try:
        from choreographer.browsers.chromium import Chromium

        browser = Chromium.find_browser(skip_local=False)
    except Exception as exc:
        return False, f"browser detection failed: {exc}"
    if browser:
        if not real_write_probe:
            return True, None
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
    return False, "Kaleido 1+ could not find Chrome or Chromium"


def save_flat_ortep_fallback(
    scene: dict[str, Any],
    overrides: dict[str, Any],
    output_path: str | Path,
    *,
    width: int,
    height: int,
    scale: float,
) -> str:
    """Save the established deterministic fallback for unavailable Plotly export."""
    fallback_style = scene_style(
        scene,
        {
            **overrides,
            "material": "flat",
            "style": "ortep",
            "projection": "orthographic",
        },
    )
    render(scene, fallback_style).save(
        str(output_path),
        width=width,
        height=height,
        scale=scale,
    )
    return "matplotlib-flat-ortep"
