"""Sidebar presentation and browser acknowledgements for scene synchronization."""
from __future__ import annotations

import json
import threading

from dash import no_update

from .display_controls import visible_display_options
from ..properties import web_colormap_options


# Clocks that move when a server edit is committed. A poll may publish
# that edit. The same clocks staying put means the browser is ahead of
# the server (an in-flight control edit) and must not be overwritten.
_STATE_VERSION_KEYS = (
    "geometry_version",
    "display_version",
    "camera_version",
    "render_revision",
)

# Editable sidebar values, in the order ``sync_agent_state`` reads them.
# Indices match ``scene_control_outputs`` (the rename box is handled
# separately and is not in this list).
_POLL_CLIENT_CONTROL_INDEX = (
    1,  # display-mode-selector
    2,  # display-options
    3,  # atom-scale-slider
    4,  # bond-radius-slider
    5,  # minor-opacity-slider
    6,  # material-selector
    7,  # style-selector
    8,  # disorder-selector
    9,  # ortep-mode-selector
    10,  # axis-scale-slider
    11,  # topology-site-index
    12,  # topology-toggle
    14,  # property-field-selector value
    15,  # property-reduction-selector
    16,  # property-component-input
    18,  # property-colormap-input value
    19,  # property-range-mode
    20,  # property-range-min
    21,  # property-range-max
    22,  # property-center-input
    23,  # property-nan-color-input
    24,  # property-colorbar-toggle
)

# A single Dash request can carry the poll interval and a tab click.
# The tab is the user's scene intent; the poll must not win that batch.
_SYNC_TRIGGER_PRIORITY = (
    "scene-tabs",
    "native-upload-sync",
    "display-defaults-btn",
    "agent-state-poll",
)


def _scene_control_values(state, *, property_options=(), visible_range=None):
    """Present editable values consistently in the layout and its browser ack."""
    property_payload = state.get("atom_property_color") or {}
    property_range = property_payload.get("value_range")
    if visible_range is None:
        visible_range = property_range
    active_colormap = property_payload.get("colormap", "viridis")
    return (
        state.get("scene_label") or state["structure"],
        state["display_mode"],
        visible_display_options(state.get("display_mode"), state.get("display_options")),
        state["atom_scale"],
        state["bond_radius"],
        state["minor_opacity"],
        state.get("material", "mesh"),
        state.get("style", "ball_stick"),
        state.get("disorder", "opacity"),
        state.get("ortep_mode", "ortep_axes"),
        state["axis_scale"],
        state["topology_site_index"],
        ["enabled"] if state.get("topology_enabled", False) else [],
        property_options,
        list(property_payload.get("fields") or []),
        property_payload.get("reduction", "auto"),
        property_payload.get("component"),
        web_colormap_options(active_colormap),
        active_colormap,
        "manual" if property_range is not None else "auto",
        None if visible_range is None else visible_range[0],
        None if visible_range is None else visible_range[1],
        property_payload.get("center"),
        str(property_payload.get("nan_color", "#BDBDBD"))[:7],
        ["show"] if property_payload.get("show_colorbar", True) else [],
    )


def _scene_controls_stamp(state, values=None, *, visible_range=None):
    values = values if values is not None else _scene_control_values(
        state, visible_range=visible_range
    )
    payload = {
        "scene_id": str(state.get("scene_id") or ""),
        "scene_label": values[0],
        "camera_revision": int(state.get("camera_revision", 0) or 0),
        "controls": [values[index] for index in _POLL_CLIENT_CONTROL_INDEX],
    }
    for key in _STATE_VERSION_KEYS:
        payload[key] = int(state.get(key, 0) or 0)
    return json.dumps(payload, sort_keys=True)


def _parse_controls_stamp(stamp):
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        parsed = json.loads(stamp)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _preferred_sync_trigger(triggered) -> str | None:
    """Pick the sync branch when several inputs fire in one request."""
    names = []
    for item in triggered or []:
        prop_id = item.get("prop_id") if isinstance(item, dict) else None
        if not prop_id:
            continue
        names.append(str(prop_id).split(".", 1)[0])
    for name in _SYNC_TRIGGER_PRIORITY:
        if name in names:
            return name
    return names[0] if names else None


def _stamp_scene_or_camera_differs(stamp, state) -> bool:
    """True when the browser has not acknowledged this scene or camera command.

    Display and geometry clocks also live in the stamp so a poll can tell
    an external edit from an in-flight keystroke. Those clocks must not by
    themselves force a full control rewrite.
    """
    parsed = _parse_controls_stamp(stamp)
    if parsed is None:
        return stamp is not None and stamp != _scene_controls_stamp(state)
    scene_matches = str(parsed.get("scene_id") or "") == str(state.get("scene_id") or "")
    try:
        acknowledged = int(parsed.get("camera_revision", 0) or 0)
        current = int(state.get("camera_revision", 0) or 0)
    except (TypeError, ValueError):
        return True
    return not (scene_matches and acknowledged == current)


def _stamp_scene_matches(stamp, scene_id) -> bool:
    parsed = _parse_controls_stamp(stamp)
    if parsed is None or "scene_id" not in parsed:
        return False
    return str(parsed.get("scene_id") or "") == str(scene_id or "")


