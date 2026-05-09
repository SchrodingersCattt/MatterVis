from __future__ import annotations

import argparse
import copy
import io
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Any, Dict, Iterable, Optional

import numpy as np
import plotly.io as pio

try:
    from dash import ALL, Dash, Input, Output, State, callback_context, dcc, html, no_update
except ImportError as exc:  # pragma: no cover - user-facing fallback
    raise SystemExit(
        "Dash is required for the browser viewer. "
        "Install it with `python -m pip install dash`."
    ) from exc

from .api import register_api
from .loader import LoadedCrystal, build_bundle_scene, build_empty_bundle, build_loaded_crystal, load_uploaded_cif
from .presets import (
    DEFAULT_CATALOG,
    DEFAULT_STYLE,
    LOCAL_STATE_DIRNAME,
    default_preset,
    default_preset_path,
    get_default_catalog,
    load_preset,
    save_preset,
    workspace_root,
)
from .renderer import build_figure, style_from_controls, topology_histogram_figure, topology_results_markdown
from .scene import scene_json
from .scenes import SceneStore
from .topology import analyze_topology, extract_coordination_shell


PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = workspace_root(PACKAGE_DIR)
DEFAULT_PRESET_PATH = default_preset_path(WORKSPACE_DIR)
LEGACY_EXPORT_MODULE = "crystal_viewer.legacy.plot_crystal"
PLACEHOLDER_STRUCTURE = "__upload__"


def _camera_store_payload(scene_id: Optional[str], camera: Optional[dict[str, Any]]) -> dict[str, Any]:
    return {"scene_id": scene_id, "camera": copy.deepcopy(camera)}


def _camera_from_store(camera_state: Optional[dict[str, Any]], scene_id: Optional[str]) -> Optional[dict[str, Any]]:
    if not isinstance(camera_state, dict):
        return None
    if "camera" in camera_state:
        if camera_state.get("scene_id") != scene_id:
            return None
        camera = camera_state.get("camera")
        return copy.deepcopy(camera) if isinstance(camera, dict) else None
    # Backward-compatible with the old store shape, but only when the
    # selected scene id is unknown. Otherwise an old active-tab camera could
    # leak into the newly selected scene.
    if scene_id is None and "eye" in camera_state:
        return copy.deepcopy(camera_state)
    return None


def _minor_opacity_disabled(disorder: Optional[str]) -> bool:
    return disorder != "opacity"


def _minor_opacity_control_style(disorder: Optional[str]) -> dict[str, Any]:
    style: dict[str, Any] = {"transition": "opacity 120ms ease"}
    if _minor_opacity_disabled(disorder):
        style["opacity"] = 0.4
    return style


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


def _coerce_hex_color(value: Any, fallback: str) -> str:
    """Reject anything that isn't ``#rrggbb`` so a malformed payload from a
    careless caller can't sneak ``red`` or ``rgba(...)`` into the data
    model and crash kaleido later. Always returns a six-digit lowercase
    hex string; ``fallback`` is used unchanged when ``value`` is bad."""
    if isinstance(value, str):
        text = value.strip()
        if _HEX_COLOR_RE.match(text):
            return text.lower()
    return fallback


def _coerce_species_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_polyhedron_spec(
    raw: Any,
    *,
    fallback_color: str,
    existing_ids: set[str],
) -> Optional[dict[str, Any]]:
    """Turn one POST/PATCH payload entry into the canonical spec shape.

    Returns ``None`` when the entry can't be salvaged (no centre species,
    not a dict, ...). Mutates ``existing_ids`` so callers building a list
    in one pass get unique ids without re-scanning the whole list.
    """
    if not isinstance(raw, dict):
        return None
    center = _coerce_species_value(raw.get("center_species"))
    if center is None:
        return None
    spec_id = str(raw.get("id") or "").strip()
    if not spec_id or spec_id in existing_ids:
        spec_id = f"poly_{uuid.uuid4().hex[:10]}"
        while spec_id in existing_ids:  # pragma: no cover - astronomically unlikely
            spec_id = f"poly_{uuid.uuid4().hex[:10]}"
    existing_ids.add(spec_id)
    name = str(raw.get("name") or center).strip() or center
    ligand = _coerce_species_value(raw.get("ligand_species"))
    color = _coerce_hex_color(raw.get("color"), fallback_color)
    enabled = bool(raw.get("enabled", True))
    return {
        "id": spec_id,
        "name": name,
        "center_species": center,
        # ``None`` keeps the legacy auto-derived ligand behaviour
        # (``_neighbor_types`` / perovskite XYn). Explicit string locks
        # the spec to that ligand species formula.
        "ligand_species": ligand,
        "color": color,
        "enabled": enabled,
    }


# Selector keys we accept on atom-group rules. Anything outside this
# set is silently dropped so a forward-compatible UI can post extra
# experimental fields without breaking the persisted state schema.
_ATOM_SELECTOR_KEYS = ("all", "elements", "is_minor")
_ATOM_GROUP_VALID_MATERIALS = {"mesh", "flat"}
_ATOM_GROUP_VALID_STYLES = {"ball", "ball_stick", "stick", "ortep", "wireframe"}


def _coerce_atom_selector(raw: Any) -> Optional[dict[str, Any]]:
    """Validate an atom-group selector dict.

    Supports today:
    - ``{"all": True}`` -- match every atom in the scene.
    - ``{"elements": ["O", "S"]}`` -- match atoms whose element symbol
      is in the list (case sensitive on the symbol; matches whatever
      ``draw_atoms[i]["elem"]`` carries).
    - ``{"is_minor": True}`` / ``{"is_minor": False}`` -- match by
      disorder major/minor flag.

    Multiple keys can be combined; an atom matches when EVERY present
    key matches (logical AND). Returns ``None`` when the dict carries
    no recognised selectors at all (forces the caller to drop the
    group instead of silently making it match everything).
    """
    if not isinstance(raw, dict):
        return None
    selector: dict[str, Any] = {}
    if raw.get("all"):
        selector["all"] = True
    elements = raw.get("elements")
    if isinstance(elements, (list, tuple)):
        cleaned = [str(item) for item in elements if item is not None and str(item).strip()]
        if cleaned:
            selector["elements"] = cleaned
    if "is_minor" in raw:
        selector["is_minor"] = bool(raw["is_minor"])
    return selector or None


def _coerce_optional_float(value: Any, *, lo: float = 0.0, hi: float = 1.0) -> Optional[float]:
    if value is None:
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, x))


