"""Named cache-key builders shared by scene, topology, and figure paths.

Cache keys are part of the invalidation contract.  Keeping their construction
in one dependency-free module prevents a callback and a cache owner from
silently drifting apart when a new state field is introduced.  The builders
return the historical tuple/string shapes so existing in-memory caches and
host integrations remain compatible while callers gain named semantics.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .transforms import transforms_cache_key
from .topology import DEFAULT_CENTROID_OFFSET_FRAC


_VOLATILE_STATE_FIELDS = {
    "version",
    "server_started_at",
    "render_revision",
    "geometry_version",
    "display_version",
    "camera_version",
    "camera_revision",
    "projection",
}


def display_state_key(state: Mapping[str, Any] | None) -> str:
    """Canonical display-state serialization shared by all figure keys."""

    state = state or {}
    return json.dumps(
        {k: v for k, v in state.items() if k not in _VOLATILE_STATE_FIELDS | {"camera"}},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _bond_threshold_key(
    bond_thresholds: Mapping[tuple[str, str], float] | None,
) -> tuple[tuple[str, str, float], ...]:
    """Return a stable, hashable representation of bond thresholds."""

    return tuple(
        sorted(
            (str(left), str(right), float(value))
            for (left, right), value in (bond_thresholds or {}).items()
        )
    )


def _hashable(value: Any) -> Any:
    """Normalize nested JSON values for tuple/set cache keys."""

    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _hashable(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_hashable(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted(_hashable(item) for item in value))
    return value


def scene_cache_key(
    *,
    display_mode: str,
    show_hydrogen: bool,
    include_boundary_replicas: bool = True,
    include_cross_boundary_bond_endpoints: bool = True,
    include_minor: bool = True,
    bond_scale: float | None = None,
    bond_thresholds: Mapping[tuple[str, str], float] | None = None,
) -> tuple[Any, ...]:
    """Key a base manifested scene by every input that changes geometry.

    This intentionally preserves the tuple used by ``build_bundle_scene``;
    callers that still have old entries in a bundle cache can continue to
    read them during a rolling upgrade.
    """

    return (
        display_mode,
        bool(show_hydrogen),
        bool(include_boundary_replicas),
        bool(include_cross_boundary_bond_endpoints),
        bool(include_minor),
        bond_scale,
        _bond_threshold_key(bond_thresholds),
    )


def legacy_scene_cache_key(
    *,
    display_mode: str,
    show_hydrogen: bool,
    include_boundary_replicas: bool = True,
    bond_scale: float | None = None,
    bond_thresholds: Mapping[tuple[str, str], float] | None = None,
) -> tuple[Any, ...]:
    """Return the pre-cross-boundary-endpoint scene key for compatibility."""

    return (
        display_mode,
        bool(show_hydrogen),
        bool(include_boundary_replicas),
        bond_scale,
        _bond_threshold_key(bond_thresholds),
    )


def fragment_table_cache_key(
    *,
    display_mode: str,
    show_hydrogen: bool,
    include_boundary_replicas: bool = True,
    include_cross_boundary_bond_endpoints: bool = True,
    include_minor: bool = True,
) -> tuple[Any, ...]:
    """Key fragment labels by the manifested atom population."""

    return (
        "scene",
        display_mode,
        bool(show_hydrogen),
        bool(include_boundary_replicas),
        bool(include_cross_boundary_bond_endpoints),
        bool(include_minor),
    )


def transformed_scene_cache_key(
    *,
    display_mode: str,
    show_hydrogen: bool,
    transforms: Sequence[Mapping[str, Any]] | None,
    include_boundary_replicas: bool = True,
    include_cross_boundary_bond_endpoints: bool = True,
    include_minor: bool = True,
) -> tuple[Any, ...]:
    """Key a post-transform scene by its source scene and transform pipeline."""

    return (
        display_mode,
        bool(show_hydrogen),
        bool(include_boundary_replicas),
        bool(include_cross_boundary_bond_endpoints),
        bool(include_minor),
        transforms_cache_key(transforms or []),
    )


def topology_spec_geometry_key(specs: Iterable[Mapping[str, Any]]) -> frozenset[tuple[Any, ...]]:
    """Extract topology fields that alter coordination-shell geometry.

    Paint fields (colour, opacity, enabled, and instance overrides) are
    deliberately absent.  They are applied by the topology painter cache and
    should not trigger an expensive MolCrysKit geometry computation.
    """

    return frozenset(
        (
            str(spec.get("center_species") or ""),
            _hashable(spec.get("ligand_species") or None),
            bool(spec.get("enforce_enclosure", True)),
            float(spec.get("centroid_offset_frac", DEFAULT_CENTROID_OFFSET_FRAC)),
            str(spec.get("level") or "molecule"),
            str(spec.get("center_kind") or "centroid"),
            _hashable(spec.get("hard_cutoff")),
            _hashable(spec.get("fallback_max")),
        )
        for spec in specs or ()
    )


def topology_geometry_cache_key(
    *,
    structure: str,
    display_mode: str | None,
    show_hydrogen: bool,
    site_index: int,
    cutoff: float,
    specs: Iterable[Mapping[str, Any]],
    transforms: Sequence[Mapping[str, Any]] | None,
) -> tuple[Any, ...]:
    """Key the expensive topology geometry computation."""

    return (
        structure,
        display_mode,
        bool(show_hydrogen),
        int(site_index),
        float(cutoff),
        topology_spec_geometry_key(specs),
        transforms_cache_key(transforms or []),
    )


def topology_side_panel_cache_key(state: Mapping[str, Any]) -> tuple[Any, ...]:
    """Key topology histogram/markdown data for ``update_view``.

    The side panel reads topology results, so paint-only fields are excluded;
    geometry knobs added by newer MolCrysKit versions are included here to
    avoid stale summaries after changing ``level`` or ``hard_cutoff``.
    """

    specs = state.get("polyhedron_specs") or ()
    return (
        state.get("scene_id"),
        state.get("structure"),
        state.get("display_mode"),
        tuple(state.get("topology_species_keys") or ()),
        state.get("topology_site_index"),
        bool(state.get("topology_enabled")),
        float(state.get("cutoff", 10.0)),
        "hydrogens" in (state.get("display_options") or ()),
        transforms_cache_key(state.get("transforms") or []),
        topology_spec_geometry_key(specs),
    )


def fragment_options_cache_key(state: Mapping[str, Any]) -> tuple[Any, ...]:
    """Key the fragment dropdown by the source scene and manifested inputs."""

    return (
        state.get("scene_id"),
        state.get("structure"),
        state.get("display_mode"),
        "hydrogens" in (state.get("display_options") or ()),
        transforms_cache_key(state.get("transforms") or []),
    )


def figure_state_cache_key(state: Mapping[str, Any]) -> str:
    """Return the canonical figure-body key and its camera compatibility rule.

    Plotly figures apply a camera after a cache hit, so ordinary figures omit
    ``camera``.  Flat ORTEP embeds the camera basis in its raster image and
    therefore retains it.  Polyhedron ``enabled`` flags are a post-cache
    visibility patch and are removed from the body key in both paths.
    """

    key_state = json.loads(display_state_key(state))
    specs = key_state.get("polyhedron_specs")
    if isinstance(specs, list):
        key_state["polyhedron_specs"] = [
            {k: v for k, v in spec.items() if k != "enabled"}
            if isinstance(spec, dict)
            else spec
            for spec in specs
        ]
    if state.get("material") == "flat" and state.get("style") == "ortep":
        key_state["camera"] = state.get("camera")
    return json.dumps(key_state, sort_keys=True, separators=(",", ":"))


__all__ = [
    "figure_state_cache_key",
    "display_state_key",
    "fragment_options_cache_key",
    "fragment_table_cache_key",
    "legacy_scene_cache_key",
    "scene_cache_key",
    "topology_geometry_cache_key",
    "topology_side_panel_cache_key",
    "topology_spec_geometry_key",
    "transformed_scene_cache_key",
]