def _stamp_label_matches(stamp, state) -> bool:
    parsed = _parse_controls_stamp(stamp)
    return parsed is not None and parsed.get("scene_label") == (
        state.get("scene_label") or state.get("structure")
    )


def _acknowledged_versions_cover(stamp, state) -> bool:
    """True when the browser ack is not older than ``state``.

    A legacy stamp that only names the scene and camera has no version
    clocks. Treat that as "write the controls" so an external edit still
    lands. Once the stamp carries clocks, an equal-or-newer ack means the
    server has not moved and differing controls are in-flight edits.
    """
    parsed = _parse_controls_stamp(stamp)
    if parsed is None or not any(key in parsed for key in _STATE_VERSION_KEYS):
        return False
    try:
        return not any(
            int(state.get(key, 0) or 0) > int(parsed.get(key, 0) or 0)
            for key in _STATE_VERSION_KEYS
        )
    except (TypeError, ValueError):
        return False


def _control_values_match(client, server) -> bool:
    if isinstance(client, (list, tuple)) or isinstance(server, (list, tuple)):
        return list(client or []) == list(server or [])
    return client == server


# Sidebar values Dash appends before the interaction fence. A direct test
# call stops at the sidebar; the browser also sends the epoch and client id.
_POLL_FENCE_EXTRA = 2

# Per browser, not per process. Two pages can sit on different scenes;
# one page's tab switch must not silence the other's poll.
_fence_lock = threading.Lock()
_browser_scenes: dict[str, str] = {}
_browser_epochs: dict[str, int] = {}


def split_poll_fence(client_controls):
    """Split ``(sidebar..., epoch, client_id)`` from a historical sidebar tuple.

    The fence is present only when every sidebar state was sent and the two
    trailing states follow it. Shorter direct calls keep the previous contract.
    """
    controls = tuple(client_controls or ())
    if len(controls) != len(_POLL_CLIENT_CONTROL_INDEX) + _POLL_FENCE_EXTRA:
        return controls, None, None, False
    *sidebar, epoch, client_id = controls
    return tuple(sidebar), epoch, client_id, True


def note_interaction_epoch(client_id, epoch) -> None:
    """Remember the newest interaction generation for one browser."""
    if not client_id or epoch is None:
        return
    try:
        observed = int(epoch)
    except (TypeError, ValueError):
        return
    with _fence_lock:
        key = str(client_id)
        if observed > _browser_epochs.get(key, 0):
            _browser_epochs[key] = observed


def claim_browser_scene(client_id, scene_id, filled: dict, epoch=None) -> None:
    """Record the scene a browser just selected and point capture at it.

    The scene note and the filled-scene guard move together. A poll that
    still names the previous scene cannot publish between those two writes.
    """
    delivered = str(scene_id or "")
    with _fence_lock:
        if client_id and delivered:
            _browser_scenes[str(client_id)] = delivered
        if epoch is not None and client_id:
            try:
                observed = int(epoch)
            except (TypeError, ValueError):
                observed = None
            if (
                observed is not None
                and observed > _browser_epochs.get(str(client_id), 0)
            ):
                _browser_epochs[str(client_id)] = observed
        filled["id"] = delivered


def poll_request_is_stale(client_id, scene_id, epoch, *, supplied: bool) -> bool:
    """Read-only check used before a poll consumes a queued snapshot."""
    if not supplied or not client_id:
        return False
    with _fence_lock:
        return _poll_is_stale_locked(str(client_id), scene_id, epoch)


def claim_poll_publish(
    client_id, scene_id, epoch, filled: dict, *, supplied: bool
) -> bool:
    """Publish a poll only for the scene and generation that sent it.

    Unfenced callers (direct tests and the first paint) keep the historical
    write. A fenced poll whose browser has since switched scenes or edited
    returns without moving the filled-scene guard.
    """
    delivered = str(scene_id or "")
    with _fence_lock:
        if supplied and client_id:
            key = str(client_id)
            if _poll_is_stale_locked(key, scene_id, epoch):
                return False
            try:
                _browser_epochs[key] = int(0 if epoch is None else epoch)
            except (TypeError, ValueError):
                return False
        filled["id"] = delivered
        return True


def _poll_is_stale_locked(client_id: str, scene_id, epoch) -> bool:
    recorded_scene = _browser_scenes.get(client_id)
    if recorded_scene is not None and recorded_scene != str(scene_id or ""):
        return True
    try:
        observed = int(0 if epoch is None else epoch)
    except (TypeError, ValueError):
        return True
    return observed < _browser_epochs.get(client_id, 0)


def _preserve_inflight_controls(outputs, client_controls, stamp, state) -> None:
    parsed = _parse_controls_stamp(stamp) or {}
    acknowledged = parsed.get("controls")
    covered = _acknowledged_versions_cover(stamp, state)
    for offset, (index, client) in enumerate(zip(_POLL_CLIENT_CONTROL_INDEX, client_controls)):
        if index >= len(outputs):
            break
        unchanged = covered or (
            isinstance(acknowledged, list)
            and offset < len(acknowledged)
            and _control_values_match(acknowledged[offset], outputs[index])
        )
        if unchanged and not _control_values_match(client, outputs[index]):
            outputs[index] = no_update


