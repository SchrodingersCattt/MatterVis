"""Pure scene-state operations and invalidation facts.

The Dash application historically passed partially-normalized dictionaries
between callbacks and :meth:`ViewerBackend.patch_state`.  This module gives
those writes a small, typed vocabulary without changing the wire format.  An
``Operation`` contains only caller intent; :func:`reduce_state` copies a state
snapshot, applies that intent, and returns invalidation facts for downstream
cache and render layers.  It never imports Dash, Plotly, or ``ViewerBackend``.

The reducer intentionally does not perform the existing, compatibility-heavy
normalization (legacy aliases, colour coercion, transform IDs, and so on).
That remains at the API boundary for this migration step.  The resulting
operation payload can therefore be passed to ``patch_state`` unchanged while
callers begin relying on deterministic operation names and invalidations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import copy
from collections.abc import Mapping
from typing import Any


class OperationKind(str, Enum):
    """Stable names for state transitions.

    The values are part of the internal protocol: they are safe to log and
    are accepted by :func:`operation_from_intent` as well as the REST intent
    endpoint's existing strings.
    """

    LOAD_STRUCTURE = "load_structure"
    SWITCH_SCENE = "switch_scene"
    RENAME_SCENE = "rename_scene"
    SET_DISPLAY_MODE = "set_display_mode"
    SET_DISPLAY_OPTIONS = "set_display_options"
    SET_RENDER_STYLE = "set_render_style"
    SET_NUMERIC_STYLE = "set_numeric_style"
    SET_CAMERA = "set_camera"
    RESET_CAMERA = "reset_camera"
    SET_PROJECTION = "set_projection"
    SET_TRANSFORMS = "set_transforms"
    SET_ATOM_GROUPS = "set_atom_groups"
    SET_BOND_GROUPS = "set_bond_groups"
    SET_POLYHEDRON_SPECS = "set_polyhedron_specs"
    SET_POLYHEDRON_INSTANCE_OVERRIDE = "set_polyhedron_instance_override"
    SELECT_TOPOLOGY_SITE = "select_topology_site"
    PATCH_STATE = "patch_state"


class Invalidation(str, Enum):
    """Facts emitted by a reducer branch.

    Consumers decide how to clear or reuse caches.  Reducer code never reaches
    into a cache, which keeps state transitions testable and side-effect free.
    """

    SCENE_GEOMETRY = "scene_geometry"
    TRANSFORM_GEOMETRY = "transform_geometry"
    TOPOLOGY_GEOMETRY = "topology_geometry"
    FIGURE_BODY = "figure_body"
    CAMERA_LAYOUT = "camera_layout"
    SIDE_PANEL = "side_panel"
    SCENE_TABS = "scene_tabs"


Invalidations = frozenset[Invalidation]


@dataclass(frozen=True)
class Operation:
    """A serializable operation envelope.

    ``payload`` remains a mapping to preserve compatibility with existing
    ``/api/v2/intent`` callers.  Named factory helpers below provide a typed
    vocabulary while allowing callers to use ``Operation`` directly when a
    future operation is not yet represented by a helper.
    """

    kind: OperationKind | str
    payload: Mapping[str, Any] = field(default_factory=dict)
    scene_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.payload, Mapping):
            raise TypeError("operation payload must be a mapping")
        object.__setattr__(self, "kind", _coerce_kind(self.kind))
        object.__setattr__(self, "payload", copy.deepcopy(dict(self.payload)))
        if self.scene_id is not None:
            object.__setattr__(self, "scene_id", str(self.scene_id))

    @property
    def name(self) -> str:
        return str(self.kind.value if isinstance(self.kind, OperationKind) else self.kind)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.name,
            "payload": copy.deepcopy(dict(self.payload)),
            "scene_id": self.scene_id,
        }


def _coerce_kind(value: OperationKind | str) -> OperationKind | str:
    if isinstance(value, OperationKind):
        return value
    text = str(value or "").strip().lower().replace("-", "_")
    try:
        return OperationKind(text)
    except ValueError:
        return text


def _operation(kind: OperationKind, payload: Mapping[str, Any] | None = None, *, scene_id: str | None = None) -> Operation:
    return Operation(kind, payload or {}, scene_id=scene_id)


# Typed operation constructors.  Returning the immutable envelope keeps these
# functions pleasant to use from callbacks while avoiding a large hierarchy of
# tiny dataclasses and retaining JSON compatibility.
def LoadStructure(structure: str, *, scene_id: str | None = None, defaults: Mapping[str, Any] | None = None) -> Operation:
    payload = {"structure": str(structure)}
    if defaults:
        payload["defaults"] = copy.deepcopy(dict(defaults))
    return _operation(OperationKind.LOAD_STRUCTURE, payload, scene_id=scene_id)


def SwitchScene(scene_id: str) -> Operation:
    return _operation(OperationKind.SWITCH_SCENE, {"scene_id": str(scene_id)}, scene_id=str(scene_id))


def RenameScene(label: str, *, scene_id: str | None = None) -> Operation:
    return _operation(OperationKind.RENAME_SCENE, {"label": str(label)}, scene_id=scene_id)


def SetDisplayMode(mode: str, *, scene_id: str | None = None, reset_topology_site: bool = True) -> Operation:
    return _operation(
        OperationKind.SET_DISPLAY_MODE,
        {"display_mode": str(mode), "reset_topology_site": bool(reset_topology_site)},
        scene_id=scene_id,
    )


def SetDisplayOptions(options: Any, *, scene_id: str | None = None) -> Operation:
    return _operation(OperationKind.SET_DISPLAY_OPTIONS, {"display_options": copy.deepcopy(options)}, scene_id=scene_id)


def SetRenderStyle(style: Mapping[str, Any] | None = None, *, scene_id: str | None = None, **values: Any) -> Operation:
    payload = dict(style or {})
    payload.update(values)
    return _operation(OperationKind.SET_RENDER_STYLE, payload, scene_id=scene_id)


def SetNumericStyle(values: Mapping[str, Any] | None = None, *, scene_id: str | None = None, **kwargs: Any) -> Operation:
    payload = dict(values or {})
    payload.update(kwargs)
    return _operation(OperationKind.SET_NUMERIC_STYLE, payload, scene_id=scene_id)


def SetCamera(camera: Mapping[str, Any], *, scene_id: str | None = None, camera_revision: int | None = None) -> Operation:
    payload: dict[str, Any] = {"camera": copy.deepcopy(dict(camera))}
    if camera_revision is not None:
        payload["camera_revision"] = int(camera_revision)
    return _operation(OperationKind.SET_CAMERA, payload, scene_id=scene_id)


def ResetCamera(*, scene_id: str | None = None) -> Operation:
    return _operation(OperationKind.RESET_CAMERA, {}, scene_id=scene_id)


def SetProjection(projection: str, *, scene_id: str | None = None) -> Operation:
    return _operation(OperationKind.SET_PROJECTION, {"projection": str(projection)}, scene_id=scene_id)


def SetTransforms(transforms: Any, *, scene_id: str | None = None) -> Operation:
    return _operation(OperationKind.SET_TRANSFORMS, {"transforms": copy.deepcopy(transforms)}, scene_id=scene_id)


def SetAtomGroups(groups: Any, *, scene_id: str | None = None) -> Operation:
    return _operation(OperationKind.SET_ATOM_GROUPS, {"atom_groups": copy.deepcopy(groups)}, scene_id=scene_id)


def SetBondGroups(groups: Any, *, scene_id: str | None = None) -> Operation:
    return _operation(OperationKind.SET_BOND_GROUPS, {"bond_groups": copy.deepcopy(groups)}, scene_id=scene_id)


def SetPolyhedronSpecs(specs: Any, *, scene_id: str | None = None) -> Operation:
    return _operation(OperationKind.SET_POLYHEDRON_SPECS, {"polyhedron_specs": copy.deepcopy(specs)}, scene_id=scene_id)


def SetPolyhedronInstanceOverride(spec_id: str, fragment_label: str, override: Mapping[str, Any], *, scene_id: str | None = None) -> Operation:
    return _operation(
        OperationKind.SET_POLYHEDRON_INSTANCE_OVERRIDE,
        {"spec_id": str(spec_id), "fragment_label": str(fragment_label), "override": copy.deepcopy(dict(override))},
        scene_id=scene_id,
    )


def SelectTopologySite(site_index: int | None, *, scene_id: str | None = None) -> Operation:
    return _operation(OperationKind.SELECT_TOPOLOGY_SITE, {"topology_site_index": site_index}, scene_id=scene_id)


def PatchState(patch: Mapping[str, Any], *, scene_id: str | None = None) -> Operation:
    return _operation(OperationKind.PATCH_STATE, dict(patch), scene_id=scene_id)


def operation_from_intent(payload: Mapping[str, Any]) -> Operation:
    """Translate the existing intent wire shape to an operation envelope."""

    if not isinstance(payload, Mapping):
        raise TypeError("intent payload must be a mapping")
    intent_type = str(payload.get("type") or "").strip().lower()
    data = payload.get("payload") or {}
    if not isinstance(data, Mapping):
        raise TypeError("intent payload.payload must be a mapping")
    scene_id = payload.get("scene_id") or data.get("scene_id")

    if intent_type == "set_style":
        return SetRenderStyle(data, scene_id=scene_id)
    if intent_type == "set_display_options":
        return SetDisplayOptions(data.get("display_options", data), scene_id=scene_id)
    if intent_type == "set_camera":
        camera = data.get("camera", data)
        return SetCamera(camera, scene_id=scene_id, camera_revision=data.get("camera_revision"))
    if intent_type == "apply_transform":
        # Existing clients send either a complete list or one transform.  A
        # complete list can be reduced directly; a single transform is kept as
        # an explicit patch for the compatibility layer to append/normalize.
        if "transforms" in data:
            return SetTransforms(data["transforms"], scene_id=scene_id)
        return PatchState(data, scene_id=scene_id)
    if intent_type == "crud_atom_group":
        return SetAtomGroups(data.get("atom_groups", data.get("items", [])), scene_id=scene_id)
    if intent_type == "crud_bond_group":
        return SetBondGroups(data.get("bond_groups", data.get("items", [])), scene_id=scene_id)
    if intent_type == "crud_polyhedron":
        return SetPolyhedronSpecs(data.get("polyhedron_specs", data.get("items", [])), scene_id=scene_id)
    if intent_type == "set_active_scene":
        target = data.get("scene_id") or scene_id
        if not target:
            raise ValueError("set_active_scene requires scene_id")
        return SwitchScene(str(target))
    if intent_type == "upload_complete":
        return Operation("upload_complete", data, scene_id=scene_id)
    if intent_type in {"patch_state", "set_state"}:
        return PatchState(data, scene_id=scene_id)
    # CRUD scene operations are handled by SceneStore, but still get a stable
    # operation envelope so callers can log/order them consistently.
    if intent_type == "crud_scene":
        return Operation("crud_scene", data, scene_id=scene_id)
    raise ValueError(f"unknown intent type: {intent_type}")


def operation_patch(operation: Operation) -> dict[str, Any]:
    """Return the legacy ``patch_state`` payload for an operation."""

    payload = copy.deepcopy(dict(operation.payload))
    kind = operation.name
    if kind == OperationKind.SET_DISPLAY_MODE.value:
        payload.pop("reset_topology_site", None)
    elif kind == OperationKind.SET_CAMERA.value:
        # No change needed; the old endpoint accepts this exact shape.
        pass
    elif kind == OperationKind.SET_PROJECTION.value:
        pass
    elif kind == OperationKind.RESET_CAMERA.value:
        # The compatibility backend owns the actual default camera calculation.
        return {}
    elif kind in {OperationKind.LOAD_STRUCTURE.value, OperationKind.SWITCH_SCENE.value, OperationKind.RENAME_SCENE.value}:
        # These are SceneStore operations and are not state patches.
        return payload
    return payload


def _set_display_options(state: dict[str, Any], value: Any) -> None:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = []
    seen: set[str] = set()
    state["display_options"] = [
        text for item in values if (text := str(item).strip()) and not (text in seen or seen.add(text))
    ]


def _apply_polyhedron_override(state: dict[str, Any], payload: Mapping[str, Any]) -> None:
    specs = copy.deepcopy(list(state.get("polyhedron_specs") or []))
    target_id = str(payload.get("spec_id") or "")
    fragment_label = str(payload.get("fragment_label") or "")
    override = payload.get("override")
    if not target_id or not fragment_label or not isinstance(override, Mapping):
        return
    for spec in specs:
        if isinstance(spec, dict) and str(spec.get("id") or "") == target_id:
            overrides = spec.setdefault("instance_overrides", {})
            if isinstance(overrides, dict):
                overrides[fragment_label] = copy.deepcopy(dict(override))
            break
    state["polyhedron_specs"] = specs


def _changed(before: Mapping[str, Any], after: Mapping[str, Any], *keys: str) -> bool:
    return any(before.get(key) != after.get(key) for key in keys)


def _invalidation_set(kind: str, before: Mapping[str, Any], after: Mapping[str, Any]) -> Invalidations:
    """Compute conservative invalidations for a reducer transition."""

    if kind == OperationKind.LOAD_STRUCTURE.value:
        return frozenset(
            {
                Invalidation.SCENE_GEOMETRY,
                Invalidation.TOPOLOGY_GEOMETRY,
                Invalidation.FIGURE_BODY,
                Invalidation.SIDE_PANEL,
                Invalidation.CAMERA_LAYOUT,
            }
        )
    if kind == OperationKind.SWITCH_SCENE.value:
        return frozenset(
            {
                Invalidation.FIGURE_BODY,
                Invalidation.SIDE_PANEL,
                Invalidation.CAMERA_LAYOUT,
            }
        )
    if kind == OperationKind.RENAME_SCENE.value:
        return frozenset({Invalidation.SCENE_TABS})
    if kind == "crud_scene":
        return frozenset({Invalidation.SCENE_TABS})
    if kind == "upload_complete":
        return frozenset(
            {
                Invalidation.SCENE_GEOMETRY,
                Invalidation.TOPOLOGY_GEOMETRY,
                Invalidation.FIGURE_BODY,
                Invalidation.SIDE_PANEL,
                Invalidation.CAMERA_LAYOUT,
            }
        )
    if kind == OperationKind.SET_DISPLAY_MODE.value:
        return frozenset({Invalidation.SCENE_GEOMETRY, Invalidation.TOPOLOGY_GEOMETRY, Invalidation.FIGURE_BODY, Invalidation.SIDE_PANEL, Invalidation.CAMERA_LAYOUT})
    if kind == OperationKind.SET_DISPLAY_OPTIONS.value:
        changed = set(before.get("display_options") or []) ^ set(after.get("display_options") or [])
        if "hydrogens" in changed:
            return frozenset({Invalidation.SCENE_GEOMETRY, Invalidation.TOPOLOGY_GEOMETRY, Invalidation.FIGURE_BODY, Invalidation.SIDE_PANEL, Invalidation.CAMERA_LAYOUT})
        if "minor_only" in changed:
            return frozenset({Invalidation.FIGURE_BODY, Invalidation.CAMERA_LAYOUT})
        if "unit_cell_box" in changed:
            return frozenset({Invalidation.FIGURE_BODY, Invalidation.CAMERA_LAYOUT})
        return frozenset({Invalidation.FIGURE_BODY}) if changed else frozenset()
    if kind == OperationKind.SET_TRANSFORMS.value:
        return frozenset({Invalidation.TRANSFORM_GEOMETRY, Invalidation.TOPOLOGY_GEOMETRY, Invalidation.FIGURE_BODY, Invalidation.SIDE_PANEL, Invalidation.CAMERA_LAYOUT})
    if kind == OperationKind.SET_NUMERIC_STYLE.value:
        keys = set(dict(after)) - set(dict(before))
        changed_keys = {key for key in keys if before.get(key) != after.get(key)} | {
            key for key in dict(before) if before.get(key) != after.get(key)
        }
        if "cutoff" in changed_keys:
            return frozenset({Invalidation.TOPOLOGY_GEOMETRY, Invalidation.FIGURE_BODY, Invalidation.SIDE_PANEL})
        return frozenset({Invalidation.FIGURE_BODY})
    if kind in {OperationKind.SET_CAMERA.value, OperationKind.RESET_CAMERA.value, OperationKind.SET_PROJECTION.value}:
        return frozenset({Invalidation.CAMERA_LAYOUT})
    if kind == OperationKind.SET_POLYHEDRON_SPECS.value:
        # Geometry and paint are intentionally conservative in this migration;
        # the cache-key phase can split colour-only edits later.
        return frozenset({Invalidation.TOPOLOGY_GEOMETRY, Invalidation.FIGURE_BODY, Invalidation.SIDE_PANEL})
    if kind == OperationKind.SET_POLYHEDRON_INSTANCE_OVERRIDE.value:
        return frozenset({Invalidation.FIGURE_BODY})
    if kind == OperationKind.SELECT_TOPOLOGY_SITE.value:
        return frozenset({Invalidation.TOPOLOGY_GEOMETRY, Invalidation.FIGURE_BODY, Invalidation.SIDE_PANEL})
    if kind == OperationKind.SET_ATOM_GROUPS.value or kind == OperationKind.SET_BOND_GROUPS.value:
        return frozenset({Invalidation.FIGURE_BODY})
    # Generic patches use field ownership as a bridge while legacy endpoints
    # continue accepting arbitrary state dictionaries.
    changed_keys = {key for key in set(before) | set(after) if before.get(key) != after.get(key)}
    invalidations: set[Invalidation] = set()
    if changed_keys & {"structure", "display_mode", "transforms", "disorder", "disorder_resolve", "disorder_replicas", "cutoff"}:
        invalidations.update({Invalidation.SCENE_GEOMETRY, Invalidation.TOPOLOGY_GEOMETRY, Invalidation.FIGURE_BODY, Invalidation.SIDE_PANEL, Invalidation.CAMERA_LAYOUT})
    if changed_keys & {"atom_groups", "bond_groups", "material", "style", "minor_opacity", "atom_scale", "bond_radius", "axis_scale", "display_options", "polyhedron_specs", "overlay_overrides"}:
        invalidations.add(Invalidation.FIGURE_BODY)
    if changed_keys & {"camera", "camera_revision", "projection"}:
        invalidations.add(Invalidation.CAMERA_LAYOUT)
    return frozenset(invalidations)


def reduce_state(state: Mapping[str, Any], operation: Operation | Mapping[str, Any]) -> tuple[dict[str, Any], Invalidations]:
    """Apply one operation to a state snapshot without side effects."""

    if not isinstance(state, Mapping):
        raise TypeError("state must be a mapping")
    if not isinstance(operation, Operation):
        if isinstance(operation, Mapping):
            operation = Operation(operation.get("kind", operation.get("type", "patch_state")), operation.get("payload", operation))
        else:
            raise TypeError("operation must be an Operation or mapping")
    before = copy.deepcopy(dict(state))
    after = copy.deepcopy(dict(state))
    payload = dict(operation.payload)
    kind = operation.name

    if kind == OperationKind.LOAD_STRUCTURE.value:
        after["structure"] = str(payload.get("structure") or after.get("structure") or "")
        defaults = payload.get("defaults")
        if isinstance(defaults, Mapping):
            after.update(copy.deepcopy(dict(defaults)))
        after["structure"] = str(payload.get("structure") or after.get("structure") or "")
    elif kind == OperationKind.SWITCH_SCENE.value:
        if payload.get("scene_id") is not None:
            after["scene_id"] = str(payload["scene_id"])
    elif kind == OperationKind.RENAME_SCENE.value:
        after["scene_label"] = str(payload.get("label") or after.get("scene_label") or "")
    elif kind == OperationKind.SET_DISPLAY_MODE.value:
        if payload.get("display_mode") is not None:
            after["display_mode"] = str(payload["display_mode"])
        if payload.get("reset_topology_site", True):
            after["topology_site_index"] = None
    elif kind == OperationKind.SET_DISPLAY_OPTIONS.value:
        _set_display_options(after, payload.get("display_options"))
    elif kind in {OperationKind.SET_RENDER_STYLE.value, OperationKind.SET_NUMERIC_STYLE.value}:
        allowed = {
            "material", "style", "disorder", "ortep_mode", "label_mode",
            "atom_scale", "bond_radius", "minor_opacity", "axis_scale", "cutoff", "fast_rendering",
        }
        for key, value in payload.items():
            if key in allowed:
                after[key] = copy.deepcopy(value)
    elif kind == OperationKind.SET_CAMERA.value:
        if isinstance(payload.get("camera"), Mapping):
            after["camera"] = copy.deepcopy(dict(payload["camera"]))
        if payload.get("camera_revision") is not None:
            after["camera_revision"] = int(payload["camera_revision"])
    elif kind == OperationKind.RESET_CAMERA.value:
        after["camera"] = None
        after["camera_revision"] = int(after.get("camera_revision", 0) or 0) + 1
    elif kind == OperationKind.SET_PROJECTION.value:
        projection = str(payload.get("projection") or "perspective")
        after["projection"] = projection
        camera = copy.deepcopy(after.get("camera")) if isinstance(after.get("camera"), Mapping) else {}
        camera["projection"] = {"type": projection}
        after["camera"] = camera
    elif kind == OperationKind.SET_TRANSFORMS.value:
        after["transforms"] = copy.deepcopy(payload.get("transforms") or [])
    elif kind == OperationKind.SET_ATOM_GROUPS.value:
        after["atom_groups"] = copy.deepcopy(payload.get("atom_groups") or [])
    elif kind == OperationKind.SET_BOND_GROUPS.value:
        after["bond_groups"] = copy.deepcopy(payload.get("bond_groups") or [])
    elif kind == OperationKind.SET_POLYHEDRON_SPECS.value:
        after["polyhedron_specs"] = copy.deepcopy(payload.get("polyhedron_specs") or [])
    elif kind == OperationKind.SET_POLYHEDRON_INSTANCE_OVERRIDE.value:
        _apply_polyhedron_override(after, payload)
    elif kind == OperationKind.SELECT_TOPOLOGY_SITE.value:
        value = payload.get("topology_site_index")
        after["topology_site_index"] = None if value in (None, "") else int(value)
    elif kind == OperationKind.PATCH_STATE.value:
        after.update(copy.deepcopy(payload))
    # Unknown operations are intentionally read-only.  They can still be
    # carried through apply_intent for compatibility, but cannot mutate state.

    return after, _invalidation_set(kind, before, after)


__all__ = [
    "Invalidation", "Invalidations", "Operation", "OperationKind", "PatchState",
    "LoadStructure", "SwitchScene", "RenameScene", "SetDisplayMode",
    "SetDisplayOptions", "SetRenderStyle", "SetNumericStyle", "SetCamera",
    "ResetCamera", "SetProjection", "SetTransforms", "SetAtomGroups",
    "SetBondGroups", "SetPolyhedronSpecs", "SetPolyhedronInstanceOverride",
    "SelectTopologySite", "operation_from_intent", "operation_patch", "reduce_state",
]