def _coerce_optional_choice(value: Any, choices: set[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text in choices else None


def _normalize_atom_group(
    raw: Any,
    *,
    existing_ids: set[str],
    fallback_color: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Coerce one user payload into the canonical atom-group shape.

    Returns ``None`` when the payload is unsalvageable (not a dict,
    no recognisable selector). Mutates ``existing_ids`` so callers
    building a list in one pass can keep ids unique without rescanning.
    """
    if not isinstance(raw, dict):
        return None
    selector = _coerce_atom_selector(raw.get("selector"))
    if selector is None:
        return None
    group_id = str(raw.get("id") or "").strip()
    if not group_id or group_id in existing_ids:
        group_id = f"grp_{uuid.uuid4().hex[:10]}"
        while group_id in existing_ids:  # pragma: no cover - astronomically unlikely
            group_id = f"grp_{uuid.uuid4().hex[:10]}"
    existing_ids.add(group_id)
    name = str(raw.get("name") or _atom_group_default_name(selector)).strip() or "group"
    color = _coerce_hex_color(raw.get("color"), fallback_color) if raw.get("color") else None
    color_light = _coerce_hex_color(raw.get("color_light"), color or "#000000") if raw.get("color_light") else None
    visible = bool(raw.get("visible", True))
    opacity = _coerce_optional_float(raw.get("opacity"))
    material = _coerce_optional_choice(raw.get("material"), _ATOM_GROUP_VALID_MATERIALS)
    style = _coerce_optional_choice(raw.get("style"), _ATOM_GROUP_VALID_STYLES)
    return {
        "id": group_id,
        "name": name,
        "selector": selector,
        "color": color,
        "color_light": color_light,
        "visible": visible,
        "opacity": opacity,
        "material": material,
        "style": style,
    }


def _atom_group_default_name(selector: dict[str, Any]) -> str:
    if selector.get("all"):
        return "all atoms"
    parts = []
    if "elements" in selector:
        parts.append("/".join(selector["elements"]))
    if "is_minor" in selector:
        parts.append("minor" if selector["is_minor"] else "major")
    return " ".join(parts) or "group"


def _normalize_atom_groups(raw_groups: Any) -> list[dict[str, Any]]:
    if raw_groups is None or not isinstance(raw_groups, (list, tuple)):
        return []
    out: list[dict[str, Any]] = []
    existing_ids: set[str] = set()
    for raw in raw_groups:
        group = _normalize_atom_group(raw, existing_ids=existing_ids)
        if group is not None:
            out.append(group)
    return out


# Phase 3 UI: per-row rendering helpers for the left-panel
# Polyhedra and Atom-group tables. Returns Dash component lists; the
# panel wires up an "Add" button and ALL/MATCH callbacks for inline
# edits + deletes (see ``_polyhedra_row_helpers`` and the matching
# block of ``register_callbacks``).
_AUTO_LIGAND_VALUE = "__auto__"


def _polyhedra_table_rows(
    specs: list[dict[str, Any]],
    species_options: list[dict[str, Any]],
):
    """Build one row of dash inputs per polyhedron spec.

    Each row id is pattern-matched ``{type, spec_id}`` so a single
    ALL-input callback can react to any inline edit and a MATCH/ALL
    callback can identify the deleted row via
    ``callback_context.triggered_id``.
    """
    from dash import dcc, html

    if not specs:
        return [
            html.Div(
                "No named polyhedra. Click \u201cAdd\u201d to register one (centre + optional ligand).",
                className="polyhedra-empty",
                style={"fontSize": "12px", "color": "#777", "margin": "6px 0"},
            )
        ]
    ligand_options = [{"label": "(auto)", "value": _AUTO_LIGAND_VALUE}] + list(species_options)
    rows = []
    for spec in specs:
        rows.append(
            html.Div(
                [
                    dcc.Input(
                        id={"type": "poly-row-color", "spec_id": spec["id"]},
                        type="color",
                        value=str(spec.get("color") or "#7C5CBF"),
                        style={
                            "width": "30px",
                            "height": "26px",
                            "padding": "0",
                            "border": "1px solid #BBB",
                            "verticalAlign": "middle",
                        },
                        debounce=False,
                    ),
                    dcc.Dropdown(
                        id={"type": "poly-row-center", "spec_id": spec["id"]},
                        options=species_options,
                        value=str(spec.get("center_species") or ""),
                        clearable=False,
                        style={"flex": "1", "minWidth": "70px", "fontSize": "12px"},
                    ),
                    html.Span("\u2192", style={"color": "#888", "fontSize": "12px"}),
                    dcc.Dropdown(
                        id={"type": "poly-row-ligand", "spec_id": spec["id"]},
                        options=ligand_options,
                        value=str(spec.get("ligand_species") or _AUTO_LIGAND_VALUE),
                        clearable=False,
                        style={"flex": "1", "minWidth": "70px", "fontSize": "12px"},
                    ),
                    dcc.Checklist(
                        id={"type": "poly-row-enabled", "spec_id": spec["id"]},
                        options=[{"label": "", "value": "yes"}],
                        value=["yes"] if spec.get("enabled", True) else [],
                        style={"display": "inline-block", "marginLeft": "4px"},
                    ),
                    html.Button(
                        "\u00d7",
                        id={"type": "poly-row-delete", "spec_id": spec["id"]},
                        n_clicks=0,
                        style={
                            "background": "transparent",
                            "border": "1px solid #DDD",
                            "color": "#A00",
                            "padding": "0 8px",
                            "cursor": "pointer",
                            "lineHeight": "20px",
                            "borderRadius": "3px",
                        },
                        title="Remove this polyhedron row",
                    ),
                ],
                style={
                    "display": "flex",
                    "alignItems": "center",
                    "gap": "4px",
                    "marginBottom": "4px",
                },
            )
        )
    return rows


_ATOM_GROUP_KIND_ALL = "all"
_ATOM_GROUP_KIND_ELEMENTS = "elements"


def _selector_kind(selector: dict[str, Any]) -> str:
    if selector.get("all"):
        return _ATOM_GROUP_KIND_ALL
    return _ATOM_GROUP_KIND_ELEMENTS


def _selector_elements_text(selector: dict[str, Any]) -> str:
    elements = selector.get("elements") or []
    return ",".join(str(e) for e in elements)


def _atom_groups_table_rows(
    groups: list[dict[str, Any]],
    element_options: list[dict[str, Any]],
):
    """Build one row of dash inputs per atom-group rule. Same
    pattern-match scheme as ``_polyhedra_table_rows``: every input id
    is ``{type, group_id}``.
    """
    from dash import dcc, html

    if not groups:
        return [
            html.Div(
                "No atom-group rules. Use the preset buttons below or click \u201cAdd\u201d to start.",
                style={"fontSize": "12px", "color": "#777", "margin": "6px 0"},
            )
        ]
    rows = []
    for group in groups:
        selector = group.get("selector") or {}
        kind = _selector_kind(selector)
        elements_text = _selector_elements_text(selector)
        rows.append(
            html.Div(
                [
                    html.Div(
                        [
                            dcc.Checklist(
                                id={"type": "ag-row-visible", "group_id": group["id"]},
                                options=[{"label": "", "value": "yes"}],
                                value=["yes"] if group.get("visible", True) else [],
                                style={"display": "inline-block"},
                            ),
                            dcc.Input(
                                id={"type": "ag-row-color", "group_id": group["id"]},
                                type="color",
                                value=str(group.get("color") or "#888888"),
                                style={
                                    "width": "30px",
                                    "height": "26px",
                                    "padding": "0",
                                    "border": "1px solid #BBB",
                                    "verticalAlign": "middle",
                                    "marginLeft": "4px",
                                },
                                debounce=False,
                            ),
                            dcc.Dropdown(
                                id={"type": "ag-row-kind", "group_id": group["id"]},
                                options=[
                                    {"label": "all atoms", "value": _ATOM_GROUP_KIND_ALL},
                                    {"label": "by element", "value": _ATOM_GROUP_KIND_ELEMENTS},
                                ],
                                value=kind,
                                clearable=False,
                                style={"flex": "1", "marginLeft": "4px", "minWidth": "100px", "fontSize": "12px"},
                            ),
                            dcc.Dropdown(
                                id={"type": "ag-row-elements", "group_id": group["id"]},
                                options=element_options,
                                value=[s for s in elements_text.split(",") if s] if kind == _ATOM_GROUP_KIND_ELEMENTS else [],
                                multi=True,
                                placeholder="Pick elements",
                                style={
                                    "flex": "2",
                                    "marginLeft": "4px",
                                    "minWidth": "120px",
                                    "fontSize": "12px",
                                    "display": "block" if kind == _ATOM_GROUP_KIND_ELEMENTS else "none",
                                },
                            ),
                            html.Button(
                                "\u00d7",
                                id={"type": "ag-row-delete", "group_id": group["id"]},
                                n_clicks=0,
                                style={
                                    "background": "transparent",
                                    "border": "1px solid #DDD",
                                    "color": "#A00",
                                    "padding": "0 8px",
                                    "cursor": "pointer",
                                    "lineHeight": "20px",
                                    "borderRadius": "3px",
                                    "marginLeft": "4px",
                                },
                                title="Remove this group rule",
                            ),
                        ],
                        style={"display": "flex", "alignItems": "center", "gap": "2px"},
                    ),
                    html.Div(
                        [
                            html.Span("opacity", style={"fontSize": "11px", "color": "#666"}),
                            dcc.Slider(
                                id={"type": "ag-row-opacity", "group_id": group["id"]},
                                min=0.0,
                                max=1.0,
                                step=0.05,
                                value=float(group.get("opacity")) if group.get("opacity") is not None else 1.0,
                                marks={0.0: "0", 0.5: "0.5", 1.0: "1"},
                                tooltip={"placement": "bottom", "always_visible": False},
                                updatemode="mouseup",
                                included=True,
                            ),
                        ],
                        style={"marginTop": "4px", "padding": "0 4px"},
                    ),
                ],
                style={
                    "marginBottom": "8px",
                    "padding": "6px",
                    "border": "1px solid #EEE",
                    "borderRadius": "4px",
                    "background": "#FAFAFA",
                },
            )
        )
    return rows


def _normalize_polyhedron_specs(
    raw_specs: Any,
    *,
    fallback_color: str = "#7C5CBF",
) -> list[dict[str, Any]]:
    """Validate a list of polyhedron-spec dicts coming from a state patch
    or REST payload. Drops malformed rows silently; callers that need to
    surface validation errors should use ``_normalize_polyhedron_spec``
    directly."""
    if raw_specs is None:
        return []
    if not isinstance(raw_specs, (list, tuple)):
        return []
    out: list[dict[str, Any]] = []
    existing_ids: set[str] = set()
    for index, raw in enumerate(raw_specs):
        spec_fallback = _POLYHEDRON_AUTO_COLORS[index % len(_POLYHEDRON_AUTO_COLORS)]
        spec = _normalize_polyhedron_spec(
            raw,
            fallback_color=fallback_color if index == 0 else spec_fallback,
            existing_ids=existing_ids,
        )
        if spec is not None:
            out.append(spec)
    return out


def _status_message(message: str, level: str = "info") -> tuple[str, str]:
    return message, _status_class(level)


def _structure_summary(scene: dict) -> str:
    if not scene.get("draw_atoms"):
        return "No structure loaded yet. Upload a CIF to begin."
    minor_atoms = sum(1 for atom in scene["draw_atoms"] if atom["is_minor"])
    minor_bonds = sum(1 for bond in scene["bonds"] if bond["is_minor"])
    overflow_count = len(scene.get("unwrap_overflow") or [])
    overflow_text = (
        f" {overflow_count} fragment(s) kept wrapped after exceeding the unwrap cap."
        if overflow_count
        else ""
    )
    if minor_atoms:
        return f"Disorder detected: {minor_atoms} minor atoms, {minor_bonds} minor bonds.{overflow_text}"
    return f"Disorder: none detected.{overflow_text}"


def _display_options_from_style(style: dict) -> list[str]:
    return [
        token
        for enabled, token in (
            (style.get("show_labels", True), "labels"),
            (style.get("show_axes", True), "axes"),
            (style.get("show_minor_only", False), "minor_only"),
            (style.get("minor_wireframe", False), "minor_wireframe"),
            (style.get("show_hydrogen", False), "hydrogens"),
            (style.get("show_unit_cell", False), "unit_cell_box"),
            (style.get("monochrome", False), "monochrome"),
        )
        if enabled
    ]


def _plotly_camera(camera: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not camera:
        return None
    if "eye" in camera:
        return camera
    position = np.array(camera.get("position", [0.0, 0.0, 1.0]), dtype=float)
    focal = np.array(camera.get("focal_point", [0.0, 0.0, 0.0]), dtype=float)
    up = np.array(camera.get("up", [0.0, 1.0, 0.0]), dtype=float)
    eye = position - focal
    norm = np.linalg.norm(eye)
    if norm < 1e-8:
        eye = np.array([0.0, 0.0, 1.8], dtype=float)
    else:
        eye = eye / norm * 1.8
    up_norm = np.linalg.norm(up)
    if up_norm < 1e-8:
        up = np.array([0.0, 1.0, 0.0], dtype=float)
    else:
        up = up / up_norm
    return {
        "eye": {"x": float(eye[0]), "y": float(eye[1]), "z": float(eye[2])},
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "up": {"x": float(up[0]), "y": float(up[1]), "z": float(up[2])},
    }


def _camera_from_relayout_data(
    relayout_data: Optional[dict[str, Any]],
    current_camera: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Extract a complete Plotly camera from Dash relayout payloads.

    Plotly may emit either ``{"scene.camera": {...}}`` or dotted partial
    updates such as ``{"scene.camera.eye.x": 1.2}``.  The latter used to be
    ignored, so the next checkbox-triggered redraw fell back to the default
    scene camera.
    """
    if not relayout_data:
        return None
    direct = relayout_data.get("scene.camera")
    if isinstance(direct, dict):
        return direct
    scene_payload = relayout_data.get("scene")
    if isinstance(scene_payload, dict) and isinstance(scene_payload.get("camera"), dict):
        return scene_payload["camera"]

    base = copy.deepcopy(_plotly_camera(current_camera) or {})
    changed = False

    def ensure_group(group: str) -> dict[str, float]:
        nonlocal changed
        value = base.setdefault(group, {})
        if not isinstance(value, dict):
            value = {}
            base[group] = value
        changed = True
        return value

    for group in ("eye", "center", "up"):
        group_payload = relayout_data.get(f"scene.camera.{group}")
        if isinstance(group_payload, dict):
            target = ensure_group(group)
            for axis in ("x", "y", "z"):
                if axis in group_payload:
                    target[axis] = float(group_payload[axis])
            continue
        for axis in ("x", "y", "z"):
            key = f"scene.camera.{group}.{axis}"
            if key in relayout_data:
                ensure_group(group)[axis] = float(relayout_data[key])
    return base if changed else None


def _camera_vectors(camera: Optional[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    cam = _plotly_camera(camera) or {
        "eye": {"x": 0.0, "y": 0.0, "z": 1.8},
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "up": {"x": 0.0, "y": 1.0, "z": 0.0},
    }
    eye = np.array([cam["eye"]["x"], cam["eye"]["y"], cam["eye"]["z"]], dtype=float)
    center = np.array([cam.get("center", {}).get("x", 0.0), cam.get("center", {}).get("y", 0.0), cam.get("center", {}).get("z", 0.0)], dtype=float)
    up = np.array([cam["up"]["x"], cam["up"]["y"], cam["up"]["z"]], dtype=float)
    up_norm = np.linalg.norm(up)
    if up_norm < 1e-8:
        up = np.array([0.0, 1.0, 0.0], dtype=float)
    else:
        up = up / up_norm
    return eye, center, up


def _camera_payload(eye: np.ndarray, center: np.ndarray, up: np.ndarray) -> dict[str, Any]:
    return {
        "eye": {"x": float(eye[0]), "y": float(eye[1]), "z": float(eye[2])},
        "center": {"x": float(center[0]), "y": float(center[1]), "z": float(center[2])},
        "up": {"x": float(up[0]), "y": float(up[1]), "z": float(up[2])},
    }


def _rotate_vector(vec: np.ndarray, axis: np.ndarray, angle_deg: float) -> np.ndarray:
    axis_norm = np.linalg.norm(axis)
    if axis_norm < 1e-8 or abs(angle_deg) < 1e-8:
        return vec
    axis = axis / axis_norm
    theta = np.deg2rad(angle_deg)
    return (
        vec * np.cos(theta)
        + np.cross(axis, vec) * np.sin(theta)
        + axis * np.dot(axis, vec) * (1.0 - np.cos(theta))
    )


def _fallback_png(message: str) -> bytes:
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return bytes.fromhex(
            "89504E470D0A1A0A0000000D4948445200000001000000010802000000907753DE"
            "0000000C49444154789C63606060000000040001F61738550000000049454E44AE426082"
        )
    image = Image.new("RGB", (960, 240), "white")
    draw = ImageDraw.Draw(image)
    draw.text((18, 18), message, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class ViewerBackend:
    def __init__(self, preset_path: str, names: Optional[Iterable[str]] = None, root_dir: Optional[str] = None):
        self.root_dir = root_dir or WORKSPACE_DIR
        self.preset_path = preset_path
        self.preset = load_preset(preset_path) if os.path.exists(preset_path) else default_preset()
        self.catalog = get_default_catalog(root_dir=self.root_dir)
        self._lock = threading.Lock()
        self._bundle_lock = threading.Lock()
        default_names = [name for name in DEFAULT_CATALOG.keys() if name in self.catalog]
        requested_names = [name for name in (names or []) if name in self.catalog]
        self.structure_names = requested_names if requested_names else default_names
        if not self.structure_names:
            self.structure_names = list(self.catalog.keys())
        self.bundles: Dict[str, LoadedCrystal] = {}
        if not self.structure_names:
            placeholder = build_empty_bundle(name=PLACEHOLDER_STRUCTURE)
            self.bundles[placeholder.name] = placeholder
            self.structure_names = [placeholder.name]
        first_name = self.structure_names[0]
        self.current_state = self.default_state(first_name)
        self.scene_store = SceneStore.load(SceneStore.default_path(self.root_dir))
        # Persisted scenes can outlive the catalog (uploads land in
        # ``tempfile.gettempdir()`` and get GC'd; ``--cif`` may have
        # been dropped). Without prune, ``scene_state(active_id)``
        # below dereferences an unknown ``structure_name`` and crashes
        # the entire app at startup with a blank page.
        scene_count_before = len(self.scene_store.scenes)
        removed_scene_ids = self.scene_store.prune(self.structure_names)
        if removed_scene_ids:
            print(
                f"[crystal_viewer] dropped {len(removed_scene_ids)} stored scene(s) "
                f"referencing unknown structures: {removed_scene_ids}",
                file=sys.stderr,
            )
        self.scene_store.ensure(self.structure_names, default_state_factory=self.default_state)
        if len(self.scene_store.scenes) != scene_count_before:
            try:
                self.scene_store.save()
            except OSError as exc:  # pragma: no cover - disk-full / read-only mount
                print(f"[crystal_viewer] could not persist scene store: {exc}", file=sys.stderr)
        if self.scene_store.active_id:
            self.current_state = self.scene_state(self.scene_store.active_id)
        self.pending_state: Optional[dict[str, Any]] = None
        self._first_figure_ready = threading.Event()
        self.version = 0

    def default_state(self, structure: str) -> dict[str, Any]:
        bundle = self.get_bundle(structure)
        scene = bundle.scene
        style = dict(DEFAULT_STYLE)
        style.update(scene.get("style", {}))
        preset_style = self.preset.get("style", {})
        entry_style = self.preset.get("structures", {}).get(structure, {}).get("style", {})
        style.update(preset_style)
        style.update(entry_style)
        if scene.get("has_minor") and "minor_wireframe" not in preset_style and "minor_wireframe" not in entry_style:
            style["minor_wireframe"] = True
        # Default selected polyhedron centres: every non-halide species in
        # the structure. That generalises the old "B-site default" without
        # baking ABX nomenclature into the UI, and gives the multi-species
        # tiling view "for free" -- e.g. DAP-4 ships with one polyhedron
        # around the NH4+ centre and one around each DABCO ring.
        species_present = self._species_summary(scene.get("fragment_table") or [])
        anion_only = {"Cl", "Br", "I", "F"}
        non_anion = [
            item for item in species_present
            if not (set(item["elements"]) and set(item["elements"]).issubset(anion_only | {"O"}))
        ]
        if non_anion:
            default_species = [item["formula"] for item in non_anion]
        elif species_present:
            default_species = [species_present[0]["formula"]]
        else:
            default_species = []
        return {
            "structure": structure,
            "atom_scale": float(style["atom_scale"]),
            "bond_radius": float(style["bond_radius"]),
            "minor_opacity": float(style["minor_opacity"]),
            "material": str(style.get("material", "mesh")),
            "style": str(style.get("style", "ball_stick")),
            "disorder": str(style.get("disorder", "outline_rings")),
            "ortep_mode": str(style.get("ortep_mode", "ortep_axes")),
            "axis_scale": float(style["axis_scale"]),
            "display_options": _display_options_from_style(style),
            "display_mode": style.get("display_mode", scene.get("display_mode", "formula_unit")),
            "topology_species_keys": list(default_species),
            "topology_site_index": None,
            "topology_enabled": bool(style.get("topology_enabled", True)),
            "topology_hull_color": str(style.get("topology_hull_color", "#7C5CBF")),
            # ``polyhedron_specs`` is the new (Phase 1) per-scene named-row
            # data model: each entry is {id, name, center_species,
            # ligand_species, color, enabled}. Empty list = fall back to the
            # legacy ``topology_species_keys`` + shared ``topology_hull_color``
            # behaviour (auto-derived neighbour types). See
            # ``agents/polyhedron_api.md`` for the API surface.
            "polyhedron_specs": [],
            # Phase 2: per-scene atom-group rules. Each entry is
            # {id, name, selector, color, color_light, visible, opacity,
            # material, style}. Selectors are ANDed across keys; the
            # supported keys are ``all``, ``elements`` (list), and
            # ``is_minor``. Multiple groups apply in list order with
            # later-wins semantics on overlapping atoms. Empty list =
            # no overrides; the legacy ``monochrome`` flag is still
            # honoured when no atom_groups are present. See
            # ``agents/atom_groups_api.md`` for the API surface.
            "atom_groups": [],
            "fast_rendering": bool(style.get("fast_rendering", False)),
            "camera": scene.get("camera"),
            "cutoff": 10.0,
        }

    def _bump_version(self):
        self.version += 1

    def list_structures(self) -> list[dict[str, Any]]:
        return [self.get_bundle(name).metadata() for name in self.structure_names]

    def structure_options(self) -> list[dict[str, str]]:
        return [
            {
                "label": "Upload CIF to begin" if name == PLACEHOLDER_STRUCTURE else name,
                "value": name,
            }
            for name in self.structure_names
        ]

    def scene_options(self) -> list[dict[str, Any]]:
        return self.scene_store.list()

    def scene_tabs(self) -> list[Any]:
        tabs = []
        for scene in self.scene_store.list():
            tabs.append(
                dcc.Tab(
                    label=scene["label"],
                    value=scene["id"],
                    id=f"scene-tab-{scene['id']}",
                )
            )
        return tabs

    def scene_close_buttons(self) -> list[Any]:
        buttons = []
        for scene in self.scene_store.list():
            buttons.append(
                html.Button(
                    html.Span("\u00d7", id=f"scene-tab-close-{scene['id']}"),
                    id={"type": "tab-close", "scene_id": scene["id"]},
                    className="tab-close-x",
                    n_clicks=0,
                    title=f"Close {scene['label']}",
                )
            )
        return buttons

    def scene_state(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        scene = self.scene_store.get(scene_id)
        defaults = self.default_state(scene.structure_name)
        return scene.state(defaults)

    def active_scene_id(self) -> Optional[str]:
        return self.scene_store.active_id

    def create_scene(
        self,
        *,
        structure: Optional[str] = None,
        label: Optional[str] = None,
        state: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        structure = structure or self.get_state().get("structure") or (self.structure_names[0] if self.structure_names else PLACEHOLDER_STRUCTURE)
        if structure not in self.structure_names:
            raise KeyError(structure)
        base_state = self.default_state(structure)
        if state:
            base_state.update(self.normalize_state(state))
        scene = self.scene_store.add(
            label=label or structure,
            structure_name=structure,
            state_patch=base_state,
            camera=base_state.get("camera"),
        )
        self.current_state = self.scene_state(scene.id)
        self.pending_state = copy.deepcopy(self.current_state)
        self._bump_version()
        return scene.to_dict()

    def update_scene(self, scene_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        scene = self.scene_store.get(scene_id)
        if "label" in payload and len(payload) == 1:
            scene = self.scene_store.rename(scene_id, payload["label"])
        else:
            patch = dict(payload)
            if "state" in patch:
                state_patch = patch.pop("state") or {}
                state_patch = self.normalize_state(state_patch, scene_id=scene_id)
                patch.update(state_patch)
            scene = self.scene_store.patch_scene(scene_id, patch)
        if self.scene_store.active_id == scene_id:
            self.current_state = self.scene_state(scene_id)
            self.pending_state = copy.deepcopy(self.current_state)
        self._bump_version()
        return scene.to_dict()

    def delete_scene(self, scene_id: str) -> dict[str, Any]:
        removed = self.scene_store.remove(scene_id)
        if self.scene_store.active_id:
            self.current_state = self.scene_state(self.scene_store.active_id)
        self.pending_state = copy.deepcopy(self.current_state)
        self._bump_version()
        return removed.to_dict()

    def duplicate_scene(self, scene_id: str, label: Optional[str] = None) -> dict[str, Any]:
        scene = self.scene_store.duplicate(scene_id, label=label)
        self.current_state = self.scene_state(scene.id)
        self.pending_state = copy.deepcopy(self.current_state)
        self._bump_version()
        return scene.to_dict()

    def reorder_scenes(self, order: Iterable[str]) -> list[str]:
        order = self.scene_store.reorder(order)
        self._bump_version()
        return order

    def set_active_scene(self, scene_id: str, *, broadcast: bool = True) -> dict[str, Any]:
        # ``broadcast`` controls whether ``pending_state`` is armed for
        # the next ``sync_agent_state`` poll. The REST API agent path
        # (``/api/v1/scenes/.../activate``) wants this so the browser
        # UI picks up the change. Dash callbacks that originate *from*
        # the same UI must pass ``broadcast=False``: otherwise they
        # echo the change back to themselves on the next poll tick,
        # which (a) re-runs every per-control callback (refresh
        # topology species, refresh fragment options, ...) and (b)
        # triggers a full ``update_view`` for nothing -- doubling the
        # 1 MB-per-frame transfer cost on every click that carries a
        # ``scene-tabs.value`` Input.
        scene = self.scene_store.set_active(scene_id)
        self.current_state = self.scene_state(scene.id)
        if broadcast:
            self.pending_state = copy.deepcopy(self.current_state)
        self._bump_version()
        return scene.to_dict()

    @staticmethod
    def _species_summary(fragments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Group fragments by their stoichiometric ``formula`` (e.g. ``C8N1``,
        ``ClO4``, ``N1``) and return one summary per distinct species,
        sorted by heavy-atom count then occurrence count.

        This is the species-checkbox source of truth: each entry carries a
        ``formula`` (the stable selector value), a count, and the elements
        present so the UI can colour-code or filter without re-deriving
        from raw fragments."""
        by_formula: dict[str, dict[str, Any]] = {}
        for frag in fragments:
            formula = frag.get("formula") or frag.get("species") or "?"
            entry = by_formula.get(formula)
            if entry is None:
                entry = {
                    "formula": formula,
                    "count": 0,
                    "heavy": int(frag.get("heavy_atom_count", 0) or 0),
                    "elements": list(frag.get("elem_set") or []),
                }
                by_formula[formula] = entry
            entry["count"] += 1
        return sorted(by_formula.values(), key=lambda item: (item["heavy"], -item["count"]))

    def species_options(self, structure: Optional[str] = None) -> list[dict[str, Any]]:
        """Checklist options for the species-based polyhedron selector.

        One entry per stoichiometrically distinct fragment present in the
        currently displayed scene. Each entry's ``value`` is the formula
        string (used as a stable group key) and the ``label`` shows the
        formula together with how many sites it covers, so the user sees
        e.g. ``C8N1 \u00d72`` for the DABCO rings of DAP-4.
        """
        target = structure or (self.structure_names[0] if self.structure_names else None)
        if target is None or target not in self.bundles:
            return []
        scene = self.get_bundle(target).scene
        return [
            {
                "label": f"{item['formula']} \u00d7{item['count']}",
                "value": item["formula"],
            }
            for item in self._species_summary(scene.get("fragment_table") or [])
        ]

    def element_options(self, state: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        """Distinct element symbols present in the active scene's
        ``draw_atoms``. Used by the Phase 3 atom-group editor's
        "by element" picker so the user can pick from real elements
        rather than typing free-form symbols.

        Returns a list of ``{"label": "O", "value": "O"}`` dicts in
        the order elements first appear in the scene (so e.g. for a
        perovskite the cations come first, then the anions, matching
        the user's mental model).
        """
        state = state or self.get_state()
        try:
            scene = self.scene_for_state(state)
        except Exception:
            return []
        seen: dict[str, None] = {}
        for atom in scene.get("draw_atoms") or []:
            elem = str(atom.get("elem") or "").strip()
            if elem and elem not in seen:
                seen[elem] = None
        return [{"label": elem, "value": elem} for elem in seen]

    def fragment_options(self, state: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        """Dropdown options for the right-panel "Analyze fragment" selector.

        One entry per fragment in the current scene. The ``value`` is the
        fragment index (matching what ``topology_site_index`` already
        used), the ``label`` is the human-readable id + formula. Crucially
        this list is *not* filtered by the species checkboxes -- the user
        can tile only ClO4 polyhedra and still ask the right panel to
        analyse a C6N2 fragment, which is the "decouple display from
        analysis" UX the user asked for.
        """
        state = state or self.get_state()
        try:
            scene = self.scene_for_state(state)
        except Exception:
            return []
        options: list[dict[str, Any]] = []
        for frag in scene.get("fragment_table") or []:
            label = frag.get("label") or f"#{frag['index']}"
            formula = frag.get("formula") or frag.get("species") or ""
            text = f"{label}  \u00b7  {formula}" if formula else str(label)
            options.append({"label": text, "value": int(frag["index"])})
        return options

    def _drop_placeholder(self) -> None:
        if PLACEHOLDER_STRUCTURE in self.structure_names and len(self.structure_names) == 1:
            self.structure_names = []
        self.bundles.pop(PLACEHOLDER_STRUCTURE, None)

    def get_bundle(self, name: str) -> LoadedCrystal:
        if name in self.bundles:
            return self.bundles[name]
        if name not in self.catalog:
            raise KeyError(name)

        entry = self.catalog[name]
        built = build_loaded_crystal(
            name=name,
            cif_path=entry["cif_path"],
            title=entry["title"],
            preset=self.preset,
            source="catalog",
        )

        with self._bundle_lock:
            existing = self.bundles.get(name)
            if existing is not None:
                return existing
            self.bundles[name] = built
            return built

    def get_scene_json(self, name: str) -> dict[str, Any]:
        state = self.get_state()
        if state["structure"] != name:
            state = self.normalize_state({"structure": name})
        bundle = self.get_bundle(name)
        scene = self.scene_for_state(state)
        return {
            "name": bundle.name,
            "title": bundle.title,
            "scene": scene_json(scene),
            "fragment_table": copy.deepcopy(scene.get("fragment_table", [])),
            "topology_fragment_table": copy.deepcopy(bundle.topology_fragment_table),
            "summary": _structure_summary(scene),
        }

    def normalize_state(self, patch: Optional[dict[str, Any]], scene_id: Optional[str] = None) -> dict[str, Any]:
        if scene_id is not None:
            state = self.scene_state(scene_id)
        else:
            state = copy.deepcopy(self.current_state)
        patch = patch or {}
        if "scene_id" in patch and patch["scene_id"] in self.scene_store.scenes:
            scene_id = str(patch["scene_id"])
            state = self.scene_state(scene_id)
        if "structure" in patch and patch["structure"] in self.structure_names:
            structure = patch["structure"]
            defaults = self.default_state(structure)
            state.update(defaults)
            state["structure"] = structure
        if scene_id is not None:
            state["scene_id"] = scene_id
            scene = self.scene_store.get(scene_id)
            state["scene_label"] = scene.label
        for key in ("atom_scale", "bond_radius", "minor_opacity", "axis_scale", "cutoff"):
            if key in patch and patch[key] is not None:
                state[key] = float(patch[key])
        for key in ("material", "style", "disorder", "ortep_mode"):
            if key in patch and patch[key] is not None:
                state[key] = str(patch[key])
        if "display_options" in patch and patch["display_options"] is not None:
            state["display_options"] = list(patch["display_options"])
        if "display_mode" in patch and patch["display_mode"] is not None:
            state["display_mode"] = str(patch["display_mode"])
            if "topology_site_index" not in patch:
                state["topology_site_index"] = None
        if "topology_species_keys" in patch:
            value = patch["topology_species_keys"]
            if value is None:
                state["topology_species_keys"] = []
            else:
                state["topology_species_keys"] = [str(item) for item in value if item is not None]
        # Legacy A/B/X selection: translate the type to the matching list of
        # species formulas in the active scene so existing /api/v1 callers (and
        # the example scripts shipped under scripts/) keep working without
        # learning the new species-checkbox vocabulary.
        if patch.get("topology_fragment_type"):
            requested_type = str(patch["topology_fragment_type"])
            structure = state.get("structure")
            if structure and structure in self.bundles:
                fragments = self.get_bundle(structure).scene.get("fragment_table") or []
                matched = {
                    f.get("formula") or f.get("species")
                    for f in fragments
                    if f.get("type") == requested_type
                }
                state["topology_species_keys"] = [k for k in matched if k]
        if patch.get("topology_show_all_sites") and not state.get("topology_species_keys"):
            structure = state.get("structure")
            if structure and structure in self.bundles:
                fragments = self.get_bundle(structure).scene.get("fragment_table") or []
                state["topology_species_keys"] = sorted(
                    {f.get("formula") or f.get("species") for f in fragments if f.get("formula") or f.get("species")}
                )
        if "topology_site_index" in patch:
            value = patch["topology_site_index"]
            state["topology_site_index"] = None if value in ("", None) else int(value)
        if "topology_enabled" in patch:
            state["topology_enabled"] = bool(patch["topology_enabled"])
        if "topology_hull_color" in patch and patch["topology_hull_color"]:
            state["topology_hull_color"] = str(patch["topology_hull_color"])
        if "polyhedron_specs" in patch:
            # Empty list is a valid override (= "drop all named specs and
            # fall back to legacy topology_species_keys"); ``None`` means
            # the same. Treat both uniformly.
            state["polyhedron_specs"] = _normalize_polyhedron_specs(
                patch.get("polyhedron_specs") or [],
                fallback_color=state.get("topology_hull_color", "#7C5CBF"),
            )
        if "atom_groups" in patch:
            # Same semantics as polyhedron_specs: empty list / None
            # both mean "drop all overrides; use legacy monochrome
            # flag (if any) and element palette".
            state["atom_groups"] = _normalize_atom_groups(patch.get("atom_groups") or [])
        if "fast_rendering" in patch:
            state["fast_rendering"] = bool(patch["fast_rendering"])
        if "camera" in patch and patch["camera"] is not None:
            state["camera"] = patch["camera"]
        return state

    def get_state(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        with self._lock:
            if scene_id is not None:
                return copy.deepcopy(self.scene_state(scene_id))
            return copy.deepcopy(self.current_state)

    def patch_state(
        self,
        patch: Optional[dict[str, Any]],
        scene_id: Optional[str] = None,
        *,
        broadcast: bool = True,
    ) -> dict[str, Any]:
        # ``broadcast`` controls whether ``pending_state`` is armed for
        # the next ``sync_agent_state`` poll. REST/WS callers want this
        # so the browser UI picks up the change. Dash callbacks that
        # originate *from* the same UI (``capture_camera`` in
        # particular) must pass ``broadcast=False``: otherwise the next
        # 5 s poll echoes that camera back into ``camera-state-store``,
        # ``update_view`` rebuilds with the stale-by-debounce camera
        # value, and the user sees the view "snap back" to where the
        # last ``relayoutData`` left it. The same logic applies to any
        # other UI-originated patch where the browser is already
        # authoritative for the field being changed.
        with self._lock:
            target_scene_id = scene_id or (patch or {}).get("scene_id") or self.scene_store.active_id
            self.current_state = self.normalize_state(patch, scene_id=target_scene_id)
            if target_scene_id:
                scene_payload = copy.deepcopy(self.current_state)
                scene_payload.pop("scene_id", None)
                scene_payload.pop("scene_label", None)
                self.scene_store.patch_scene(target_scene_id, scene_payload)
            if broadcast:
                self.pending_state = copy.deepcopy(self.current_state)
            self._bump_version()
            return copy.deepcopy(self.current_state)

    def pop_pending_state(self) -> Optional[dict[str, Any]]:
        with self._lock:
            pending = self.pending_state
            self.pending_state = None
            return copy.deepcopy(pending) if pending else None

    def record_state(self, patch: Optional[dict[str, Any]], scene_id: Optional[str] = None) -> None:
        with self._lock:
            target_scene_id = scene_id or (patch or {}).get("scene_id") or self.scene_store.active_id
            self.current_state = self.normalize_state(patch, scene_id=target_scene_id)
            if target_scene_id:
                scene_payload = copy.deepcopy(self.current_state)
                scene_payload.pop("scene_id", None)
                scene_payload.pop("scene_label", None)
                self.scene_store.patch_scene(target_scene_id, scene_payload)
            self._bump_version()

    def show_hydrogen_for_state(self, state: Optional[dict[str, Any]] = None) -> bool:
        state = self.current_state if state is None else state
        return "hydrogens" in set(state.get("display_options", []))

    def scene_for_state(self, state: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        state = self.current_state if state is None else state
        bundle = self.get_bundle(state["structure"])
        scene = build_bundle_scene(
            bundle,
            display_mode=state.get("display_mode", "formula_unit"),
            show_hydrogen=self.show_hydrogen_for_state(state),
            preset=self.preset,
        )
        bundle.scene = scene
        bundle.fragment_table = scene.get("fragment_table", bundle.fragment_table)
        return scene

    def style_for_state(self, state: Optional[dict[str, Any]] = None, scene: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        state = self.current_state if state is None else state
        scene = self.scene_for_state(state) if scene is None else scene
        style = dict(scene.get("style", {}))
        style.update(
            style_from_controls(
                state["atom_scale"],
                state["bond_radius"],
                state["minor_opacity"],
                state["axis_scale"],
                state["display_options"],
                material=state.get("material"),
                render_style=state.get("style"),
                disorder=state.get("disorder"),
                ortep_mode=state.get("ortep_mode"),
            )
        )
        style["display_mode"] = state.get("display_mode", scene.get("display_mode", "formula_unit"))
        style["material"] = state.get("material", style.get("material", "mesh"))
        style["style"] = state.get("style", style.get("style", "ball_stick"))
        style["disorder"] = state.get("disorder", style.get("disorder", "outline_rings"))
        style["ortep_mode"] = state.get("ortep_mode", style.get("ortep_mode", "ortep_axes"))
        style["fast_rendering"] = bool(state.get("fast_rendering", False)) or style["material"] == "flat"
        style["topology_enabled"] = bool(state.get("topology_enabled", True))
        style["topology_hull_color"] = str(state.get("topology_hull_color", "#7C5CBF"))
        # Phase 2: per-scene atom-group rules ride along on the style
        # dict so the renderer dispatcher can partition draw_atoms by
        # (effective_material, effective_style) without touching the
        # backend layer. Renderer reads ``style["atom_groups"]`` only
        # if the list is non-empty; the legacy ``monochrome`` flag is
        # otherwise honoured untouched.
        style["atom_groups"] = list(state.get("atom_groups") or [])
        return style

    def add_uploaded_bundle(self, contents: str, filename: str) -> LoadedCrystal:
        bundle = load_uploaded_cif(
            contents=contents,
            filename=filename,
            existing_names=self.structure_names,
            preset=self.preset,
        )
        self._drop_placeholder()
        self.bundles[bundle.name] = bundle
        self.structure_names.append(bundle.name)
        self.create_scene(structure=bundle.name, label=bundle.name)
        return bundle

    def add_uploaded_file_bytes(self, data: bytes, filename: str) -> LoadedCrystal:
        # Sanitise the user-supplied filename before joining it onto a
        # writable directory. ``os.path.join("/tmp/uploads", "/etc/passwd")``
        # silently drops the prefix and writes ``/etc/passwd``; even
        # without an absolute escape, ``../../foo`` walks outside the
        # upload directory. ``secure_filename`` strips both classes of
        # attack and the realpath check below is a belt-and-braces
        # second line of defence in case Werkzeug's normalisation rules
        # ever change.
        from werkzeug.utils import secure_filename

        upload_dir = os.path.realpath(os.path.join(tempfile.gettempdir(), "crystal_viewer_uploads"))
        os.makedirs(upload_dir, exist_ok=True)
        safe = secure_filename(filename or "") or "upload.cif"
        if not safe.lower().endswith(".cif"):
            safe = f"{safe}.cif"
        path = os.path.realpath(os.path.join(upload_dir, safe))
        if os.path.commonpath([path, upload_dir]) != upload_dir:
            raise ValueError(f"unsafe upload filename: {filename!r}")
        with open(path, "wb") as handle:
            handle.write(data)
        stem = os.path.splitext(safe)[0]
        safe_name = stem
        suffix = 2
        while safe_name in self.structure_names:
            safe_name = f"{stem}_{suffix}"
            suffix += 1
        bundle = build_loaded_crystal(name=safe_name, cif_path=path, title=stem, preset=self.preset, source="upload")
        self._drop_placeholder()
        self.bundles[bundle.name] = bundle
        self.structure_names.append(bundle.name)
        self.create_scene(structure=bundle.name, label=bundle.name)
        return bundle

    def topology_candidates(self, structure: str, fragment_type: Optional[str] = None) -> list[dict[str, Any]]:
        state = self.get_state()
        if state["structure"] != structure:
            state = self.normalize_state({"structure": structure})
        fragments = self.scene_for_state(state).get("fragment_table", [])
        if fragment_type and fragment_type not in ("", "Any"):
            filtered = [fragment for fragment in fragments if fragment.get("type") == fragment_type]
            if filtered:
                return filtered
        return fragments

    def fragment_index_for_atom(self, scene: dict, atom_index: int) -> Optional[int]:
        for fragment in scene.get("fragment_table", []):
            if atom_index in fragment.get("site_indices", []):
                return int(fragment["index"])
        atom = scene["draw_atoms"][atom_index]
        atom_cart = np.array(atom["cart"], dtype=float)
        fragments = scene.get("fragment_table", [])
        if not fragments:
            return atom_index
        distances = [
            (float(np.linalg.norm(np.array(fragment["center"], dtype=float) - atom_cart)), int(fragment["index"]))
            for fragment in fragments
        ]
        distances.sort(key=lambda item: item[0])
        return distances[0][1]

    def _display_fragment(self, scene: dict, display_index: int | None) -> Optional[dict[str, Any]]:
        if display_index is None:
            return None
        return next((fragment for fragment in scene.get("fragment_table", []) if int(fragment["index"]) == int(display_index)), None)

    def _pbc_distance(self, bundle: LoadedCrystal, frac_a, frac_b) -> float:
        delta = np.array(frac_b, dtype=float) - np.array(frac_a, dtype=float)
        delta -= np.round(delta)
        return float(np.linalg.norm(np.array(bundle.M, dtype=float) @ delta))

    def map_display_fragment_to_topology(self, bundle: LoadedCrystal, display_fragment: dict | None) -> Optional[dict[str, Any]]:
        if display_fragment is None:
            return None
        # Prefer matching by stoichiometric formula (the species-checkbox
        # identity); fall back to A/B/X type for older payloads where the
        # formula field hasn't been populated yet.
        display_formula = display_fragment.get("formula") or display_fragment.get("species")
        candidates = [
            fragment
            for fragment in bundle.topology_fragment_table
            if (fragment.get("formula") or fragment.get("species")) == display_formula
        ]
        if not candidates:
            candidates = [
                fragment
                for fragment in bundle.topology_fragment_table
                if fragment.get("type") == display_fragment.get("type")
            ]
        if not candidates:
            candidates = list(bundle.topology_fragment_table)
        if not candidates:
            return None
        display_frac = np.array(display_fragment.get("frac_center", [0.0, 0.0, 0.0]), dtype=float)
        ranked = []
        for fragment in candidates:
            ranked.append((self._pbc_distance(bundle, display_frac, fragment.get("frac_center", [0.0, 0.0, 0.0])), fragment))
        ranked.sort(key=lambda item: item[0])
        return ranked[0][1]

    def resolve_topology_site(
        self,
        *,
        state: dict[str, Any],
        structure: str,
        explicit_site: Optional[int],
        species_keys: Optional[list[str]],
        click_data: Optional[dict[str, Any]],
    ) -> Optional[int]:
        """Resolve which fragment index gets the right-panel histogram +
        topology results.

        Display (which species the polyhedra overlay tiles) and analysis
        (which single fragment is in the right panel) are independent:
        an ``explicit_site`` from the "Analyze fragment" dropdown wins
        unconditionally, even when its formula is not in the currently
        tiled ``species_keys`` set. Only when no explicit site was given
        do we fall through to the click target / first-match defaults
        scoped by the tiled species.
        """
        scene = self.scene_for_state(state)
        fragments = scene.get("fragment_table", [])
        species_set = {str(key) for key in species_keys or [] if key}
        if explicit_site is not None:
            chosen = self._display_fragment(scene, explicit_site)
            if chosen is not None:
                return int(explicit_site)
        if click_data and click_data.get("points"):
            point = click_data["points"][0]
            custom = point.get("customdata")
            if custom:
                atom_index = int(custom[0])
                return self.fragment_index_for_atom(scene, atom_index)
        if species_set:
            candidates = [
                fragment
                for fragment in fragments
                if (fragment.get("formula") or fragment.get("species")) in species_set
            ]
            if not candidates:
                return None
        else:
            candidates = fragments
        if candidates:
            return int(candidates[0]["index"])
        return None

    # ---- polyhedron_specs CRUD ---------------------------------------
    #
    # All methods operate on the active scene's state by default;
    # callers may pass ``scene_id`` to target a specific tab. They
    # always return the persisted list of specs (post-normalisation)
    # and emit a broadcast so every connected client picks up the
    # change. Wraps ``patch_state`` so the existing version bump,
    # autosave, and pending-state machinery just works.

    def list_polyhedron_specs(self, scene_id: Optional[str] = None) -> list[dict[str, Any]]:
        state = self.get_state(scene_id)
        return list(state.get("polyhedron_specs") or [])

    def _resolve_specs(self, scene_id: Optional[str]) -> tuple[Optional[str], list[dict[str, Any]]]:
        scene_id = scene_id or self.active_scene_id()
        state = self.get_state(scene_id)
        specs = list(state.get("polyhedron_specs") or [])
        return scene_id, [dict(spec) for spec in specs]

    def add_polyhedron_spec(
        self,
        center_species: str,
        ligand_species: Optional[str] = None,
        *,
        name: Optional[str] = None,
        color: Optional[str] = None,
        enabled: bool = True,
        scene_id: Optional[str] = None,
        spec_id: Optional[str] = None,
    ) -> dict[str, Any]:
        scene_id, specs = self._resolve_specs(scene_id)
        fallback_color = _POLYHEDRON_AUTO_COLORS[len(specs) % len(_POLYHEDRON_AUTO_COLORS)]
        existing_ids = {spec["id"] for spec in specs}
        spec = _normalize_polyhedron_spec(
            {
                "id": spec_id,
                "name": name,
                "center_species": center_species,
                "ligand_species": ligand_species,
                "color": color,
                "enabled": enabled,
            },
            fallback_color=fallback_color,
            existing_ids=existing_ids,
        )
        if spec is None:
            raise ValueError(
                f"invalid polyhedron spec (missing center_species?): {center_species!r}"
            )
        specs.append(spec)
        self.patch_state({"polyhedron_specs": specs}, scene_id=scene_id)
        return spec

    def update_polyhedron_spec(
        self,
        spec_id: str,
        patch: dict[str, Any],
        *,
        scene_id: Optional[str] = None,
    ) -> dict[str, Any]:
        scene_id, specs = self._resolve_specs(scene_id)
        for index, spec in enumerate(specs):
            if spec["id"] == spec_id:
                merged = dict(spec)
                merged.update(patch or {})
                merged["id"] = spec_id
                # Re-normalise via the single-row helper so the same
                # color/species coercion as POST applies.
                replacement = _normalize_polyhedron_spec(
                    merged,
                    fallback_color=spec["color"],
                    existing_ids={s["id"] for s in specs if s["id"] != spec_id},
                )
                if replacement is None:
                    raise ValueError(
                        f"invalid polyhedron spec patch for {spec_id!r}: {patch!r}"
                    )
                specs[index] = replacement
                self.patch_state({"polyhedron_specs": specs}, scene_id=scene_id)
                return replacement
        raise KeyError(f"unknown polyhedron spec id: {spec_id!r}")

    def remove_polyhedron_spec(
        self,
        spec_id: str,
        *,
        scene_id: Optional[str] = None,
    ) -> bool:
        scene_id, specs = self._resolve_specs(scene_id)
        before = len(specs)
        specs = [spec for spec in specs if spec["id"] != spec_id]
        if len(specs) == before:
            return False
        self.patch_state({"polyhedron_specs": specs}, scene_id=scene_id)
        return True

    def reorder_polyhedron_specs(
        self,
        ordered_ids: Iterable[str],
        *,
        scene_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        scene_id, specs = self._resolve_specs(scene_id)
        index_by_id = {spec["id"]: spec for spec in specs}
        wanted = [str(item) for item in ordered_ids]
        if set(wanted) != set(index_by_id):
            raise ValueError(
                "reorder list must contain exactly the existing spec ids; "
                f"got {wanted!r}, have {sorted(index_by_id)}"
            )
        ordered = [index_by_id[spec_id] for spec_id in wanted]
        self.patch_state({"polyhedron_specs": ordered}, scene_id=scene_id)
        return ordered

    # ---- atom_groups CRUD ---------------------------------------------
    #
    # Same shape as polyhedron CRUD: scoped to one scene, persisted via
    # patch_state, returns the canonical post-normalisation list. See
    # agents/atom_groups_api.md.

    def list_atom_groups(self, scene_id: Optional[str] = None) -> list[dict[str, Any]]:
        return list(self.get_state(scene_id).get("atom_groups") or [])

    def _resolve_atom_groups(self, scene_id: Optional[str]) -> tuple[Optional[str], list[dict[str, Any]]]:
        scene_id = scene_id or self.active_scene_id()
        return scene_id, [dict(group) for group in (self.get_state(scene_id).get("atom_groups") or [])]

    def add_atom_group(
        self,
        selector: dict[str, Any],
        *,
        name: Optional[str] = None,
        color: Optional[str] = None,
        color_light: Optional[str] = None,
        visible: bool = True,
        opacity: Optional[float] = None,
        material: Optional[str] = None,
        style: Optional[str] = None,
        scene_id: Optional[str] = None,
        group_id: Optional[str] = None,
    ) -> dict[str, Any]:
        scene_id, groups = self._resolve_atom_groups(scene_id)
        existing_ids = {grp["id"] for grp in groups}
        group = _normalize_atom_group(
            {
                "id": group_id,
                "name": name,
                "selector": selector,
                "color": color,
                "color_light": color_light,
                "visible": visible,
                "opacity": opacity,
                "material": material,
                "style": style,
            },
            existing_ids=existing_ids,
        )
        if group is None:
            raise ValueError(
                f"invalid atom_group payload (missing/empty selector?): {selector!r}"
            )
        groups.append(group)
        self.patch_state({"atom_groups": groups}, scene_id=scene_id)
        return group

    def update_atom_group(
        self,
        group_id: str,
        patch: dict[str, Any],
        *,
        scene_id: Optional[str] = None,
    ) -> dict[str, Any]:
        scene_id, groups = self._resolve_atom_groups(scene_id)
        for index, group in enumerate(groups):
            if group["id"] == group_id:
                merged = dict(group)
                merged.update(patch or {})
                merged["id"] = group_id
                replacement = _normalize_atom_group(
                    merged,
                    existing_ids={g["id"] for g in groups if g["id"] != group_id},
                )
                if replacement is None:
                    raise ValueError(
                        f"invalid atom_group patch for {group_id!r}: {patch!r}"
                    )
                groups[index] = replacement
                self.patch_state({"atom_groups": groups}, scene_id=scene_id)
                return replacement
        raise KeyError(f"unknown atom_group id: {group_id!r}")

    def remove_atom_group(self, group_id: str, *, scene_id: Optional[str] = None) -> bool:
        scene_id, groups = self._resolve_atom_groups(scene_id)
        before = len(groups)
        groups = [grp for grp in groups if grp["id"] != group_id]
        if len(groups) == before:
            return False
        self.patch_state({"atom_groups": groups}, scene_id=scene_id)
        return True

    def reorder_atom_groups(
        self,
        ordered_ids: Iterable[str],
        *,
        scene_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        scene_id, groups = self._resolve_atom_groups(scene_id)
        index_by_id = {grp["id"]: grp for grp in groups}
        wanted = [str(item) for item in ordered_ids]
        if set(wanted) != set(index_by_id):
            raise ValueError(
                "reorder list must contain exactly the existing atom_group ids; "
                f"got {wanted!r}, have {sorted(index_by_id)}"
            )
        ordered = [index_by_id[group_id] for group_id in wanted]
        self.patch_state({"atom_groups": ordered}, scene_id=scene_id)
        return ordered

    # ---- topology computation -----------------------------------------

    def _effective_polyhedron_specs(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Resolve the per-render list of specs from explicit
        ``polyhedron_specs`` if present, otherwise synthesise one entry
        per legacy ``topology_species_keys`` value (auto ligand, shared
        ``topology_hull_color``)."""
        explicit = list(state.get("polyhedron_specs") or [])
        if explicit:
            return [dict(spec) for spec in explicit if spec.get("enabled", True)]
        species_keys = list(state.get("topology_species_keys") or [])
        if not species_keys:
            return []
        color = str(state.get("topology_hull_color", "#7C5CBF"))
        return [
            {
                "id": f"legacy_{index}",
                "name": str(key),
                "center_species": str(key),
                "ligand_species": None,
                "color": color,
                "enabled": True,
            }
            for index, key in enumerate(species_keys)
        ]

    def topology_for_state(self, state: dict[str, Any], click_data: Optional[dict[str, Any]] = None):
        if not state.get("topology_enabled", True):
            return None
        structure = state["structure"]
        bundle = self.get_bundle(structure)
        scene = self.scene_for_state(state)
        effective_specs = self._effective_polyhedron_specs(state)
        if not effective_specs:
            return None
        # Legacy code paths below still consume a single ``species_keys``
        # list (used to resolve the analysis anchor when the user clicks
        # in the viewer). Reconstruct it from the union of every active
        # spec's center species so a click on any rendered polyhedron
        # still snaps the analysis panel.
        species_keys = sorted({spec["center_species"] for spec in effective_specs})
        if not species_keys:
            return None
        site_index = self.resolve_topology_site(
            state=state,
            structure=structure,
            explicit_site=state.get("topology_site_index"),
            species_keys=species_keys,
            click_data=click_data,
        )
        if site_index is None:
            return None
        # Memoize the (heavy) topology dict on the bundle keyed on the
        # state fields that actually influence GEOMETRY. Per-spec colour
        # is intentionally not in the key -- it only affects the
        # renderer's painter cache (``_background_dict_cache`` etc),
        # which is keyed independently on the per-spec colour tuple.
        # That way swapping a hull colour stays a cheap re-paint and
        # doesn't recompute coordination shells for every tile.
        cutoff = float(state.get("cutoff", 10.0))
        spec_geometry_key = tuple(
            (
                spec["center_species"],
                spec.get("ligand_species") or None,
            )
            for spec in effective_specs
        )
        cache_key = (
            structure,
            state.get("display_mode"),
            bool("hydrogens" in (state.get("display_options") or [])),
            int(site_index),
            cutoff,
            spec_geometry_key,
        )
        cache = getattr(bundle, "_topology_state_cache", None)
        if cache is None:
            cache = {}
            bundle._topology_state_cache = cache
        cached_geometry = cache.get(cache_key)
        if cached_geometry is None:
            cached_geometry = self._compute_topology_geometry(
                bundle=bundle,
                scene=scene,
                effective_specs=effective_specs,
                site_index=site_index,
                cutoff=cutoff,
            )
            cache[cache_key] = cached_geometry
        if cached_geometry is None:
            return None
        # Re-attach the per-render colour overrides on every call. The
        # geometry payload is shared across colour permutations; we only
        # ever copy a small list of dicts, never the heavy hull arrays.
        return self._attach_spec_colors(cached_geometry, effective_specs)

    def _compute_topology_geometry(
        self,
        *,
        bundle,
        scene: dict[str, Any],
        effective_specs: list[dict[str, Any]],
        site_index: int,
        cutoff: float,
    ) -> Optional[dict[str, Any]]:
        display_fragment = self._display_fragment(scene, site_index)
        topology_fragment = self.map_display_fragment_to_topology(bundle, display_fragment)
        if topology_fragment is None:
            return None

        # Group enabled specs by (center_species -> [spec_index_in_specs, ...])
        # so each fragment in the scene knows which spec(s) own it.
        # Multiple specs may share a centre species but request different
        # ligand restrictions (e.g. "Pb -> Cl" red vs "Pb -> Br" blue in
        # mixed-halide perovskites); the same fragment then participates
        # in both spec_results.
        center_to_spec_indices: dict[str, list[int]] = {}
        for index, spec in enumerate(effective_specs):
            center_to_spec_indices.setdefault(spec["center_species"], []).append(index)

        primary_display_index = int(display_fragment["index"]) if display_fragment else None
        primary_formula = (
            (display_fragment.get("formula") or display_fragment.get("species"))
            if display_fragment else None
        )
        # Pick the spec that "owns" the analysis anchor. Preference goes
        # to a spec whose center species matches the clicked fragment;
        # if none match, fall back to the first enabled spec so the
        # right-hand histogram still has data to render.
        analysis_spec_index = 0
        if primary_formula and primary_formula in center_to_spec_indices:
            analysis_spec_index = center_to_spec_indices[primary_formula][0]
        analysis_spec = effective_specs[analysis_spec_index]
        analysis_ligand = analysis_spec.get("ligand_species") or None

        primary = analyze_topology(
            bundle,
            center_index=int(topology_fragment["index"]),
            cutoff=cutoff,
            display_center=display_fragment.get("center") if display_fragment else None,
            display_label=display_fragment.get("label") if display_fragment else None,
            display_type=display_fragment.get("type") if display_fragment else None,
            ligand_species=[analysis_ligand] if analysis_ligand else None,
        )

        # Build per-spec overlay lists. For each fragment whose formula
        # matches a spec's center species, run the lighter
        # ``extract_coordination_shell`` (skips planarity / prism /
        # angular passes -- those only matter for the analysis anchor).
        # The same fragment may appear in multiple specs if those specs
        # share its centre species but differ in ligand selection; the
        # cache hit on (center_index, cutoff, ligand_species) makes the
        # repeat call cheap.
        spec_results: list[dict[str, Any]] = []
        legacy_extras: list[dict[str, Any]] = []
        for index, spec in enumerate(effective_specs):
            center_species = spec["center_species"]
            ligand = spec.get("ligand_species") or None
            ligand_arg = [ligand] if ligand else None
            overlays: list[dict[str, Any]] = []
            for frag in scene.get("fragment_table") or []:
                formula_key = frag.get("formula") or frag.get("species")
                if formula_key != center_species:
                    continue
                is_anchor = (
                    index == analysis_spec_index
                    and primary_display_index is not None
                    and int(frag["index"]) == primary_display_index
                )
                if is_anchor:
                    overlays.append(
                        {
                            "center_coords": primary["center_coords"],
                            "center_label": primary.get("center_label"),
                            "shell_coords": primary["shell_coords"],
                            "distances": primary["distances"],
                            "is_analysis_anchor": True,
                        }
                    )
                    continue
                mapped = self.map_display_fragment_to_topology(bundle, frag)
                if mapped is None:
                    continue
                try:
                    extra = extract_coordination_shell(
                        bundle,
                        center_index=int(mapped["index"]),
                        cutoff=cutoff,
                        display_center=frag.get("center"),
                        display_label=frag.get("label"),
                        display_type=frag.get("type"),
                        ligand_species=ligand_arg,
                    )
                except Exception:
                    continue
                if not extra.get("shell_coords"):
                    # Empty shell would render as nothing anyway; skip
                    # the entry so renderer caches stay tidy.
                    continue
                overlay = {
                    "center_coords": extra.get("center_coords"),
                    "center_label": extra.get("center_label"),
                    "shell_coords": extra.get("shell_coords"),
                    "distances": extra.get("distances"),
                    "is_analysis_anchor": False,
                }
                overlays.append(overlay)
                legacy_extras.append(
                    {
                        "center_coords": overlay["center_coords"],
                        "center_label": overlay["center_label"],
                        "shell_coords": overlay["shell_coords"],
                        "distances": overlay["distances"],
                    }
                )
            spec_results.append(
                {
                    "spec_id": spec["id"],
                    "name": spec["name"],
                    "center_species": center_species,
                    "ligand_species": ligand,
                    "overlays": overlays,
                }
            )

        primary = dict(primary)
        if legacy_extras:
            primary["extra_overlays"] = legacy_extras
        primary["spec_results"] = spec_results
        primary["analysis_spec_id"] = analysis_spec["id"]
        return primary

    def _attach_spec_colors(
        self,
        cached_geometry: dict[str, Any],
        effective_specs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Re-stamp per-spec colours onto a geometry payload pulled from
        the bundle cache. The geometry dict is shared across colour
        changes; we copy a small wrapper so the renderer's painter
        cache (keyed on the colour tuple) doesn't get polluted by
        stale values."""
        color_by_id = {spec["id"]: spec.get("color", "#7C5CBF") for spec in effective_specs}
        spec_results = []
        for entry in cached_geometry.get("spec_results", []) or []:
            spec_id = entry.get("spec_id")
            recoloured = dict(entry)
            recoloured["color"] = color_by_id.get(spec_id, "#7C5CBF")
            spec_results.append(recoloured)
        out = dict(cached_geometry)
        out["spec_results"] = spec_results
        # Drop any painter caches the renderer attached to a sibling
        # colour permutation -- the new wrapper starts clean.
        out.pop("_background_dict_cache", None)
        out.pop("_foreground_dict_cache", None)
        return out

    def figure_for_state(self, state: Optional[dict[str, Any]] = None, click_data: Optional[dict[str, Any]] = None):
        state = self.get_state() if state is None else state
        scene = self.scene_for_state(state)
        topology_data = self.topology_for_state(state, click_data=click_data)
        fig = build_figure(scene, self.style_for_state(state, scene=scene), topology_data=topology_data)
        camera = _plotly_camera(state.get("camera"))
        if camera:
            fig.update_layout(scene_camera=camera)
        return fig, topology_data

    def render_current_png(self, scene_id: Optional[str] = None) -> bytes:
        fig, _ = self.figure_for_state(self.get_state(scene_id))
        try:
            return pio.to_image(fig, format="png", scale=2)
        except Exception as exc:  # pragma: no cover - depends on local Chrome/Kaleido state
            return _fallback_png(f"Plotly image export failed: {exc}")

    def default_camera(self, state: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        scene = self.scene_for_state(self.get_state() if state is None else state)
        return _plotly_camera(scene.get("camera")) or _plotly_camera(None)

    def get_camera(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        state = self.get_state(scene_id)
        return _plotly_camera(state.get("camera")) or self.default_camera(state)

    def set_camera(self, camera: dict[str, Any], scene_id: Optional[str] = None) -> dict[str, Any]:
        self.patch_state({"camera": camera}, scene_id=scene_id)
        return self.get_camera(scene_id)

    def camera_action(self, action: str, scene_id: Optional[str] = None, **payload) -> dict[str, Any]:
        if action == "reset":
            return self.set_camera(self.default_camera(self.get_state(scene_id)), scene_id=scene_id)

        eye, center, up = _camera_vectors(self.get_camera(scene_id))
        if action == "zoom":
            factor = float(payload.get("factor", 1.0))
            if abs(factor) > 1e-8:
                eye = eye / factor
        elif action == "pan":
            delta = np.array(
                [
                    float(payload.get("dx", 0.0)),
                    float(payload.get("dy", 0.0)),
                    float(payload.get("dz", 0.0)),
                ],
                dtype=float,
            )
            center = center + delta
        elif action == "orbit":
            yaw_deg = float(payload.get("yaw_deg", 0.0))
            pitch_deg = float(payload.get("pitch_deg", 0.0))
            eye = _rotate_vector(eye, up, yaw_deg)
            right = np.cross(eye, up)
            if np.linalg.norm(right) > 1e-8:
                eye = _rotate_vector(eye, right, pitch_deg)
                up = _rotate_vector(up, right, pitch_deg)
        camera = _camera_payload(eye, center, up)
        return self.set_camera(camera, scene_id=scene_id)

    def _safe_preset_path(self, path: Optional[str]) -> Optional[str]:
        """Resolve ``path`` against ``<root>/.local`` and reject anything
        that escapes that directory.

        The REST handlers expose ``/api/v{1,2}/preset/save`` and
        ``/preset/load`` with a client-controlled ``path`` field. Without
        this guard, any caller able to reach the API has an
        arbitrary-file-write (and an arbitrary-JSON-read) primitive on
        the host. Restricting to ``<root>/.local`` keeps the caller-
        facing contract (``path`` still works) while collapsing the
        attack surface to a single state directory the app already
        owns. ``path=None`` falls through to the default location.
        """
        if path is None:
            return None
        safe_root = os.path.realpath(os.path.join(self.root_dir, LOCAL_STATE_DIRNAME))
        os.makedirs(safe_root, exist_ok=True)
        candidate = path if os.path.isabs(path) else os.path.join(safe_root, path)
        resolved = os.path.realpath(candidate)
        if os.path.commonpath([resolved, safe_root]) != safe_root:
            raise ValueError(
                f"preset path must resolve inside {safe_root!r}, got {path!r}"
            )
        return resolved

    def save_preset(self, path: Optional[str] = None) -> dict[str, Any]:
        target = self._safe_preset_path(path) or self.preset_path
        state = self.get_state()
        bundle = self.get_bundle(state["structure"])
        scene = self.scene_for_state(state)
        preset_data = load_preset(target) if os.path.exists(target) else default_preset()
        preset_data["style"].update(self.style_for_state(state))
        preset_data.setdefault("structures", {})
        preset_data["structures"][bundle.name] = {
            "camera": state.get("camera") or scene.get("camera"),
            "show_hydrogen": self.show_hydrogen_for_state(state),
            "style": self.style_for_state(state),
        }
        save_preset(target, preset_data)
        self.preset = preset_data
        return {"path": target, "structure": bundle.name}

    def load_preset_from_path(self, path: Optional[str]) -> dict[str, Any]:
        if not path:
            raise ValueError("path is required")
        target = self._safe_preset_path(path)
        self.preset = load_preset(target)
        self.preset_path = target
        for bundle in self.bundles.values():
            bundle.scene_cache.clear()
            cache = getattr(bundle, "_topology_state_cache", None)
            if cache:
                cache.clear()
        structure = self.get_state()["structure"]
        self.patch_state(self.default_state(structure))
        return {"path": target, "state": self.get_state()}

    def export_static(self, output_path: Optional[str] = None) -> dict[str, Any]:
        state = self.get_state()
        if state.get("structure") == PLACEHOLDER_STRUCTURE:
            return {
                "returncode": 1,
                "stdout": "",
                "stderr": "No structure is loaded yet. Upload or preload a CIF before exporting.",
            }
        self.save_preset()
        cmd = [
            os.environ.get("PYTHON", "python"),
            "-m",
            LEGACY_EXPORT_MODULE,
            "--preset",
            self.preset_path,
            "--both",
        ]
        proc = subprocess.run(cmd, cwd=self.root_dir, capture_output=True, text=True)
        payload = {
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
        if output_path:
            payload["output_path"] = output_path
        return payload

    def query_topology(self, structure: str, center_index: int, cutoff: float = 10.0, scene_id: Optional[str] = None) -> dict[str, Any]:
        state = self.get_state(scene_id)
        if state["structure"] != structure:
            state = self.normalize_state({"structure": structure}, scene_id=scene_id)
        state["topology_site_index"] = center_index
        state["cutoff"] = cutoff
        return self.topology_for_state(state)

    def websocket_snapshot(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "state": self.get_state(),
            "structures": self.list_structures(),
        }


def create_app(
    preset_path: str = DEFAULT_PRESET_PATH,
    names=None,
    root_dir: Optional[str] = None,
    cif_paths: Optional[Iterable[str]] = None,
) -> Dash:
    backend = ViewerBackend(preset_path=preset_path, names=names, root_dir=root_dir)
    for cif_path in cif_paths or []:
        bundle = build_loaded_crystal(
            name=os.path.splitext(os.path.basename(cif_path))[0],
            cif_path=cif_path,
            title=os.path.splitext(os.path.basename(cif_path))[0],
            preset=backend.preset,
            source="cli",
        )
        backend.bundles[bundle.name] = bundle
        if bundle.name not in backend.structure_names:
            backend.structure_names.append(bundle.name)
        if not any(scene["structure_name"] == bundle.name for scene in backend.scene_options()):
            backend.create_scene(structure=bundle.name, label=bundle.name)
    if cif_paths:
        backend._drop_placeholder()
    if backend.structure_names and backend.current_state.get("structure") not in backend.structure_names:
        backend.current_state = backend.default_state(backend.structure_names[0])
    if backend.scene_store.active_id:
        backend.current_state = backend.scene_state(backend.scene_store.active_id)
    app = Dash(__name__, assets_folder=os.path.join(PACKAGE_DIR, "assets"))
    app.crystal_backend = backend

    # gzip + brotli the JSON figure responses. ``update_view`` ships
    # ~1 MB of base64 mesh data per click and most of that string
    # alphabet is plain ASCII, so it compresses to ~150-250 kB. On
    # any user with <2 Mbit/s downstream that's the difference
    # between a Labels-toggle taking ~5 s and ~0.5 s. Flask-Compress
    # only kicks in for ``Accept-Encoding`` clients and skips bodies
    # below ``COMPRESS_MIN_SIZE``, so it has no effect on the tiny
    # capture_state / poll responses.
    try:
        from flask_compress import Compress

        app.server.config.setdefault("COMPRESS_MIMETYPES", [
            "text/html", "text/css", "text/javascript",
            "application/javascript", "application/json", "application/octet-stream",
        ])
        app.server.config.setdefault("COMPRESS_LEVEL", 6)
        app.server.config.setdefault("COMPRESS_BR_LEVEL", 4)
        app.server.config.setdefault("COMPRESS_MIN_SIZE", 1024)
        Compress(app.server)
    except Exception:
        # Compression is opportunistic; the app must still serve
        # without it (e.g. on a stripped-down install).
        pass

    first_state = backend.get_state()
    first_figure, first_topology = backend.figure_for_state(first_state)
    backend._first_figure_ready.set()
    first_scene = backend.scene_for_state(first_state)

    app.layout = html.Div(
        [
            dcc.Store(id="agent-state-store", data=first_state),
            dcc.Store(
                id="camera-state-store",
                data=_camera_store_payload(first_state.get("scene_id"), first_state.get("camera")),
            ),
            dcc.Download(id="export-download"),
            dcc.Interval(id="status-dismiss-timer", interval=5000, n_intervals=0, disabled=True),
            # 5 s is a deliberate compromise: long enough to avoid
            # interleaving a poll between every two user clicks (which
            # otherwise re-pumps the whole control set through the
            # cascade), short enough that REST API mutations show up
            # in the UI within one human reaction time. When the API
            # path becomes WebSocket-driven we'll be able to take this
            # interval up to 30 s and let pushed messages do the work.
            dcc.Interval(id="agent-state-poll", interval=5000, n_intervals=0),
            html.Div(id="state-sync-sentinel", style={"display": "none"}),
            html.Div(
                [
                    html.H3("Crystal Viewer", style={"marginTop": "0"}),
                    html.Div(
                        [
                            html.Label("Scenes", style={"fontWeight": "bold"}),
                            html.Div(
                                [
                                    html.Button(
                                        "+",
                                        id="scene-new-tab-btn",
                                        n_clicks=0,
                                        title="Duplicate active scene as new tab",
                                    ),
                                    html.Span("Duplicate tab", className="scene-new-tab-hint"),
                                ],
                                style={"float": "right"},
                            ),
                        ],
                        style={"marginBottom": "4px"},
                    ),
                    dcc.Tabs(
                        id="scene-tabs",
                        value=first_state.get("scene_id") or backend.active_scene_id(),
                        children=backend.scene_tabs(),
                        parent_className="scene-tabs",
                    ),
                    html.Div(
                        id="scene-tab-close-row",
                        children=backend.scene_close_buttons(),
                        className="scene-tab-close-row",
                    ),
                    html.Div(
                        [
                            dcc.Input(
                                id="scene-tab-rename-input",
                                type="text",
                                value=first_state.get("scene_label") or first_state["structure"],
                                placeholder="Scene label",
                                style={"width": "68%", "marginRight": "6px"},
                            ),
                            html.Button("Rename", id="scene-rename-btn", n_clicks=0),
                            html.Button("Close", id="scene-tab-close-active", n_clicks=0, style={"marginLeft": "6px"}),
                        ],
                        style={"marginTop": "8px", "marginBottom": "8px"},
                    ),
                    html.Div(
                        id="structure-summary",
                        children=_structure_summary(first_scene),
                        style={"marginBottom": "12px", "fontSize": "13px", "color": "#444444"},
                    ),
                    html.Label("Upload CIF"),
                    dcc.Upload(
                        id="scene-cif-upload",
                        children=html.Div(["Drag and drop CIF, or click to upload"]),
                        multiple=True,
                        style={
                            "border": "1px dashed #999999",
                            "padding": "10px",
                            "marginBottom": "12px",
                            "textAlign": "center",
                        },
                    ),
                    html.Div(
                        id="upload-status",
                        style={"marginBottom": "12px", "whiteSpace": "pre-wrap", "fontSize": "13px"},
                    ),
                    html.Label("Display Scope"),
                    dcc.Dropdown(
                        id="display-mode-selector",
                        options=[
                            {"label": "Formula unit cluster", "value": "formula_unit"},
                            {"label": "Unit cell", "value": "unit_cell"},
                            {"label": "Asymmetric unit", "value": "asymmetric_unit"},
                            {"label": "Isolated cluster (no PBC)", "value": "cluster"},
                        ],
                        value=first_state["display_mode"],
                        clearable=False,
                        style={"marginBottom": "12px"},
                    ),
                    html.Label("Display"),
                    dcc.Checklist(
                        id="display-options",
                        options=[
                            {"label": "Labels", "value": "labels"},
                            {"label": "Axes", "value": "axes"},
                            {"label": "Minor Only", "value": "minor_only"},
                            {"label": "Hydrogens", "value": "hydrogens"},
                            {"label": "Unit Cell Box", "value": "unit_cell_box"},
                            # Phase 3: legacy "Monochrome atoms" toggle
                            # has been replaced by the Atom-Groups
                            # editor below (one-click "Monochrome"
                            # preset). Backend still honours the
                            # ``monochrome`` flag for callers / saved
                            # presets that set it directly.
                        ],
                        value=[opt for opt in first_state["display_options"] if opt != "monochrome"],
                    ),
                    html.Div(style={"height": "10px"}),
                    html.Label("Material / Style / Disorder"),
                    html.Div(
                        [
                            dcc.Dropdown(
                                id="material-selector",
                                options=[
                                    {"label": "Mesh 3D", "value": "mesh"},
                                    {"label": "Flat billboard", "value": "flat"},
                                ],
                                value=first_state.get("material", "mesh"),
                                clearable=False,
                                style={"flex": "1"},
                            ),
                            dcc.Dropdown(
                                id="style-selector",
                                options=[
                                    {"label": "Ball-stick", "value": "ball_stick"},
                                    {"label": "Ball", "value": "ball"},
                                    {"label": "Stick", "value": "stick"},
                                    {"label": "ORTEP", "value": "ortep"},
                                    {"label": "Wireframe", "value": "wireframe"},
                                ],
                                value=first_state.get("style", "ball_stick"),
                                clearable=False,
                                style={"flex": "1"},
                            ),
                            dcc.Dropdown(
                                id="disorder-selector",
                                options=[
                                    {"label": "Outline rings", "value": "outline_rings"},
                                    {"label": "Opacity from occ.", "value": "opacity"},
                                    {"label": "Dashed bonds", "value": "dashed_bonds"},
                                    {"label": "Colour shift", "value": "color_shift"},
                                    {"label": "None", "value": "none"},
                                ],
                                value=first_state.get("disorder", "outline_rings"),
                                clearable=False,
                                style={"flex": "1"},
                            ),
                        ],
                        style={"display": "flex", "gap": "6px", "marginBottom": "10px"},
                    ),
                    html.Label("ORTEP Draw Mode"),
                    dcc.Dropdown(
                        id="ortep-mode-selector",
                        options=[
                            {"label": "Solid ellipsoids", "value": "ortep_solid"},
                            {"label": "Principal axes", "value": "ortep_axes"},
                            {"label": "Octant shading", "value": "ortep_octant"},
                        ],
                        value=first_state.get("ortep_mode", "ortep_axes"),
                        clearable=False,
                        style={"marginBottom": "10px"},
                    ),
                    html.Label("Atom Scale"),
                    dcc.Slider(
                        id="atom-scale-slider",
                        min=0.5, max=1.8, step=0.02,
                        value=float(first_state["atom_scale"]),
                        marks={0.5: "0.5", 1.0: "1.0", 1.5: "1.5", 1.8: "1.8"},
                        tooltip={"placement": "bottom", "always_visible": False},
                        updatemode="mouseup",
                    ),
                    html.Label("Bond Radius"),
                    dcc.Slider(
                        id="bond-radius-slider",
                        min=0.05, max=0.40, step=0.01,
                        value=float(first_state["bond_radius"]),
                        marks={0.05: "0.05", 0.20: "0.20", 0.40: "0.40"},
                        tooltip={"placement": "bottom", "always_visible": False},
                        updatemode="mouseup",
                    ),
                    html.Div(
                        [
                            html.Label("Minor Opacity"),
                            dcc.Slider(
                                id="minor-opacity-slider",
                                min=0.10, max=0.90, step=0.02,
                                value=float(first_state["minor_opacity"]),
                                marks={0.1: "0.1", 0.5: "0.5", 0.9: "0.9"},
                                tooltip={"placement": "bottom", "always_visible": False},
                                updatemode="mouseup",
                                disabled=_minor_opacity_disabled(first_state.get("disorder", "outline_rings")),
                            ),
                        ],
                        id="minor-opacity-control",
                        style=_minor_opacity_control_style(first_state.get("disorder", "outline_rings")),
                    ),
                    html.Label("Axis Scale"),
                    dcc.Slider(
                        id="axis-scale-slider",
                        min=0.05, max=0.25, step=0.01,
                        value=float(first_state["axis_scale"]),
                        marks={0.05: "0.05", 0.15: "0.15", 0.25: "0.25"},
                        tooltip={"placement": "bottom", "always_visible": False},
                        updatemode="mouseup",
                    ),
                    html.Hr(),
                    html.H4("Topology"),
                    dcc.Checklist(
                        id="topology-toggle",
                        options=[{"label": "Show topology overlay", "value": "enabled"}],
                        value=["enabled"] if first_state.get("topology_enabled", True) else [],
                    ),
                    html.Label(
                        "Polyhedron centres (check one or more species)",
                        style={"fontSize": "13px", "marginTop": "6px", "display": "block"},
                    ),
                    dcc.Checklist(
                        id="topology-species",
                        options=backend.species_options(first_state["structure"]),
                        value=list(first_state.get("topology_species_keys") or []),
                        style={"marginTop": "4px"},
                        inputStyle={"marginRight": "6px"},
                        labelStyle={"display": "block", "fontFamily": "monospace"},
                    ),
                    html.Div(
                        [
                            html.Label(
                                "Fallback colour (legacy)",
                                style={"marginRight": "8px", "fontSize": "12px", "color": "#777"},
                            ),
                            dcc.Input(
                                id="topology-hull-color",
                                type="color",
                                value=first_state.get("topology_hull_color", "#7C5CBF"),
                                style={"width": "40px", "height": "24px", "padding": "0", "border": "1px solid #BBB", "verticalAlign": "middle"},
                            ),
                        ],
                        style={"display": "flex", "alignItems": "center", "marginTop": "8px"},
                    ),
                    html.Hr(),
                    # ---- Phase 3: Named polyhedra table ----
                    html.Div(
                        [
                            html.H4(
                                "Named polyhedra",
                                style={"display": "inline-block", "marginRight": "8px"},
                            ),
                            html.Button(
                                "+ Add",
                                id="polyhedra-add-btn",
                                n_clicks=0,
                                style={
                                    "fontSize": "12px",
                                    "padding": "2px 8px",
                                    "verticalAlign": "middle",
                                    "cursor": "pointer",
                                },
                                title="Add a named polyhedron row (centre + optional ligand restriction + colour).",
                            ),
                        ],
                        style={"display": "flex", "alignItems": "center"},
                    ),
                    html.Div(
                        id="polyhedra-rows-container",
                        children=_polyhedra_table_rows(
                            first_state.get("polyhedron_specs") or [],
                            backend.species_options(first_state["structure"]),
                        ),
                        style={"marginTop": "6px"},
                    ),
                    html.Hr(),
                    # ---- Phase 3: Atom groups table ----
                    html.Div(
                        [
                            html.H4(
                                "Atom groups",
                                style={"display": "inline-block", "marginRight": "8px"},
                            ),
                            html.Button(
                                "+ Add",
                                id="atom-groups-add-btn",
                                n_clicks=0,
                                style={
                                    "fontSize": "12px",
                                    "padding": "2px 8px",
                                    "verticalAlign": "middle",
                                    "cursor": "pointer",
                                },
                                title="Add an empty atom-group rule. Pick a selector (all / by-element) and a colour.",
                            ),
                        ],
                        style={"display": "flex", "alignItems": "center"},
                    ),
                    html.Div(
                        [
                            html.Button(
                                "Monochrome",
                                id="atom-groups-preset-mono",
                                n_clicks=0,
                                style={"fontSize": "12px", "padding": "2px 8px", "marginRight": "4px", "cursor": "pointer"},
                                title="Add an 'all atoms = #000000' rule (replacement for the legacy Monochrome checkbox).",
                            ),
                            html.Button(
                                "Hide H",
                                id="atom-groups-preset-hide-h",
                                n_clicks=0,
                                style={"fontSize": "12px", "padding": "2px 8px", "marginRight": "4px", "cursor": "pointer"},
                                title="Add a 'hydrogen invisible' rule.",
                            ),
                            html.Button(
                                "Clear all",
                                id="atom-groups-clear-btn",
                                n_clicks=0,
                                style={"fontSize": "12px", "padding": "2px 8px", "cursor": "pointer", "color": "#A00"},
                                title="Drop every atom-group rule for this scene.",
                            ),
                        ],
                        style={"marginTop": "6px"},
                    ),
                    html.Div(
                        id="atom-groups-rows-container",
                        children=_atom_groups_table_rows(
                            first_state.get("atom_groups") or [],
                            backend.element_options(first_state),
                        ),
                        style={"marginTop": "6px"},
                    ),
                    html.Hr(),
                    html.Div(style={"height": "12px"}),
                    html.Button("Save Preset", id="save-preset-btn", n_clicks=0),
                    html.Button("Export Static Figure", id="export-btn", n_clicks=0, style={"marginLeft": "8px"}),
                    html.Div(
                        id="status-banner",
                        children=f"Preset: {preset_path}",
                        className=_status_class("idle"),
                    ),
                    html.Div(id="status", style={"display": "none"}),
                ],
                id="left-panel",
                style={
                    "width": "340px",
                    "minWidth": "260px",
                    "maxWidth": "640px",
                    "flex": "0 0 auto",
                    "padding": "16px",
                    "borderRight": "1px solid #DDDDDD",
                    "fontFamily": "Arial, sans-serif",
                    "overflowY": "auto",
                    "height": "100vh",
                },
            ),
            html.Div(id="left-splitter", className="panel-splitter"),
            html.Div(
                [
                    dcc.Loading(
                        dcc.Graph(id="crystal-graph", figure=first_figure, style={"height": "100vh"}),
                        type="circle",
                        color="#7C5CBF",
                        # Avoid a spinner flash on every short callback
                        # (capture_state is ~10 ms; a spinner that
                        # appears for 50 ms reads as a stutter, not
                        # progress). The 300 ms threshold is short
                        # enough that on slow updates (cold figure
                        # rebuild ~1.5 s, dense topology ~600 ms)
                        # the user still gets feedback well before
                        # they would start wondering if the click
                        # registered.
                        delay_show=300,
                        delay_hide=0,
                    )
                ],
                id="center-panel",
                style={"flex": "1", "minWidth": 0},
            ),
            html.Div(id="right-splitter", className="panel-splitter"),
            html.Div(
                [
                    html.Div(
                        [
                            html.Button(
                                "Analysis",
                                id="analysis-panel-toggle",
                                className="analysis-panel-toggle",
                                n_clicks=0,
                                title="Show or hide analysis panel",
                            ),
                            html.Div(
                                [
                                    html.Div("Analysis", className="analysis-panel-title"),
                                    html.Div(
                                        "Topology, score summaries, and future analysis modules.",
                                        className="analysis-panel-subtitle",
                                    ),
                                ],
                                className="analysis-panel-heading",
                            ),
                        ],
                        className="analysis-panel-header",
                    ),
                    html.Div(
                        [
                            html.Section(
                                [
                                    html.Div("Topology", className="analysis-section-title"),
                                    html.Label(
                                        "Analyze fragment",
                                        htmlFor="topology-site-index",
                                        className="analysis-label",
                                    ),
                                    dcc.Dropdown(
                                        id="topology-site-index",
                                        options=backend.fragment_options(first_state),
                                        value=first_state.get("topology_site_index"),
                                        placeholder="(first match of selected species, or click in viewer)",
                                        clearable=True,
                                        className="analysis-control",
                                    ),
                                    html.Div(
                                        "Display tiling and analysis are independent: switch the analysed "
                                        "fragment here without changing what is drawn.",
                                        className="analysis-help",
                                    ),
                                    dcc.Graph(
                                        id="topology-histogram",
                                        figure=topology_histogram_figure(first_topology),
                                        className="analysis-graph",
                                        style={"height": "260px"},
                                    ),
                                    html.Pre(
                                        id="topology-results",
                                        children=topology_results_markdown(first_topology),
                                        className="analysis-results",
                                    ),
                                ],
                                className="analysis-section",
                            ),
                        ],
                        className="analysis-panel-body",
                    ),
                ],
                id="right-panel",
                className="analysis-panel analysis-panel--collapsed",
                style={
                    "width": "320px",
                    "minWidth": "260px",
                    "maxWidth": "640px",
                    "flex": "0 0 auto",
                    "padding": "16px",
                    "borderLeft": "1px solid #DDDDDD",
                    "backgroundColor": "#FAFAFA",
                    "height": "100vh",
                    "overflowY": "auto",
                },
            ),
        ],
        id="viewer-root",
        style={"display": "flex", "height": "100vh", "backgroundColor": "#FFFFFF"},
    )

    def scene_control_outputs(state: dict[str, Any]) -> tuple[Any, ...]:
        scene_id = state.get("scene_id") or backend.active_scene_id()
        return (
            state.get("scene_label") or state["structure"],
            state["display_mode"],
            state["display_options"],
            state["atom_scale"],
            state["bond_radius"],
            state["minor_opacity"],
            state.get("material", "mesh"),
            state.get("style", "ball_stick"),
            state.get("disorder", "outline_rings"),
            state.get("ortep_mode", "ortep_axes"),
            state["axis_scale"],
            list(state.get("topology_species_keys") or []),
            state["topology_site_index"],
            ["enabled"] if state.get("topology_enabled", True) else [],
            state.get("topology_hull_color", "#7C5CBF"),
            state,
            _camera_store_payload(scene_id, state.get("camera")),
        )

    @app.callback(
        Output("scene-tabs", "children"),
        Output("scene-tabs", "value"),
        Output("upload-status", "children"),
        Input("scene-cif-upload", "contents"),
        State("scene-cif-upload", "filename"),
        prevent_initial_call=True,
    )
    def upload_cif(contents_list, filenames):
        if not contents_list:
            return no_update, no_update, no_update
        names_out = []
        for contents, filename in zip(contents_list, filenames or []):
            bundle = backend.add_uploaded_bundle(contents, filename)
            names_out.append(bundle.name)
        return backend.scene_tabs(), backend.active_scene_id(), f"Uploaded CIF(s): {', '.join(names_out)}"

    @app.callback(
        Output("topology-species", "options"),
        Output("topology-species", "value", allow_duplicate=True),
        Input("scene-tabs", "value"),
        State("topology-species", "value"),
        prevent_initial_call=True,
    )
    def refresh_species_options(scene_id, current_value):
        structure = backend.get_state(scene_id).get("structure")
        opts = backend.species_options(structure)
        valid_values = {opt["value"] for opt in opts}
        keep = [v for v in (current_value or []) if v in valid_values]
        if not keep:
            # Re-derive a sensible default for the freshly selected structure
            # rather than leaving the checkbox group empty.
            default = backend.default_state(structure).get("topology_species_keys") or []
            keep = list(default)
        return opts, keep

    @app.callback(
        Output("topology-site-index", "value", allow_duplicate=True),
        Input("crystal-graph", "clickData"),
        State("scene-tabs", "value"),
        State("display-mode-selector", "value"),
        State("display-options", "value"),
        prevent_initial_call=True,
    )
    def click_to_select_fragment(click_data, scene_id, display_mode, display_options):
        if not click_data or not click_data.get("points"):
            return no_update
        try:
            structure = backend.get_state(scene_id).get("structure")
            state = backend.normalize_state(
                {
                    "scene_id": scene_id,
                    "structure": structure,
                    "display_mode": display_mode,
                    "display_options": display_options,
                }
            )
            resolved = backend.resolve_topology_site(
                state=state,
                structure=structure,
                explicit_site=None,
                species_keys=None,
                click_data=click_data,
            )
        except Exception:
            return no_update
        return resolved if resolved is not None else no_update

    @app.callback(
        Output("topology-site-index", "options"),
        Output("topology-site-index", "value", allow_duplicate=True),
        Input("scene-tabs", "value"),
        Input("display-mode-selector", "value"),
        Input("display-options", "value"),
        State("topology-site-index", "value"),
        prevent_initial_call=True,
    )
    def refresh_fragment_options(scene_id, display_mode, display_options, current_value):
        # The fragment options reflect the *scene* fragments, so they
        # change when the user switches structures, display modes
        # (formula unit / unit cell / cluster), or toggles hydrogens.
        # When the previously analysed fragment falls outside the new
        # scene we clear the dropdown so the topology callback falls
        # back to the "first match of selected species" default.
        # Of the five Display checkboxes only Hydrogens affects which
        # fragments exist. The other four (Labels/Axes/Minor Only/
        # Unit Cell Box) all fire this callback too because they share
        # the ``display-options`` Input, but recomputing the options
        # would do nothing useful and ``backend.fragment_options`` can
        # easily hit ~1s on dense unit cells. Short-circuit those.
        hydrogens_on = "hydrogens" in (display_options or [])
        cache_key = (scene_id, display_mode, hydrogens_on)
        cached = getattr(refresh_fragment_options, "_cache", None)
        if cached is not None and cached[0] == cache_key:
            opts = cached[1]
        else:
            try:
                structure = backend.get_state(scene_id).get("structure")
                state = backend.normalize_state(
                    {
                        "scene_id": scene_id,
                        "structure": structure,
                        "display_mode": display_mode,
                        "display_options": display_options,
                    }
                )
            except Exception:
                return no_update, no_update
            opts = backend.fragment_options(state)
            refresh_fragment_options._cache = (cache_key, opts)
        valid_values = {opt["value"] for opt in opts}
        keep = current_value if current_value in valid_values else None
        # The ``topology-site-index.value`` Output also writes the
        # ``capture_state`` Input. Whenever we re-emit the same value
        # we still cause Dash to fire a second ``capture_state``; if
        # *that* returns ``no_update`` (which it will, since the patch
        # is identical), Dash 2.18 collapses the whole agent-state
        # update chain and ``update_view`` is never queued. Returning
        # ``no_update`` for ``value`` whenever it's already correct
        # avoids the spurious second capture entirely.
        prev_opts = getattr(refresh_fragment_options, "_last_opts", None)
        opts_out = no_update if prev_opts == opts else opts
        if opts_out is not no_update:
            refresh_fragment_options._last_opts = opts
        value_out = no_update if keep == current_value else keep
        return opts_out, value_out

    @app.callback(
        Output("scene-tabs", "children", allow_duplicate=True),
        Output("scene-tabs", "value", allow_duplicate=True),
        Output("status", "children", allow_duplicate=True),
        Input("scene-new-tab-btn", "n_clicks"),
        Input("scene-rename-btn", "n_clicks"),
        Input("scene-tab-close-active", "n_clicks"),
        State("scene-tabs", "value"),
        State("scene-tab-rename-input", "value"),
        prevent_initial_call=True,
    )
    def mutate_scene_tabs(_, __, ___, active_scene_id, label):
        triggered = callback_context.triggered[0]["prop_id"].split(".")[0] if callback_context.triggered else None
        if not active_scene_id:
            return no_update, no_update, no_update
        try:
            if triggered == "scene-new-tab-btn":
                scene = backend.duplicate_scene(active_scene_id)
                return backend.scene_tabs(), scene["id"], f"Duplicated scene: {scene['label']}"
            if triggered == "scene-rename-btn":
                scene = backend.update_scene(active_scene_id, {"label": label or ""})
                return backend.scene_tabs(), scene["id"], f"Renamed scene: {scene['label']}"
            if triggered == "scene-tab-close-active":
                if len(backend.scene_options()) <= 1:
                    return no_update, active_scene_id, "At least one scene tab must remain."
                backend.delete_scene(active_scene_id)
                return backend.scene_tabs(), backend.active_scene_id(), "Closed scene."
        except Exception as exc:
            return no_update, active_scene_id, f"Scene action failed: {exc}"
        return no_update, active_scene_id, no_update

    @app.callback(
        Output("scene-tabs", "children", allow_duplicate=True),
        Output("scene-tab-close-row", "children", allow_duplicate=True),
        Output("scene-tabs", "value", allow_duplicate=True),
        Output("status-banner", "children", allow_duplicate=True),
        Output("status-banner", "className", allow_duplicate=True),
        Output("status-dismiss-timer", "disabled", allow_duplicate=True),
        Output("status-dismiss-timer", "n_intervals", allow_duplicate=True),
        Input({"type": "tab-close", "scene_id": ALL}, "n_clicks"),
        State("scene-tabs", "value"),
        prevent_initial_call=True,
    )
    def close_scene_tab(close_clicks, active_scene_id):
        if not close_clicks or not any(close_clicks):
            return (no_update,) * 7
        triggered = getattr(callback_context, "triggered_id", None)
        if not isinstance(triggered, dict):
            return (no_update,) * 7
        scene_id = triggered.get("scene_id")
        if not scene_id:
            return (no_update,) * 7
        if len(backend.scene_options()) <= 1:
            message, class_name = _status_message("At least one scene tab must remain.", "warning")
            return no_update, no_update, active_scene_id, message, class_name, False, 0
        try:
            backend.delete_scene(scene_id)
        except Exception as exc:
            message, class_name = _status_message(f"Scene action failed: {exc}", "error")
            return no_update, no_update, active_scene_id, message, class_name, False, 0
        message, class_name = _status_message("Closed scene.", "success")
        return backend.scene_tabs(), backend.scene_close_buttons(), backend.active_scene_id(), message, class_name, False, 0

    @app.callback(
        Output("scene-tab-close-row", "children", allow_duplicate=True),
        Input("scene-tabs", "children"),
        prevent_initial_call=True,
    )
    def refresh_scene_close_buttons(_):
        return backend.scene_close_buttons()

    @app.callback(
        Output("status-banner", "children", allow_duplicate=True),
        Output("status-banner", "className", allow_duplicate=True),
        Output("status-dismiss-timer", "disabled", allow_duplicate=True),
        Output("status-dismiss-timer", "n_intervals", allow_duplicate=True),
        Input("status", "children"),
        prevent_initial_call=True,
    )
    def mirror_legacy_status(message):
        if not message:
            return no_update, no_update, no_update, no_update
        text = str(message)
        level = "success"
        lowered = text.lower()
        if "failed" in lowered or "error" in lowered:
            level = "error"
        elif "must" in lowered or "warning" in lowered:
            level = "warning"
        return text, _status_class(level), False, 0

    # IMPORTANT: tab-switching (scene-tabs.value) and the agent-state
    # poll (agent-state-poll.n_intervals) MUST share one callback that
    # writes to the control props below. Splitting them into two
    # callbacks -- with one using allow_duplicate=True -- triggers a
    # Dash 2.18 bug where the *user-event* listener on every prop in
    # the duplicate set is silently disabled: checkboxes, sliders and
    # dropdowns still update the DOM but their onChange never reaches
    # the server, so ``capture_state`` never fires. Concretely we saw
    # all of Labels/Display Scope/Material/Style/Disorder turn into
    # dead UI while the figure froze. Keeping a single non-duplicate
    # writer per prop restores the dispatch.
    @app.callback(
        Output("scene-tabs", "children", allow_duplicate=True),
        Output("scene-tabs", "value", allow_duplicate=True),
        Output("scene-tab-rename-input", "value"),
        Output("display-mode-selector", "value"),
        Output("display-options", "value"),
        Output("atom-scale-slider", "value"),
        Output("bond-radius-slider", "value"),
        Output("minor-opacity-slider", "value"),
        Output("material-selector", "value"),
        Output("style-selector", "value"),
        Output("disorder-selector", "value"),
        Output("ortep-mode-selector", "value"),
        Output("axis-scale-slider", "value"),
        Output("topology-species", "value"),
        Output("topology-site-index", "value"),
        Output("topology-toggle", "value"),
        Output("topology-hull-color", "value"),
        Output("agent-state-store", "data"),
        Output("camera-state-store", "data"),
        Input("agent-state-poll", "n_intervals"),
        Input("scene-tabs", "value"),
        prevent_initial_call=True,
    )
    def sync_agent_state(_n_intervals, scene_id):
        triggered = (
            callback_context.triggered[0]["prop_id"].split(".")[0]
            if callback_context.triggered
            else None
        )
        if triggered == "scene-tabs":
            if not scene_id:
                return (no_update,) * 19
            backend.set_active_scene(scene_id, broadcast=False)
            state = backend.get_state(scene_id)
            return (
                no_update,
                no_update,
                *scene_control_outputs(state),
            )
        state = backend.pop_pending_state()
        if not state:
            return (no_update,) * 19
        # Defence-in-depth against the camera-snap-back bug: even when
        # the poll path legitimately picks up an externally-driven
        # state change (REST agent, WebSocket, scene CRUD), do NOT push
        # the stored camera back into ``camera-state-store``. The
        # browser already owns the camera; overwriting it with whatever
        # was last captured (potentially several seconds stale because
        # of Plotly's relayout debouncing) yanks the user's view
        # mid-rotation. ``capture_camera`` is the single writer for
        # camera-state-store on the UI path; the REST surface should
        # use the dedicated ``/api/v2/camera`` endpoint to push a
        # camera change to the browser.
        outputs = list(scene_control_outputs(state))
        outputs[-1] = no_update  # camera-state-store slot
        return (
            backend.scene_tabs(),
            state.get("scene_id") or backend.active_scene_id(),
            *outputs,
        )

    @app.callback(
        Output("agent-state-store", "data", allow_duplicate=True),
        Input("scene-tabs", "value"),
        Input("display-mode-selector", "value"),
        Input("display-options", "value"),
        Input("atom-scale-slider", "value"),
        Input("bond-radius-slider", "value"),
        Input("minor-opacity-slider", "value"),
        Input("material-selector", "value"),
        Input("style-selector", "value"),
        Input("disorder-selector", "value"),
        Input("ortep-mode-selector", "value"),
        Input("axis-scale-slider", "value"),
        Input("topology-species", "value"),
        Input("topology-site-index", "value"),
        Input("topology-toggle", "value"),
        Input("topology-hull-color", "value"),
        prevent_initial_call=True,
    )
    def capture_state(
        scene_id,
        display_mode,
        display_options,
        atom_scale,
        bond_radius,
        minor_opacity,
        material,
        render_style,
        disorder,
        ortep_mode,
        axis_scale,
        species_keys,
        site_index,
        topology_toggle,
        topology_hull_color,
    ):
        triggered = callback_context.triggered[0]["prop_id"].split(".")[0] if callback_context.triggered else None
        if triggered == "scene-tabs":
            return no_update
        if scene_id:
            backend.set_active_scene(scene_id, broadcast=False)
        patch: dict[str, Any] = {
            "scene_id": scene_id,
            "display_mode": display_mode,
            "display_options": display_options,
            "atom_scale": atom_scale,
            "bond_radius": bond_radius,
            "minor_opacity": minor_opacity,
            "material": material or "mesh",
            "style": render_style or "ball_stick",
            "disorder": disorder or "outline_rings",
            "ortep_mode": ortep_mode or "ortep_axes",
            "axis_scale": axis_scale,
            "topology_species_keys": list(species_keys or []),
            "topology_site_index": None if site_index in ("", None) else int(site_index),
            "topology_enabled": "enabled" in (topology_toggle or []),
            "topology_hull_color": topology_hull_color or "#7C5CBF",
            "fast_rendering": material == "flat",
        }
        # Skip the write -- and the cascade through ``update_view`` --
        # if every captured field already matches the persisted state.
        # The chain ``Labels click -> capture_state -> agent-state-store
        # -> refresh_fragment_options -> topology-site-index.value ->
        # capture_state -> agent-state-store`` would otherwise double up
        # every figure render, doubling the 1.4 MB-per-frame cost.
        prev = backend.get_state(scene_id)
        if all(prev.get(k) == v for k, v in patch.items() if k != "scene_id"):
            return no_update
        backend.record_state(patch)
        return backend.get_state()

    # ------------------------------------------------------------------
    # Phase 3 UI: Named-polyhedra table.
    #
    # ONE callback handles Add / Delete (pattern-matched) / inline edit
    # (pattern-matched ALL inputs) / scene-change. Dispatch is by
    # ``callback_context.triggered_id``:
    #
    # - "polyhedra-add-btn" / "scene-tabs" -> rebuild children from
    #   backend state (the inline ALL inputs are stale during these
    #   triggers because the row count just changed).
    # - dict with "type": "poly-row-delete" -> remove the row whose
    #   spec_id is in the triggered_id, rebuild children.
    # - dict with "type": "poly-row-color" / "...-center" / "...-ligand"
    #   / "...-enabled" -> reconstruct the spec list from the live ALL
    #   inputs, persist via ``patch_state``, return ``no_update`` so we
    #   don't tear down the row React keys mid-edit.
    # ------------------------------------------------------------------
    @app.callback(
        Output("polyhedra-rows-container", "children", allow_duplicate=True),
        Input("polyhedra-add-btn", "n_clicks"),
        Input("scene-tabs", "value"),
        Input({"type": "poly-row-color", "spec_id": ALL}, "value"),
        Input({"type": "poly-row-center", "spec_id": ALL}, "value"),
        Input({"type": "poly-row-ligand", "spec_id": ALL}, "value"),
        Input({"type": "poly-row-enabled", "spec_id": ALL}, "value"),
        Input({"type": "poly-row-delete", "spec_id": ALL}, "n_clicks"),
        State({"type": "poly-row-color", "spec_id": ALL}, "id"),
        prevent_initial_call=True,
    )
    def manage_polyhedra(
        add_clicks,
        active_scene_id,
        colors,
        centers,
        ligands,
        enableds,
        deletes,
        color_ids,
    ):
        triggered = getattr(callback_context, "triggered_id", None)
        scene_id = active_scene_id or backend.active_scene_id()
        species_options = backend.species_options(
            backend.get_state(scene_id).get("structure")
        )

        def _rebuild():
            specs = backend.list_polyhedron_specs(scene_id=scene_id)
            return _polyhedra_table_rows(specs, species_options)

        if triggered == "scene-tabs":
            return _rebuild()

        if triggered == "polyhedra-add-btn":
            if not species_options:
                return _rebuild()
            try:
                backend.add_polyhedron_spec(
                    center_species=str(species_options[0]["value"]),
                    enabled=True,
                    scene_id=scene_id,
                )
            except Exception:
                return no_update
            return _rebuild()

        if isinstance(triggered, dict) and triggered.get("type") == "poly-row-delete":
            spec_id = triggered.get("spec_id")
            if not spec_id:
                return no_update
            backend.remove_polyhedron_spec(spec_id, scene_id=scene_id)
            return _rebuild()

        if isinstance(triggered, dict) and triggered.get("type", "").startswith("poly-row-"):
            # Inline edit. Reconstruct the full spec list from the
            # current ALL-input values and persist it. We rely on
            # ``color_ids`` (one id-dict per row) to give us the spec_id
            # ordering that matches the value lists.
            if not color_ids:
                return no_update
            existing = {
                spec["id"]: spec
                for spec in backend.list_polyhedron_specs(scene_id=scene_id)
            }
            new_specs: list[dict[str, Any]] = []
            for index, id_dict in enumerate(color_ids):
                spec_id = id_dict.get("spec_id")
                base = existing.get(spec_id, {})
                ligand_value = ligands[index] if index < len(ligands) else _AUTO_LIGAND_VALUE
                if ligand_value == _AUTO_LIGAND_VALUE:
                    ligand_value = None
                new_specs.append(
                    {
                        "id": spec_id,
                        "name": base.get("name") or "",
                        "color": colors[index] if index < len(colors) else base.get("color"),
                        "center_species": centers[index] if index < len(centers) else base.get("center_species"),
                        "ligand_species": ligand_value,
                        "enabled": "yes" in (enableds[index] if index < len(enableds) else []),
                    }
                )
            try:
                backend.patch_state({"polyhedron_specs": new_specs}, scene_id=scene_id)
            except Exception:
                pass
            return no_update

        return no_update

    # ------------------------------------------------------------------
    # Phase 3 UI: Atom-groups table.
    #
    # Same pattern as the polyhedra callback, plus three quick-preset
    # buttons (Monochrome / Hide H / Clear all) that translate to
    # backend CRUD calls.
    # ------------------------------------------------------------------
    @app.callback(
        Output("atom-groups-rows-container", "children", allow_duplicate=True),
        Input("atom-groups-add-btn", "n_clicks"),
        Input("atom-groups-preset-mono", "n_clicks"),
        Input("atom-groups-preset-hide-h", "n_clicks"),
        Input("atom-groups-clear-btn", "n_clicks"),
        Input("scene-tabs", "value"),
        Input({"type": "ag-row-visible", "group_id": ALL}, "value"),
        Input({"type": "ag-row-color", "group_id": ALL}, "value"),
        Input({"type": "ag-row-kind", "group_id": ALL}, "value"),
        Input({"type": "ag-row-elements", "group_id": ALL}, "value"),
        Input({"type": "ag-row-opacity", "group_id": ALL}, "value"),
        Input({"type": "ag-row-delete", "group_id": ALL}, "n_clicks"),
        State({"type": "ag-row-color", "group_id": ALL}, "id"),
        prevent_initial_call=True,
    )
    def manage_atom_groups(
        add_clicks,
        mono_clicks,
        hide_h_clicks,
        clear_clicks,
        active_scene_id,
        visibles,
        colors,
        kinds,
        elements_lists,
        opacities,
        deletes,
        color_ids,
    ):
        triggered = getattr(callback_context, "triggered_id", None)
        scene_id = active_scene_id or backend.active_scene_id()

        def _rebuild():
            groups = backend.list_atom_groups(scene_id=scene_id)
            return _atom_groups_table_rows(
                groups, backend.element_options(backend.get_state(scene_id))
            )

        if triggered == "scene-tabs":
            return _rebuild()

        if triggered == "atom-groups-add-btn":
            try:
                backend.add_atom_group(selector={"all": True}, color="#888888", scene_id=scene_id)
            except Exception:
                return no_update
            return _rebuild()

        if triggered == "atom-groups-preset-mono":
            backend.add_atom_group(
                selector={"all": True},
                color="#000000",
                name="monochrome",
                scene_id=scene_id,
            )
            return _rebuild()

        if triggered == "atom-groups-preset-hide-h":
            backend.add_atom_group(
                selector={"elements": ["H"]},
                visible=False,
                name="hide H",
                scene_id=scene_id,
            )
            return _rebuild()

        if triggered == "atom-groups-clear-btn":
            for group in list(backend.list_atom_groups(scene_id=scene_id)):
                backend.remove_atom_group(group["id"], scene_id=scene_id)
            return _rebuild()

        if isinstance(triggered, dict) and triggered.get("type") == "ag-row-delete":
            group_id = triggered.get("group_id")
            if not group_id:
                return no_update
            backend.remove_atom_group(group_id, scene_id=scene_id)
            return _rebuild()

        if isinstance(triggered, dict) and triggered.get("type", "").startswith("ag-row-"):
            if not color_ids:
                return no_update
            new_groups: list[dict[str, Any]] = []
            for index, id_dict in enumerate(color_ids):
                group_id = id_dict.get("group_id")
                kind_value = kinds[index] if index < len(kinds) else _ATOM_GROUP_KIND_ALL
                if kind_value == _ATOM_GROUP_KIND_ALL:
                    selector = {"all": True}
                else:
                    selector = {
                        "elements": list(elements_lists[index]) if index < len(elements_lists) and elements_lists[index] else []
                    }
                opacity_value = opacities[index] if index < len(opacities) else 1.0
                opacity_payload = None if opacity_value is None or float(opacity_value) >= 0.999 else float(opacity_value)
                new_groups.append(
                    {
                        "id": group_id,
                        "selector": selector,
                        "color": colors[index] if index < len(colors) else None,
                        "visible": "yes" in (visibles[index] if index < len(visibles) else ["yes"]),
                        "opacity": opacity_payload,
                    }
                )
            try:
                backend.patch_state({"atom_groups": new_groups}, scene_id=scene_id)
            except Exception:
                pass
            # Special case: switching kind from "all" -> "by element"
            # needs to reveal the elements multi-select that's
            # display:none in the existing DOM. Rebuild children to
            # update the visibility toggle.
            if triggered.get("type") == "ag-row-kind":
                return _rebuild()
            return no_update

        return no_update

    @app.callback(
        Output("camera-state-store", "data", allow_duplicate=True),
        Input("crystal-graph", "relayoutData"),
        State("camera-state-store", "data"),
        State("scene-tabs", "value"),
        prevent_initial_call=True,
    )
    def capture_camera(relayout_data, camera_state, scene_id):
        camera = _camera_from_relayout_data(
            relayout_data,
            _camera_from_store(camera_state, scene_id) or backend.get_state(scene_id).get("camera"),
        )
        if not camera:
            return no_update
        # ``broadcast=False`` is essential here: the browser is the
        # source of truth for the camera, so we must NOT arm
        # ``pending_state`` -- otherwise the next 5 s ``agent-state-poll``
        # echoes this camera back through ``sync_agent_state`` ->
        # ``camera-state-store`` -> ``update_view`` and the figure
        # re-renders with whatever camera was captured at that exact
        # moment, snapping the user's view back periodically. See
        # ``tests/app/test_camera_capture_no_poll_echo.py``.
        backend.patch_state({"camera": camera}, scene_id=scene_id, broadcast=False)
        return _camera_store_payload(scene_id, camera)

    @app.callback(
        Output("minor-opacity-slider", "disabled"),
        Output("minor-opacity-control", "style"),
        Input("disorder-selector", "value"),
    )
    def gate_minor_opacity(disorder):
        return _minor_opacity_disabled(disorder), _minor_opacity_control_style(disorder)

    @app.callback(
        Output("crystal-graph", "figure"),
        Output("topology-histogram", "figure"),
        Output("topology-results", "children"),
        Output("structure-summary", "children"),
        Input("agent-state-store", "data"),
        State("camera-state-store", "data"),
    )
    def update_view(
        agent_state,
        camera_state,
    ):
        state = backend.normalize_state(agent_state or backend.get_state())
        camera = _camera_from_store(camera_state, state.get("scene_id"))
        if camera:
            state["camera"] = camera
        fig, topology_data = backend.figure_for_state(state)
        # The right-hand sidebar only changes when the *topology* state
        # or the chosen scene changes. Keep a memo on the callback
        # itself so toggling Labels / Axes / Atom Scale -- which all
        # leave the topology untouched -- skips serialising the
        # histogram + markdown + structure summary every time. Each of
        # these is only ~1-3 kB but they re-render on the client, and
        # the markdown table tear-down was visible in the CPU profile.
        topo_key = (
            state.get("scene_id"),
            state.get("structure"),
            state.get("display_mode"),
            tuple(state.get("topology_species_keys") or ()),
            state.get("topology_site_index"),
            state.get("topology_enabled"),
            "hydrogens" in (state.get("display_options") or []),
        )
        prev_key = getattr(update_view, "_topo_cache_key", None)
        if prev_key == topo_key:
            return fig, no_update, no_update, no_update
        update_view._topo_cache_key = topo_key
        summary = _structure_summary(backend.scene_for_state(state))
        return fig, topology_histogram_figure(topology_data), topology_results_markdown(topology_data), summary

    @app.callback(
        Output("status-banner", "children"),
        Output("status-banner", "className"),
        Output("export-download", "data"),
        Output("status-dismiss-timer", "disabled"),
        Output("status-dismiss-timer", "n_intervals"),
        Input("save-preset-btn", "n_clicks"),
        Input("export-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def save_or_export(_, __):
        triggered = callback_context.triggered[0]["prop_id"].split(".")[0] if callback_context.triggered else None
        if triggered == "export-btn":
            png = backend.render_current_png(backend.active_scene_id())
            scene_label = backend.get_state().get("scene_label") or "mattervis"
            filename = f"{scene_label.replace(os.sep, '_')}.png"
            message, class_name = _status_message(f"Export ready: {filename}", "success")
            return message, class_name, dcc.send_bytes(lambda buffer: buffer.write(png), filename), False, 0
        result = backend.save_preset()
        message, class_name = _status_message(f"Saved preset: {result['path']}", "success")
        return message, class_name, no_update, False, 0

    @app.callback(
        Output("status-banner", "children", allow_duplicate=True),
        Output("status-banner", "className", allow_duplicate=True),
        Output("status-dismiss-timer", "disabled", allow_duplicate=True),
        Input("status-dismiss-timer", "n_intervals"),
        prevent_initial_call=True,
    )
    def dismiss_status(n_intervals):
        if not n_intervals:
            return no_update, no_update, no_update
        return "", _status_class("idle"), True

    register_api(app, backend)
    if str(os.environ.get("MATTERVIS_PREWARM", "0")).lower() in {"1", "true", "yes", "on"}:
        _start_cache_prewarm(backend)
    if str(os.environ.get("MATTERVIS_AUDIT", "0")).lower() in {"1", "true", "yes", "on"}:
        _install_callback_audit(app)
    return app


def _install_callback_audit(app) -> None:
    """Log every /_dash-update-component request: which inputs changed
    (changedPropIds), which output owner was targeted, plus the
    response status / payload size and the originating User-Agent
    so we can tell if a "no response" report is coming from an
    embedded webview that does not propagate React events.

    Opt-in via ``MATTERVIS_AUDIT=1``; not safe for production
    because it parses every request body."""
    import sys

    import flask

    server = app.server

    @server.before_request
    def _before():
        flask.g._mv_t0 = time.perf_counter()

    @server.after_request
    def _after(response):
        if flask.request.path != "/_dash-update-component":
            return response
        try:
            payload = flask.request.get_json(silent=True) or {}
            changed = payload.get("changedPropIds") or []
        except Exception:
            changed = []
        # Sample polls 1/100 so the log stays useful; always log everything else.
        if changed == ["agent-state-poll.n_intervals"]:
            counter = getattr(flask.g, "_mv_poll_n", 0) + 1
            try:
                flask.g._mv_poll_n = counter
            except Exception:
                pass
            if counter % 100 != 1:
                return response
        t0 = getattr(flask.g, "_mv_t0", None)
        dt_ms = ((time.perf_counter() - t0) * 1000.0) if t0 is not None else -1.0
        ip = flask.request.headers.get("X-Forwarded-For") or flask.request.remote_addr or "?"
        ua = (flask.request.headers.get("User-Agent") or "?")[:80]
        out_id = payload.get("output", "")[:120]
        try:
            resp_len = len(response.get_data())
        except Exception:
            resp_len = -1
        sys.stdout.write(
            f"[mv-audit] ip={ip} ua={ua!r} {dt_ms:7.1f}ms status={response.status_code} resp={resp_len}B "
            f"changed={changed} out={out_id}\n"
        )
        sys.stdout.flush()
        return response


def _start_cache_prewarm(backend: ViewerBackend) -> None:
    """Warm expensive scene / mesh caches after the Dash app is ready.

    Structure and display-scope switching feels slow mostly on the first
    visit to a dense unit cell: building the scene, sphere/cylinder Mesh3d
    arrays, and Plotly trace dicts can cost several seconds for PEP.  The
    renderer already has warm-path caches; this background pass simply fills
    them for the structures that were explicitly loaded at startup or via
    upload, without changing the current UI state.
    """

    def _worker():
        # Let the initial server-side figure finish before trickling through
        # heavier display scopes. The prewarm thread is opt-in via
        # MATTERVIS_PREWARM=1 so it cannot steal CPU from the default first
        # interaction path.
        ready = getattr(backend, "_first_figure_ready", None)
        if ready is not None:
            ready.wait(timeout=1.5)
        else:
            time.sleep(1.5)
        names = list(backend.bundles.keys())
        for name in names:
            try:
                bundle = backend.get_bundle(name)
            except Exception:
                continue
            defaults = backend.default_state(name)
            for display_mode in ("formula_unit", "asymmetric_unit", "unit_cell"):
                old_scene = bundle.scene
                try:
                    scene = build_bundle_scene(
                        bundle,
                        display_mode=display_mode,
                        show_hydrogen=False,
                        preset=backend.preset,
                    )
                    style = dict(scene.get("style", {}))
                    style.update(
                        style_from_controls(
                            defaults["atom_scale"],
                            defaults["bond_radius"],
                            defaults["minor_opacity"],
                            defaults["axis_scale"],
                            defaults["display_options"],
                        )
                    )
                    style["display_mode"] = display_mode
                    style["fast_rendering"] = bool(defaults.get("fast_rendering", False))
                    # Warm atom/bond mesh payloads first. Topology overlays are
                    # cached on demand because selected species/site can vary.
                    style["topology_enabled"] = False
                    build_figure(scene, style, topology_data=None)
                    # Also warm the default topology path for this display
                    # scope, then restore the visible bundle.scene pointer so
                    # metadata for the currently selected view does not jump
                    # around while the background thread is working.
                    state = dict(defaults)
                    state["display_mode"] = display_mode
                    backend.figure_for_state(state)
                except Exception:
                    continue
                finally:
                    bundle.scene = old_scene

    thread = threading.Thread(target=_worker, name="mattervis-cache-prewarm", daemon=True)
    thread.start()


def _build_parser():
    parser = argparse.ArgumentParser(description="Standalone crystal viewer with topology analysis.")
    parser.add_argument("--preset", default=DEFAULT_PRESET_PATH, help="Preset JSON to load and save.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    parser.add_argument("--port", type=int, default=8051, help="Port to expose.")
    parser.add_argument("--structure", nargs="*", help="Serve only selected catalog structure(s).")
    parser.add_argument(
        "--cif",
        action="append",
        default=[],
        help="Optional CIF path to preload. Repeat the flag to preload multiple files: --cif a.cif --cif b.cif.",
    )
    parser.add_argument("--api-only", action="store_true", help="Reserved for automation mode; still serves the same app.")
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    app = create_app(args.preset, names=args.structure, root_dir=WORKSPACE_DIR, cif_paths=args.cif or [])
    print(f"Serving crystal viewer at http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
