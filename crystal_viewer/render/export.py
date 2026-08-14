"""Helpers shared by command-line static export paths."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
from typing import Any

from ..scene import scene_style
from .api import render


def plotly_static_export_available() -> tuple[bool, str | None]:
    """Check whether the installed Kaleido generation can find a browser."""
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
        return True, None
    return False, "Kaleido 1+ could not find Chrome or Chromium"


def save_flat_ortep_fallback(
    scene: dict[str, Any],
    overrides: dict[str, Any],
    output_path: str | Path,
    *,
    width: int,
    height: int,
    scale: float,
) -> None:
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
