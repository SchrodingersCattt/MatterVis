"""Pure selectors for the viewer's stored scene state.

The Dash app historically derived display flags in each callback and mixed
those flags into ad-hoc cache tuples.  This module is the small, dependency
free seam used by the first state-machine migration step.  Selectors read a
state mapping and return new values; they never normalise or mutate the input.

The sets below are deliberately explicit.  They document which values are
durable scene intent and which values are transport details while retaining
unknown keys in :func:`partition_state` so a newer client can round-trip a
field before this module knows about it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any

from ..transforms import transforms_cache_key


# Display options accepted by the viewer.  Unknown options are retained by
# ``display_option_set`` for forwards compatibility, but are not treated as a
# geometry or viewport input unless a selector explicitly opts into them.
DISPLAY_OPTION_TOKENS = frozenset(
    {
        "hydrogens",
        "unit_cell_box",
        "labels",
        "axes",
        "minor_only",
        "minor_wireframe",
        # Kept for old REST clients; normalize_state migrates it to a group.
        "monochrome",
    }
)
GEOMETRY_DISPLAY_OPTIONS = frozenset({"hydrogens"})
VIEWPORT_DISPLAY_OPTIONS = frozenset({"hydrogens", "unit_cell_box", "minor_only"})
# Only labels and axes can be changed by the trace-only browser patch.  The
# minor-wireframe token is still presentation-only for cache purposes, but it
# changes line traces and therefore requires the normal render path.
COSMETIC_DISPLAY_OPTIONS = frozenset({"labels", "axes"})


# The target state model in docs/redesign/state.md calls out these keys as
# durable user intent.  The additional keys are existing scene fields that
# must remain durable while the reducer migration is staged.
STORED_STATE_KEYS = frozenset(
    {
        "scene_id",
        "scene_label",
        "structure",
        "display_mode",
        "display_options",
        "atom_scale",
        "bond_radius",
        "minor_opacity",
        "axis_scale",
        "material",
        "style",
        "disorder",
        "ortep_mode",
        "label_mode",
        "projection",
        "cutoff",
        "topology_site_index",
        "topology_enabled",
        "topology_species_keys",
        "topology_hull_color",
        "polyhedron_specs",
        "atom_groups",
        "bond_groups",
        "atom_property_color",
        "transforms",
        "disorder_resolve",
        "disorder_replicas",
        "selection",
        "bfdh_morphology",
        "bfdh_morphology_color",
        "camera",
        "camera_revision",
    }
)
DERIVED_STATE_KEYS = frozenset(
    {
        "show_hydrogen",
        "show_unit_cell",
        "show_axes",
        "show_labels",
        "monochrome",
        "fragment_options",
        "topology_payload",
        "figure",
        "uirevision",
    }
)
EPHEMERAL_STATE_KEYS = frozenset(
    {
        "version",
        "server_started_at",
        "render_revision",
        "geometry_version",
        "display_version",
        "camera_version",
        "pending_state",
        "camera-state-store",
        "camera_state_store",
        "click_data",
        "hover_data",
        "rightclick_target",
        # Legacy request aliases are accepted at an API boundary and should
        # never be persisted as scene state.
        "topology_fragment_type",
        "topology_show_all_sites",
        "supercell",
    }
)


@dataclass(frozen=True)
class StateSlices:
    """A read-only snapshot of the three state categories.

    ``unknown`` is intentionally exposed during migration.  It lets callers
    audit fields that have not yet been assigned to the contract without
    silently dropping them from a round-trip.
    """

    stored: dict[str, Any]
    derived: dict[str, Any]
    ephemeral: dict[str, Any]
    unknown: dict[str, Any]


def _stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def display_option_set(state_or_options: Mapping[str, Any] | Iterable[str] | None) -> frozenset[str]:
    """Return display option tokens as a set without changing the input."""

    if isinstance(state_or_options, Mapping):
        values = state_or_options.get("display_options") or ()
    else:
        values = state_or_options or ()
    if isinstance(values, str):
        values = (values,)
    return frozenset(str(value) for value in values if value is not None)


def display_option_delta(
    before: Mapping[str, Any] | Iterable[str] | None,
    after: Mapping[str, Any] | Iterable[str] | None,
) -> frozenset[str]:
    """Return the option tokens whose enabled state changed."""

    return display_option_set(before) ^ display_option_set(after)


def has_display_option(state: Mapping[str, Any] | None, token: str) -> bool:
    """Return whether ``token`` is enabled in ``state``."""

    return str(token) in display_option_set(state)


def show_hydrogen(state: Mapping[str, Any] | None) -> bool:
    return has_display_option(state, "hydrogens")


def show_unit_cell(state: Mapping[str, Any] | None) -> bool:
    return has_display_option(state, "unit_cell_box")


def show_axes(state: Mapping[str, Any] | None) -> bool:
    return has_display_option(state, "axes")


def show_labels(state: Mapping[str, Any] | None) -> bool:
    state = state or {}
    return has_display_option(state, "labels") and str(
        state.get("label_mode", "unique_sites")
    ).lower() not in {"none", "hidden", "off"}


def _has_monochrome_group(state: Mapping[str, Any]) -> bool:
    """Recognise the legacy all-atoms black group without mutating state."""

    for group in state.get("atom_groups") or ():
        if not isinstance(group, Mapping):
            continue
        selector = group.get("selector") or {}
        color = str(group.get("color") or "").lower()
        if selector.get("all") and color in {"#000000", "black"}:
            return True
    return False


def derived_state(state: Mapping[str, Any] | None) -> dict[str, Any]:
    """Compute derived flags from stored state.

    The returned dictionary is newly allocated.  In particular, callers may
    add presentation-only keys without changing a persisted scene snapshot.
    """

    state = state or {}
    return {
        "show_hydrogen": show_hydrogen(state),
        "show_unit_cell": show_unit_cell(state),
        "show_axes": show_axes(state),
        "show_labels": show_labels(state),
        "monochrome": has_display_option(state, "monochrome")
        or _has_monochrome_group(state),
        # ``fast_rendering`` is an explicit scene setting.  Flat material is
        # the one documented style that opts into the fast path by itself.
        "fast_rendering": bool(state.get("fast_rendering", False))
        or str(state.get("material", "")).lower() == "flat",
    }


def stored_state(state: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a deep-copied snapshot of durable scene intent."""

    state = state or {}
    return deepcopy({key: state[key] for key in STORED_STATE_KEYS if key in state})


