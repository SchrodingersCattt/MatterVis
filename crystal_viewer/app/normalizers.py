from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *

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


def _coerce_polyhedron_enforce_enclosure(raw: Any) -> bool:
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in {"gap", "gap_only", "false", "0", "no", "off"}:
            return False
        if text in {"gap_enclosure", "enclosure", "true", "1", "yes", "on"}:
            return True
    return bool(raw) if raw is not None else True


def _coerce_centroid_offset_frac(raw: Any) -> float:
    value = _coerce_optional_float(
        raw,
        lo=0.0,
        hi=10.0,
    )
    return float(DEFAULT_CENTROID_OFFSET_FRAC if value is None else value)


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
    enforce_enclosure = _coerce_polyhedron_enforce_enclosure(raw.get("enforce_enclosure", True))
    centroid_offset_frac = _coerce_centroid_offset_frac(
        raw.get("centroid_offset_frac", DEFAULT_CENTROID_OFFSET_FRAC)
    )
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
        "enforce_enclosure": enforce_enclosure,
        "centroid_offset_frac": centroid_offset_frac,
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
    from ..transforms import KNOWN_TRANSFORM_KINDS

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

__all__ = [name for name in globals() if not name.startswith("__")]
