"""Rendering facade with backend-neutral imports and lazy frontends."""

from __future__ import annotations

from importlib import import_module
import sys
from types import ModuleType

from .contracts import (
    CameraSpec,
    LinePrimitive,
    RenderPlan,
    RenderResult,
    RenderSpec,
    TextPrimitive,
    TriangleMeshPrimitive,
    ViewSpec,
    ViewportPlan,
)
from .planning import prepare_render
from ..properties import AtomPropertyColorSpec


_LAZY_EXPORTS = {
    "build_figure": (".figures", "build_figure"),
    "build_publication_figure": (".figures", "build_publication_figure"),
    "build_row_figure": (".figures", "build_row_figure"),
    "cylinder_entity": (".geometry", "cylinder_entity"),
    "geometry_entity_traces": (".geometry", "geometry_entity_traces"),
    "implicit_entity": (".geometry", "implicit_entity"),
    "mesh_entity": (".geometry", "mesh_entity"),
    "through_cylinder_entity": (".geometry", "through_cylinder_entity"),
    "validate_geometry_style": (".geometry", "validate_geometry_style"),
    "build_static_publication_figure": (
        ".publication",
        "build_static_publication_figure",
    ),
    "normalize_cell_overlays": (".overlay.cells", "normalize_cell_overlays"),
    "DENSE_COORDINATION_PRESET": (".publication", "DENSE_COORDINATION_PRESET"),
    "normalize_vector_overlays": (".overlay.vectors", "normalize_vector_overlays"),
    "paper_vector_label_annotations": (
        ".overlay.vectors",
        "paper_vector_label_annotations",
    ),
    "resolve_vector_overlays": (".overlay.vectors", "resolve_vector_overlays"),
    "vector_mesh_traces": (".overlay.vectors", "vector_mesh_traces"),
    "vector_overlay_bounds": (".overlay.vectors", "vector_overlay_bounds"),
}


def cpu_render(*args, **kwargs):
    """Render through the CPU backend without importing it until invocation."""
    from .cpu import render

    return render(*args, **kwargs)


class _CallableRenderModule(ModuleType):
    """Keep ``mat_viewer.render(...)`` callable after importing this subpackage.

    Python necessarily installs this module on ``mat_viewer.render`` when the
    subpackage is imported.  Making the facade module callable preserves the
    public agent API without eagerly importing a backend.
    """

    def __call__(self, *args, **kwargs):
        from ..agent import render

        return render(*args, **kwargs)


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value


__all__ = [
    "AtomPropertyColorSpec",
    "CameraSpec",
    "DENSE_COORDINATION_PRESET",
    "LinePrimitive",
    "RenderPlan",
    "RenderResult",
    "RenderSpec",
    "TextPrimitive",
    "TriangleMeshPrimitive",
    "ViewSpec",
    "ViewportPlan",
    "build_figure",
    "build_publication_figure",
    "build_row_figure",
    "build_static_publication_figure",
    "cpu_render",
    "normalize_cell_overlays",
    "normalize_vector_overlays",
    "paper_vector_label_annotations",
    "prepare_render",
    "resolve_vector_overlays",
    "vector_mesh_traces",
    "vector_overlay_bounds",
    "cylinder_entity",
    "geometry_entity_traces",
    "implicit_entity",
    "mesh_entity",
    "through_cylinder_entity",
    "validate_geometry_style",
]


sys.modules[__name__].__class__ = _CallableRenderModule
