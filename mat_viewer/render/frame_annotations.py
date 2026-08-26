"""Resolve auditable per-frame fields into animation labels."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from hashlib import sha256
import math
from pathlib import Path
import re
from string import Formatter
from typing import Any, Literal, Mapping, Sequence

from .animation_time import TimePosition

FrameFieldRole = Literal["progress", "observable", "stage"]
_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class FrameFieldSpec:
    """One value resolved for every selected source frame."""

    name: str
    source: str
    role: FrameFieldRole = "observable"
    unit: str = ""
    scale: float = 1.0
    offset: float = 0.0

    def __post_init__(self) -> None:
        if not _FIELD_NAME.fullmatch(self.name):
            raise ValueError(
                "frame-field name must start with a letter or underscore and "
                "contain only letters, digits, and underscores"
            )
        if self.role not in {"progress", "observable", "stage"}:
            raise ValueError("frame-field role must be progress, observable, or stage")
        if not self.source:
            raise ValueError("frame-field source cannot be empty")
        if not math.isfinite(self.scale) or not math.isfinite(self.offset):
            raise ValueError("frame-field scale and offset must be finite")


@dataclass(frozen=True, slots=True)
class FrameAnnotationSpec:
    """Template and fields for one deterministic paper-space frame label."""

    fields: tuple[FrameFieldSpec, ...]
    template: str
    position: TimePosition = "top-left"

    def __post_init__(self) -> None:
        if not self.fields:
            raise ValueError("frame annotations require at least one --frame-field")
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("frame-field names must be unique")
        if not self.template:
            raise ValueError("--frame-label cannot be empty")
        if self.position not in {
            "top-left",
            "top-right",
            "bottom-left",
            "bottom-right",
        }:
            raise ValueError("unsupported frame-label position")
        referenced = set()
        for _, field_name, _, _ in Formatter().parse(self.template):
            if field_name is None:
                continue
            if not _FIELD_NAME.fullmatch(field_name):
                raise ValueError(
                    "--frame-label fields must be simple frame-field names"
                )
            referenced.add(field_name)
        missing = referenced - set(names)
        if missing:
            raise ValueError(
                "--frame-label references undefined field(s): "
                + ", ".join(sorted(missing))
            )


@dataclass(frozen=True, slots=True)
class ResolvedFrameField:
    spec: FrameFieldSpec
    values: tuple[Any, ...]
    provenance: Mapping[str, Any]

    def to_metadata(self) -> dict[str, Any]:
        payload = asdict(self.spec)
        payload["values"] = list(self.values)
        payload["provenance"] = dict(self.provenance)
        return payload


@dataclass(frozen=True, slots=True)
class FrameAnnotationSeries:
    fields: tuple[ResolvedFrameField, ...]
    labels: tuple[str, ...]
    spec: FrameAnnotationSpec

    def to_metadata(self) -> dict[str, Any]:
        return {
            "displayed": True,
            "template": self.spec.template,
            "position": self.spec.position,
            "labels": list(self.labels),
            "fields": [field.to_metadata() for field in self.fields],
        }


def parse_frame_field(value: str) -> FrameFieldSpec:
    """Parse NAME=SOURCE[,role=...][,unit=...][,scale=...][,offset=...]."""

    if "=" not in value:
        raise ValueError("--frame-field must use NAME=SOURCE")
    name, remainder = value.split("=", 1)
    tokens = [token.strip() for token in remainder.split(",")]
    source = tokens[0]
    options: dict[str, str] = {}
    for token in tokens[1:]:
        if "=" not in token:
            raise ValueError(
                "frame-field modifiers must use KEY=VALUE after the source"
            )
        key, option_value = token.split("=", 1)
        key = key.strip()
        if key not in {"role", "unit", "scale", "offset"}:
            raise ValueError(f"unsupported frame-field modifier: {key}")
        if key in options:
            raise ValueError(f"duplicate frame-field modifier: {key}")
        options[key] = option_value.strip()
    return FrameFieldSpec(
        name=name.strip(),
        source=source,
        role=options.get("role", "observable"),
        unit=options.get("unit", ""),
        scale=float(options.get("scale", "1")),
        offset=float(options.get("offset", "0")),
    )


def parse_frame_fields(values: Sequence[str]) -> tuple[FrameFieldSpec, ...]:
    return tuple(parse_frame_field(value) for value in values)


def coerce_frame_annotation_spec(
    value: FrameAnnotationSpec | Mapping[str, Any] | None,
) -> FrameAnnotationSpec | None:
    if value is None or isinstance(value, FrameAnnotationSpec):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("frame_annotation must be FrameAnnotationSpec or a mapping")
    payload = dict(value)
    payload["fields"] = tuple(
        field if isinstance(field, FrameFieldSpec) else FrameFieldSpec(**dict(field))
        for field in payload.get("fields", ())
    )
    return FrameAnnotationSpec(**payload)


def _frame_info(frame: Any) -> Mapping[str, Any]:
    info = getattr(frame, "info", None)
    if isinstance(info, Mapping):
        return info
    if isinstance(frame, Mapping):
        nested = frame.get("frame_info")
        if isinstance(nested, Mapping):
            return nested
    return {}


def _source_frame_indices(frames: Sequence[Any]) -> tuple[int, ...]:
    indices = []
    for ordinal, frame in enumerate(frames):
        value = getattr(frame, "index", None)
        if value is None and isinstance(frame, Mapping):
            value = frame.get("frame_index")
        indices.append(ordinal if value is None else int(value))
    return tuple(indices)


def _coerce_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("frame-field values must be finite")
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        raise ValueError("frame-field values must be finite")
    return number


def _apply_transform(value: Any, spec: FrameFieldSpec) -> Any:
    if spec.role == "stage":
        if spec.scale != 1.0 or spec.offset != 0.0:
            raise ValueError(
                f"frame field {spec.name!r} is categorical and cannot use "
                "scale/offset"
            )
        return str(value)
    coerced = _coerce_value(value)
    if spec.scale == 1.0 and spec.offset == 0.0:
        return coerced
    if isinstance(coerced, bool) or not isinstance(coerced, (int, float)):
        raise ValueError(
            f"frame field {spec.name!r} is nonnumeric and cannot use scale/offset"
        )
    return float(coerced) * spec.scale + spec.offset


def _table_values(
    source: str,
    indices: Sequence[int],
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    payload = source.removeprefix("table:")
    if ":" not in payload:
        raise ValueError("table frame-field source must use table:PATH:COLUMN")
    path_text, column = payload.rsplit(":", 1)
    path = Path(path_text).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"frame-field table does not exist: {path}")
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".tab"} else ","
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames or column not in reader.fieldnames:
            raise ValueError(
                f"frame-field table column {column!r} was not found in {path}"
            )
        rows = list(reader)
    values = []
    for index in indices:
        if index < 0 or index >= len(rows):
            raise ValueError(
                f"frame-field table has no row for source frame index {index}"
            )
        values.append(rows[index][column])
    return tuple(values), {
        "kind": "table",
        "path": str(path),
        "sha256": sha256(path.read_bytes()).hexdigest(),
        "column": column,
        "row_mapping": "source_frame_index",
    }


def _resolve_field(
    frames: Sequence[Any],
    indices: Sequence[int],
    spec: FrameFieldSpec,
) -> ResolvedFrameField:
    source = spec.source
    if source == "index":
        raw_values = tuple(indices)
        provenance: dict[str, Any] = {"kind": "source_frame_index"}
    elif source.startswith("metadata:"):
        key = source.removeprefix("metadata:")
        if not key:
            raise ValueError("metadata frame-field source requires a key")
        infos = [_frame_info(frame) for frame in frames]
        if not all(key in info for info in infos):
            raise ValueError(
                f"metadata frame-field {spec.name!r} requires {key!r} on every "
                "selected frame"
            )
        raw_values = tuple(info[key] for info in infos)
        provenance = {"kind": "frame_metadata", "key": key}
    elif source.startswith("linear:"):
        parts = source.split(":")
        if len(parts) != 3:
            raise ValueError("linear frame-field source must use linear:START:STEP")
        start, step = map(float, parts[1:])
        if not math.isfinite(start) or not math.isfinite(step):
            raise ValueError("linear frame-field start and step must be finite")
        raw_values = tuple(start + index * step for index in indices)
        provenance = {
            "kind": "linear",
            "start": start,
            "step": step,
            "index": "source_frame_index",
        }
    elif source.startswith("table:"):
        raw_values, provenance = _table_values(source, indices)
    else:
        raise ValueError(
            "frame-field source must be index, metadata:KEY, "
            "linear:START:STEP, or table:PATH:COLUMN"
        )

    values = tuple(_apply_transform(value, spec) for value in raw_values)
    return ResolvedFrameField(spec=spec, values=values, provenance=provenance)


def resolve_frame_annotations(
    frames: Sequence[Any],
    spec: FrameAnnotationSpec | Mapping[str, Any],
) -> FrameAnnotationSeries:
    resolved_spec = coerce_frame_annotation_spec(spec)
    if resolved_spec is None:
        raise TypeError("frame annotation spec is required")
    selected = tuple(frames)
    if not selected:
        raise ValueError("cannot resolve frame annotations for an empty trajectory")
    indices = _source_frame_indices(selected)
    fields = tuple(
        _resolve_field(selected, indices, field) for field in resolved_spec.fields
    )
    labels = []
    for ordinal in range(len(selected)):
        values = {field.spec.name: field.values[ordinal] for field in fields}
        try:
            labels.append(resolved_spec.template.format_map(values))
        except (KeyError, ValueError) as exc:
            raise ValueError(f"could not format --frame-label: {exc}") from exc
    return FrameAnnotationSeries(
        fields=fields,
        labels=tuple(labels),
        spec=resolved_spec,
    )


__all__ = [
    "FrameAnnotationSeries",
    "FrameAnnotationSpec",
    "FrameFieldSpec",
    "ResolvedFrameField",
    "coerce_frame_annotation_spec",
    "parse_frame_field",
    "parse_frame_fields",
    "resolve_frame_annotations",
]
