from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import io
import json
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
from molcrys_kit.utils.geometry import minimum_image_distance

try:
    from dash import ALL, Dash, Input, Output, Patch, State, callback_context, dcc, html, no_update
except ImportError as exc:  # pragma: no cover - user-facing fallback
    raise SystemExit(
        "Dash is required for the browser viewer. "
        "Install it with `python -m pip install dash`."
    ) from exc

from . import perf_log
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
from .scenes import Scene, SceneStore
from .topology import analyze_topology, extract_coordination_shell


PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = workspace_root(PACKAGE_DIR)
DEFAULT_PRESET_PATH = default_preset_path(WORKSPACE_DIR)
LEGACY_EXPORT_MODULE = "crystal_viewer.legacy.plot_crystal"
PLACEHOLDER_STRUCTURE = "__upload__"


class ApiError(RuntimeError):
    """Exception with an HTTP status that REST handlers can surface."""

    status_code = 400

    def __init__(self, message: str, *, hint: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.hint = hint
        if status_code is not None:
            self.status_code = int(status_code)


class TopologyUnavailable(ApiError):
    status_code = 409


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
    instance_overrides = _coerce_instance_overrides(raw.get("instance_overrides"))
    return {
        "id": spec_id,
        "name": name,
        "center_species": center,
        # ``None`` persists for API compatibility but is not rendered by the
        # MCK molecule-level path; explicit strings lock the spec to that
        # ligand species formula.
        "ligand_species": ligand,
        "color": color,
        "enabled": enabled,
        # Per-fragment override map: ``{fragment_label: {color: "#hex",
        # visible: bool}}``. Empty dict means every fragment matched by
        # this spec inherits the spec-level colour and visibility.
        "instance_overrides": instance_overrides,
    }


def _coerce_instance_overrides(raw: Any) -> dict[str, dict[str, Any]]:
    """Validate the per-fragment override map on a polyhedron spec.

    Accepts ``{fragment_label: {color: "#hex", visible: bool}}`` or
    a list of ``{label: ..., color: ..., visible: ...}`` entries.
    Unknown keys on each entry are dropped silently. Returns an
    empty dict when the input is empty / malformed.
    """
    if raw is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    if isinstance(raw, dict):
        items = raw.items()
        for label, entry in items:
            if not isinstance(entry, dict):
                continue
            label_key = str(label)
            cleaned: dict[str, Any] = {}
            color = entry.get("color")
            if color:
                hex_color = _coerce_hex_color(color, "")
                if hex_color:
                    cleaned["color"] = hex_color
            if "visible" in entry:
                cleaned["visible"] = bool(entry["visible"])
            if cleaned:
                out[label_key] = cleaned
    elif isinstance(raw, (list, tuple)):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            label = str(entry.get("label") or entry.get("center_label") or "").strip()
            if not label:
                continue
            cleaned = {}
            color = entry.get("color")
            if color:
                hex_color = _coerce_hex_color(color, "")
                if hex_color:
                    cleaned["color"] = hex_color
            if "visible" in entry:
                cleaned["visible"] = bool(entry["visible"])
            if cleaned:
                out[label] = cleaned
    return out


# Selector keys we accept on atom-group rules. Anything outside this
# set is silently dropped so a forward-compatible UI can post extra
# experimental fields without breaking the persisted state schema.
_ATOM_SELECTOR_KEYS = (
    "all",
    "elements",
    "is_minor",
    "labels",
    "atom_indices",
    "fragment_labels",
    "fragment_indices",
)
_ATOM_GROUP_VALID_MATERIALS = {"mesh", "flat"}
_ATOM_GROUP_VALID_STYLES = {"ball", "ball_stick", "stick", "ortep", "wireframe"}

_BOND_SELECTOR_KEYS = ("all", "between_elements", "labels", "is_minor")


def _coerce_atom_selector(raw: Any) -> Optional[dict[str, Any]]:
    """Validate an atom-group selector dict.

    Supports today:
    - ``{"all": True}`` -- match every atom in the scene.
    - ``{"elements": ["O", "S"]}`` -- element symbol filter.
    - ``{"is_minor": True/False}`` -- disorder major/minor flag.
    - ``{"labels": ["Pb1", "Cl3"]}`` -- exact atom label list (post-Phase 4
      AI API affordance: the selector that survived a structure transform
      and remains valid against the manifested atom list).
    - ``{"atom_indices": [0, 5]}`` -- exact 0-based atom-index list in
      the current ``draw_atoms`` (volatile across transforms; for
      one-shot interactive picks).
    - ``{"fragment_labels": ["B0", "X3"]}`` -- match every atom whose
      fragment-table label is in the list (used by the per-instance
      polyhedron override pipeline when the user wants atoms inside
      a specific polyhedron repainted as a group).
    - ``{"fragment_indices": [2]}`` -- by 0-based fragment index.

    Multiple keys are combined with logical AND -- e.g.
    ``{"elements": ["Pb"], "is_minor": False}`` means "major Pb atoms
    only". The matcher in :mod:`crystal_viewer.atom_groups` enforces
    this; see ``atom_matches_selector``.
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
    labels = raw.get("labels")
    if isinstance(labels, (list, tuple)):
        cleaned = [str(item) for item in labels if item is not None and str(item).strip()]
        if cleaned:
            selector["labels"] = cleaned
    atom_indices = raw.get("atom_indices")
    if isinstance(atom_indices, (list, tuple)):
        cleaned_idx: list[int] = []
        for item in atom_indices:
            try:
                cleaned_idx.append(int(item))
            except (TypeError, ValueError):
                continue
        if cleaned_idx:
            selector["atom_indices"] = cleaned_idx
    fragment_labels = raw.get("fragment_labels")
    if isinstance(fragment_labels, (list, tuple)):
        cleaned = [str(item) for item in fragment_labels if item is not None and str(item).strip()]
        if cleaned:
            selector["fragment_labels"] = cleaned
    fragment_indices = raw.get("fragment_indices")
    if isinstance(fragment_indices, (list, tuple)):
        cleaned_fi: list[int] = []
        for item in fragment_indices:
            try:
                cleaned_fi.append(int(item))
            except (TypeError, ValueError):
                continue
        if cleaned_fi:
            selector["fragment_indices"] = cleaned_fi
    return selector or None


def _coerce_bond_selector(raw: Any) -> Optional[dict[str, Any]]:
    """Validate a bond-group selector dict.

    Supports today:
    - ``{"all": True}`` -- every bond.
    - ``{"between_elements": ["O", "H"]}`` -- bonds whose endpoint
      elements form an unordered match against the listed pair / triple.
      A length-2 list matches O-H or H-O; a length-1 list (e.g. ``["O"]``)
      matches O-X for any second element. Length 3+ uses a "both ends in
      the set" rule (useful for picking out e.g. M-X bonds where X is
      a halide family).
    - ``{"labels": ["Pb1-Cl3"]}`` -- exact bond identifier strings
      (constructed as ``"<atom_i_label>-<atom_j_label>"`` in label order).
    - ``{"is_minor": True/False}`` -- pick by major/minor flag (matches
      the bond-level ``is_minor`` field already on the scene).
    """
    if not isinstance(raw, dict):
        return None
    selector: dict[str, Any] = {}
    if raw.get("all"):
        selector["all"] = True
    between_elements = raw.get("between_elements")
    if isinstance(between_elements, (list, tuple)):
        cleaned = [str(item) for item in between_elements if item is not None and str(item).strip()]
        if cleaned:
            selector["between_elements"] = cleaned
    labels = raw.get("labels")
    if isinstance(labels, (list, tuple)):
        cleaned = [str(item) for item in labels if item is not None and str(item).strip()]
        if cleaned:
            selector["labels"] = cleaned
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


_BOND_GROUP_FALLBACK_COLOR = "#7C5CBF"


def _bond_group_default_name(selector: dict[str, Any]) -> str:
    if selector.get("all"):
        return "all bonds"
    parts = []
    if "between_elements" in selector:
        parts.append("/".join(selector["between_elements"]))
    if "labels" in selector:
        parts.append(f"{len(selector['labels'])} labels")
    if "is_minor" in selector:
        parts.append("minor" if selector["is_minor"] else "major")
    return " ".join(parts) or "bond group"


def _normalize_bond_group(
    raw: Any,
    *,
    existing_ids: set[str],
    fallback_color: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Coerce one user payload into the canonical bond-group shape.

    The shape mirrors :func:`_normalize_atom_group`. Fields:
    - ``id``: stable id (auto-generated if missing).
    - ``name``: display label.
    - ``selector``: dict (see :func:`_coerce_bond_selector`).
    - ``color``: hex string (None means inherit per-atom bond colours).
    - ``visible``: bool.
    - ``opacity``: float in [0, 1] or None.
    - ``radius_scale``: float >0 multiplier on the ``style.bond_radius``.
    """
    if not isinstance(raw, dict):
        return None
    selector = _coerce_bond_selector(raw.get("selector"))
    if selector is None:
        return None
    group_id = str(raw.get("id") or "").strip()
    if not group_id or group_id in existing_ids:
        group_id = f"bgrp_{uuid.uuid4().hex[:10]}"
        while group_id in existing_ids:  # pragma: no cover - astronomically unlikely
            group_id = f"bgrp_{uuid.uuid4().hex[:10]}"
    existing_ids.add(group_id)
    name = str(raw.get("name") or _bond_group_default_name(selector)).strip() or "bond group"
    color = _coerce_hex_color(raw.get("color"), fallback_color) if raw.get("color") else None
    visible = bool(raw.get("visible", True))
    opacity = _coerce_optional_float(raw.get("opacity"))
    radius_scale = raw.get("radius_scale")
    try:
        radius_value = float(radius_scale) if radius_scale is not None else None
    except (TypeError, ValueError):
        radius_value = None
    if radius_value is not None:
        radius_value = max(0.05, min(8.0, radius_value))
    return {
        "id": group_id,
        "name": name,
        "selector": selector,
        "color": color,
        "visible": visible,
        "opacity": opacity,
        "radius_scale": radius_value,
    }


def _normalize_bond_groups(raw_groups: Any) -> list[dict[str, Any]]:
    if raw_groups is None or not isinstance(raw_groups, (list, tuple)):
        return []
    out: list[dict[str, Any]] = []
    existing_ids: set[str] = set()
    for raw in raw_groups:
        group = _normalize_bond_group(raw, existing_ids=existing_ids)
        if group is not None:
            out.append(group)
    return out


_TRANSFORM_KIND_NAMES = {
    "repeat": "Repeat",
    "grow_radius": "Grow by radius",
    "grow_bonds": "Grow by bonds",
    "complete_fragment": "Complete fragments",
    "complete_polyhedron": "Complete polyhedra",
    "by_symmetry": "Apply symmetry",
    "slab": "Slab",
}


def _normalize_transform(
    raw: Any,
    *,
    existing_ids: set[str],
) -> Optional[dict[str, Any]]:
    """Coerce one transform payload into the canonical spec shape.

    See :mod:`crystal_viewer.transforms` for the full schema. We
    validate the ``kind`` and trust the kind-specific dispatcher to
    coerce its own params; the only non-trivial coercion done here is
    the ``seeds`` selector (reuses the atom-group selector grammar).
    """
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip()
    from .transforms import KNOWN_TRANSFORM_KINDS

    if kind not in KNOWN_TRANSFORM_KINDS:
        return None
    transform_id = str(raw.get("id") or "").strip()
    if not transform_id or transform_id in existing_ids:
        transform_id = f"trf_{uuid.uuid4().hex[:10]}"
        while transform_id in existing_ids:  # pragma: no cover - astronomically unlikely
            transform_id = f"trf_{uuid.uuid4().hex[:10]}"
    existing_ids.add(transform_id)
    name = str(raw.get("name") or _TRANSFORM_KIND_NAMES.get(kind, kind)).strip() or kind
    enabled = bool(raw.get("enabled", True))
    params_raw = raw.get("params") or {}
    if not isinstance(params_raw, dict):
        params_raw = {}
    params: dict[str, Any] = {}
    if kind == "repeat":
        for axis in ("a", "b", "c"):
            try:
                params[axis] = max(1, int(params_raw.get(axis, 1) or 1))
            except (TypeError, ValueError):
                params[axis] = 1
    elif kind in ("grow_radius", "grow_bonds", "complete_fragment", "complete_polyhedron", "by_symmetry"):
        seeds = _coerce_atom_selector(params_raw.get("seeds"))
        params["seeds"] = seeds or {}
        if kind == "grow_radius":
            try:
                params["radius"] = max(0.0, float(params_raw.get("radius", 0.0) or 0.0))
            except (TypeError, ValueError):
                params["radius"] = 0.0
        elif kind == "grow_bonds":
            try:
                params["hops"] = max(0, int(params_raw.get("hops", 1) or 1))
            except (TypeError, ValueError):
                params["hops"] = 1
        elif kind == "complete_fragment":
            try:
                params["max_hops"] = max(1, int(params_raw.get("max_hops", 32) or 32))
            except (TypeError, ValueError):
                params["max_hops"] = 32
        elif kind == "complete_polyhedron":
            try:
                params["cutoff"] = max(0.0, float(params_raw.get("cutoff", 4.0) or 4.0))
            except (TypeError, ValueError):
                params["cutoff"] = 4.0
        elif kind == "by_symmetry":
            ops_in = params_raw.get("ops") or []
            ops_out: list[list[Any]] = []
            for op in ops_in:
                if not isinstance(op, (list, tuple)) or len(op) != 2:
                    continue
                R, t = op
                try:
                    R_arr = [[float(x) for x in row] for row in R]
                    t_arr = [float(x) for x in t]
                    if len(R_arr) == 3 and all(len(row) == 3 for row in R_arr) and len(t_arr) == 3:
                        ops_out.append([R_arr, t_arr])
                except (TypeError, ValueError):
                    continue
            params["ops"] = ops_out
    elif kind == "slab":
        miller_raw = params_raw.get("miller") or [1, 0, 0]
        try:
            miller = [int(x) for x in miller_raw]
        except (TypeError, ValueError):
            miller = [1, 0, 0]
        if len(miller) != 3:
            miller = [1, 0, 0]
        params["miller"] = miller
        layers = params_raw.get("layers")
        if layers is not None:
            try:
                params["layers"] = max(1, int(layers))
            except (TypeError, ValueError):
                params["layers"] = None
        else:
            params["layers"] = None
        min_thickness = params_raw.get("min_thickness")
        if min_thickness is not None:
            try:
                params["min_thickness"] = max(0.0, float(min_thickness))
            except (TypeError, ValueError):
                params["min_thickness"] = None
        else:
            params["min_thickness"] = None
        try:
            params["vacuum"] = max(0.0, float(params_raw.get("vacuum", 10.0) or 10.0))
        except (TypeError, ValueError):
            params["vacuum"] = 10.0
    return {
        "id": transform_id,
        "name": name,
        "kind": kind,
        "params": params,
        "enabled": enabled,
    }


def _normalize_transforms(raw_transforms: Any) -> list[dict[str, Any]]:
    if raw_transforms is None or not isinstance(raw_transforms, (list, tuple)):
        return []
    out: list[dict[str, Any]] = []
    existing_ids: set[str] = set()
    for raw in raw_transforms:
        transform = _normalize_transform(raw, existing_ids=existing_ids)
        if transform is not None:
            out.append(transform)
    return out


def _legacy_monochrome_group(existing_ids: set[str]) -> dict[str, Any]:
    """Synthesize an atom_group representing the legacy ``monochrome``
    flag: paint every atom black. Used by ``normalize_state`` to
    auto-migrate old presets so the renderer has a single source of
    truth (atom_groups) and the ``monochrome`` flag becomes inert.
    """
    return _normalize_atom_group(
        {
            "selector": {"all": True},
            "color": "#000000",
            "name": "monochrome (migrated)",
        },
        existing_ids=existing_ids,
    )


# Phase 3 UI: per-row rendering helpers for the left-panel
# Polyhedra and Atom-group tables. Returns Dash component lists; the
# panel wires up an "Add" button and ALL/MATCH callbacks for inline
# edits + deletes (see ``_polyhedra_row_helpers`` and the matching
# block of ``register_callbacks``).
_AUTO_LIGAND_VALUE = "__auto__"


# Bucket boundaries (in milliseconds) used by the perf-log panel to
# colour-code event durations. Keep small so the user can spot
# ``> 500 ms`` red rows at a glance during a slow interaction.
_PERF_FAST_MS = 50.0
_PERF_SLOW_MS = 500.0


def _perf_log_row(entry: dict[str, Any]) -> Any:
    """Render one perf-log event as a Dash row.

    Layout: ``[hh:mm:ss.mmm] [label] [duration ms] [info kv pairs]``
    The duration cell is coloured green / amber / red based on
    ``_PERF_FAST_MS`` / ``_PERF_SLOW_MS`` so slow events pop out
    visually.
    """
    iso = entry.get("iso", "")
    clock = iso.split("T", 1)[1] if "T" in iso else iso
    label = entry.get("label", "")
    ms = entry.get("ms")
    if ms is None:
        ms_text = ""
        ms_class = "perf-log-ms perf-log-ms--none"
    else:
        ms_text = f"{ms:6.1f} ms"
        if ms < _PERF_FAST_MS:
            ms_class = "perf-log-ms perf-log-ms--fast"
        elif ms < _PERF_SLOW_MS:
            ms_class = "perf-log-ms perf-log-ms--mid"
        else:
            ms_class = "perf-log-ms perf-log-ms--slow"
    info = entry.get("info") or {}
    info_pairs = []
    for key, value in info.items():
        if isinstance(value, (list, tuple)):
            value = ",".join(str(v) for v in value[:3]) + ("…" if len(value) > 3 else "")
        text = str(value)
        if len(text) > 36:
            text = text[:33] + "…"
        info_pairs.append(f"{key}={text}")
    info_text = " ".join(info_pairs)
    return html.Div(
        [
            html.Span(clock, className="perf-log-clock"),
            html.Span(label, className="perf-log-label"),
            html.Span(ms_text, className=ms_class),
            html.Span(info_text, className="perf-log-info"),
        ],
        className="perf-log-row",
    )


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
_ATOM_GROUP_KIND_MINOR = "minor"
_ATOM_GROUP_KIND_MAJOR = "major"
_ATOM_GROUP_INHERIT = "__inherit__"