def ephemeral_state(state: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return transport/request fields, including legacy aliases."""

    state = state or {}
    return deepcopy({key: state[key] for key in EPHEMERAL_STATE_KEYS if key in state})


def partition_state(state: Mapping[str, Any] | None) -> StateSlices:
    """Partition a state mapping into stored, derived, ephemeral, unknown."""

    state = state or {}
    known = STORED_STATE_KEYS | DERIVED_STATE_KEYS | EPHEMERAL_STATE_KEYS
    return StateSlices(
        stored=stored_state(state),
        derived=derived_state(state),
        ephemeral=ephemeral_state(state),
        unknown=deepcopy(
            {key: value for key, value in state.items() if key not in known}
        ),
    )


def scene_geometry_cache_key(state: Mapping[str, Any] | None) -> tuple[Any, ...]:
    """Key for the untransformed scene cache.

    Labels, axes, colours, camera and other presentation fields are absent by
    construction.  Hydrogen visibility is the only display option that
    changes atom/bond identity in the base scene.
    """

    state = state or {}
    return (
        state.get("structure"),
        state.get("display_mode", "formula_unit"),
        show_hydrogen(state),
    )


def transform_scene_cache_key(state: Mapping[str, Any] | None) -> tuple[Any, ...]:
    """Key for transformed scene geometry, excluding row id/name metadata."""

    state = state or {}
    return (*scene_geometry_cache_key(state), transforms_cache_key(state.get("transforms") or ()))


def geometry_state_fields(state: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the broader update identity used by the async view worker.

    This preserves the existing worker contract while keeping display token
    semantics in one place.  ``labels`` and ``axes`` are trace-only updates;
    all other options remain part of the worker's state identity for now.
    """

    state = state or {}
    options = display_option_set(state)
    fields = {
        key: state.get(key)
        for key in (
            "structure",
            "display_mode",
            "transforms",
            "disorder",
            "disorder_resolve",
            "disorder_replicas",
            "cutoff",
        )
    }
    fields["display_options"] = sorted(options - {"labels", "axes"})
    fields["hydrogens"] = show_hydrogen(state)
    return fields


def viewport_signature(state: Mapping[str, Any] | None) -> str:
    """Return the state-only viewport signature used by layout/camera code.

    Scene dimensions are supplied by the render resolver in later migration
    phases.  This signature captures the state inputs that can change those
    dimensions and intentionally ignores label/axis toggles and camera data.
    """

    state = state or {}
    options = display_option_set(state)
    return _stable(
        {
            "structure": state.get("structure"),
            "display_mode": state.get("display_mode", "formula_unit"),
            "display_options": sorted(options & VIEWPORT_DISPLAY_OPTIONS),
            "topology_enabled": bool(state.get("topology_enabled", False)),
            "transforms": transforms_cache_key(state.get("transforms") or ()),
        }
    )


# Names used by the migration notes and by downstream callers that prefer the
# selector vocabulary over the older ``show_*`` helpers.
select_display_options = display_option_set
select_derived_state = derived_state
select_stored_state = stored_state
select_ephemeral_state = ephemeral_state
select_viewport_signature = viewport_signature


__all__ = [
    "COSMETIC_DISPLAY_OPTIONS",
    "DERIVED_STATE_KEYS",
    "DISPLAY_OPTION_TOKENS",
    "EPHEMERAL_STATE_KEYS",
    "GEOMETRY_DISPLAY_OPTIONS",
    "STORED_STATE_KEYS",
    "VIEWPORT_DISPLAY_OPTIONS",
    "StateSlices",
    "derived_state",
    "display_option_delta",
    "display_option_set",
    "ephemeral_state",
    "geometry_state_fields",
    "has_display_option",
    "partition_state",
    "scene_geometry_cache_key",
    "select_display_options",
    "select_derived_state",
    "select_ephemeral_state",
    "select_stored_state",
    "select_viewport_signature",
    "show_axes",
    "show_hydrogen",
    "show_labels",
    "show_unit_cell",
    "stored_state",
    "transform_scene_cache_key",
    "viewport_signature",
]
