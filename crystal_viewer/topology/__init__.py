from __future__ import annotations

# Public API — all symbols documented in agents/polyhedron_api.md
from .analysis import (  # noqa: F401
    DEFAULT_CENTROID_OFFSET_FRAC,
    _classify_shell_payload,
    _empty_shape_payload,
    _hull_encloses_center,
    analyze_topology,
    classify_fragments,
    classify_shell,
    compute_angular_signature,
    convex_hull_payload,
    detect_coordination_number,
    detect_prism_vs_antiprism,
    extract_coordination_shell,
    ideal_polyhedra_for_cn,
    planarity_analysis,
)

__all__ = [
    "DEFAULT_CENTROID_OFFSET_FRAC",
    "_classify_shell_payload",
    "_empty_shape_payload",
    "_hull_encloses_center",
    "analyze_topology",
    "classify_fragments",
    "classify_shell",
    "compute_angular_signature",
    "convex_hull_payload",
    "detect_coordination_number",
    "detect_prism_vs_antiprism",
    "extract_coordination_shell",
    "ideal_polyhedra_for_cn",
    "planarity_analysis",
]