def _selector_kind(selector: dict[str, Any]) -> str:
    if selector.get("all"):
        return _ATOM_GROUP_KIND_ALL
    if "is_minor" in selector and "elements" not in selector:
        return _ATOM_GROUP_KIND_MINOR if selector["is_minor"] else _ATOM_GROUP_KIND_MAJOR
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
                                    {"label": "minor only", "value": _ATOM_GROUP_KIND_MINOR},
                                    {"label": "major only", "value": _ATOM_GROUP_KIND_MAJOR},
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
                    html.Div(
                        [
                            html.Span("material", style={"fontSize": "11px", "color": "#666", "marginRight": "4px"}),
                            dcc.Dropdown(
                                id={"type": "ag-row-material", "group_id": group["id"]},
                                options=[
                                    {"label": "(scene default)", "value": _ATOM_GROUP_INHERIT},
                                    {"label": "mesh (3D)", "value": "mesh"},
                                    {"label": "flat (2D)", "value": "flat"},
                                ],
                                value=group.get("material") or _ATOM_GROUP_INHERIT,
                                clearable=False,
                                style={"flex": "1", "fontSize": "12px"},
                            ),
                            html.Span("style", style={"fontSize": "11px", "color": "#666", "marginLeft": "8px", "marginRight": "4px"}),
                            dcc.Dropdown(
                                id={"type": "ag-row-style", "group_id": group["id"]},
                                options=[
                                    {"label": "(scene default)", "value": _ATOM_GROUP_INHERIT},
                                    {"label": "ball+stick", "value": "ball_stick"},
                                    {"label": "ball", "value": "ball"},
                                    {"label": "stick", "value": "stick"},
                                    {"label": "ortep", "value": "ortep"},
                                    {"label": "wireframe", "value": "wireframe"},
                                ],
                                value=group.get("style") or _ATOM_GROUP_INHERIT,
                                clearable=False,
                                style={"flex": "1", "fontSize": "12px"},
                            ),
                        ],
                        style={"display": "flex", "alignItems": "center", "marginTop": "4px", "padding": "0 4px"},
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


# --- Phase 4 UI: transforms + bond_groups + polyhedron search supercell ---
#
# These mirror the polyhedra/atom_groups table pattern: each row gets
# pattern-matched ``{type, transform_id|group_id}`` ids so a single
# ALL-input callback handles add/edit/delete dispatching by
# ``callback_context.triggered_id``. The widgets are deliberately minimal --
# the contract is "every backend feature is reachable from the UI", not
# "the UI is pretty". Polished forms can land later; the right-click /
# keyboard layer can also push selectors into the same text inputs used
# here.

_BOND_GROUP_KIND_ALL = "all"
_BOND_GROUP_KIND_BETWEEN = "between"
_BOND_GROUP_KIND_MINOR = "minor"
_BOND_GROUP_KIND_MAJOR = "major"


def _bond_selector_kind(selector: dict[str, Any]) -> str:
    if selector.get("all"):
        return _BOND_GROUP_KIND_ALL
    if selector.get("between_elements"):
        return _BOND_GROUP_KIND_BETWEEN
    if "is_minor" in selector:
        return _BOND_GROUP_KIND_MINOR if selector["is_minor"] else _BOND_GROUP_KIND_MAJOR
    return _BOND_GROUP_KIND_ALL


def _bond_selector_elements_text(selector: dict[str, Any]) -> list[str]:
    return [str(x) for x in (selector.get("between_elements") or [])]


def _bond_groups_table_rows(
    groups: list[dict[str, Any]],
    element_options: list[dict[str, Any]],
):
    """One row of dash inputs per bond-group rule. Same pattern-match
    scheme as ``_atom_groups_table_rows`` but with bond-specific
    selectors (all / between elements / minor / major) and bond-specific
    style fields (color / opacity / radius_scale).
    """
    from dash import dcc, html

    if not groups:
        return [
            html.Div(
                "No bond-group rules. Click \u201cAdd\u201d or right-click a bond to start.",
                style={"fontSize": "12px", "color": "#777", "margin": "6px 0"},
            )
        ]
    rows = []
    for group in groups:
        selector = group.get("selector") or {}
        kind = _bond_selector_kind(selector)
        between_values = _bond_selector_elements_text(selector)
        rows.append(
            html.Div(
                [
                    html.Div(
                        [
                            dcc.Checklist(
                                id={"type": "bg-row-visible", "group_id": group["id"]},
                                options=[{"label": "", "value": "yes"}],
                                value=["yes"] if group.get("visible", True) else [],
                                style={"display": "inline-block"},
                            ),
                            dcc.Input(
                                id={"type": "bg-row-color", "group_id": group["id"]},
                                type="color",
                                value=str(group.get("color") or _BOND_GROUP_FALLBACK_COLOR),
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
                                id={"type": "bg-row-kind", "group_id": group["id"]},
                                options=[
                                    {"label": "all bonds", "value": _BOND_GROUP_KIND_ALL},
                                    {"label": "between elements", "value": _BOND_GROUP_KIND_BETWEEN},
                                    {"label": "minor only", "value": _BOND_GROUP_KIND_MINOR},
                                    {"label": "major only", "value": _BOND_GROUP_KIND_MAJOR},
                                ],
                                value=kind,
                                clearable=False,
                                style={"flex": "1", "marginLeft": "4px", "minWidth": "100px", "fontSize": "12px"},
                            ),
                            dcc.Dropdown(
                                id={"type": "bg-row-elements", "group_id": group["id"]},
                                options=element_options,
                                value=between_values if kind == _BOND_GROUP_KIND_BETWEEN else [],
                                multi=True,
                                placeholder="Pick 1\u20132 elements (e.g. Pb, Cl)",
                                style={
                                    "flex": "2",
                                    "marginLeft": "4px",
                                    "minWidth": "120px",
                                    "fontSize": "12px",
                                    "display": "block" if kind == _BOND_GROUP_KIND_BETWEEN else "none",
                                },
                            ),
                            html.Button(
                                "\u00d7",
                                id={"type": "bg-row-delete", "group_id": group["id"]},
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
                                title="Remove this bond-group rule",
                            ),
                        ],
                        style={"display": "flex", "alignItems": "center", "gap": "2px"},
                    ),
                    html.Div(
                        [
                            html.Span("opacity", style={"fontSize": "11px", "color": "#666"}),
                            dcc.Slider(
                                id={"type": "bg-row-opacity", "group_id": group["id"]},
                                min=0.0,
                                max=1.0,
                                step=0.05,
                                value=float(group.get("opacity") if group.get("opacity") is not None else 1.0),
                                marks={0.0: "0", 0.5: "0.5", 1.0: "1"},
                                tooltip={"placement": "bottom", "always_visible": False},
                                updatemode="mouseup",
                                included=True,
                            ),
                        ],
                        style={"marginTop": "4px", "padding": "0 4px"},
                    ),
                    html.Div(
                        [
                            html.Span("radius \u00d7", style={"fontSize": "11px", "color": "#666"}),
                            dcc.Slider(
                                id={"type": "bg-row-radius", "group_id": group["id"]},
                                min=0.1,
                                max=3.0,
                                step=0.1,
                                value=float(group.get("radius_scale") if group.get("radius_scale") is not None else 1.0),
                                marks={0.5: "0.5", 1.0: "1", 2.0: "2"},
                                tooltip={"placement": "bottom", "always_visible": False},
                                updatemode="mouseup",
                                included=True,
                            ),
                        ],
                        style={"marginTop": "4px", "padding": "0 4px"},
                    ),
                ],
                style={
                    "padding": "6px 4px",
                    "marginBottom": "6px",
                    "border": "1px solid #EEE",
                    "borderRadius": "4px",
                    "background": "#FAFAFA",
                },
            )
        )
    return rows


# Seed-selector text format used in the Transforms UI rows. The text input
# accepts (case-insensitive):
#   - ``"all"``                 -> {"all": true}
#   - ``"elem:Pb,Cl"`` / ``"el:Pb"`` -> {"elements": ["Pb","Cl"]}
#   - ``"label:Pb1,Cl3"`` / ``"lab:Pb1"`` -> {"labels": ["Pb1","Cl3"]}
#   - ``"index:0,5"`` / ``"idx:0,5"`` -> {"atom_indices": [0,5]}
#   - ``"frag:A0"`` / ``"fragment:A0"`` -> {"fragment_labels": ["A0"]}
# Bare comma-separated values (no prefix) are treated as ``elements``
# because that is the most common case for AI scripting.

def _seed_selector_to_text(seeds: dict[str, Any] | None) -> str:
    if not isinstance(seeds, dict) or not seeds:
        return ""
    if seeds.get("all"):
        return "all"
    if seeds.get("elements"):
        return "elem:" + ",".join(str(x) for x in seeds["elements"])
    if seeds.get("labels"):
        return "label:" + ",".join(str(x) for x in seeds["labels"])
    if seeds.get("atom_indices"):
        return "index:" + ",".join(str(x) for x in seeds["atom_indices"])
    if seeds.get("fragment_labels"):
        return "frag:" + ",".join(str(x) for x in seeds["fragment_labels"])
    return ""


def _seed_text_to_selector(text: Any) -> dict[str, Any]:
    if text is None:
        return {}
    raw = str(text).strip()
    if not raw:
        return {}
    if raw.lower() == "all":
        return {"all": True}
    if ":" in raw:
        prefix, rest = raw.split(":", 1)
        prefix = prefix.strip().lower()
        values = [v.strip() for v in rest.split(",") if v.strip()]
        if not values:
            return {}
        if prefix in ("elem", "element", "elements", "el"):
            return {"elements": values}
        if prefix in ("label", "labels", "lab"):
            return {"labels": values}
        if prefix in ("index", "indices", "idx", "atom_index"):
            try:
                return {"atom_indices": [int(v) for v in values]}
            except ValueError:
                return {}
        if prefix in ("frag", "fragment", "fragment_labels"):
            return {"fragment_labels": values}
        return {}
    # No prefix: treat as element list (common AI / quick-typing case).
    values = [v.strip() for v in raw.split(",") if v.strip()]
    return {"elements": values} if values else {}


def _transform_param_widgets(transform: dict[str, Any]) -> list[Any]:
    """Build the per-kind parameter widgets for one transform row.

    All widgets carry ``{type: "trf-param-<field>", transform_id: ...}``
    ids so the parent callback can identify them. The set of widgets is
    chosen per ``kind``; absent fields render as nothing so the row
    height stays predictable per transform kind.
    """
    from dash import dcc, html

    transform_id = transform["id"]
    kind = transform.get("kind") or "repeat"
    params = transform.get("params") or {}
    children: list[Any] = []
    if kind == "repeat":
        for axis in ("a", "b", "c"):
            children.append(
                html.Span(axis, style={"fontSize": "11px", "color": "#666", "margin": "0 2px 0 6px"})
            )
            children.append(
                dcc.Input(
                    id={"type": f"trf-param-{axis}", "transform_id": transform_id},
                    type="number",
                    min=1,
                    step=1,
                    value=int(params.get(axis, 1) or 1),
                    style={"width": "50px", "fontSize": "12px"},
                    debounce=True,
                )
            )
    elif kind in ("grow_radius", "grow_bonds", "complete_fragment", "complete_polyhedron", "by_symmetry"):
        children.append(
            html.Span("seeds", style={"fontSize": "11px", "color": "#666", "marginRight": "4px"})
        )
        children.append(
            dcc.Input(
                id={"type": "trf-param-seeds", "transform_id": transform_id},
                type="text",
                value=_seed_selector_to_text(params.get("seeds")),
                placeholder="elem:Pb  /  label:Pb1  /  all",
                style={"flex": "1", "minWidth": "100px", "fontSize": "12px"},
                debounce=True,
            )
        )
        if kind == "grow_radius":
            children.extend([
                html.Span("\u00c5", style={"fontSize": "11px", "color": "#666", "margin": "0 2px 0 6px"}),
                dcc.Input(
                    id={"type": "trf-param-radius", "transform_id": transform_id},
                    type="number",
                    min=0.0,
                    step=0.1,
                    value=float(params.get("radius", 0.0) or 0.0),
                    style={"width": "60px", "fontSize": "12px"},
                    debounce=True,
                ),
            ])
        elif kind == "grow_bonds":
            children.extend([
                html.Span("hops", style={"fontSize": "11px", "color": "#666", "margin": "0 2px 0 6px"}),
                dcc.Input(
                    id={"type": "trf-param-hops", "transform_id": transform_id},
                    type="number",
                    min=0,
                    step=1,
                    value=int(params.get("hops", 1) or 1),
                    style={"width": "50px", "fontSize": "12px"},
                    debounce=True,
                ),
            ])
        elif kind == "complete_fragment":
            children.extend([
                html.Span("max hops", style={"fontSize": "11px", "color": "#666", "margin": "0 2px 0 6px"}),
                dcc.Input(
                    id={"type": "trf-param-maxhops", "transform_id": transform_id},
                    type="number",
                    min=1,
                    step=1,
                    value=int(params.get("max_hops", 32) or 32),
                    style={"width": "50px", "fontSize": "12px"},
                    debounce=True,
                ),
            ])
        elif kind == "complete_polyhedron":
            children.extend([
                html.Span("cutoff \u00c5", style={"fontSize": "11px", "color": "#666", "margin": "0 2px 0 6px"}),
                dcc.Input(
                    id={"type": "trf-param-cutoff", "transform_id": transform_id},
                    type="number",
                    min=0.0,
                    step=0.1,
                    value=float(params.get("cutoff", 4.0) or 4.0),
                    style={"width": "60px", "fontSize": "12px"},
                    debounce=True,
                ),
            ])
        elif kind == "by_symmetry":
            # JSON ops textarea -- power-user / AI path. Empty = no ops.
            import json as _json
            ops_json = _json.dumps(params.get("ops") or [])
            children.append(
                dcc.Textarea(
                    id={"type": "trf-param-ops", "transform_id": transform_id},
                    value=ops_json,
                    placeholder='[[[[r11,r12,r13],[r21,r22,r23],[r31,r32,r33]],[tx,ty,tz]], ...]',
                    style={"width": "100%", "minHeight": "40px", "fontSize": "11px", "fontFamily": "monospace", "marginTop": "4px"},
                ),
            )
    elif kind == "slab":
        miller = params.get("miller") or [1, 0, 0]
        for i, axis in enumerate(("h", "k", "l")):
            children.append(
                html.Span(axis, style={"fontSize": "11px", "color": "#666", "margin": "0 2px 0 6px"})
            )
            children.append(
                dcc.Input(
                    id={"type": f"trf-param-miller-{i}", "transform_id": transform_id},
                    type="number",
                    step=1,
                    value=int(miller[i] if i < len(miller) else 0),
                    style={"width": "44px", "fontSize": "12px"},
                    debounce=True,
                )
            )
        children.extend([
            html.Span("layers", style={"fontSize": "11px", "color": "#666", "margin": "0 2px 0 6px"}),
            dcc.Input(
                id={"type": "trf-param-layers", "transform_id": transform_id},
                type="number",
                min=1,
                step=1,
                value=int(params.get("layers") or 3),
                style={"width": "50px", "fontSize": "12px"},
                debounce=True,
            ),
            html.Span("vacuum \u00c5", style={"fontSize": "11px", "color": "#666", "margin": "0 2px 0 6px"}),
            dcc.Input(
                id={"type": "trf-param-vacuum", "transform_id": transform_id},
                type="number",
                min=0.0,
                step=0.5,
                value=float(params.get("vacuum", 10.0) or 10.0),
                style={"width": "60px", "fontSize": "12px"},
                debounce=True,
            ),
        ])
    return children


def _transforms_table_rows(transforms: list[dict[str, Any]]):
    """One row per transform spec. Each row carries the kind label,
    enabled/delete controls, and a kind-specific parameter line."""
    from dash import dcc, html

    if not transforms:
        return [
            html.Div(
                "No transforms. Use the Add menu below to repeat the cell, grow by radius, slab, ...",
                style={"fontSize": "12px", "color": "#777", "margin": "6px 0"},
            )
        ]
    rows = []
    for index, transform in enumerate(transforms):
        kind = transform.get("kind") or "repeat"
        rows.append(
            html.Div(
                [
                    html.Div(
                        [
                            html.Span(
                                f"{index + 1}.",
                                style={"fontSize": "11px", "color": "#888", "marginRight": "4px", "minWidth": "16px"},
                            ),
                            html.Span(
                                _TRANSFORM_KIND_NAMES.get(kind, kind),
                                style={
                                    "fontSize": "12px",
                                    "fontWeight": "bold",
                                    "color": "#444",
                                    "marginRight": "6px",
                                    "flex": "1",
                                },
                            ),
                            dcc.Checklist(
                                id={"type": "trf-row-enabled", "transform_id": transform["id"]},
                                options=[{"label": "", "value": "yes"}],
                                value=["yes"] if transform.get("enabled", True) else [],
                                style={"display": "inline-block", "marginLeft": "4px"},
                            ),
                            html.Button(
                                "\u25b2",
                                id={"type": "trf-row-up", "transform_id": transform["id"]},
                                n_clicks=0,
                                disabled=index == 0,
                                style={
                                    "background": "transparent",
                                    "border": "1px solid #DDD",
                                    "color": "#666",
                                    "padding": "0 4px",
                                    "cursor": "pointer" if index > 0 else "not-allowed",
                                    "lineHeight": "18px",
                                    "borderRadius": "3px",
                                    "marginLeft": "2px",
                                    "fontSize": "11px",
                                },
                                title="Move earlier in the pipeline",
                            ),
                            html.Button(
                                "\u25bc",
                                id={"type": "trf-row-down", "transform_id": transform["id"]},
                                n_clicks=0,
                                disabled=index >= len(transforms) - 1,
                                style={
                                    "background": "transparent",
                                    "border": "1px solid #DDD",
                                    "color": "#666",
                                    "padding": "0 4px",
                                    "cursor": "pointer" if index < len(transforms) - 1 else "not-allowed",
                                    "lineHeight": "18px",
                                    "borderRadius": "3px",
                                    "marginLeft": "2px",
                                    "fontSize": "11px",
                                },
                                title="Move later in the pipeline",
                            ),
                            html.Button(
                                "\u00d7",
                                id={"type": "trf-row-delete", "transform_id": transform["id"]},
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
                                title="Remove this transform",
                            ),
                        ],
                        style={"display": "flex", "alignItems": "center"},
                    ),
                    html.Div(
                        _transform_param_widgets(transform),
                        style={
                            "display": "flex",
                            "alignItems": "center",
                            "flexWrap": "wrap",
                            "gap": "2px",
                            "marginTop": "4px",
                        },
                    ),
                ],
                style={
                    "padding": "6px 4px",
                    "marginBottom": "6px",
                    "border": "1px solid #EEE",
                    "borderRadius": "4px",
                    "background": "#F8F8FB",
                },
            )
        )
    return rows


# Dispatch table for right-click + keyboard actions. The Dash callback
# resolves which mutation to run; this helper does the actual backend
# calls so the callback stays a thin shim. ``target`` is the full
# rightclick-target store payload; ``payload`` is shorthand for
# ``target.get("payload")``. Optional kwargs (``color``, ``radius``,
# ``hops``) are passed through from the popover / keyboard layer.
def _dispatch_rightclick_action(
    backend: Any,
    scene_id: Optional[str],
    action: str,
    kind: Optional[str],
    payload: dict[str, Any],
    target: dict[str, Any],
    *,
    color: Optional[str] = None,
    radius: Optional[float] = None,
    hops: Optional[int] = None,
) -> None:
    if action in ("supercell_2x", "supercell_clear"):
        n = 2 if action == "supercell_2x" else 1
        backend.patch_state(
            {"supercell": {"a": n, "b": n, "c": n}},
            scene_id=scene_id,
        )
        return

    if not kind or kind == "_global":
        # Bare keyboard shortcut without a hovered target. ``r`` /
        # ``R`` were handled above; everything else needs a target.
        return

    if action == "hide":
        if kind == "atom":
            label = payload.get("label")
            if not label:
                return
            backend.add_atom_group(
                selector={"labels": [str(label)]},
                color="#888888",
                visible=False,
                name=f"hide {label}",
                scene_id=scene_id,
            )
        elif kind == "polyhedron":
            spec_id = payload.get("spec_id")
            frag = payload.get("fragment_label")
            if not spec_id or not frag:
                return
            existing = backend.list_polyhedron_specs(scene_id=scene_id)
            base = next((s for s in existing if s["id"] == spec_id), None)
            if base is None:
                return
            overrides = dict(base.get("instance_overrides") or {})
            overrides[str(frag)] = dict(overrides.get(str(frag), {}), visible=False)
            backend.update_polyhedron_spec(
                spec_id, {"instance_overrides": overrides}, scene_id=scene_id
            )
        elif kind == "bond":
            elements = payload.get("element_pair") or ""
            parts = [p.strip() for p in str(elements).split("\u2013") if p.strip()] or [
                p.strip() for p in str(elements).split("-") if p.strip()
            ]
            selector = (
                {"between_elements": parts}
                if len(parts) == 2
                else {"labels": [str(payload.get("label_pair") or "")]}
            )
            backend.add_bond_group(
                selector=selector,
                color="#888888",
                visible=False,
                name=f"hide {elements or 'bond'}",
                scene_id=scene_id,
            )
        return

    if action == "set_color":
        if not color:
            return
        if kind == "atom":
            label = payload.get("label")
            if not label:
                return
            backend.add_atom_group(
                selector={"labels": [str(label)]},
                color=color,
                visible=True,
                name=f"colour {label}",
                scene_id=scene_id,
            )
        elif kind == "polyhedron":
            spec_id = payload.get("spec_id")
            frag = payload.get("fragment_label")
            if not spec_id or not frag:
                return
            existing = backend.list_polyhedron_specs(scene_id=scene_id)
            base = next((s for s in existing if s["id"] == spec_id), None)
            if base is None:
                return
            overrides = dict(base.get("instance_overrides") or {})
            overrides[str(frag)] = dict(overrides.get(str(frag), {}), color=color)
            backend.update_polyhedron_spec(
                spec_id, {"instance_overrides": overrides}, scene_id=scene_id
            )
        elif kind == "bond":
            elements = payload.get("element_pair") or ""
            parts = [p.strip() for p in str(elements).split("\u2013") if p.strip()] or [
                p.strip() for p in str(elements).split("-") if p.strip()
            ]
            selector = (
                {"between_elements": parts}
                if len(parts) == 2
                else {"labels": [str(payload.get("label_pair") or "")]}
            )
            backend.add_bond_group(
                selector=selector,
                color=color,
                visible=True,
                name=f"colour {elements or 'bond'}",
                scene_id=scene_id,
            )
        return

    if action == "grow_bonds":
        seeds = _seeds_from_payload(kind, payload)
        if seeds is None:
            return
        n_hops = int(target.get("hops") or hops or 1)
        backend.add_transform(
            kind="grow_bonds",
            params={"seeds": seeds, "hops": max(1, n_hops)},
            scene_id=scene_id,
        )
        return

    if action == "grow_radius":
        seeds = _seeds_from_payload(kind, payload)
        if seeds is None:
            return
        r = float(target.get("radius") or radius or 4.0)
        backend.add_transform(
            kind="grow_radius",
            params={"seeds": seeds, "radius": max(0.0, r)},
            scene_id=scene_id,
        )
        return

    if action == "complete_fragment":
        seeds = _seeds_from_payload(kind, payload)
        if seeds is None:
            return
        backend.add_transform(
            kind="complete_fragment",
            params={"seeds": seeds, "max_hops": 32},
            scene_id=scene_id,
        )
        return

    if action == "promote_to_group":
        if kind == "atom":
            elem = payload.get("element")
            if not elem:
                return
            backend.add_atom_group(
                selector={"elements": [str(elem)]},
                color="#888888",
                visible=True,
                name=f"all {elem}",
                scene_id=scene_id,
            )
        elif kind == "bond":
            elements = payload.get("element_pair") or ""
            parts = [p.strip() for p in str(elements).split("\u2013") if p.strip()] or [
                p.strip() for p in str(elements).split("-") if p.strip()
            ]
            if len(parts) != 2:
                return
            backend.add_bond_group(
                selector={"between_elements": parts},
                color="#7C5CBF",
                visible=True,
                name=f"all {parts[0]}\u2013{parts[1]}",
                scene_id=scene_id,
            )
        elif kind == "polyhedron":
            # Already a polyhedron spec -- promotion would be a no-op.
            return
        return

    if action == "colour_picker":
        # Keyboard shortcut: just leave the popover open; the JS will
        # not have created the popover yet, so we open one centred on
        # the screen by re-pushing target with no action.
        # The render callback ignores the absence of x/y and uses
        # 0/0 -- good enough for this MVP.
        return

    if action == "analyze":
        # Sets the topology focus to this fragment so the right-side
        # analysis panel updates. We piggyback on the existing
        # ``topology_site_index`` state field (per scene) -- the
        # update_view callback already re-renders when it changes.
        if kind == "atom":
            atom_index = payload.get("index")
            if atom_index is None:
                return
            try:
                state = backend.get_state(scene_id)
                site_index = backend.fragment_index_for_atom(
                    backend.scene_for_state(state), int(atom_index)
                )
                if site_index is not None:
                    backend.patch_state(
                        {"topology_site_index": int(site_index)}, scene_id=scene_id
                    )
            except Exception:
                pass
        elif kind == "polyhedron":
            # The picked-payload already came from the topology side;
            # promoting the spec to "topology focus" is a no-op for
            # now.
            pass
        return


def _seeds_from_payload(kind: Optional[str], payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    if kind == "atom":
        idx = payload.get("index")
        if idx is None:
            label = payload.get("label")
            if label:
                return {"labels": [str(label)]}
            return None
        return {"atom_indices": [int(idx)]}
    if kind == "polyhedron":
        frag = payload.get("fragment_label")
        if frag:
            return {"fragment_labels": [str(frag)]}
        return None
    if kind == "bond":
        # Bonds aren't atoms, but we can seed from the constituent
        # element pair as a coarse fallback.
        elements = payload.get("element_pair") or ""
        parts = [p.strip() for p in str(elements).split("\u2013") if p.strip()] or [
            p.strip() for p in str(elements).split("-") if p.strip()
        ]
        if parts:
            return {"elements": parts}
        return None
    return None


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


def _camera_payload(
    eye: np.ndarray,
    center: np.ndarray,
    up: np.ndarray,
    *,
    projection: Optional[str] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "eye": {"x": float(eye[0]), "y": float(eye[1]), "z": float(eye[2])},
        "center": {"x": float(center[0]), "y": float(center[1]), "z": float(center[2])},
        "up": {"x": float(up[0]), "y": float(up[1]), "z": float(up[2])},
    }
    if projection is not None:
        payload["projection"] = {"type": str(projection)}
    return payload


# ---------------------------------------------------------------------
# Axis-aligned camera presets (VESTA-style "down a / b / c / a* / b* / c*")
# ---------------------------------------------------------------------
#
# ``M`` carries the lattice vectors as rows (M[0] = a, etc.).
# Fractional coordinates are row vectors (cart = frac @ M), so reciprocal
# vectors live in the columns of M^-1. ``camera_for_axis`` picks
# a unit view direction along the requested axis, picks an "up"
# reference axis from the remaining lattice vectors (real-space when
# the request is real-space, reciprocal-space when reciprocal), and
# uses Gram-Schmidt to orthogonalise that "up" against the view
# direction so non-orthogonal cells still produce a sane camera. The
# ``eye`` magnitude is preserved across alignments so the user's zoom
# level survives an axis switch.

_AXIS_VIEW_KEYS = ("a", "b", "c", "a*", "b*", "c*")


def _normalize_axis_key(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower().replace(" ", "")
    if text in {"a", "b", "c", "a*", "b*", "c*"}:
        return text
    # Tolerate alternative forms: "astar", "a-star", "areciprocal"
    aliases = {
        "astar": "a*", "a-star": "a*", "areciprocal": "a*", "a_reciprocal": "a*",
        "bstar": "b*", "b-star": "b*", "breciprocal": "b*", "b_reciprocal": "b*",
        "cstar": "c*", "c-star": "c*", "creciprocal": "c*", "c_reciprocal": "c*",
    }
    return aliases.get(text)


def _lattice_axes(M: np.ndarray) -> dict[str, np.ndarray]:
    """Return unit vectors for a, b, c, a*, b*, c* derived from
    cartesian row-lattice matrix ``M`` (rows = a, b, c)."""
    M_arr = np.asarray(M, dtype=float)
    if M_arr.shape != (3, 3):
        raise ValueError(f"expected 3x3 lattice matrix, got shape {M_arr.shape}")
    real_rows = [M_arr[i] for i in range(3)]
    try:
        recip = np.linalg.inv(M_arr)
    except np.linalg.LinAlgError as exc:
        raise ValueError("lattice matrix is singular; cannot build reciprocal axes") from exc
    recip_cols = [recip[:, i] for i in range(3)]
    out: dict[str, np.ndarray] = {}
    for key, vec in zip(("a", "b", "c"), real_rows):
        norm = float(np.linalg.norm(vec))
        out[key] = vec / norm if norm > 1e-12 else np.array([1.0, 0.0, 0.0])
    for key, vec in zip(("a*", "b*", "c*"), recip_cols):
        norm = float(np.linalg.norm(vec))
        out[key] = vec / norm if norm > 1e-12 else np.array([1.0, 0.0, 0.0])
    return out


def _orthogonalise_up(view_dir: np.ndarray, up_pick: np.ndarray) -> np.ndarray:
    """Project ``up_pick`` onto the plane perpendicular to ``view_dir``
    (Gram-Schmidt) and normalise. Falls back to a canonical up if the
    pick is degenerate (parallel to view_dir)."""
    proj = up_pick - float(np.dot(up_pick, view_dir)) * view_dir
    norm = float(np.linalg.norm(proj))
    if norm < 1e-9:
        # ``up_pick`` is parallel to ``view_dir`` -- pick the closest
        # canonical world axis that isn't.
        for fallback in (
            np.array([0.0, 0.0, 1.0]),
            np.array([0.0, 1.0, 0.0]),
            np.array([1.0, 0.0, 0.0]),
        ):
            proj = fallback - float(np.dot(fallback, view_dir)) * view_dir
            norm = float(np.linalg.norm(proj))
            if norm > 1e-9:
                break
    return proj / norm


def camera_for_axis(
    M: np.ndarray,
    axis: str,
    *,
    eye_distance: float = 1.8,
    center: Optional[np.ndarray] = None,
    projection: Optional[str] = None,
) -> dict[str, Any]:
    """Build a Plotly camera dict that looks down the requested axis.

    ``axis`` is one of ``a``, ``b``, ``c``, ``a*``, ``b*``, ``c*``.
    The camera ``up`` follows the VESTA convention:

    - looking down ``a``, ``b``  -> up = c (orthogonalised vs. view)
    - looking down ``c``         -> up = b
    - looking down ``a*``, ``b*`` -> up = c*
    - looking down ``c*``        -> up = b*

    Non-orthogonal cells go through Gram-Schmidt so the up is always
    perpendicular to the view direction; degenerate picks fall back
    to a canonical world axis.
    """
    key = _normalize_axis_key(axis)
    if key is None:
        raise ValueError(f"unknown axis: {axis!r}; pick one of {_AXIS_VIEW_KEYS}")
    axes = _lattice_axes(M)
    view_dir = axes[key]
    # VESTA-style up choice: pick the lattice axis that gives the
    # most useful "up" -- conventionally ``c`` for in-plane views,
    # ``b`` for the [001] view. Reciprocal lookups stay reciprocal.
    up_pick_map_real = {"a": "c", "b": "c", "c": "b"}
    up_pick_map_recip = {"a*": "c*", "b*": "c*", "c*": "b*"}
    up_key = up_pick_map_real.get(key) or up_pick_map_recip[key]
    up = _orthogonalise_up(view_dir, axes[up_key])
    center_arr = np.array([0.0, 0.0, 0.0]) if center is None else np.asarray(center, dtype=float)
    eye = center_arr + float(eye_distance) * view_dir
    return _camera_payload(eye, center_arr, up, projection=projection)


_VALID_PROJECTIONS = ("perspective", "orthographic")


def _coerce_projection(value: Any, *, fallback: str = "perspective") -> str:
    text = str(value or "").strip().lower()
    if text in _VALID_PROJECTIONS:
        return text
    return fallback


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
        self.server_started_at = time.time()
        self.catalog = get_default_catalog(root_dir=self.root_dir)
        self._lock = threading.Lock()
        self._bundle_lock = threading.Lock()
        default_names = [name for name in DEFAULT_CATALOG.keys() if name in self.catalog]
        requested_names = [name for name in (names or []) if name in self.catalog]
        self.structure_names = requested_names if requested_names else default_names
        if not self.structure_names:
            self.structure_names = list(self.catalog.keys())
        self.bundles: Dict[str, LoadedCrystal] = {}
        self.upload_manifest_path = os.path.join(self.root_dir, LOCAL_STATE_DIRNAME, "crystal_view_uploads.json")
        self.upload_manifest = self._load_upload_manifest()
        self._restore_uploaded_bundles()
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
        self._figure_cache: dict[str, tuple[Any, Any]] = {}
        self._figure_cache_order: list[str] = []

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
            "label_mode": str(style.get("label_mode", "unique_sites")),
            "display_mode": style.get("display_mode", scene.get("display_mode", "formula_unit")),
            "topology_species_keys": list(default_species),
            "topology_site_index": None,
            "topology_enabled": False,
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
            # supported keys are ``all``, ``elements`` (list),
            # ``is_minor``, and the Phase-4 additions ``labels``,
            # ``atom_indices``, ``fragment_labels``, ``fragment_indices``.
            # Multiple groups apply in list order with later-wins
            # semantics on overlapping atoms. Empty list = no overrides;
            # the legacy ``monochrome`` flag is still honoured when no
            # atom_groups are present. See ``agents/atom_groups_api.md``.
            "atom_groups": [],
            # Phase 4: per-scene bond-group rules. Each entry is
            # {id, name, selector, color, visible, opacity,
            # radius_scale}. Selectors support ``all``,
            # ``between_elements`` (unordered), ``labels`` (atom-pair
            # ids), and ``is_minor``. Empty list = render bonds as the
            # endpoint atoms dictate. See ``agents/bond_groups_api.md``.
            "bond_groups": [],
            # Phase 4: list of structure-mutation transforms. See
            # ``crystal_viewer.transforms`` and
            # ``agents/transforms_api.md`` for the schema. Empty list =
            # no transform; ``apply_transforms`` short-circuits.
            "transforms": [],
            "fast_rendering": bool(style.get("fast_rendering", False)),
            "camera": scene.get("camera"),
            # Phase 4: camera projection mode mirrored onto state so a
            # caller can inspect / set it via REST without diffing the
            # Plotly camera dict. ``style_for_state`` propagates this
            # to ``style["projection"]`` so the renderer picks it up.
            "projection": _coerce_projection(
                style.get("projection", "perspective"),
                fallback="perspective",
            ),
            "cutoff": 10.0,
        }

    def _bump_version(self):
        self.version += 1
        self._figure_cache.clear()
        self._figure_cache_order.clear()

    def wait_for_version(self, version: int, *, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while self.version < int(version):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        return True

    def server_started_iso(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.server_started_at))

    def healthz(self) -> dict[str, Any]:
        return {
            "ok": True,
            "uptime_s": max(0.0, time.time() - self.server_started_at),
            "server_started_at": self.server_started_iso(),
            "scenes": len(self.scene_store.scenes),
            "structures": len(self.structure_names),
            "version": self.version,
        }

    def _load_upload_manifest(self) -> dict[str, Any]:
        if not os.path.exists(self.upload_manifest_path):
            return {"version": 1, "uploads": {}}
        try:
            with open(self.upload_manifest_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return {"version": 1, "uploads": {}}
        if not isinstance(payload, dict):
            return {"version": 1, "uploads": {}}
        payload.setdefault("version", 1)
        payload.setdefault("uploads", {})
        return payload

    def _save_upload_manifest(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.upload_manifest_path)), exist_ok=True)
        with open(self.upload_manifest_path, "w", encoding="utf-8") as handle:
            json.dump(self.upload_manifest, handle, indent=2, ensure_ascii=False)

    def _restore_uploaded_bundles(self) -> None:
        uploads = self.upload_manifest.get("uploads") or {}
        changed = False
        for digest, record in list(uploads.items()):
            if not isinstance(record, dict):
                uploads.pop(digest, None)
                changed = True
                continue
            name = str(record.get("name") or "")
            path = str(record.get("path") or "")
            if not name or not path or not os.path.exists(path):
                uploads.pop(digest, None)
                changed = True
                continue
            if name in self.structure_names:
                continue
            try:
                bundle = build_loaded_crystal(
                    name=name,
                    cif_path=path,
                    title=str(record.get("title") or name),
                    preset=self.preset,
                    source="upload",
                )
            except Exception:
                uploads.pop(digest, None)
                changed = True
                continue
            self.bundles[bundle.name] = bundle
            self.structure_names.append(bundle.name)
        if changed:
            try:
                self._save_upload_manifest()
            except OSError:
                pass

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
        requested_label = label or structure
        scene = self.scene_store.add(
            label=requested_label,
            structure_name=structure,
            state_patch=base_state,
            camera=base_state.get("camera"),
        )
        self.current_state = self.scene_state(scene.id)
        self.pending_state = copy.deepcopy(self.current_state)
        self._bump_version()
        payload = scene.to_dict()
        payload["requested_label"] = str(requested_label)
        payload["label_renamed"] = payload["label"] != str(requested_label)
        return payload

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

    def get_scene_json(self, name: str, *, after_transforms: bool = False) -> dict[str, Any]:
        state = self.get_state()
        if state["structure"] != name:
            state = self.normalize_state({"structure": name})
        if not after_transforms:
            state = dict(state)
            state["transforms"] = []
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
        for key in ("material", "style", "disorder", "ortep_mode", "label_mode"):
            if key in patch and patch[key] is not None:
                state[key] = str(patch[key])
        if state.get("style") == "ortep" and "display_mode" not in patch:
            state["display_mode"] = "asymmetric_unit"
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
        if "bond_groups" in patch:
            state["bond_groups"] = _normalize_bond_groups(patch.get("bond_groups") or [])
        if "transforms" in patch:
            state["transforms"] = _normalize_transforms(patch.get("transforms") or [])
        # ``supercell`` is a v2 shorthand: ``{"a": Na, "b": Nb, "c": Nc}``
        # is rewritten to a single ``repeat`` transform appended to the
        # transforms list. Keeps the AI scripting path one-line for the
        # most common "show me a 2x2x2" request without forcing the
        # caller to construct a transform spec.
        if "supercell" in patch and patch["supercell"] is not None:
            sc = patch["supercell"]
            try:
                a = max(1, int(sc.get("a", 1) if isinstance(sc, dict) else sc[0]))
                b = max(1, int(sc.get("b", 1) if isinstance(sc, dict) else sc[1]))
                c = max(1, int(sc.get("c", 1) if isinstance(sc, dict) else sc[2]))
            except (TypeError, ValueError, KeyError, IndexError):
                a = b = c = 1
            existing = list(state.get("transforms") or [])
            # Always replace any existing repeat transform from a previous
            # supercell shorthand call instead of stacking; otherwise the AI
            # ends up with [repeat 2x2x2, repeat 3x3x3] and the user gets a
            # 6x6x6. ``{1,1,1}`` therefore acts as "clear the supercell".
            existing = [t for t in existing if t.get("kind") != "repeat"]
            if (a, b, c) != (1, 1, 1):
                existing_ids = {t["id"] for t in existing}
                normalized = _normalize_transform(
                    {"kind": "repeat", "params": {"a": a, "b": b, "c": c}, "name": f"Repeat {a}x{b}x{c}"},
                    existing_ids=existing_ids,
                )
                if normalized is not None:
                    existing.append(normalized)
            state["transforms"] = existing
        if "fast_rendering" in patch:
            state["fast_rendering"] = bool(patch["fast_rendering"])
        # ---- legacy migration: monochrome=True --> atom_group rule ----
        #
        # Old presets / agent scripts may still set ``monochrome=True``
        # on the display options (or via ``"monochrome"`` in
        # ``display_options``). Promote that to a single all-atoms
        # black ``atom_group`` so the renderer has a single source of
        # truth and the legacy flag becomes inert. Idempotent: we skip
        # if the user already has any explicit colour rule.
        wants_mono = False
        if "display_options" in patch:
            wants_mono = "monochrome" in (state.get("display_options") or [])
        existing_groups = list(state.get("atom_groups") or [])
        has_explicit_color_rule = any(g.get("color") for g in existing_groups)
        if wants_mono and not has_explicit_color_rule:
            existing_ids = {g["id"] for g in existing_groups}
            migrated = _legacy_monochrome_group(existing_ids)
            if migrated is not None:
                state["atom_groups"] = existing_groups + [migrated]
        if "camera" in patch and patch["camera"] is not None:
            state["camera"] = patch["camera"]
        # ``camera_revision`` is the uirevision-bump counter written by
        # ``camera_action`` / ``align_camera``. ``normalize_state``
        # whitelists keys, so without an explicit pass-through the
        # bump silently drops on the floor and Plotly keeps clamping
        # the figure to whatever rotation the user drag-saved last.
        if "camera_revision" in patch and patch["camera_revision"] is not None:
            try:
                state["camera_revision"] = int(patch["camera_revision"])
            except (TypeError, ValueError):
                pass
        # Phase 4 (view tools): top-level ``projection`` is a v2 state
        # key that mirrors ``camera.projection.type``. Accept either
        # spelling so AI callers don't have to dig into the camera
        # dict; ``set_projection`` keeps the two in sync.
        if "projection" in patch and patch["projection"] is not None:
            state["projection"] = _coerce_projection(
                patch["projection"], fallback=str(state.get("projection", "perspective"))
            )
        elif isinstance(patch.get("camera"), dict):
            cam_proj = patch["camera"].get("projection")
            if isinstance(cam_proj, dict) and "type" in cam_proj:
                state["projection"] = _coerce_projection(
                    cam_proj["type"], fallback=str(state.get("projection", "perspective"))
                )
        return state

    def get_state(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        with self._lock:
            if scene_id is not None:
                state = copy.deepcopy(self.scene_state(scene_id))
            else:
                state = copy.deepcopy(self.current_state)
            state["server_started_at"] = self.server_started_iso()
            state["version"] = self.version
            return state

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
            state = copy.deepcopy(self.current_state)
            state["version"] = self.version
            state["server_started_at"] = self.server_started_iso()
            return state

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
        # Phase 4: ``state["transforms"]`` is the structure-mutation
        # pipeline. ``build_bundle_scene`` short-circuits when the list
        # is empty so the no-transform path stays a single dict lookup.
        transforms = list(state.get("transforms") or [])
        scene = build_bundle_scene(
            bundle,
            display_mode=state.get("display_mode", "formula_unit"),
            show_hydrogen=self.show_hydrogen_for_state(state),
            preset=self.preset,
            transforms=transforms,
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
        style["label_mode"] = state.get("label_mode", style.get("label_mode", "unique_sites"))
        style["fast_rendering"] = bool(state.get("fast_rendering", False)) or style["material"] == "flat"
        style["topology_enabled"] = bool(state.get("topology_enabled", False))
        style["topology_hull_color"] = str(state.get("topology_hull_color", "#7C5CBF"))
        # Phase 2: per-scene atom-group rules ride along on the style
        # dict so the renderer dispatcher can partition draw_atoms by
        # (effective_material, effective_style) without touching the
        # backend layer. Renderer reads ``style["atom_groups"]`` only
        # if the list is non-empty; the legacy ``monochrome`` flag is
        # otherwise honoured untouched.
        style["atom_groups"] = list(state.get("atom_groups") or [])
        # Phase 4: bond-group rules ride on the style dict so the
        # renderer's bond pipeline (``_bond_segments``) can decorate
        # each bond with ``_render_color`` / ``_render_visible`` /
        # ``_render_opacity_scale`` / ``_render_radius_scale``. The
        # tagging itself happens in ``figure_for_state`` (where the
        # scene is mutable); this entry is the single source of truth
        # for downstream callers.
        style["bond_groups"] = list(state.get("bond_groups") or [])
        # Phase 4 (view tools): persist the camera projection choice
        # onto the style dict so the renderer's
        # ``_plotly_camera_from_scene`` picks orthographic vs.
        # perspective without rebuilding the scene.
        style["projection"] = _coerce_projection(
            state.get("projection", style.get("projection", "perspective")),
            fallback=str(style.get("projection", "perspective")),
        )
        if isinstance(state.get("camera"), dict):
            style["camera"] = copy.deepcopy(state["camera"])
        # Plotly's ``layout.scene.uirevision`` makes the WebGL camera
        # state persist across redraws -- reusing the same revision
        # means a mouse-drag rotation survives a Labels toggle. The
        # flip side: when the user clicks Reset / down-a / down-b /
        # ... the layout's new camera is silently ignored unless the
        # revision changes. ``camera_revision`` (bumped by
        # ``camera_action`` and ``align_camera``) gives the renderer
        # exactly that signal: Reset triggers a fresh revision so
        # Plotly accepts the new camera, while pan/orbit updates that
        # flow through ``patch_state`` directly leave it untouched.
        style["uirevision"] = "{name}__{rev}".format(
            name=scene.get("name", "scene"),
            rev=int(state.get("camera_revision", 0) or 0),
        )
        return style

    def add_uploaded_bundle(self, contents: str, filename: str) -> LoadedCrystal:
        # Charge the three legs (decode + parse via gemmi, register
        # bundle, create scene) separately so the perf log makes the
        # actual bottleneck obvious. Empirically the ``load_uploaded_cif``
        # call dominates for non-trivial structures (CIF parsing +
        # symmetry expansion + bond perception).
        with perf_log.time_block(
            "upload:load_uploaded_cif",
            kind="event",
            filename=filename,
            data_url_bytes=len(contents or ""),
        ):
            bundle = load_uploaded_cif(
                contents=contents,
                filename=filename,
                existing_names=self.structure_names,
                preset=self.preset,
            )
        with perf_log.time_block("upload:create_scene", kind="event", structure=bundle.name):
            self._drop_placeholder()
            self.bundles[bundle.name] = bundle
            self.structure_names.append(bundle.name)
            self.create_scene(structure=bundle.name, label=bundle.name)
            _prewarm_bundle_async(self, bundle.name)
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

        digest = hashlib.sha256(data).hexdigest()
        existing_record = (self.upload_manifest.get("uploads") or {}).get(digest)
        if isinstance(existing_record, dict):
            existing_name = existing_record.get("name")
            if existing_name in self.structure_names:
                bundle = self.get_bundle(existing_name)
                setattr(bundle, "_upload_existing", True)
                return bundle

        upload_dir = os.path.realpath(os.path.join(tempfile.gettempdir(), "crystal_viewer_uploads"))
        os.makedirs(upload_dir, exist_ok=True)
        safe = secure_filename(filename or "") or "upload.cif"
        if not safe.lower().endswith(".cif"):
            safe = f"{safe}.cif"
        path = os.path.realpath(os.path.join(upload_dir, safe))
        if os.path.commonpath([path, upload_dir]) != upload_dir:
            raise ValueError(f"unsafe upload filename: {filename!r}")
        with perf_log.time_block(
            "upload:write_temp_file",
            kind="event",
            filename=safe,
            bytes=len(data),
        ):
            with open(path, "wb") as handle:
                handle.write(data)
        stem = os.path.splitext(safe)[0]
        safe_name = stem
        suffix = 2
        while safe_name in self.structure_names:
            safe_name = f"{stem}_{suffix}"
            suffix += 1
        # ``build_loaded_crystal`` parses the CIF (gemmi), expands
        # symmetry, builds bonds and -- if the preset asks for it --
        # runs molcryskit topology analysis. For a 1.6 MB CIF this is
        # by far the slowest leg of the upload (~15 s). Charging it
        # separately makes the bottleneck unambiguous in the log.
        with perf_log.time_block(
            "upload:build_loaded_crystal",
            kind="event",
            structure=safe_name,
            cif_path=path,
        ):
            bundle = build_loaded_crystal(name=safe_name, cif_path=path, title=stem, preset=self.preset, source="upload")
        with perf_log.time_block("upload:create_scene", kind="event", structure=bundle.name):
            self._drop_placeholder()
            self.bundles[bundle.name] = bundle
            self.structure_names.append(bundle.name)
            self.create_scene(structure=bundle.name, label=bundle.name)
            _prewarm_bundle_async(self, bundle.name)
        self.upload_manifest.setdefault("uploads", {})[digest] = {
            "name": bundle.name,
            "path": path,
            "sha256": digest,
            "original_filename": filename,
            "title": stem,
        }
        try:
            self._save_upload_manifest()
        except OSError as exc:  # pragma: no cover - read-only / disk-full
            print(f"[crystal_viewer] could not persist upload manifest: {exc}", file=sys.stderr)
        setattr(bundle, "_upload_existing", False)
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
        return float(
            minimum_image_distance(
                np.array(frac_b, dtype=float),
                np.array(frac_a, dtype=float),
                np.array(bundle.M, dtype=float),
            )
        )

    def map_display_fragment_to_topology(self, bundle: LoadedCrystal, display_fragment: dict | None) -> Optional[dict[str, Any]]:
        if display_fragment is None:
            return None
        source_molecule_index = display_fragment.get("source_molecule_index")
        if source_molecule_index is not None:
            matched = next(
                (
                    fragment
                    for fragment in bundle.topology_fragment_table
                    if fragment.get("source_molecule_index") == source_molecule_index
                ),
                None,
            )
            if matched is not None:
                return matched
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
                # Phase 4: customdata schema is
                # ``[kind, idx, label, elem, is_minor, fragment_label]``.
                # We read by index 1 when the first slot is a kind tag
                # ("atom"), and fall back to index 0 for backwards
                # compatibility with any frontend payload still on the
                # legacy schema (cached page from before redeploy).
                if isinstance(custom[0], str) and len(custom) > 1:
                    atom_index_raw = custom[1]
                else:
                    atom_index_raw = custom[0]
                try:
                    atom_index = int(atom_index_raw)
                except (TypeError, ValueError):
                    return None
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

    # ---- bond_groups CRUD ---------------------------------------------
    #
    # Mirror of atom_groups CRUD; see ``agents/bond_groups_api.md``.

    def list_bond_groups(self, scene_id: Optional[str] = None) -> list[dict[str, Any]]:
        return list(self.get_state(scene_id).get("bond_groups") or [])

    def _resolve_bond_groups(self, scene_id: Optional[str]) -> tuple[Optional[str], list[dict[str, Any]]]:
        scene_id = scene_id or self.active_scene_id()
        return scene_id, [dict(group) for group in (self.get_state(scene_id).get("bond_groups") or [])]

    def add_bond_group(
        self,
        selector: dict[str, Any],
        *,
        name: Optional[str] = None,
        color: Optional[str] = None,
        visible: bool = True,
        opacity: Optional[float] = None,
        radius_scale: Optional[float] = None,
        scene_id: Optional[str] = None,
        group_id: Optional[str] = None,
    ) -> dict[str, Any]:
        scene_id, groups = self._resolve_bond_groups(scene_id)
        existing_ids = {grp["id"] for grp in groups}
        group = _normalize_bond_group(
            {
                "id": group_id,
                "name": name,
                "selector": selector,
                "color": color,
                "visible": visible,
                "opacity": opacity,
                "radius_scale": radius_scale,
            },
            existing_ids=existing_ids,
        )
        if group is None:
            raise ValueError(
                f"invalid bond_group payload (missing/empty selector?): {selector!r}"
            )
        groups.append(group)
        self.patch_state({"bond_groups": groups}, scene_id=scene_id)
        return group

    def update_bond_group(
        self,
        group_id: str,
        patch: dict[str, Any],
        *,
        scene_id: Optional[str] = None,
    ) -> dict[str, Any]:
        scene_id, groups = self._resolve_bond_groups(scene_id)
        for index, group in enumerate(groups):
            if group["id"] == group_id:
                merged = dict(group)
                merged.update(patch or {})
                merged["id"] = group_id
                replacement = _normalize_bond_group(
                    merged,
                    existing_ids={g["id"] for g in groups if g["id"] != group_id},
                )
                if replacement is None:
                    raise ValueError(
                        f"invalid bond_group patch for {group_id!r}: {patch!r}"
                    )
                groups[index] = replacement
                self.patch_state({"bond_groups": groups}, scene_id=scene_id)
                return replacement
        raise KeyError(f"unknown bond_group id: {group_id!r}")

    def remove_bond_group(self, group_id: str, *, scene_id: Optional[str] = None) -> bool:
        scene_id, groups = self._resolve_bond_groups(scene_id)
        before = len(groups)
        groups = [grp for grp in groups if grp["id"] != group_id]
        if len(groups) == before:
            return False
        self.patch_state({"bond_groups": groups}, scene_id=scene_id)
        return True

    def reorder_bond_groups(
        self,
        ordered_ids: Iterable[str],
        *,
        scene_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        scene_id, groups = self._resolve_bond_groups(scene_id)
        index_by_id = {grp["id"]: grp for grp in groups}
        wanted = [str(item) for item in ordered_ids]
        if set(wanted) != set(index_by_id):
            raise ValueError(
                "reorder list must contain exactly the existing bond_group ids; "
                f"got {wanted!r}, have {sorted(index_by_id)}"
            )
        ordered = [index_by_id[group_id] for group_id in wanted]
        self.patch_state({"bond_groups": ordered}, scene_id=scene_id)
        return ordered

    # ---- transforms CRUD ----------------------------------------------
    #
    # Mirrors atom_groups CRUD. The whole pipeline is a list; ordering
    # matters (each transform takes the result of the previous one as
    # its input scene). See ``agents/transforms_api.md``.

    def list_transforms(self, scene_id: Optional[str] = None) -> list[dict[str, Any]]:
        return list(self.get_state(scene_id).get("transforms") or [])

    def _resolve_transforms(self, scene_id: Optional[str]) -> tuple[Optional[str], list[dict[str, Any]]]:
        scene_id = scene_id or self.active_scene_id()
        return scene_id, [dict(t) for t in (self.get_state(scene_id).get("transforms") or [])]

    def add_transform(
        self,
        kind: str,
        params: Optional[dict[str, Any]] = None,
        *,
        name: Optional[str] = None,
        enabled: bool = True,
        scene_id: Optional[str] = None,
        transform_id: Optional[str] = None,
        auto_promote: bool = True,
    ) -> dict[str, Any]:
        scene_id, transforms = self._resolve_transforms(scene_id)
        existing_ids = {t["id"] for t in transforms}
        transform = _normalize_transform(
            {
                "id": transform_id,
                "name": name,
                "kind": kind,
                "params": params or {},
                "enabled": enabled,
            },
            existing_ids=existing_ids,
        )
        if transform is None:
            raise ValueError(f"invalid transform spec (unknown kind?): kind={kind!r}, params={params!r}")
        state = self.get_state(scene_id)
        warnings: list[str] = []
        promoted_from: str | None = None
        mutates_geometry = transform["kind"] in {
            "repeat",
            "grow_radius",
            "grow_bonds",
            "complete_fragment",
            "complete_polyhedron",
            "by_symmetry",
            "slab",
        }
        if mutates_geometry and state.get("display_mode") == "formula_unit":
            message = (
                "display_mode=formula_unit trims transform output; "
                "MatterVis promoted the scene to unit_cell for this transform."
            )
            warnings.append(message)
            if auto_promote:
                promoted_from = "formula_unit"
                state["display_mode"] = "unit_cell"
            else:
                warnings[-1] = (
                    "display_mode=formula_unit will trim transform output; "
                    "set display_mode=unit_cell before rendering."
                )
        if transform["kind"] == "slab":
            fragments = (self.get_bundle(state["structure"]).fragment_table or [])
            if len(fragments) > 1:
                warnings.append(
                    "slab transform on a molecular crystal can cut covalent fragments; "
                    "validate the result before using it as a surface model."
                )
        if transform["kind"] == "repeat":
            from .transforms import MAX_ATOMS_AFTER_TRANSFORM

            scene = self.scene_for_state(state)
            atom_count = len(scene.get("draw_atoms") or [])
            repeat_atoms = (
                atom_count
                * int(transform["params"].get("a", 1))
                * int(transform["params"].get("b", 1))
                * int(transform["params"].get("c", 1))
            )
            if repeat_atoms > MAX_ATOMS_AFTER_TRANSFORM:
                raise ValueError(
                    f"repeat transform would produce {repeat_atoms} atoms, "
                    f"exceeds MAX_ATOMS_AFTER_TRANSFORM={MAX_ATOMS_AFTER_TRANSFORM}"
                )
        transforms.append(transform)
        patch = {"transforms": transforms}
        if promoted_from:
            patch["display_mode"] = state["display_mode"]
        self.patch_state(patch, scene_id=scene_id)
        response = dict(transform)
        if warnings:
            response["warnings"] = warnings
        if promoted_from:
            response["display_mode_auto_promoted"] = f"{promoted_from} -> {state['display_mode']}"
        return response

    def update_transform(
        self,
        transform_id: str,
        patch: dict[str, Any],
        *,
        scene_id: Optional[str] = None,
    ) -> dict[str, Any]:
        scene_id, transforms = self._resolve_transforms(scene_id)
        for index, transform in enumerate(transforms):
            if transform["id"] == transform_id:
                merged = dict(transform)
                merged.update(patch or {})
                merged["id"] = transform_id
                replacement = _normalize_transform(
                    merged,
                    existing_ids={t["id"] for t in transforms if t["id"] != transform_id},
                )
                if replacement is None:
                    raise ValueError(
                        f"invalid transform patch for {transform_id!r}: {patch!r}"
                    )
                transforms[index] = replacement
                self.patch_state({"transforms": transforms}, scene_id=scene_id)
                return replacement
        raise KeyError(f"unknown transform id: {transform_id!r}")

    def remove_transform(self, transform_id: str, *, scene_id: Optional[str] = None) -> bool:
        scene_id, transforms = self._resolve_transforms(scene_id)
        before = len(transforms)
        transforms = [t for t in transforms if t["id"] != transform_id]
        if len(transforms) == before:
            return False
        self.patch_state({"transforms": transforms}, scene_id=scene_id)
        return True

    def reorder_transforms(
        self,
        ordered_ids: Iterable[str],
        *,
        scene_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        scene_id, transforms = self._resolve_transforms(scene_id)
        index_by_id = {t["id"]: t for t in transforms}
        wanted = [str(item) for item in ordered_ids]
        if set(wanted) != set(index_by_id):
            raise ValueError(
                "reorder list must contain exactly the existing transform ids; "
                f"got {wanted!r}, have {sorted(index_by_id)}"
            )
        ordered = [index_by_id[transform_id] for transform_id in wanted]
        self.patch_state({"transforms": ordered}, scene_id=scene_id)
        return ordered

    # ---- polyhedron instance overrides --------------------------------
    #
    # A per-fragment override of the spec-level colour / visibility.
    # Applies on top of the existing spec colour without mutating it,
    # so the right-click "Set this one cyan" path stays scoped to the
    # picked instance only.

    def set_polyhedron_instance_override(
        self,
        spec_id: str,
        fragment_label: str,
        override: dict[str, Any],
        *,
        scene_id: Optional[str] = None,
    ) -> dict[str, Any]:
        scene_id, specs = self._resolve_specs(scene_id)
        for index, spec in enumerate(specs):
            if spec["id"] != spec_id:
                continue
            current = dict(spec.get("instance_overrides") or {})
            cleaned: dict[str, Any] = {}
            color = override.get("color") if isinstance(override, dict) else None
            if color:
                hex_color = _coerce_hex_color(color, "")
                if hex_color:
                    cleaned["color"] = hex_color
            if isinstance(override, dict) and "visible" in override:
                cleaned["visible"] = bool(override["visible"])
            if cleaned:
                current[str(fragment_label)] = cleaned
            else:
                current.pop(str(fragment_label), None)
            spec_patch = dict(spec)
            spec_patch["instance_overrides"] = current
            specs[index] = spec_patch
            self.patch_state({"polyhedron_specs": specs}, scene_id=scene_id)
            return spec_patch
        raise KeyError(f"unknown polyhedron spec id: {spec_id!r}")

    def clear_polyhedron_instance_override(
        self,
        spec_id: str,
        fragment_label: str,
        *,
        scene_id: Optional[str] = None,
    ) -> dict[str, Any]:
        return self.set_polyhedron_instance_override(
            spec_id,
            fragment_label,
            {},
            scene_id=scene_id,
        )

    # ---- topology computation -----------------------------------------

    def _effective_polyhedron_specs(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Resolve the per-render list of explicit named polyhedron specs.

        MatterVis no longer synthesises auto-ligand specs from
        ``topology_species_keys``; molecule-level packing shells are delegated
        to MolCrysKit and require explicit centre/ligand formulas.
        """
        explicit = list(state.get("polyhedron_specs") or [])
        if explicit:
            return [dict(spec) for spec in explicit if spec.get("enabled", True)]
        return []

    def topology_for_state(
        self,
        state: dict[str, Any],
        click_data: Optional[dict[str, Any]] = None,
        *,
        strict: bool = False,
    ):
        if not state.get("topology_enabled", False):
            if strict:
                raise TopologyUnavailable(
                    "topology is disabled for this scene",
                    hint="POST /api/v2/state with topology_enabled=true, or include center_species and ligand_species in the topology request.",
                )
            return None
        structure = state["structure"]
        bundle = self.get_bundle(structure)
        scene = self.scene_for_state(state)
        effective_specs = self._effective_polyhedron_specs(state)
        if not effective_specs:
            if strict:
                raise TopologyUnavailable(
                    "no enabled polyhedron specs are registered for this scene",
                    hint="POST /api/v2/polyhedra first, or include center_species and ligand_species in the topology request.",
                )
            return None
        # Legacy code paths below still consume a single ``species_keys``
        # list (used to resolve the analysis anchor when the user clicks
        # in the viewer). Reconstruct it from the union of every active
        # spec's center species so a click on any rendered polyhedron
        # still snaps the analysis panel.
        species_keys = sorted({spec["center_species"] for spec in effective_specs})
        if not species_keys:
            if strict:
                raise TopologyUnavailable("no center species are available for topology analysis")
            return None
        site_index = self.resolve_topology_site(
            state=state,
            structure=structure,
            explicit_site=state.get("topology_site_index"),
            species_keys=species_keys,
            click_data=click_data,
        )
        if site_index is None:
            if strict:
                raise TopologyUnavailable(
                    "could not resolve a topology fragment for center_index",
                    hint="Use an index from GET /api/v2/scene/{name} topology_fragment_table.",
                )
            return None
        # Memoize the (heavy) topology dict on the bundle keyed on the
        # state fields that actually influence GEOMETRY. Per-spec colour
        # is intentionally not in the key -- it only affects the
        # renderer's painter cache (``_background_dict_cache`` etc),
        # which is keyed independently on the per-spec colour tuple.
        # That way swapping a hull colour stays a cheap re-paint and
        # doesn't recompute coordination shells for every tile.
        cutoff = float(state.get("cutoff", 10.0))
        spec_geometry_key = frozenset(
            (
                spec["center_species"],
                spec.get("ligand_species") or None,
            )
            for spec in effective_specs
        )
        # Phase 4: ``transforms`` change which fragments exist and must be
        # in the geometry cache key. Per-spec colours and
        # ``instance_overrides`` stay OUT of the key (they only affect
        # the renderer's painter cache; see ``_attach_spec_colors``).
        from .transforms import transforms_cache_key

        transforms_key = transforms_cache_key(state.get("transforms") or [])
        cache_key = (
            structure,
            state.get("display_mode"),
            bool("hydrogens" in (state.get("display_options") or [])),
            int(site_index),
            cutoff,
            spec_geometry_key,
            transforms_key,
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
            if strict:
                raise TopologyUnavailable("topology analysis produced no geometry for the requested fragment")
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
        # shape-classification passes -- those only matter for the
        # analysis anchor).
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
                            "hull": primary.get("hull"),
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
                    "hull": extra.get("hull"),
                    "is_analysis_anchor": False,
                }
                overlays.append(overlay)
                legacy_extras.append(
                    {
                        "center_coords": overlay["center_coords"],
                        "center_label": overlay["center_label"],
                        "shell_coords": overlay["shell_coords"],
                        "distances": overlay["distances"],
                        "hull": overlay.get("hull"),
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
        """Re-stamp per-spec colours and per-fragment instance overrides
        onto a geometry payload pulled from the bundle cache. The
        geometry dict is shared across colour changes; we copy a small
        wrapper so the renderer's painter cache (keyed on the colour
        tuple) doesn't get polluted by stale values."""
        color_by_id = {spec["id"]: spec.get("color", "#7C5CBF") for spec in effective_specs}
        overrides_by_id: dict[str, dict[str, dict[str, Any]]] = {
            spec["id"]: dict(spec.get("instance_overrides") or {}) for spec in effective_specs
        }
        spec_results = []
        for entry in cached_geometry.get("spec_results", []) or []:
            spec_id = entry.get("spec_id")
            recoloured = dict(entry)
            recoloured["color"] = color_by_id.get(spec_id, "#7C5CBF")
            spec_overrides = overrides_by_id.get(spec_id) or {}
            if spec_overrides:
                # Patch each overlay with its per-fragment override (if
                # any). The override key is the fragment label; we copy
                # the overlay dict so the cached geometry stays clean.
                new_overlays = []
                for overlay in entry.get("overlays") or []:
                    label = str(overlay.get("center_label") or "")
                    override = spec_overrides.get(label)
                    if override:
                        patched = dict(overlay)
                        if "color" in override:
                            patched["color"] = override["color"]
                        if "visible" in override:
                            patched["visible"] = bool(override["visible"])
                        new_overlays.append(patched)
                    else:
                        new_overlays.append(overlay)
                recoloured["overlays"] = new_overlays
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
        scene_id = state.get("scene_id")
        cache_key = None
        if click_data is None:
            try:
                cache_key = json.dumps(_json_safe(state), sort_keys=True, separators=(",", ":"))
            except Exception:
                cache_key = None
        if cache_key is not None and cache_key in self._figure_cache:
            cached_fig, cached_topology = self._figure_cache[cache_key]
            return copy.deepcopy(cached_fig), copy.deepcopy(cached_topology)
        with perf_log.time_block("scene_for_state", kind="event", scene_id=scene_id):
            scene = self.scene_for_state(state)
        atom_count = len(scene.get("draw_atoms", []))
        bond_count = len(scene.get("bonds", []))
        replica_count = sum(1 for atom in scene.get("draw_atoms", []) if atom.get("_is_boundary_replica"))
        with perf_log.time_block(
            "topology_for_state",
            kind="event",
            scene_id=scene_id,
            n_specs=len((state.get("polyhedron_specs") or [])),
        ):
            topology_data = self.topology_for_state(state, click_data=click_data)
        with perf_log.time_block(
            "build_figure",
            kind="event",
            scene_id=scene_id,
            atoms=atom_count,
            bonds=bond_count,
            replicas=replica_count,
        ):
            fig = build_figure(scene, self.style_for_state(state, scene=scene), topology_data=topology_data)
        camera = _plotly_camera(state.get("camera"))
        if camera:
            fig.update_layout(scene_camera=camera)
        if cache_key is not None:
            self._figure_cache[cache_key] = (copy.deepcopy(fig), copy.deepcopy(topology_data))
            self._figure_cache_order.append(cache_key)
            while len(self._figure_cache_order) > 16:
                old_key = self._figure_cache_order.pop(0)
                self._figure_cache.pop(old_key, None)
        return fig, topology_data

    def render_current_png(
        self,
        scene_id: Optional[str] = None,
        *,
        raise_errors: bool = False,
        width: int | None = None,
        height: int | None = None,
        scale: float = 2.0,
        fast: bool = False,
    ) -> bytes:
        state = self.get_state(scene_id)
        if fast:
            state = copy.deepcopy(state)
            state["material"] = "flat"
            state["fast_rendering"] = True
        fig, _ = self.figure_for_state(state)
        kwargs: dict[str, Any] = {"format": "png", "scale": float(scale)}
        if width is not None:
            kwargs["width"] = int(width)
        if height is not None:
            kwargs["height"] = int(height)
        try:
            with perf_log.time_block("http:screenshot", kind="http", scene_id=scene_id, fast=bool(fast)):
                return pio.to_image(fig, **kwargs)
        except Exception as exc:  # pragma: no cover - depends on local Chrome/Kaleido state
            if raise_errors:
                raise
            return _fallback_png(f"Plotly image export failed: {exc}")

    def default_camera(self, state: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        scene = self.scene_for_state(self.get_state() if state is None else state)
        return _plotly_camera(scene.get("camera")) or _plotly_camera(None)

    def get_camera(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        state = self.get_state(scene_id)
        return _plotly_camera(state.get("camera")) or self.default_camera(state)

    def set_camera(
        self,
        camera: dict[str, Any],
        scene_id: Optional[str] = None,
        *,
        broadcast: bool = True,
    ) -> dict[str, Any]:
        self.patch_state({"camera": camera}, scene_id=scene_id, broadcast=broadcast)
        return self.get_camera(scene_id)

    def camera_action(
        self,
        action: str,
        scene_id: Optional[str] = None,
        *,
        broadcast: bool = True,
        **payload,
    ) -> dict[str, Any]:
        if action == "reset":
            self._bump_camera_revision(scene_id=scene_id, broadcast=broadcast)
            return self.set_camera(
                self.default_camera(self.get_state(scene_id)),
                scene_id=scene_id,
                broadcast=broadcast,
            )

        if action == "align":
            self._bump_camera_revision(scene_id=scene_id, broadcast=broadcast)
            return self.align_camera(payload.get("axis"), scene_id=scene_id, broadcast=broadcast)

        if action == "fit":
            self._bump_camera_revision(scene_id=scene_id, broadcast=broadcast)
            state = self.get_state(scene_id)
            camera = self.default_camera(state)
            # Plotly uses unitless eye vectors against the already fixed
            # world-cube ranges; 1.55 fills most structures without clipping.
            eye = camera.get("eye", {})
            norm = np.linalg.norm([eye.get("x", 0.0), eye.get("y", 0.0), eye.get("z", 0.0)])
            if norm > 1e-8:
                scale = 1.55 / norm
                camera["eye"] = {axis: float(eye.get(axis, 0.0)) * scale for axis in ("x", "y", "z")}
            return self.set_camera(camera, scene_id=scene_id, broadcast=broadcast)

        if action in ("projection", "set_projection"):
            return self.set_projection(
                payload.get("type") or payload.get("projection"),
                scene_id=scene_id,
                broadcast=broadcast,
            )

        current_camera = self.get_camera(scene_id)
        eye, center, up = _camera_vectors(current_camera)
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
        # Preserve the existing projection across orbit/pan/zoom so the
        # caller doesn't have to repeat ``set_projection`` after every
        # movement (the renderer would otherwise default to perspective).
        projection = None
        proj_payload = (current_camera or {}).get("projection")
        if isinstance(proj_payload, dict) and proj_payload.get("type"):
            projection = str(proj_payload["type"])
        elif isinstance(self.get_state(scene_id).get("projection"), str):
            projection = self.get_state(scene_id)["projection"]
        camera = _camera_payload(eye, center, up, projection=projection)
        return self.set_camera(camera, scene_id=scene_id, broadcast=broadcast)

    def align_camera(
        self,
        axis: Any,
        scene_id: Optional[str] = None,
        *,
        broadcast: bool = True,
    ) -> dict[str, Any]:
        """Look down the requested lattice axis (``a``/``b``/``c``) or
        reciprocal axis (``a*``/``b*``/``c*``).

        Preserves the current eye-to-center distance so the user's
        zoom level survives an axis switch (mirrors VESTA's behaviour
        where the alignment buttons rotate but do not zoom).
        """
        key = _normalize_axis_key(axis)
        if key is None:
            raise ValueError(f"unknown axis: {axis!r}; pick one of {_AXIS_VIEW_KEYS}")
        state = self.get_state(scene_id)
        scene = self.scene_for_state(state)
        M = np.asarray(scene["M"], dtype=float)
        current = self.get_camera(scene_id)
        eye, center, _up = _camera_vectors(current)
        eye_distance = float(np.linalg.norm(eye - center))
        if eye_distance < 1e-6:
            eye_distance = 1.8
        # Carry projection through the alignment so users who have
        # opted into orthographic don't get bounced back to perspective
        # every time they hit a "down a" button.
        projection = None
        proj_payload = (current or {}).get("projection")
        if isinstance(proj_payload, dict) and proj_payload.get("type"):
            projection = str(proj_payload["type"])
        elif isinstance(state.get("projection"), str):
            projection = state["projection"]
        camera = camera_for_axis(
            M,
            key,
            eye_distance=eye_distance,
            center=center,
            projection=projection,
        )
        return self.set_camera(camera, scene_id=scene_id, broadcast=broadcast)

    def set_projection(
        self,
        projection: Any,
        scene_id: Optional[str] = None,
        *,
        broadcast: bool = True,
    ) -> dict[str, Any]:
        """Toggle the camera projection between ``perspective`` and
        ``orthographic``. Persists onto ``state["projection"]`` so the
        next ``style_for_state`` reflects the choice and so the REST
        ``GET /state`` echoes back what was set.
        """
        normalized = _coerce_projection(projection, fallback="perspective")
        self.patch_state({"projection": normalized}, scene_id=scene_id, broadcast=broadcast)
        # Stamp ``projection`` onto the persisted camera dict so a
        # subsequent ``set_camera`` round-trip (e.g. user drags the
        # scene to a new orientation) doesn't drop the choice.
        camera = dict(self.get_camera(scene_id))
        camera["projection"] = {"type": normalized}
        return self.set_camera(camera, scene_id=scene_id, broadcast=broadcast)

    def _bump_camera_revision(self, scene_id: Optional[str] = None, *, broadcast: bool = True) -> int:
        """Increment ``state['camera_revision']`` so the next figure
        rebuild gets a fresh ``layout.scene.uirevision`` and Plotly
        accepts the layout-supplied camera instead of preserving the
        user's last mouse-drag rotation.

        Mouse-drag updates flow through ``patch_state`` directly (not
        through ``camera_action``) so they intentionally do NOT bump
        the revision -- preserving Plotly's drag continuity across
        non-camera UI toggles like Labels/Hydrogens.
        """
        state = self.get_state(scene_id)
        current = int(state.get("camera_revision", 0) or 0)
        self.patch_state({"camera_revision": current + 1}, scene_id=scene_id, broadcast=broadcast)
        return current + 1

    def _safe_preset_path(self, path: Optional[str], *, allow_external: bool = False) -> Optional[str]:
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
        if allow_external:
            return os.path.realpath(path)
        safe_root = os.path.realpath(os.path.join(self.root_dir, LOCAL_STATE_DIRNAME))
        os.makedirs(safe_root, exist_ok=True)
        candidate = path if os.path.isabs(path) else os.path.join(safe_root, path)
        resolved = os.path.realpath(candidate)
        if os.path.commonpath([resolved, safe_root]) != safe_root:
            raise ValueError(
                f"preset path must resolve inside {safe_root!r}, got {path!r}"
            )
        return resolved

    def save_preset(self, path: Optional[str] = None, *, allow_external: bool = False) -> dict[str, Any]:
        target = self._safe_preset_path(path, allow_external=allow_external) or self.preset_path
        state = self.get_state()
        bundle = self.get_bundle(state["structure"])
        scene = self.scene_for_state(state)
        preset_data = load_preset(target) if os.path.exists(target) else default_preset()
        preset_data["version"] = max(int(preset_data.get("version", 1) or 1), 2)
        preset_data["style"].update(self.style_for_state(state))
        preset_data.setdefault("structures", {})
        preset_data["structures"][bundle.name] = {
            "camera": state.get("camera") or scene.get("camera"),
            "show_hydrogen": self.show_hydrogen_for_state(state),
            "style": self.style_for_state(state),
        }
        preset_data["scenes"] = [item for item in self.scene_store.list()]
        preset_data["active_id"] = self.scene_store.active_id
        preset_data["order"] = list(self.scene_store.order)
        save_preset(target, preset_data)
        self.preset = preset_data
        return {"path": target, "structure": bundle.name, "scenes": len(preset_data["scenes"])}

    def load_preset_from_path(self, path: Optional[str], *, allow_external: bool = False) -> dict[str, Any]:
        if not path:
            raise ValueError("path is required")
        target = self._safe_preset_path(path, allow_external=allow_external)
        self.preset = load_preset(target)
        self.preset_path = target
        if isinstance(self.preset.get("scenes"), list):
            store = SceneStore(self.scene_store.path)
            for item in self.preset.get("scenes") or []:
                try:
                    scene = Scene.from_dict(item)
                except Exception:
                    continue
                if scene.structure_name not in self.structure_names:
                    continue
                if scene.id in store.scenes:
                    continue
                store.scenes[scene.id] = scene
                store.order.append(scene.id)
            order = [str(item) for item in (self.preset.get("order") or [])]
            if order and set(order) == set(store.scenes):
                store.order = order
            active_id = self.preset.get("active_id")
            store.active_id = str(active_id) if active_id in store.scenes else (store.order[0] if store.order else None)
            if store.scenes:
                self.scene_store = store
                self.scene_store.save()
        for bundle in self.bundles.values():
            bundle.scene_cache.clear()
            cache = getattr(bundle, "_topology_state_cache", None)
            if cache:
                cache.clear()
        structure = self.get_state()["structure"]
        if self.scene_store.active_id:
            self.current_state = self.scene_state(self.scene_store.active_id)
            self.pending_state = copy.deepcopy(self.current_state)
            self._bump_version()
        else:
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

    def query_topology(
        self,
        structure: str,
        center_index: int,
        cutoff: float = 10.0,
        scene_id: Optional[str] = None,
        *,
        center_species: Optional[str] = None,
        ligand_species: Optional[str] = None,
        level: str = "molecule",
    ) -> dict[str, Any]:
        if cutoff <= 0 or cutoff > 1000:
            raise ApiError("cutoff must be in the range (0, 1000]", status_code=400)
        state = self.get_state(scene_id)
        if state["structure"] != structure:
            state = self.normalize_state({"structure": structure}, scene_id=scene_id)
        scene = self.scene_for_state(state)
        if self._display_fragment(scene, center_index) is None:
            raise ApiError(
                f"center_index {center_index} is not present in this scene's fragment table",
                hint="Use an index from GET /api/v2/scene/{name} topology_fragment_table.",
                status_code=400,
            )
        state["topology_site_index"] = center_index
        state["cutoff"] = cutoff
        level = str(level or "molecule")
        if level not in {"molecule", "atom"}:
            raise ApiError("level must be 'molecule' or 'atom'", status_code=400)
        if level == "atom":
            display_fragment = self._display_fragment(scene, center_index)
            topology_fragment = self.map_display_fragment_to_topology(self.get_bundle(structure), display_fragment)
            if topology_fragment is None:
                raise TopologyUnavailable("could not map display fragment to topology fragment")
            try:
                return analyze_topology(
                    self.get_bundle(structure),
                    center_index=int(topology_fragment["index"]),
                    cutoff=cutoff,
                    display_center=display_fragment.get("center") if display_fragment else None,
                    display_label=display_fragment.get("label") if display_fragment else None,
                    display_type=display_fragment.get("type") if display_fragment else None,
                    ligand_species=[ligand_species] if ligand_species else None,
                    level="atom",
                    center_species=center_species,
                )
            except ValueError as exc:
                raise ApiError(str(exc), status_code=400) from exc
        if center_species is not None or ligand_species is not None:
            if not center_species:
                fragment = self._display_fragment(scene, center_index)
                center_species = (fragment or {}).get("formula") or (fragment or {}).get("species")
            state["topology_enabled"] = True
            state["polyhedron_specs"] = _normalize_polyhedron_specs(
                [
                    {
                        "id": "ephemeral_topology_request",
                        "name": str(center_species or "Topology"),
                        "center_species": center_species,
                        "ligand_species": ligand_species,
                        "enabled": True,
                    }
                ],
                fallback_color=state.get("topology_hull_color", "#7C5CBF"),
            )
        try:
            result = self.topology_for_state(state, strict=True)
        except ValueError as exc:
            raise ApiError(str(exc), status_code=400) from exc
        if result is None:
            raise TopologyUnavailable("topology analysis was unavailable for this request")
        return result

    def websocket_snapshot(self, *, include_figure: bool = False) -> dict[str, Any]:
        state = self.get_state()
        snapshot = {
            "version": self.version,
            "state": state,
            "structures": self.list_structures(),
        }
        if include_figure:
            fig, _ = self.figure_for_state(state)
            snapshot["figure"] = fig.to_plotly_json()
            snapshot["figure_version"] = self.version
        return snapshot


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
            dcc.Store(id="fast-ui-event-store", data=None),
            html.Div(
                id="fast-view-metadata",
                children=_fast_view_metadata(
                    backend,
                    first_state,
                    _camera_store_payload(first_state.get("scene_id"), first_state.get("camera")),
                ),
                style={"display": "none"},
            ),
            dcc.Store(id="native-upload-sync", data={"seq": 0}),
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
            # Phase 4: right-click + keyboard shortcut wiring -----------
            # The JS in ``assets/right_click_menu.js`` writes the
            # picked-target payload into ``rightclick-target.data``;
            # ``assets/keyboard_shortcuts.js`` writes the same store but
            # with an extra ``action`` field for one-key dispatch.
            # ``rightclick-target-fallback`` is a defensive hidden input
            # the JS uses if ``dash_clientside.set_props`` is not yet
            # bootstrapped (e.g. very early page load); a tiny callback
            # keeps the store in sync with that input.
            dcc.Store(id="rightclick-target", data=None),
            dcc.Input(
                id="rightclick-target-fallback",
                type="hidden",
                value="",
                debounce=False,
            ),
            html.Div(
                id="rightclick-menu",
                className="rightclick-menu rightclick-menu--hidden",
                children=[],
                style={"top": "0px", "left": "0px"},
            ),
            html.Div(
                id="kbd-help",
                className="kbd-help kbd-help--hidden",
                children=[
                    html.Button(
                        "\u00d7",
                        id="kbd-help-close",
                        n_clicks=0,
                        className="kbd-help__close",
                        title="Close",
                    ),
                    html.Div("Keyboard shortcuts", className="kbd-help__title"),
                    html.Div(
                        [
                            html.Span("?", className="kbd-help__key"),
                            html.Span("Toggle this panel"),
                        ],
                        className="kbd-help__row",
                    ),
                    html.Div(
                        [
                            html.Span("r", className="kbd-help__key"),
                            html.Span("Repeat 2\u00d72\u00d72 (replace existing)"),
                        ],
                        className="kbd-help__row",
                    ),
                    html.Div(
                        [
                            html.Span("Shift+r", className="kbd-help__key"),
                            html.Span("Clear repeat (back to home cell)"),
                        ],
                        className="kbd-help__row",
                    ),
                    html.Div(
                        [
                            html.Span("g", className="kbd-help__key"),
                            html.Span("Grow by 1 bond hop from hovered atom"),
                        ],
                        className="kbd-help__row",
                    ),
                    html.Div(
                        [
                            html.Span("Shift+g", className="kbd-help__key"),
                            html.Span("Grow by 4\u202f\u00c5 from hovered atom"),
                        ],
                        className="kbd-help__row",
                    ),
                    html.Div(
                        [
                            html.Span("h", className="kbd-help__key"),
                            html.Span("Hide hovered atom / bond / polyhedron"),
                        ],
                        className="kbd-help__row",
                    ),
                    html.Div(
                        [
                            html.Span("c", className="kbd-help__key"),
                            html.Span("Open colour picker for hovered target"),
                        ],
                        className="kbd-help__row",
                    ),
                    html.Div(
                        [
                            html.Span("p", className="kbd-help__key"),
                            html.Span("Promote hovered atom to a group rule"),
                        ],
                        className="kbd-help__row",
                    ),
                ],
            ),
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
                    html.Div(
                        [
                            dcc.Input(
                                id="scene-cif-upload-input",
                                type="file",
                                multiple=True,
                                style={"display": "none"},
                            ),
                            html.Div(
                                "Drag and drop CIF, or click to upload",
                                id="scene-cif-upload",
                                role="button",
                                tabIndex=0,
                                **{"aria-label": "Upload CIF"},
                                style={
                                    "border": "1px dashed #999999",
                                    "padding": "10px",
                                    "marginBottom": "12px",
                                    "textAlign": "center",
                                    "cursor": "pointer",
                                    "userSelect": "none",
                                },
                            ),
                        ],
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
                    # ---- Phase 4 (view tools): VESTA-style axis-aligned
                    # views + perspective / orthographic toggle.
                    #
                    # Six small buttons map to ``align`` actions on the
                    # backend; the radio mirrors ``state["projection"]``.
                    # All wiring lives in ``apply_view_action`` /
                    # ``apply_view_projection`` callbacks below.
                    html.Label("View"),
                    html.Div(
                        [
                            html.Button(
                                "a", id="view-align-a", n_clicks=0,
                                className="view-align-btn",
                                title="Look down lattice axis a",
                            ),
                            html.Button(
                                "b", id="view-align-b", n_clicks=0,
                                className="view-align-btn",
                                title="Look down lattice axis b",
                            ),
                            html.Button(
                                "c", id="view-align-c", n_clicks=0,
                                className="view-align-btn",
                                title="Look down lattice axis c",
                            ),
                            html.Button(
                                "a*", id="view-align-astar", n_clicks=0,
                                className="view-align-btn",
                                title="Look down reciprocal axis a*",
                            ),
                            html.Button(
                                "b*", id="view-align-bstar", n_clicks=0,
                                className="view-align-btn",
                                title="Look down reciprocal axis b*",
                            ),
                            html.Button(
                                "c*", id="view-align-cstar", n_clicks=0,
                                className="view-align-btn",
                                title="Look down reciprocal axis c*",
                            ),
                            html.Button(
                                "Reset", id="view-reset", n_clicks=0,
                                className="view-align-btn view-reset-btn",
                                title="Reset to scene-default camera",
                            ),
                        ],
                        className="view-align-row",
                    ),
                    dcc.RadioItems(
                        id="view-projection",
                        options=[
                            {"label": "Perspective", "value": "perspective"},
                            {"label": "Orthographic", "value": "orthographic"},
                        ],
                        value=str(first_state.get("projection", "perspective")),
                        inline=True,
                        className="view-projection-row",
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
                    html.H4("Polyhedra"),
                    dcc.Checklist(
                        id="topology-toggle",
                        options=[{"label": "Show polyhedra overlay", "value": "enabled"}],
                        value=["enabled"] if first_state.get("topology_enabled", False) else [],
                    ),
                    html.Div(
                        [
                            html.Div(
                                "Each row defines one MolCrysKit molecule-level packing polyhedron: "
                                "centre species + explicit ligand species + colour. The overlay "
                                "tiles every matching site in the structure.",
                                style={"fontSize": "11px", "color": "#777", "marginTop": "4px"},
                            ),
                            # ---- Named polyhedra table ----
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
                                        title="Add a named polyhedron row (centre + explicit ligand restriction + colour).",
                                    ),
                                ],
                                style={"display": "flex", "alignItems": "center", "marginTop": "8px"},
                            ),
                            html.Div(
                                id="polyhedra-rows-container",
                                children=_polyhedra_table_rows(
                                    first_state.get("polyhedron_specs") or [],
                                    backend.species_options(first_state["structure"]),
                                ),
                                style={"marginTop": "6px"},
                            ),
                        ],
                        id="polyhedra-controls",
                        style=_polyhedra_controls_style(first_state.get("topology_enabled", False)),
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
                        "Tip: to hide hydrogens use the Hydrogens checkbox under Display "
                        "Options above; that path also rebuilds bonds correctly. "
                        "Atom-group rules tweak per-atom colour / opacity / material.",
                        style={"fontSize": "11px", "color": "#777", "marginTop": "4px"},
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
                    # ---- Phase 4: Bond groups table ----
                    html.Div(
                        [
                            html.H4(
                                "Bond groups",
                                style={"display": "inline-block", "marginRight": "8px"},
                            ),
                            html.Button(
                                "+ Add",
                                id="bond-groups-add-btn",
                                n_clicks=0,
                                style={
                                    "fontSize": "12px",
                                    "padding": "2px 8px",
                                    "verticalAlign": "middle",
                                    "cursor": "pointer",
                                },
                                title="Add a bond-styling rule (selector + colour / opacity / radius scale).",
                            ),
                        ],
                        style={"display": "flex", "alignItems": "center"},
                    ),
                    html.Div(
                        "Per-rule overrides for bond colour, visibility, opacity, and "
                        "radius. Selector \u2018between elements\u2019 picks a Pb\u2013Cl style; "
                        "\u2018minor only\u2019 / \u2018major only\u2019 follow disorder flags.",
                        style={"fontSize": "11px", "color": "#777", "marginTop": "4px"},
                    ),
                    html.Div(
                        id="bond-groups-rows-container",
                        children=_bond_groups_table_rows(
                            first_state.get("bond_groups") or [],
                            backend.element_options(first_state),
                        ),
                        style={"marginTop": "6px"},
                    ),
                    html.Hr(),
                    # ---- Phase 4: Transforms pipeline ----
                    html.Div(
                        [
                            html.H4(
                                "Transforms",
                                style={"display": "inline-block", "marginRight": "8px"},
                            ),
                            dcc.Dropdown(
                                id="transforms-kind-select",
                                options=[
                                    {"label": label, "value": kind}
                                    for kind, label in _TRANSFORM_KIND_NAMES.items()
                                ],
                                value="repeat",
                                clearable=False,
                                style={"width": "150px", "fontSize": "12px", "display": "inline-block", "marginRight": "4px"},
                            ),
                            html.Button(
                                "+ Add",
                                id="transforms-add-btn",
                                n_clicks=0,
                                style={
                                    "fontSize": "12px",
                                    "padding": "2px 8px",
                                    "verticalAlign": "middle",
                                    "cursor": "pointer",
                                },
                                title="Append a new transform of the selected kind. Default params = a sane no-op.",
                            ),
                        ],
                        style={"display": "flex", "alignItems": "center", "gap": "4px", "flexWrap": "wrap"},
                    ),
                    html.Div(
                        [
                            html.Button(
                                "2\u00d72\u00d72",
                                id="transforms-preset-2x",
                                n_clicks=0,
                                style={"fontSize": "12px", "padding": "2px 8px", "marginRight": "4px", "cursor": "pointer"},
                                title="Quick preset: append a repeat 2\u00d72\u00d72 (or replace the existing repeat).",
                            ),
                            html.Button(
                                "3\u00d73\u00d73",
                                id="transforms-preset-3x",
                                n_clicks=0,
                                style={"fontSize": "12px", "padding": "2px 8px", "marginRight": "4px", "cursor": "pointer"},
                                title="Quick preset: repeat 3\u00d73\u00d73.",
                            ),
                            html.Button(
                                "Home cell",
                                id="transforms-clear-repeat",
                                n_clicks=0,
                                style={"fontSize": "12px", "padding": "2px 8px", "marginRight": "4px", "cursor": "pointer"},
                                title="Drop any repeat transform (back to single home cell).",
                            ),
                            html.Button(
                                "Clear all",
                                id="transforms-clear-btn",
                                n_clicks=0,
                                style={"fontSize": "12px", "padding": "2px 8px", "cursor": "pointer", "color": "#A00"},
                                title="Drop every transform (back to the raw scene).",
                            ),
                        ],
                        style={"marginTop": "6px"},
                    ),
                    html.Div(
                        "Transforms run top \u2192 bottom; each sees the previous one\u2019s output. "
                        "Seed format: \u2018all\u2019, \u2018elem:Pb,Cl\u2019, \u2018label:Pb1\u2019, \u2018index:0,5\u2019, "
                        "\u2018frag:A0\u2019. Bare \u2018Pb,Cl\u2019 = elements.",
                        style={"fontSize": "11px", "color": "#777", "marginTop": "4px"},
                    ),
                    html.Div(
                        id="transforms-rows-container",
                        children=_transforms_table_rows(first_state.get("transforms") or []),
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
            # Floating "Server log" panel (bottom-right). Polls
            # ``/api/v1/perf`` every second to show the user which
            # callbacks fired and how long each one took. Collapsed by
            # default to keep the UI clean; click the header to
            # expand. Lives outside the right-panel so the analysis
            # column can be hidden without losing the perf signal.
            html.Div(
                [
                    html.Div(
                        [
                            html.Button(
                                "Server log ▾",
                                id="perf-log-toggle",
                                n_clicks=0,
                                className="perf-log-toggle",
                            ),
                            html.Button(
                                "Clear",
                                id="perf-log-clear",
                                n_clicks=0,
                                className="perf-log-clear",
                            ),
                        ],
                        className="perf-log-header",
                    ),
                    html.Div(
                        id="perf-log-body",
                        className="perf-log-body",
                        children=[
                            html.Div(
                                "Waiting for events… (interact with the UI to see callbacks)",
                                className="perf-log-empty",
                            )
                        ],
                    ),
                    dcc.Interval(id="perf-log-poll", interval=1000, n_intervals=0),
                    dcc.Store(id="perf-log-cursor", data={"seq": 0, "events": []}),
                ],
                id="perf-log-panel",
                className="perf-log-panel perf-log-panel--collapsed",
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
            state["topology_site_index"],
            ["enabled"] if state.get("topology_enabled", False) else [],
            state,
            _camera_store_payload(scene_id, state.get("camera")),
        )

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
        Output("topology-site-index", "value"),
        Output("topology-toggle", "value"),
        Output("agent-state-store", "data"),
        Output("camera-state-store", "data"),
        Input("agent-state-poll", "n_intervals"),
        Input("native-upload-sync", "data"),
        Input("scene-tabs", "value"),
        prevent_initial_call=True,
    )
    def sync_agent_state(_n_intervals, _native_upload_sync, scene_id):
        triggered = (
            callback_context.triggered[0]["prop_id"].split(".")[0]
            if callback_context.triggered
            else None
        )
        n_outputs = 17
        if triggered == "scene-tabs":
            if not scene_id:
                return (no_update,) * n_outputs
            backend.set_active_scene(scene_id, broadcast=False)
            state = backend.get_state(scene_id)
            return (
                no_update,
                no_update,
                *scene_control_outputs(state),
            )
        state = backend.pop_pending_state()
        if not state:
            return (no_update,) * n_outputs
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
        Input("topology-site-index", "value"),
        Input("topology-toggle", "value"),
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
        site_index,
        topology_toggle,
    ):
        triggered = callback_context.triggered[0]["prop_id"].split(".")[0] if callback_context.triggered else None
        if triggered == "scene-tabs":
            return no_update
        if scene_id:
            backend.set_active_scene(scene_id, broadcast=False)
        prev = backend.get_state(scene_id)
        prev_options = set(prev.get("display_options") or [])
        next_options = set(display_options or [])
        hydrogens_changed = ("hydrogens" in prev_options) != ("hydrogens" in next_options)
        display_changed = display_mode != prev.get("display_mode")
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
            "topology_site_index": None if display_changed or site_index in ("", None) else int(site_index),
            "topology_enabled": "enabled" in (topology_toggle or []),
            "fast_rendering": material == "flat",
        }
        if triggered in {"display-options", "axis-scale-slider", "minor-opacity-slider"} and not hydrogens_changed:
            # Style-only controls are patched directly onto the current
            # Plotly figure by ``patch_fast_style_controls`` below. Persist
            # their state for API callers, but do not touch
            # ``agent-state-store`` or the full-figure callback.
            if all(prev.get(k) == v for k, v in patch.items() if k != "scene_id"):
                return no_update
            backend.record_state(patch)
            perf_log.record(
                "callback:capture_state",
                kind="cb",
                info={
                    "trigger": triggered,
                    "scene_id": scene_id,
                    "fast_path": True,
                },
            )
            return no_update
        # Skip the write -- and the cascade through ``update_view`` --
        # if every captured field already matches the persisted state.
        # The chain ``Labels click -> capture_state -> agent-state-store
        # -> refresh_fragment_options -> topology-site-index.value ->
        # capture_state -> agent-state-store`` would otherwise double up
        # every figure render, doubling the 1.4 MB-per-frame cost.
        if all(prev.get(k) == v for k, v in patch.items() if k != "scene_id"):
            return no_update
        backend.record_state(patch)
        perf_log.record(
            "callback:capture_state",
            kind="cb",
            info={
                "trigger": triggered,
                "scene_id": scene_id,
            },
        )
        return backend.get_state()

    @app.callback(
        Output("crystal-graph", "figure", allow_duplicate=True),
        Output("fast-view-metadata", "children", allow_duplicate=True),
        Input("display-options", "value"),
        Input("axis-scale-slider", "value"),
        Input("minor-opacity-slider", "value"),
        State("crystal-graph", "figure"),
        State("scene-tabs", "value"),
        prevent_initial_call=True,
    )
    def patch_fast_style_controls(display_options, axis_scale, minor_opacity, current_figure, scene_id):
        """Patch style-only trace attributes without rebuilding the figure.

        Hydrogens remain on the full scene path because they change the atom
        and bond sets. Labels/axes/unit-cell/minor-only/minor-opacity only
        flip trace visibility/opacity, so a small Dash Patch is enough.
        """
        scene_id = scene_id or backend.active_scene_id()
        prev = backend.get_state(scene_id)
        prev_options = set(prev.get("display_options") or [])
        next_options = set(display_options or [])
        if ("hydrogens" in prev_options) != ("hydrogens" in next_options):
            return no_update, no_update
        patch_payload = {
            "display_options": list(display_options or []),
            "axis_scale": axis_scale,
            "minor_opacity": minor_opacity,
        }
        backend.record_state(patch_payload, scene_id=scene_id)
        fig_patch = _fast_style_patch_for_figure(
            current_figure,
            display_options=display_options,
            minor_opacity=minor_opacity,
        )
        return fig_patch, _fast_view_metadata(backend, backend.get_state(scene_id))

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
        Output("agent-state-store", "data", allow_duplicate=True),
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
        # The second Output (``agent-state-store.data``) is the
        # critical perf fix for the inline-edit path: without it, an
        # in-row colour / centre / ligand / enabled change has to
        # wait for the 5 s ``agent-state-poll`` to round-trip via
        # ``sync_agent_state`` before ``update_view`` re-renders the
        # figure. Pushing the new state directly here cuts the
        # perceived latency from ~2.5 s (avg) to "the next frame".
        # ``broadcast=False`` on patch_state below stops the same
        # change from echoing back through the poll path on the next
        # tick.
        cb_start = time.monotonic()
        triggered = getattr(callback_context, "triggered_id", None)
        scene_id = active_scene_id or backend.active_scene_id()
        triggered_label = (
            triggered.get("type") if isinstance(triggered, dict) else triggered
        )
        species_options = backend.species_options(
            backend.get_state(scene_id).get("structure")
        )

        def _rebuild():
            specs = backend.list_polyhedron_specs(scene_id=scene_id)
            return _polyhedra_table_rows(specs, species_options)

        if triggered == "scene-tabs":
            return _rebuild(), no_update

        if triggered == "polyhedra-add-btn":
            if not species_options:
                return _rebuild(), no_update
            center_species = str(species_options[0]["value"])
            ligand_species = next(
                (str(option["value"]) for option in species_options if str(option["value"]) != center_species),
                None,
            )
            try:
                backend.add_polyhedron_spec(
                    center_species=center_species,
                    ligand_species=ligand_species,
                    enabled=True,
                    scene_id=scene_id,
                )
            except Exception:
                return no_update, no_update
            return _rebuild(), backend.get_state()

        if isinstance(triggered, dict) and triggered.get("type") == "poly-row-delete":
            spec_id = triggered.get("spec_id")
            if not spec_id:
                return no_update, no_update
            backend.remove_polyhedron_spec(spec_id, scene_id=scene_id)
            return _rebuild(), backend.get_state()

        if isinstance(triggered, dict) and triggered.get("type", "").startswith("poly-row-"):
            # Inline edit. Reconstruct the full spec list from the
            # current ALL-input values and persist it. We rely on
            # ``color_ids`` (one id-dict per row) to give us the spec_id
            # ordering that matches the value lists.
            if not color_ids:
                return no_update, no_update
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
                backend.patch_state({"polyhedron_specs": new_specs}, scene_id=scene_id, broadcast=False)
            except Exception:
                return no_update, no_update
            perf_log.record(
                "callback:manage_polyhedra",
                duration_ms=(time.monotonic() - cb_start) * 1000.0,
                kind="cb",
                info={
                    "trigger": triggered_label,
                    "n_specs": len(new_specs),
                    "scene_id": scene_id,
                },
            )
            # ``no_update`` for children to avoid mid-edit React tear-down.
            return no_update, backend.get_state()

        return no_update, no_update

    # ------------------------------------------------------------------
    # Phase 3 UI: Atom-groups table.
    #
    # Same pattern as the polyhedra callback, plus three quick-preset
    # buttons (Monochrome / Hide H / Clear all) that translate to
    # backend CRUD calls.
    # ------------------------------------------------------------------
    @app.callback(
        Output("atom-groups-rows-container", "children", allow_duplicate=True),
        Output("agent-state-store", "data", allow_duplicate=True),
        Input("atom-groups-add-btn", "n_clicks"),
        Input("atom-groups-preset-mono", "n_clicks"),
        Input("atom-groups-clear-btn", "n_clicks"),
        Input("scene-tabs", "value"),
        Input({"type": "ag-row-visible", "group_id": ALL}, "value"),
        Input({"type": "ag-row-color", "group_id": ALL}, "value"),
        Input({"type": "ag-row-kind", "group_id": ALL}, "value"),
        Input({"type": "ag-row-elements", "group_id": ALL}, "value"),
        Input({"type": "ag-row-opacity", "group_id": ALL}, "value"),
        Input({"type": "ag-row-material", "group_id": ALL}, "value"),
        Input({"type": "ag-row-style", "group_id": ALL}, "value"),
        Input({"type": "ag-row-delete", "group_id": ALL}, "n_clicks"),
        State({"type": "ag-row-color", "group_id": ALL}, "id"),
        prevent_initial_call=True,
    )
    def manage_atom_groups(
        add_clicks,
        mono_clicks,
        clear_clicks,
        active_scene_id,
        visibles,
        colors,
        kinds,
        elements_lists,
        opacities,
        materials,
        styles,
        deletes,
        color_ids,
    ):
        # Same perf rationale as ``manage_polyhedra``: the second
        # Output pushes the new state straight into ``agent-state-store``
        # so ``update_view`` re-renders on the next frame instead of
        # waiting for the 5 s ``agent-state-poll``. Without it, an
        # opacity / colour / visibility change has a 0-5 s perceived
        # latency.
        cb_start = time.monotonic()
        triggered = getattr(callback_context, "triggered_id", None)
        scene_id = active_scene_id or backend.active_scene_id()
        triggered_label = (
            triggered.get("type") if isinstance(triggered, dict) else triggered
        )

        def _rebuild():
            groups = backend.list_atom_groups(scene_id=scene_id)
            return _atom_groups_table_rows(
                groups, backend.element_options(backend.get_state(scene_id))
            )

        if triggered == "scene-tabs":
            return _rebuild(), no_update

        if triggered == "atom-groups-add-btn":
            try:
                backend.add_atom_group(selector={"all": True}, color="#888888", scene_id=scene_id)
            except Exception:
                return no_update, no_update
            return _rebuild(), backend.get_state()

        if triggered == "atom-groups-preset-mono":
            backend.add_atom_group(
                selector={"all": True},
                color="#000000",
                name="monochrome",
                scene_id=scene_id,
            )
            return _rebuild(), backend.get_state()

        if triggered == "atom-groups-clear-btn":
            for group in list(backend.list_atom_groups(scene_id=scene_id)):
                backend.remove_atom_group(group["id"], scene_id=scene_id)
            return _rebuild(), backend.get_state()

        if isinstance(triggered, dict) and triggered.get("type") == "ag-row-delete":
            group_id = triggered.get("group_id")
            if not group_id:
                return no_update, no_update
            backend.remove_atom_group(group_id, scene_id=scene_id)
            return _rebuild(), backend.get_state()

        if isinstance(triggered, dict) and triggered.get("type", "").startswith("ag-row-"):
            if not color_ids:
                return no_update, no_update
            new_groups: list[dict[str, Any]] = []
            for index, id_dict in enumerate(color_ids):
                group_id = id_dict.get("group_id")
                kind_value = kinds[index] if index < len(kinds) else _ATOM_GROUP_KIND_ALL
                if kind_value == _ATOM_GROUP_KIND_ALL:
                    selector: dict[str, Any] = {"all": True}
                elif kind_value == _ATOM_GROUP_KIND_MINOR:
                    selector = {"is_minor": True}
                elif kind_value == _ATOM_GROUP_KIND_MAJOR:
                    selector = {"is_minor": False}
                else:
                    selector = {
                        "elements": list(elements_lists[index]) if index < len(elements_lists) and elements_lists[index] else []
                    }
                opacity_value = opacities[index] if index < len(opacities) else 1.0
                opacity_payload = None if opacity_value is None or float(opacity_value) >= 0.999 else float(opacity_value)
                material_value = materials[index] if index < len(materials) else _ATOM_GROUP_INHERIT
                style_value = styles[index] if index < len(styles) else _ATOM_GROUP_INHERIT
                new_groups.append(
                    {
                        "id": group_id,
                        "selector": selector,
                        "color": colors[index] if index < len(colors) else None,
                        "visible": "yes" in (visibles[index] if index < len(visibles) else ["yes"]),
                        "opacity": opacity_payload,
                        "material": None if material_value == _ATOM_GROUP_INHERIT else material_value,
                        "style": None if style_value == _ATOM_GROUP_INHERIT else style_value,
                    }
                )
            try:
                backend.patch_state({"atom_groups": new_groups}, scene_id=scene_id, broadcast=False)
            except Exception:
                return no_update, no_update
            perf_log.record(
                "callback:manage_atom_groups",
                duration_ms=(time.monotonic() - cb_start) * 1000.0,
                kind="cb",
                info={
                    "trigger": triggered_label,
                    "n_groups": len(new_groups),
                    "scene_id": scene_id,
                },
            )
            # Special case: switching kind from "all" -> "by element"
            # needs to reveal the elements multi-select that's
            # display:none in the existing DOM. Rebuild children to
            # update the visibility toggle.
            if triggered.get("type") == "ag-row-kind":
                return _rebuild(), backend.get_state()
            return no_update, backend.get_state()

        return no_update, no_update

    # ------------------------------------------------------------------
    # Phase 4 UI: Bond-groups table.
    #
    # Same dispatch pattern as ``manage_atom_groups`` -- pattern-matched
    # row inputs, single ALL callback, ``agent-state-store`` second
    # Output for instant re-render. Only difference: bond-specific
    # selectors (between elements / minor / major) and per-bond
    # ``radius_scale``.
    # ------------------------------------------------------------------
    @app.callback(
        Output("bond-groups-rows-container", "children", allow_duplicate=True),
        Output("agent-state-store", "data", allow_duplicate=True),
        Input("bond-groups-add-btn", "n_clicks"),
        Input("scene-tabs", "value"),
        Input({"type": "bg-row-visible", "group_id": ALL}, "value"),
        Input({"type": "bg-row-color", "group_id": ALL}, "value"),
        Input({"type": "bg-row-kind", "group_id": ALL}, "value"),
        Input({"type": "bg-row-elements", "group_id": ALL}, "value"),
        Input({"type": "bg-row-opacity", "group_id": ALL}, "value"),
        Input({"type": "bg-row-radius", "group_id": ALL}, "value"),
        Input({"type": "bg-row-delete", "group_id": ALL}, "n_clicks"),
        State({"type": "bg-row-color", "group_id": ALL}, "id"),
        prevent_initial_call=True,
    )
    def manage_bond_groups(
        add_clicks,
        active_scene_id,
        visibles,
        colors,
        kinds,
        elements_lists,
        opacities,
        radius_scales,
        deletes,
        color_ids,
    ):
        cb_start = time.monotonic()
        triggered = getattr(callback_context, "triggered_id", None)
        scene_id = active_scene_id or backend.active_scene_id()
        triggered_label = (
            triggered.get("type") if isinstance(triggered, dict) else triggered
        )

        def _rebuild():
            groups = backend.list_bond_groups(scene_id=scene_id)
            return _bond_groups_table_rows(
                groups, backend.element_options(backend.get_state(scene_id))
            )

        if triggered == "scene-tabs":
            return _rebuild(), no_update

        if triggered == "bond-groups-add-btn":
            try:
                backend.add_bond_group(selector={"all": True}, scene_id=scene_id)
            except Exception:
                return no_update, no_update
            return _rebuild(), backend.get_state()

        if isinstance(triggered, dict) and triggered.get("type") == "bg-row-delete":
            group_id = triggered.get("group_id")
            if not group_id:
                return no_update, no_update
            backend.remove_bond_group(group_id, scene_id=scene_id)
            return _rebuild(), backend.get_state()

        if isinstance(triggered, dict) and triggered.get("type", "").startswith("bg-row-"):
            if not color_ids:
                return no_update, no_update
            new_groups: list[dict[str, Any]] = []
            for index, id_dict in enumerate(color_ids):
                group_id = id_dict.get("group_id")
                kind_value = kinds[index] if index < len(kinds) else _BOND_GROUP_KIND_ALL
                if kind_value == _BOND_GROUP_KIND_ALL:
                    selector: dict[str, Any] = {"all": True}
                elif kind_value == _BOND_GROUP_KIND_MINOR:
                    selector = {"is_minor": True}
                elif kind_value == _BOND_GROUP_KIND_MAJOR:
                    selector = {"is_minor": False}
                else:
                    elements = [str(e) for e in (elements_lists[index] if index < len(elements_lists) else []) if e]
                    selector = {"between_elements": elements} if elements else {"all": True}
                new_groups.append(
                    {
                        "id": group_id,
                        "selector": selector,
                        "color": colors[index] if index < len(colors) else None,
                        "visible": "yes" in (visibles[index] if index < len(visibles) else []),
                        "opacity": float(opacities[index]) if index < len(opacities) and opacities[index] is not None else None,
                        "radius_scale": float(radius_scales[index]) if index < len(radius_scales) and radius_scales[index] is not None else None,
                        "enabled": True,
                    }
                )
            try:
                backend.patch_state({"bond_groups": new_groups}, scene_id=scene_id, broadcast=False)
            except Exception:
                return no_update, no_update
            perf_log.record(
                "callback:manage_bond_groups",
                duration_ms=(time.monotonic() - cb_start) * 1000.0,
                kind="cb",
                info={
                    "trigger": triggered_label,
                    "n_groups": len(new_groups),
                    "scene_id": scene_id,
                },
            )
            # ``bg-row-kind`` toggles between visible/hidden elements
            # multi-select, so rebuild children to reveal it.
            if triggered.get("type") == "bg-row-kind":
                return _rebuild(), backend.get_state()
            return no_update, backend.get_state()

        return no_update, no_update

    # ------------------------------------------------------------------
    # Phase 4 UI: Transforms pipeline.
    #
    # The ``transforms-rows-container`` shows one row per transform spec
    # in ``state["transforms"]``, in pipeline order. Mutations:
    #
    #   - ``transforms-add-btn`` + ``transforms-kind-select`` -> append a
    #     new transform of the selected kind (sane defaults).
    #   - ``transforms-preset-2x`` / ``-3x`` / ``-clear-repeat`` /
    #     ``-clear-btn`` -> quick presets for the most common cases.
    #   - ``trf-row-delete`` / ``-up`` / ``-down`` -> remove / reorder.
    #   - ``trf-row-enabled`` and any ``trf-param-*`` -> patch the
    #     transform via ``update_transform`` (kind-aware).
    #
    # Because per-row parameter widgets vary by kind, the dispatch reads
    # ``State`` lists for every possible widget type and only consumes
    # the ones the row's kind cares about. Empty / missing values fall
    # back to the spec's defaults.
    # ------------------------------------------------------------------
    @app.callback(
        Output("transforms-rows-container", "children", allow_duplicate=True),
        Output("agent-state-store", "data", allow_duplicate=True),
        Input("transforms-add-btn", "n_clicks"),
        Input("transforms-preset-2x", "n_clicks"),
        Input("transforms-preset-3x", "n_clicks"),
        Input("transforms-clear-repeat", "n_clicks"),
        Input("transforms-clear-btn", "n_clicks"),
        Input("scene-tabs", "value"),
        Input({"type": "trf-row-enabled", "transform_id": ALL}, "value"),
        Input({"type": "trf-row-delete", "transform_id": ALL}, "n_clicks"),
        Input({"type": "trf-row-up", "transform_id": ALL}, "n_clicks"),
        Input({"type": "trf-row-down", "transform_id": ALL}, "n_clicks"),
        Input({"type": "trf-param-a", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-b", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-c", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-seeds", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-radius", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-hops", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-maxhops", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-cutoff", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-ops", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-miller-0", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-miller-1", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-miller-2", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-layers", "transform_id": ALL}, "value"),
        Input({"type": "trf-param-vacuum", "transform_id": ALL}, "value"),
        State("transforms-kind-select", "value"),
        State({"type": "trf-row-enabled", "transform_id": ALL}, "id"),
        prevent_initial_call=True,
    )
    def manage_transforms(
        add_clicks,
        preset_2x_clicks,
        preset_3x_clicks,
        clear_repeat_clicks,
        clear_all_clicks,
        active_scene_id,
        enableds,
        deletes,
        ups,
        downs,
        param_a,
        param_b,
        param_c,
        param_seeds,
        param_radius,
        param_hops,
        param_maxhops,
        param_cutoff,
        param_ops,
        param_miller0,
        param_miller1,
        param_miller2,
        param_layers,
        param_vacuum,
        kind_select,
        enabled_ids,
    ):
        cb_start = time.monotonic()
        triggered = getattr(callback_context, "triggered_id", None)
        scene_id = active_scene_id or backend.active_scene_id()
        triggered_label = (
            triggered.get("type") if isinstance(triggered, dict) else triggered
        )

        def _rebuild():
            return _transforms_table_rows(backend.list_transforms(scene_id=scene_id))

        if triggered == "scene-tabs":
            return _rebuild(), no_update

        # Quick presets ------------------------------------------------
        if triggered == "transforms-preset-2x" or triggered == "transforms-preset-3x":
            n = 2 if triggered == "transforms-preset-2x" else 3
            try:
                backend.patch_state(
                    {"supercell": {"a": n, "b": n, "c": n}},
                    scene_id=scene_id,
                )
            except Exception:
                return no_update, no_update
            return _rebuild(), backend.get_state()

        if triggered == "transforms-clear-repeat":
            try:
                backend.patch_state(
                    {"supercell": {"a": 1, "b": 1, "c": 1}},
                    scene_id=scene_id,
                )
            except Exception:
                return no_update, no_update
            return _rebuild(), backend.get_state()

        if triggered == "transforms-clear-btn":
            try:
                backend.patch_state({"transforms": []}, scene_id=scene_id)
            except Exception:
                return no_update, no_update
            return _rebuild(), backend.get_state()

        if triggered == "transforms-add-btn":
            kind = kind_select or "repeat"
            defaults_by_kind = {
                "repeat": {"a": 2, "b": 2, "c": 2},
                "grow_radius": {"seeds": {"all": True}, "radius": 4.0},
                "grow_bonds": {"seeds": {"all": True}, "hops": 1},
                "complete_fragment": {"seeds": {"all": True}, "max_hops": 32},
                "complete_polyhedron": {"seeds": {"all": True}, "cutoff": 4.0},
                "by_symmetry": {"seeds": {"all": True}, "ops": []},
                "slab": {"miller": [0, 0, 1], "layers": 3, "vacuum": 10.0},
            }
            try:
                backend.add_transform(
                    kind=kind,
                    params=defaults_by_kind.get(kind, {}),
                    scene_id=scene_id,
                )
            except Exception:
                return no_update, no_update
            return _rebuild(), backend.get_state()

        if isinstance(triggered, dict) and triggered.get("type") == "trf-row-delete":
            transform_id = triggered.get("transform_id")
            if not transform_id:
                return no_update, no_update
            backend.remove_transform(transform_id, scene_id=scene_id)
            return _rebuild(), backend.get_state()

        if isinstance(triggered, dict) and triggered.get("type") in ("trf-row-up", "trf-row-down"):
            transform_id = triggered.get("transform_id")
            transforms = list(backend.list_transforms(scene_id=scene_id))
            ids = [t["id"] for t in transforms]
            if transform_id not in ids:
                return no_update, no_update
            i = ids.index(transform_id)
            j = i - 1 if triggered.get("type") == "trf-row-up" else i + 1
            if j < 0 or j >= len(ids):
                return no_update, no_update
            ids[i], ids[j] = ids[j], ids[i]
            try:
                backend.reorder_transforms(ids, scene_id=scene_id)
            except Exception:
                return no_update, no_update
            return _rebuild(), backend.get_state()

        # Inline edit (enabled toggle or any param change) -------------
        if isinstance(triggered, dict) and (
            triggered.get("type") == "trf-row-enabled"
            or triggered.get("type", "").startswith("trf-param-")
        ):
            if not enabled_ids:
                return no_update, no_update
            existing = {t["id"]: t for t in backend.list_transforms(scene_id=scene_id)}
            new_transforms: list[dict[str, Any]] = []
            for index, id_dict in enumerate(enabled_ids):
                transform_id = id_dict.get("transform_id")
                base = existing.get(transform_id)
                if base is None:
                    continue
                kind = base.get("kind") or "repeat"
                params: dict[str, Any] = {}
                if kind == "repeat":
                    params = {
                        "a": int(param_a[index]) if index < len(param_a) and param_a[index] is not None else int(base["params"].get("a", 1) or 1),
                        "b": int(param_b[index]) if index < len(param_b) and param_b[index] is not None else int(base["params"].get("b", 1) or 1),
                        "c": int(param_c[index]) if index < len(param_c) and param_c[index] is not None else int(base["params"].get("c", 1) or 1),
                    }
                elif kind in ("grow_radius", "grow_bonds", "complete_fragment", "complete_polyhedron", "by_symmetry"):
                    seeds_text = param_seeds[index] if index < len(param_seeds) else None
                    seeds = _seed_text_to_selector(seeds_text) if seeds_text is not None else base["params"].get("seeds") or {}
                    params["seeds"] = seeds
                    if kind == "grow_radius":
                        params["radius"] = float(param_radius[index]) if index < len(param_radius) and param_radius[index] is not None else float(base["params"].get("radius", 0.0) or 0.0)
                    elif kind == "grow_bonds":
                        params["hops"] = int(param_hops[index]) if index < len(param_hops) and param_hops[index] is not None else int(base["params"].get("hops", 1) or 1)
                    elif kind == "complete_fragment":
                        params["max_hops"] = int(param_maxhops[index]) if index < len(param_maxhops) and param_maxhops[index] is not None else int(base["params"].get("max_hops", 32) or 32)
                    elif kind == "complete_polyhedron":
                        params["cutoff"] = float(param_cutoff[index]) if index < len(param_cutoff) and param_cutoff[index] is not None else float(base["params"].get("cutoff", 4.0) or 4.0)
                    elif kind == "by_symmetry":
                        ops_text = param_ops[index] if index < len(param_ops) else None
                        if ops_text:
                            try:
                                import json as _json
                                params["ops"] = _json.loads(ops_text)
                            except (ValueError, TypeError):
                                params["ops"] = base["params"].get("ops") or []
                        else:
                            params["ops"] = base["params"].get("ops") or []
                elif kind == "slab":
                    miller = [
                        int(param_miller0[index]) if index < len(param_miller0) and param_miller0[index] is not None else (base["params"].get("miller") or [0, 0, 1])[0],
                        int(param_miller1[index]) if index < len(param_miller1) and param_miller1[index] is not None else (base["params"].get("miller") or [0, 0, 1])[1],
                        int(param_miller2[index]) if index < len(param_miller2) and param_miller2[index] is not None else (base["params"].get("miller") or [0, 0, 1])[2],
                    ]
                    layers_val = param_layers[index] if index < len(param_layers) and param_layers[index] is not None else base["params"].get("layers")
                    vacuum_val = param_vacuum[index] if index < len(param_vacuum) and param_vacuum[index] is not None else base["params"].get("vacuum", 10.0)
                    params = {
                        "miller": miller,
                        "layers": int(layers_val) if layers_val is not None else None,
                        "vacuum": float(vacuum_val or 10.0),
                    }
                new_transforms.append(
                    {
                        "id": transform_id,
                        "name": base.get("name") or "",
                        "kind": kind,
                        "params": params,
                        "enabled": "yes" in (enableds[index] if index < len(enableds) else []),
                    }
                )
            try:
                backend.patch_state({"transforms": new_transforms}, scene_id=scene_id, broadcast=False)
            except Exception:
                return no_update, no_update
            perf_log.record(
                "callback:manage_transforms",
                duration_ms=(time.monotonic() - cb_start) * 1000.0,
                kind="cb",
                info={
                    "trigger": triggered_label,
                    "n_transforms": len(new_transforms),
                    "scene_id": scene_id,
                },
            )
            # An enabled toggle doesn't change the parameter widgets,
            # but ``patch_state`` may have mutated the transform list
            # ordering / id set; safe to push state without rebuilding.
            return no_update, backend.get_state()

        return no_update, no_update

    # ------------------------------------------------------------------
    # Phase 4 UI: right-click context menu + keyboard shortcuts.
    #
    # Wiring overview:
    #   1. ``assets/right_click_menu.js`` listens for native
    #      ``contextmenu`` on ``#crystal-graph`` and writes a payload
    #      ``{kind, payload, x, y, ts}`` into
    #      ``dcc.Store(id="rightclick-target")`` via
    #      ``dash_clientside.set_props``.
    #   2. ``assets/keyboard_shortcuts.js`` writes the same store but
    #      with an extra ``action`` field (e.g. ``"supercell_2x"``,
    #      ``"hide"``, ``"grow_bonds"``) so a single dispatch callback
    #      can handle keyboard actions.
    #   3. ``sync_rightclick_fallback`` mirrors the hidden text-input
    #      fallback into the store for the rare case set_props isn't
    #      bootstrapped.
    #   4. ``render_rightclick_menu`` rebuilds the popover children
    #      based on the picked-target kind and positions it.
    #   5. ``apply_rightclick_action`` dispatches both popover button
    #      clicks (Hide / Grow / Analyze / Promote) and keyboard
    #      shortcut actions to backend mutations.
    #   6. ``apply_rightclick_color`` handles the inline colour picker
    #      that lives inside the popover.
    #   7. ``toggle_kbd_help`` shows/hides the keyboard-help overlay
    #      (close button only -- the JS handles the ``?`` toggle).
    # ------------------------------------------------------------------
    @app.callback(
        Output("rightclick-target", "data", allow_duplicate=True),
        Input("rightclick-target-fallback", "value"),
        prevent_initial_call=True,
    )
    def sync_rightclick_fallback(raw_value):
        if not raw_value:
            return no_update
        try:
            import json as _json
            payload = _json.loads(raw_value)
            return payload
        except (ValueError, TypeError):
            return no_update

    @app.callback(
        Output("rightclick-menu", "children"),
        Output("rightclick-menu", "style"),
        Output("rightclick-menu", "className"),
        Input("rightclick-target", "data"),
    )
    def render_rightclick_menu(target):
        from dash import dcc, html

        hidden_class = "rightclick-menu rightclick-menu--hidden"
        empty_style = {"top": "0px", "left": "0px"}
        if not target or not isinstance(target, dict):
            return [], empty_style, hidden_class
        kind = target.get("kind")
        if kind == "_close":
            return [], empty_style, hidden_class
        # Keyboard-shortcut path: just dispatch and don't render. We
        # still want the popover hidden (it might have been visible
        # before).
        if target.get("action"):
            return [], empty_style, hidden_class
        payload = target.get("payload") or {}
        x = int(target.get("x") or 0)
        y = int(target.get("y") or 0)
        items: list[Any] = []
        header_text = ""
        color_picker_color = "#888888"

        if kind == "atom":
            label = payload.get("label") or "(atom)"
            elem = payload.get("element") or ""
            header_text = f"Atom \u00b7 {label} ({elem})"
            items.extend([
                html.Div(
                    [
                        html.Label("Colour", htmlFor="rcm-color-picker"),
                        dcc.Input(
                            id="rcm-color-picker",
                            type="color",
                            value=color_picker_color,
                            debounce=False,
                        ),
                    ],
                    className="rightclick-menu__color",
                ),
                html.Button(
                    [html.Span("Hide this atom"), html.Span("h", className="rightclick-menu__shortcut")],
                    id="rcm-action-hide",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Button(
                    [html.Span("Grow by 1 bond hop"), html.Span("g", className="rightclick-menu__shortcut")],
                    id="rcm-action-grow-bonds",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Button(
                    [html.Span("Grow by 4\u202f\u00c5 radius"), html.Span("\u21e7g", className="rightclick-menu__shortcut")],
                    id="rcm-action-grow-radius",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Button(
                    "Complete fragment",
                    id="rcm-action-complete-fragment",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Button(
                    "Analyze coordination",
                    id="rcm-action-analyze",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Div(className="rightclick-menu__divider"),
                html.Button(
                    [html.Span("Promote to group rule"), html.Span("p", className="rightclick-menu__shortcut")],
                    id="rcm-action-promote",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
            ])
        elif kind == "polyhedron":
            label = payload.get("fragment_label") or "(polyhedron)"
            header_text = f"Polyhedron \u00b7 {label}"
            items.extend([
                html.Div(
                    [
                        html.Label("Colour", htmlFor="rcm-color-picker"),
                        dcc.Input(
                            id="rcm-color-picker",
                            type="color",
                            value=color_picker_color,
                            debounce=False,
                        ),
                    ],
                    className="rightclick-menu__color",
                ),
                html.Button(
                    [html.Span("Hide this polyhedron"), html.Span("h", className="rightclick-menu__shortcut")],
                    id="rcm-action-hide",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Button(
                    "Complete coordination",
                    id="rcm-action-complete-fragment",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Div(className="rightclick-menu__divider"),
                # Keep the rest of the popover schema consistent with
                # the atom branch so the buttons exist for the
                # callback's Inputs (Dash needs the ids present).
                html.Button(
                    "Grow polyhedron neighbourhood",
                    id="rcm-action-grow-bonds",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Button(
                    "Grow by 4\u202f\u00c5 radius",
                    id="rcm-action-grow-radius",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Button(
                    "Re-analyze",
                    id="rcm-action-analyze",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Button(
                    "Promote to group rule",
                    id="rcm-action-promote",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
            ])
        elif kind == "bond":
            label = payload.get("label_pair") or "(bond)"
            elements = payload.get("element_pair") or ""
            header_text = f"Bond \u00b7 {label} ({elements})"
            items.extend([
                html.Div(
                    [
                        html.Label("Colour", htmlFor="rcm-color-picker"),
                        dcc.Input(
                            id="rcm-color-picker",
                            type="color",
                            value=color_picker_color,
                            debounce=False,
                        ),
                    ],
                    className="rightclick-menu__color",
                ),
                html.Button(
                    [html.Span("Hide bonds like this"), html.Span("h", className="rightclick-menu__shortcut")],
                    id="rcm-action-hide",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Button(
                    "Promote to bond-group rule",
                    id="rcm-action-promote",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Div(className="rightclick-menu__divider"),
                # Hidden no-ops to satisfy callback Input list.
                html.Button("", id="rcm-action-grow-bonds", n_clicks=0, style={"display": "none"}),
                html.Button("", id="rcm-action-grow-radius", n_clicks=0, style={"display": "none"}),
                html.Button("", id="rcm-action-complete-fragment", n_clicks=0, style={"display": "none"}),
                html.Button("", id="rcm-action-analyze", n_clicks=0, style={"display": "none"}),
            ])
        else:
            return [], empty_style, hidden_class

        children: list[Any] = [html.Div(header_text, className="rightclick-menu__header")] + items
        # Position: clamp so the menu stays inside the viewport. The
        # JS sends viewport coords (clientX/clientY); we use position:
        # fixed in CSS so the same coords work directly.
        style = {
            "top": f"{max(8, y)}px",
            "left": f"{max(8, x)}px",
        }
        return children, style, "rightclick-menu"

    @app.callback(
        Output("agent-state-store", "data", allow_duplicate=True),
        Output("rightclick-target", "data", allow_duplicate=True),
        Input("rcm-action-hide", "n_clicks"),
        Input("rcm-action-grow-bonds", "n_clicks"),
        Input("rcm-action-grow-radius", "n_clicks"),
        Input("rcm-action-complete-fragment", "n_clicks"),
        Input("rcm-action-analyze", "n_clicks"),
        Input("rcm-action-promote", "n_clicks"),
        Input("rightclick-target", "data"),
        State("scene-tabs", "value"),
        prevent_initial_call=True,
    )
    def apply_rightclick_action(
        hide_clicks,
        grow_bonds_clicks,
        grow_radius_clicks,
        complete_clicks,
        analyze_clicks,
        promote_clicks,
        target,
        active_scene_id,
    ):
        triggered = getattr(callback_context, "triggered_id", None)
        if not target or not isinstance(target, dict):
            return no_update, no_update
        scene_id = active_scene_id or backend.active_scene_id()
        kind = target.get("kind")
        payload = target.get("payload") or {}
        # Keyboard path: store update with ``action`` set; do not also
        # consume button clicks on the same event.
        action = None
        if triggered == "rightclick-target":
            action = target.get("action")
            if not action:
                return no_update, no_update
        else:
            mapping = {
                "rcm-action-hide": "hide",
                "rcm-action-grow-bonds": "grow_bonds",
                "rcm-action-grow-radius": "grow_radius",
                "rcm-action-complete-fragment": "complete_fragment",
                "rcm-action-analyze": "analyze",
                "rcm-action-promote": "promote_to_group",
            }
            action = mapping.get(triggered)
            if action is None:
                return no_update, no_update

        try:
            _dispatch_rightclick_action(backend, scene_id, action, kind, payload, target)
        except Exception:  # pragma: no cover - best-effort; surface in browser console
            return no_update, {"kind": "_close", "ts": time.time()}
        # Close the popover after a successful action.
        return backend.get_state(), {"kind": "_close", "ts": time.time()}

    @app.callback(
        Output("agent-state-store", "data", allow_duplicate=True),
        Input("rcm-color-picker", "value"),
        State("rightclick-target", "data"),
        State("scene-tabs", "value"),
        prevent_initial_call=True,
    )
    def apply_rightclick_color(color, target, active_scene_id):
        if not color or not target or not isinstance(target, dict):
            return no_update
        scene_id = active_scene_id or backend.active_scene_id()
        kind = target.get("kind")
        payload = target.get("payload") or {}
        try:
            _dispatch_rightclick_action(
                backend, scene_id, "set_color", kind, payload, target, color=str(color)
            )
        except Exception:
            return no_update
        return backend.get_state()

    @app.callback(
        Output("kbd-help", "className"),
        Input("kbd-help-close", "n_clicks"),
        prevent_initial_call=True,
    )
    def close_kbd_help(_):
        return "kbd-help kbd-help--hidden"

    # ------------------------------------------------------------------
    # View tools (Phase 4): VESTA-style axis alignment + projection
    # toggle.
    #
    # Both callbacks call into ``backend.camera_action`` (the same path
    # exercised by ``POST /api/v2/camera/action``), then push the new
    # camera into ``camera-state-store`` only. The browser-side fast path
    # has already relaid out the Plotly scene, so touching
    # ``agent-state-store`` would just trigger a wasteful full-figure
    # rebuild for a layout-only change.
    # ------------------------------------------------------------------
    @app.callback(
        Output("camera-state-store", "data", allow_duplicate=True),
        Input("view-align-a", "n_clicks"),
        Input("view-align-b", "n_clicks"),
        Input("view-align-c", "n_clicks"),
        Input("view-align-astar", "n_clicks"),
        Input("view-align-bstar", "n_clicks"),
        Input("view-align-cstar", "n_clicks"),
        Input("view-reset", "n_clicks"),
        State("scene-tabs", "value"),
        prevent_initial_call=True,
    )
    def apply_view_action(_a, _b, _c, _astar, _bstar, _cstar, _reset, scene_id):
        triggered = getattr(callback_context, "triggered_id", None)
        if not triggered:
            return no_update
        scene_id = scene_id or backend.active_scene_id()
        button_to_axis = {
            "view-align-a": "a",
            "view-align-b": "b",
            "view-align-c": "c",
            "view-align-astar": "a*",
            "view-align-bstar": "b*",
            "view-align-cstar": "c*",
        }
        try:
            if triggered == "view-reset":
                camera = backend.camera_action("reset", scene_id=scene_id, broadcast=False)
            elif triggered in button_to_axis:
                camera = backend.camera_action(
                    "align",
                    scene_id=scene_id,
                    broadcast=False,
                    axis=button_to_axis[triggered],
                )
            else:
                return no_update
        except Exception:  # pragma: no cover - best-effort, surface in console
            return no_update
        return _camera_store_payload(scene_id, camera)

    @app.callback(
        Output("view-projection", "value", allow_duplicate=True),
        Input("agent-state-store", "data"),
        prevent_initial_call=True,
    )
    def sync_view_projection_from_state(state):
        # Mirror ``state["projection"]`` onto the radio so externally
        # driven changes (REST mutations, scene switches) keep the UI
        # honest. The matched-value short-circuit in
        # ``apply_view_projection`` prevents the round-trip from
        # ratcheting the figure cache.
        if not isinstance(state, dict):
            return no_update
        return _coerce_projection(state.get("projection") or "perspective")

    @app.callback(
        Output("camera-state-store", "data", allow_duplicate=True),
        Input("view-projection", "value"),
        State("scene-tabs", "value"),
        prevent_initial_call=True,
    )
    def apply_view_projection(projection, scene_id):
        if not projection:
            return no_update
        scene_id = scene_id or backend.active_scene_id()
        # Skip the redraw if the user clicked the radio that was
        # already selected -- avoids ratcheting the figure JSON cache
        # for a no-op.
        current = backend.get_state(scene_id).get("projection", "perspective")
        if str(projection) == str(current):
            return no_update
        try:
            camera = backend.set_projection(projection, scene_id=scene_id, broadcast=False)
        except Exception:  # pragma: no cover
            return no_update
        return _camera_store_payload(scene_id, camera)

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
        Output("fast-view-metadata", "children", allow_duplicate=True),
        Input("agent-state-store", "data"),
        State("camera-state-store", "data"),
        prevent_initial_call=True,
    )
    def refresh_fast_view_metadata(agent_state, camera_state):
        state = backend.normalize_state(agent_state or backend.get_state())
        return _fast_view_metadata(backend, state, camera_state)

    @app.callback(
        Output("minor-opacity-slider", "disabled"),
        Output("minor-opacity-control", "style"),
        Input("disorder-selector", "value"),
    )
    def gate_minor_opacity(disorder):
        return _minor_opacity_disabled(disorder), _minor_opacity_control_style(disorder)

    @app.callback(
        Output("polyhedra-controls", "style"),
        Input("topology-toggle", "value"),
    )
    def gate_polyhedra_controls(topology_toggle):
        return _polyhedra_controls_style("enabled" in (topology_toggle or []))

    # ------------------------------------------------------------------
    # Perf-log panel
    #
    # Polls the in-process ``perf_log`` ring buffer every second and
    # appends new entries to the on-screen list. Each entry shows a
    # local-time clock, the callback / event label, the duration
    # (colour-coded), and a short payload summary (filename, atom
    # count, ...). The store keeps the latest sequence number so the
    # poll only ships new events.
    # ------------------------------------------------------------------
    @app.callback(
        Output("perf-log-panel", "className"),
        Input("perf-log-toggle", "n_clicks"),
        State("perf-log-panel", "className"),
        prevent_initial_call=True,
    )
    def toggle_perf_log(_, current_class):
        cls = current_class or "perf-log-panel perf-log-panel--collapsed"
        if "perf-log-panel--collapsed" in cls:
            return "perf-log-panel perf-log-panel--expanded"
        return "perf-log-panel perf-log-panel--collapsed"

    @app.callback(
        Output("perf-log-cursor", "data", allow_duplicate=True),
        Input("perf-log-clear", "n_clicks"),
        prevent_initial_call=True,
    )
    def clear_perf_log(_):
        perf_log.clear()
        return {"seq": perf_log.latest_seq(), "events": []}

    @app.callback(
        Output("perf-log-body", "children"),
        Output("perf-log-cursor", "data"),
        Input("perf-log-poll", "n_intervals"),
        State("perf-log-cursor", "data"),
    )
    def refresh_perf_log(_, cursor):
        cursor = cursor or {"seq": 0, "events": []}
        new_events = perf_log.recent(limit=200, since_seq=int(cursor.get("seq", 0)))
        if not new_events and cursor.get("events"):
            return no_update, no_update
        merged = list(cursor.get("events") or []) + new_events
        # Keep only the latest 80 entries on screen so the DOM stays
        # cheap; the full ring buffer is still available via
        # ``GET /api/v1/perf``.
        merged = merged[-80:]
        rows = [_perf_log_row(entry) for entry in reversed(merged)]
        latest = merged[-1]["seq"] if merged else int(cursor.get("seq", 0))
        return rows, {"seq": latest, "events": merged}

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
        # ``update_view`` is the dominant cost when the user pokes a
        # slider or a colour swatch -- it rebuilds the figure, the
        # topology histogram, and the structure-summary table in one
        # callback. Wrap it so the perf log makes the total wall time
        # observable. ``figure_for_state`` itself is instrumented
        # internally with three sub-blocks (``scene_for_state``,
        # ``topology_for_state``, ``build_figure``) so the user can
        # tell which leg is slow without re-profiling.
        cb_start = time.monotonic()
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
            # Phase 1/2: per-scene specs and atom-group rules both
            # affect what the right-side analysis panel reads (which
            # spec owns the histogram, which atoms are hidden from
            # the structure summary). Without these the markdown
            # table can stay stale when the user adds / removes a
            # named polyhedron or atom-group rule.
            tuple(
                (s.get("id"), s.get("center_species"), s.get("ligand_species"), s.get("color"), bool(s.get("enabled", True)))
                for s in (state.get("polyhedron_specs") or [])
            ),
            tuple(
                (
                    g.get("id"),
                    bool(g.get("visible", True)),
                    g.get("color"),
                    g.get("opacity"),
                    tuple(sorted((g.get("selector") or {}).get("elements") or [])) if (g.get("selector") or {}).get("elements") else None,
                    bool((g.get("selector") or {}).get("all", False)),
                    (g.get("selector") or {}).get("is_minor"),
                )
                for g in (state.get("atom_groups") or [])
            ),
        )
        prev_key = getattr(update_view, "_topo_cache_key", None)
        if prev_key == topo_key:
            perf_log.record(
                "callback:update_view",
                duration_ms=(time.monotonic() - cb_start) * 1000.0,
                kind="cb",
                info={
                    "scene_id": state.get("scene_id"),
                    "side_panel": "cached",
                },
            )
            return fig, no_update, no_update, no_update
        update_view._topo_cache_key = topo_key
        with perf_log.time_block("update_view:side_panel", kind="event"):
            summary = _structure_summary(backend.scene_for_state(state))
            histogram = topology_histogram_figure(topology_data)
            md = topology_results_markdown(topology_data)
        perf_log.record(
            "callback:update_view",
            duration_ms=(time.monotonic() - cb_start) * 1000.0,
            kind="cb",
            info={"scene_id": state.get("scene_id"), "side_panel": "rebuilt"},
        )
        return fig, histogram, md, summary

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
    if str(os.environ.get("MATTERVIS_PREWARM", "1")).lower() not in {"0", "false", "no", "off"}:
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


_PREWARM_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mattervis-prewarm")


def _prewarm_bundle_async(backend: ViewerBackend, structure_name: str) -> None:
    def _job():
        try:
            bundle = backend.get_bundle(structure_name)
            defaults = backend.default_state(structure_name)
        except Exception:
            return
        old_scene = getattr(bundle, "scene", None)
        try:
            for display_mode in ("formula_unit", "asymmetric_unit", "unit_cell", "cluster"):
                for show_hydrogen in (False, True):
                    try:
                        scene = build_bundle_scene(
                            bundle,
                            display_mode=display_mode,
                            show_hydrogen=show_hydrogen,
                            preset=backend.preset,
                        )
                        style = dict(scene.get("style", {}))
                        options = list(defaults.get("display_options") or [])
                        if show_hydrogen and "hydrogens" not in options:
                            options.append("hydrogens")
                        elif not show_hydrogen:
                            options = [opt for opt in options if opt != "hydrogens"]
                        style.update(
                            style_from_controls(
                                defaults["atom_scale"],
                                defaults["bond_radius"],
                                defaults["minor_opacity"],
                                defaults["axis_scale"],
                                options,
                                material=defaults.get("material"),
                                render_style=defaults.get("style"),
                                disorder=defaults.get("disorder"),
                                ortep_mode=defaults.get("ortep_mode"),
                            )
                        )
                        style["display_mode"] = display_mode
                        style["topology_enabled"] = False
                        build_figure(scene, style, topology_data=None)
                    except Exception:
                        continue
        finally:
            if old_scene is not None:
                bundle.scene = old_scene

    try:
        _PREWARM_EXECUTOR.submit(_job)
    except Exception:
        pass


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
        # heavier display scopes. The prewarm thread is on by default and
        # can be disabled with MATTERVIS_PREWARM=0 for constrained hosts.
        ready = getattr(backend, "_first_figure_ready", None)
        if ready is not None:
            ready.wait(timeout=1.5)
        else:
            time.sleep(1.5)
        for name in list(backend.bundles.keys()):
            _prewarm_bundle_async(backend, name)

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
