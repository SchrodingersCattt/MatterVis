"""Explicit policies for scientific properties on structural edits.

Silent dropping is forbidden. A field that is present and omitted from
the request policy rejects the operation. ``drop`` requires a reason.
``copy`` is refused when the output length differs. ``transform`` follows
the operation's source-index map and refuses invented values.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from .errors import PropertyPolicyError

SITE_FIELDS = ("charges", "forces", "adp", "velocities", "scalars", "formal_charges")
STRUCTURE_FIELDS = ("energy", "stress")
# Laboratory-frame quantities. ``transform`` during a rotation applies R
# to vectors and ``R U R^T`` to tensors. Other site fields are reindexed only.
_CARTESIAN_VECTOR_FIELDS = frozenset({"forces", "velocities"})
_CARTESIAN_TENSOR_FIELDS = frozenset({"adp"})


def empty_properties() -> dict[str, Any]:
    return {"site": {}, "structure": {}}


def normalize_properties(properties: Mapping[str, Any] | None) -> dict[str, Any]:
    """Accept either a flat site-array dict or ``{site, structure}``."""

    if not properties:
        return empty_properties()
    if "site" in properties or "structure" in properties:
        site = dict(properties.get("site") or {})
        structure = dict(properties.get("structure") or {})
    else:
        site = {}
        structure = {}
        for key, value in properties.items():
            if key in STRUCTURE_FIELDS:
                structure[key] = value
            else:
                site[key] = value
    return {
        "site": {str(key): _freeze_sequence(value) for key, value in site.items()},
        "structure": {str(key): _freeze_scalar_or_sequence(value) for key, value in structure.items()},
    }


def _freeze_sequence(value: Any) -> tuple:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise PropertyPolicyError("site properties must be per-site sequences")
    return tuple(_freeze_scalar_or_sequence(item) for item in value)


def _freeze_scalar_or_sequence(value: Any):
    if isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes)):
        return tuple(_freeze_scalar_or_sequence(item) for item in value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        number = float(value)
        if number.is_integer() and abs(number) < 2**53 and isinstance(value, int):
            return int(value)
        return float(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return number


def property_fingerprint(properties: Mapping[str, Any]) -> list:
    """Stable, JSON-ready view of a property payload."""

    normalized = normalize_properties(properties)
    return [
        sorted(normalized["site"].items()),
        sorted(normalized["structure"].items()),
    ]


def apply_property_policy(
    properties: Mapping[str, Any],
    policy: Mapping[str, Any] | None,
    *,
    command: str,
    index_map: list[int | None],
    invalid_structure_fields: set[str],
    site_orientations: Mapping[int, Any] | None = None,
) -> tuple[dict[str, Any], tuple[dict[str, str], ...]]:
    """Return transformed properties and the explicit drop record.

    ``index_map[output_index]`` is the source global index, or ``None``
    when the output site is new. Structure-level fields listed by the
    operation as invalid cannot be copied or blindly transformed.

    ``site_orientations`` maps a source global index to the active Cartesian
    rotation applied to that site. ``transform`` uses it for Cartesian
    vector and tensor fields. ``copy`` keeps the stored numbers.
    """

    current = normalize_properties(properties)
    requested = dict(policy or {})
    output_count = len(index_map)
    site_out: dict[str, tuple] = {}
    structure_out: dict[str, Any] = {}
    dropped: list[dict[str, str]] = []

    for scope, bucket, destination in (
        ("site", current["site"], site_out),
        ("structure", current["structure"], structure_out),
    ):
        for key, value in bucket.items():
            spec = requested.get(key)
            if spec is None:
                raise PropertyPolicyError(
                    f"{scope} property {key!r} is present on the input and the "
                    f"{command} request does not declare a property policy for it"
                )
            if not isinstance(spec, Mapping):
                raise PropertyPolicyError(f"property policy for {key!r} must be an object")
            action = str(spec.get("action") or "")
            if action == "reject":
                raise PropertyPolicyError(spec.get("reason") or f"property {key!r} rejected {command}")
            if action == "drop" or action == "recompute":
                reason = str(spec.get("reason") or "").strip()
                if not reason:
                    raise PropertyPolicyError(f"property {key!r} {action} requires an explicit reason")
                dropped.append({"field": key, "scope": scope, "action": action, "reason": reason})
                continue
            if scope == "structure" and key in invalid_structure_fields and action in {"copy", "transform"}:
                raise PropertyPolicyError(
                    f"structure property {key!r} cannot be {action} by {command}; "
                    "drop it with a reason or reject the operation"
                )
            if action == "copy":
                if scope == "site" and len(value) != output_count:
                    raise PropertyPolicyError(
                        f"property {key!r} cannot be copied: input length {len(value)} "
                        f"does not match output length {output_count}"
                    )
                destination[key] = value
                continue
            if action == "transform":
                if scope == "structure":
                    if "value" not in spec:
                        raise PropertyPolicyError(
                            f"structure property {key!r} transform requires an explicit value"
                        )
                    destination[key] = _freeze_scalar_or_sequence(spec["value"])
                    continue
                destination[key] = _map_site_values(
                    key,
                    value,
                    index_map,
                    spec,
                    site_orientations or {},
                )
                continue
            raise PropertyPolicyError(f"unknown property action {action!r} for {key!r}")

    unknown = set(requested) - set(current["site"]) - set(current["structure"])
    if unknown:
        raise PropertyPolicyError(
            "property policy names fields that are not on the input: " + ", ".join(sorted(unknown))
        )
    return {"site": site_out, "structure": structure_out}, tuple(dropped)


def _map_site_values(
    key: str,
    values: tuple,
    index_map: list[int | None],
    spec: Mapping[str, Any],
    site_orientations: Mapping[int, Any],
) -> tuple:
    supplied = spec.get("new_values")
    supplied_map: dict[int, Any] = {}
    if isinstance(supplied, Mapping):
        supplied_map = {int(index): item for index, item in supplied.items()}
    elif isinstance(supplied, (list, tuple)):
        new_positions = [index for index, source in enumerate(index_map) if source is None]
        if len(supplied) != len(new_positions):
            raise PropertyPolicyError(
                f"property {key!r} new_values length {len(supplied)} does not match "
                f"{len(new_positions)} inserted sites"
            )
        supplied_map = dict(zip(new_positions, supplied))
    elif supplied is not None:
        raise PropertyPolicyError(f"property {key!r} new_values must be a list or object")

    output = []
    for output_index, source_index in enumerate(index_map):
        if source_index is None:
            if output_index not in supplied_map:
                raise PropertyPolicyError(
                    f"property {key!r} has no value for inserted site {output_index}; "
                    "supply new_values or drop the field with a reason"
                )
            output.append(_freeze_scalar_or_sequence(supplied_map[output_index]))
            continue
        if not 0 <= int(source_index) < len(values):
            raise PropertyPolicyError(f"property {key!r} source index {source_index} is outside the input")
        output.append(
            _orient_property(key, values[int(source_index)], site_orientations.get(int(source_index)))
        )
    return tuple(output)


def _orient_property(key: str, value: Any, rotation: Any) -> Any:
    """Rotate one laboratory-frame property value, or return it unchanged."""

    if rotation is None or key not in _CARTESIAN_VECTOR_FIELDS | _CARTESIAN_TENSOR_FIELDS:
        return value
    matrix = np.asarray(rotation, dtype=float)
    if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
        raise PropertyPolicyError(f"property {key!r} rotation must be a finite 3x3 matrix")
    if key in _CARTESIAN_VECTOR_FIELDS:
        vector = _numeric_rows(value, key, shape=(3,))
        return _freeze_scalar_or_sequence((matrix @ vector).tolist())
    tensor, flat = _tensor_value(key, value)
    rotated = matrix @ tensor @ matrix.T
    if flat:
        return _freeze_scalar_or_sequence(rotated.reshape(9).tolist())
    return _freeze_scalar_or_sequence(rotated.tolist())


def _numeric_rows(value: Any, key: str, *, shape: tuple[int, ...]) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.shape != shape or not np.all(np.isfinite(array)):
        expected = "x".join(str(item) for item in shape)
        raise PropertyPolicyError(
            f"property {key!r} must contain finite values with shape ({expected}) to rotate"
        )
    return array


def _tensor_value(key: str, value: Any) -> tuple[np.ndarray, bool]:
    array = np.asarray(value, dtype=float)
    if array.shape == (3, 3) and np.all(np.isfinite(array)):
        return array, False
    if array.shape == (9,) and np.all(np.isfinite(array)):
        return array.reshape(3, 3), True
    raise PropertyPolicyError(
        f"property {key!r} must be a finite 3x3 tensor or 9 components to rotate"
    )


__all__ = [
    "SITE_FIELDS",
    "STRUCTURE_FIELDS",
    "apply_property_policy",
    "empty_properties",
    "normalize_properties",
    "property_fingerprint",
]
