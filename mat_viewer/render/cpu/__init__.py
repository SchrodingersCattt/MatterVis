"""Browser-independent CPU rendering backend."""

from __future__ import annotations

from pathlib import Path

from ..contracts import RenderPlan, RenderResult
from .raster import render_png, render_rgba
from .vector import render_vector


def render(
    plan: RenderPlan,
    output: str | Path | None = None,
    *,
    format: str | None = None,
) -> RenderResult:
    """Render a plan to PNG, SVG, or PDF."""
    destination = Path(output) if output is not None else None
    requested = (
        destination.suffix.lstrip(".")
        if destination is not None
        else format or str(plan.metadata.get("output_format", "png"))
    ).lower()
    if requested == "png":
        return render_png(plan, destination)
    if requested in {"svg", "pdf"}:
        return render_vector(plan, destination, format=requested)
    raise ValueError(f"CPU backend supports .png, .svg, and .pdf; got {requested!r}")


__all__ = ["render", "render_png", "render_rgba", "render_vector"]
