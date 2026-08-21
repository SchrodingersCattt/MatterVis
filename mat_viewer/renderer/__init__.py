"""Legacy renderer facade whose implementation modules load on first use."""

from __future__ import annotations

from importlib import import_module

_MODULES = (
    "..render.api",
    "..render.compass",
    "..render.figures",
    "..render.publication",
    "..render.scene_traces",
    "..render.style",
    "..render.topology",
    "..render.viewport",
    "..render.overlay.vectors",
)


def __getattr__(name: str):
    for module_name in _MODULES:
        module = import_module(module_name, __name__)
        if hasattr(module, name):
            value = getattr(module, name)
            globals()[name] = value
            return value
    raise AttributeError(name)


__all__ = [
    "DENSE_COORDINATION_PRESET",
    "FigureResult",
    "ViewportAccumulator",
    "axis_key_overlay",
    "build_figure",
    "build_publication_figure",
    "build_row_figure",
    "build_static_publication_figure",
    "compose_axis_key_layout",
    "normalize_vector_overlays",
    "paper_vector_label_annotations",
    "render",
    "resolve_vector_overlays",
    "style_from_controls",
    "topology_histogram_figure",
    "topology_results_markdown",
    "uniform_viewport",
    "validate_style_schema",
    "vector_mesh_traces",
    "vector_overlay_bounds",
]
