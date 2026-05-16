from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .app_shared import *
from .app_camera_helpers import _camera_from_store, _coerce_projection, _plotly_camera

def _json_safe(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _fast_view_metadata(backend: "ViewerBackend", state: dict[str, Any], camera_state: Optional[dict[str, Any]] = None) -> str:
    """Small JSON blob consumed by assets/near_zero_latency.js.

    It intentionally contains only cheap, camera/style-relevant fields so
    high-frequency view controls can update Plotly locally without waiting for
    the heavy Dash figure callback.
    """
    state = backend.normalize_state(state or backend.get_state())
    scene_id = state.get("scene_id")
    scene = backend.scene_for_state(state)
    camera = _camera_from_store(camera_state, scene_id) or state.get("camera") or scene.get("camera")
    payload = {
        "scene_id": scene_id,
        "M": _json_safe(scene.get("M")),
        "camera": _json_safe(_plotly_camera(camera) or backend.default_camera(state)),
        "default_camera": _json_safe(backend.default_camera(state)),
        "projection": _coerce_projection(state.get("projection", "perspective")),
        "camera_revision": int(state.get("camera_revision", 0) or 0),
        "display_options": list(state.get("display_options") or []),
        "axis_scale": float(state.get("axis_scale", 1.0) or 1.0),
        "minor_opacity": float(state.get("minor_opacity", 0.35) or 0.35),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _fast_style_patch_for_figure(
    figure: Optional[dict[str, Any]],
    *,
    display_options: Iterable[str] | None,
    minor_opacity: float | None = None,
) -> Patch | Any:
    """Patch trace visibility/opacity for style-only controls.

    The renderer stamps role metadata onto traces. This helper only flips
    those lightweight fields and never rebuilds Mesh3d coordinates.
    """
    if not isinstance(figure, dict):
        return no_update
    options = set(display_options or [])
    show_labels = "labels" in options
    show_axes = "axes" in options
    show_unit_cell = "unit_cell_box" in options
    minor_only = "minor_only" in options
    patch = Patch()
    changed = False
    try:
        minor_alpha = max(0.05, float(minor_opacity)) if minor_opacity is not None else None
    except (TypeError, ValueError):
        minor_alpha = None

    for idx, trace in enumerate(figure.get("data") or []):
        if not isinstance(trace, dict):
            continue
        meta = trace.get("meta") if isinstance(trace.get("meta"), dict) else {}
        role = meta.get("mv_role") or trace.get("name")
        is_minor = bool(meta.get("mv_minor", False))
        hide_on_minor_only = bool(meta.get("mv_hide_on_minor_only", False))
        visible: bool | None = None
        if role in {"labels", "atom-label", "atom-label-major", "atom-label-minor"}:
            visible = show_labels and (not minor_only or is_minor)
        elif role in {"axes", "axes-shafts", "axes-labels"}:
            visible = show_axes
        elif role in {"unit_cell", "unit-cell", "unit-cell-box"}:
            visible = show_unit_cell
        elif role in {"atom", "bond", "atom_selection", "bond_selection"} and minor_only and not is_minor:
            visible = False
        elif role in {"atom", "bond", "atom_selection", "bond_selection"} and not minor_only:
            visible = True
        elif hide_on_minor_only and minor_only:
            visible = False
        elif hide_on_minor_only:
            visible = True
        if visible is not None:
            patch["data"][idx]["visible"] = visible
            changed = True
        if minor_alpha is not None and is_minor and role in {"atom", "bond", "minor_overlay", "minor-outline", "minor-bond"}:
            if trace.get("type") == "scatter3d":
                patch["data"][idx]["marker"]["opacity"] = minor_alpha
            else:
                patch["data"][idx]["opacity"] = minor_alpha
            changed = True
    return patch if changed else no_update


def _display_options_can_fast_patch(prev_options: Iterable[str] | None, next_options: Iterable[str] | None) -> bool:
    """Only cosmetic label/axis toggles are safe for trace-only patching."""
    changed = set(prev_options or []) ^ set(next_options or [])
    return changed.issubset({"labels", "axes"})


def _minor_opacity_disabled(disorder: Optional[str]) -> bool:
    return disorder != "opacity"


def _minor_opacity_control_style(disorder: Optional[str]) -> dict[str, Any]:
    style: dict[str, Any] = {"transition": "opacity 120ms ease"}
    if _minor_opacity_disabled(disorder):
        style["opacity"] = 0.4
    return style


def _polyhedra_controls_style(enabled: bool) -> dict[str, Any]:
    return {} if enabled else {"display": "none"}


def _status_class(level: str = "info") -> str:
    return f"status-banner status-banner--{level}"


# Colour-blind-friendly cycling palette for auto-assigned polyhedron specs.
# Built off Okabe-Ito with one extra warm purple so 8-spec scenes still
# read distinctly. Callers can always override per-spec; this just gives
# them a sane default when they POST {"name": ...} without a colour.
_POLYHEDRON_AUTO_COLORS = (
    "#7C5CBF",
    "#E07C24",
    "#1F77B4",
    "#2CA02C",
    "#D62728",
    "#9467BD",
    "#17BECF",
    "#BCBD22",
)

_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

__all__ = [name for name in globals() if not name.startswith("__")]
