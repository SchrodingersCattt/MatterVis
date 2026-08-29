"""Standalone interactive HTML export helpers."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any


def _standalone_compass_script() -> str:
    return (
        files("mat_viewer")
        .joinpath("render", "assets", "standalone_compass.js")
        .read_text(encoding="utf-8")
    )


def write_interactive_html(fig: Any, output_path: str | Path) -> None:
    """Write a Plotly HTML whose lattice compass follows camera rotation."""
    fig.write_html(
        str(output_path),
        include_plotlyjs="cdn",
        full_html=True,
        post_script=_standalone_compass_script(),
    )
