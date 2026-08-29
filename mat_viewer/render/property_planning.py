"""Property-colour preparation shared by render-plan construction."""

from __future__ import annotations

from typing import Any

from ..properties import (
    SourcePropertyContext,
    coerce_atom_property_color_spec,
    map_property_colors,
    property_metadata,
    resolve_source_property_context,
    rgba_to_hex,
)


def resolve_render_property_context(
    source: Any,
    atom_property_color: Any,
) -> SourcePropertyContext | None:
    if atom_property_color is None:
        return None
    if isinstance(atom_property_color, SourcePropertyContext):
        return atom_property_color
    spec = coerce_atom_property_color_spec(atom_property_color)
    assert spec is not None
    return resolve_source_property_context(source, spec)


def prepare_render_property(
    context: SourcePropertyContext | None,
    scene: dict[str, Any],
) -> tuple[list[str] | None, dict[str, Any] | None, list[str]]:
    if context is None:
        return None, None, []
    frame_index = int(
        scene.get("frame_index")
        if scene.get("frame_index") is not None
        else context.source_frame_indices[0]
    )
    reduced = context.frame(frame_index)
    colors = rgba_to_hex(
        map_property_colors(
            reduced.values,
            context.scale,
            nan_color=context.spec.nan_color,
        )
    )
    metadata = property_metadata(
        context.spec,
        reduced,
        context.scale,
        manifest_hash=context.manifest_hash,
    )
    warnings = []
    if context.scale.missing_count:
        warnings.append(
            f"atom property contains {context.scale.missing_count} "
            f"NaN/Inf value(s); using {context.spec.nan_color}"
        )
    return colors, metadata, warnings


def property_color_for_atom(
    colors: list[str] | None,
    source_index: int,
    display_index: int,
) -> str | None:
    if colors is None:
        return None
    if not 0 <= source_index < len(colors):
        raise ValueError(
            f"display atom {display_index} maps to source index {source_index}, "
            f"outside the selected atom property with {len(colors)} values"
        )
    return colors[source_index]


def reserve_property_colorbar(
    metadata: dict[str, Any] | None,
    width: int,
) -> float:
    if not metadata or not metadata["show_colorbar"]:
        return 1.0
    colorbar_width_px = min(max(float(width) * 0.14, 72.0), 128.0)
    viewport_width_fraction = max(
        0.1,
        (float(width) - colorbar_width_px) / float(width),
    )
    metadata["colorbar_rect"] = [
        viewport_width_fraction,
        0.08,
        1.0 - viewport_width_fraction,
        0.84,
    ]
    return viewport_width_fraction
