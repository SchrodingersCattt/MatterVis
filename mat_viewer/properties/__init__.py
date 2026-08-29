"""Backend-neutral per-atom property discovery, reduction, and colour mapping.

This module owns the scientific contract for continuous atom colours.  It has
no dependency on a renderer: all frontends consume the same 256-entry LUT and
the same resolved range.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Iterable, Literal, Mapping, Sequence
import warnings

import numpy as np

PropertyReduction = Literal[
    "auto",
    "scalar",
    "magnitude",
    "component",
    "trace",
    "mean_normal",
    "von_mises",
]


@dataclass(frozen=True, slots=True)
class AtomPropertyColorSpec:
    """Describe how one or more per-atom fields become continuous colours."""

    fields: tuple[str, ...]
    reduction: PropertyReduction = "auto"
    component: str | int | None = None
    colormap: str = "viridis"
    value_range: tuple[float, float] | None = None
    center: float | None = None
    nan_color: str = "#BDBDBD"
    show_colorbar: bool = True
    label: str | None = None
    unit: str | None = None

    def __post_init__(self) -> None:
        fields = tuple(
            str(value).strip() for value in self.fields if str(value).strip()
        )
        if not fields:
            raise ValueError("atom property colouring requires at least one field")
        object.__setattr__(self, "fields", fields)
        supported = {
            "auto",
            "scalar",
            "magnitude",
            "component",
            "trace",
            "mean_normal",
            "von_mises",
        }
        reduction = str(self.reduction).strip().lower().replace("-", "_")
        if reduction not in supported:
            raise ValueError(
                f"unsupported atom property reduction {self.reduction!r}; "
                f"choose one of {', '.join(sorted(supported))}"
            )
        object.__setattr__(self, "reduction", reduction)
        if reduction == "component" and self.component is None:
            raise ValueError("component reduction requires component=NAME_OR_INDEX")
        if reduction != "component" and self.component is not None:
            raise ValueError("component is only valid with reduction='component'")
        if self.value_range is not None:
            if len(self.value_range) != 2:
                raise ValueError("value_range must contain (minimum, maximum)")
            lower, upper = (float(value) for value in self.value_range)
            if not np.isfinite((lower, upper)).all() or lower > upper:
                raise ValueError("value_range must contain finite MIN <= MAX")
            object.__setattr__(self, "value_range", (lower, upper))
        if self.center is not None and not np.isfinite(float(self.center)):
            raise ValueError("center must be finite")
        object.__setattr__(
            self, "center", None if self.center is None else float(self.center)
        )
        object.__setattr__(self, "colormap", str(self.colormap).strip())
        if not self.colormap:
            raise ValueError("colormap must not be empty")
        _parse_color(self.nan_color)
        if not isinstance(self.show_colorbar, bool):
            raise TypeError("show_colorbar must be a bool")


@dataclass(frozen=True, slots=True)
class PropertyDescriptor:
    """Bounded metadata for one available per-atom field."""

    field: str
    source: Literal["array", "column", "sidecar"]
    name: str
    dtype: str
    shape_tail: tuple[int, ...] = ()
    components: tuple[str, ...] = ()
    unit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "source": self.source,
            "name": self.name,
            "dtype": self.dtype,
            "shape_tail": list(self.shape_tail),
            "components": list(self.components),
            "unit": self.unit,
        }


@dataclass(frozen=True, slots=True)
class PropertyCatalog:
    """Resolve qualified fields while rejecting ambiguous bare names."""

    descriptors: tuple[PropertyDescriptor, ...] = ()

    def resolve(self, field: str) -> PropertyDescriptor:
        requested = str(field).strip()
        exact = [item for item in self.descriptors if item.field == requested]
        if len(exact) == 1:
            return exact[0]
        if ":" in requested:
            raise ValueError(f"unknown atom property field {requested!r}")
        matches = [item for item in self.descriptors if item.name == requested]
        if not matches:
            raise ValueError(f"unknown atom property field {requested!r}")
        if len(matches) > 1:
            choices = ", ".join(sorted(item.field for item in matches))
            raise ValueError(
                f"ambiguous atom property field {requested!r}; qualify it as {choices}"
            )
        return matches[0]

    def to_dict(self) -> list[dict[str, Any]]:
        return [descriptor.to_dict() for descriptor in self.descriptors]


@dataclass(frozen=True, slots=True)
class ResolvedPropertyScale:
    """A deterministic scalar range and RGBA lookup table."""

    value_range: tuple[float, float]
    center: float | None
    lut: np.ndarray
    finite_count: int
    missing_count: int
    lut_hash: str
    scope: str = "selected_source_frames_and_atoms"

    def __post_init__(self) -> None:
        lut = np.ascontiguousarray(self.lut, dtype=np.uint8)
        if lut.shape != (256, 4):
            raise ValueError("property LUT must have shape (256, 4)")
        lut.setflags(write=False)
        object.__setattr__(self, "lut", lut)


@dataclass(frozen=True, slots=True)
class ReducedProperty:
    """Reduced scalar values plus their field metadata."""

    values: np.ndarray
    reduction: str
    fields: tuple[str, ...]
    components: tuple[str, ...] = ()
    unit: str | None = None

    def __post_init__(self) -> None:
        values = np.ascontiguousarray(self.values, dtype=np.float32)
        if values.ndim != 1:
            raise ValueError("reduced atom property values must have shape (N,)")
        values.setflags(write=False)
        object.__setattr__(self, "values", values)


@dataclass(frozen=True, slots=True)
class SourcePropertyContext:
    """Resolved values for selected source frames before display replication."""

    spec: AtomPropertyColorSpec
    frames: tuple[ReducedProperty, ...]
    source_frame_indices: tuple[int, ...]
    scale: ResolvedPropertyScale
    manifest_hash: str | None = None

    def frame(self, source_frame_index: int) -> ReducedProperty:
        try:
            ordinal = self.source_frame_indices.index(int(source_frame_index))
        except ValueError as exc:
            raise ValueError(
                f"atom property context has no source frame {source_frame_index}"
            ) from exc
        return self.frames[ordinal]


def catalog_from_arrays(
    arrays: Mapping[str, Any],
    *,
    source: Literal["array", "sidecar"] = "array",
    units: Mapping[str, str | None] | None = None,
    components: Mapping[str, Sequence[str]] | None = None,
) -> PropertyCatalog:
    """Build bounded descriptors without copying array payloads."""

    prefix = f"{source}:"
    descriptors: list[PropertyDescriptor] = []
    for raw_name, raw_values in arrays.items():
        name = str(raw_name)
        if name.startswith(("array:", "column:", "sidecar:")):
            field = name
            name = name.split(":", 1)[1]
        else:
            field = f"{prefix}{name}"
        array = np.asarray(raw_values)
        if array.ndim < 1:
            continue
        descriptors.append(
            PropertyDescriptor(
                field=field,
                source=source,
                name=name,
                dtype=str(array.dtype),
                shape_tail=tuple(int(value) for value in array.shape[1:]),
                components=tuple(
                    str(value) for value in (components or {}).get(name, ())
                ),
                unit=(units or {}).get(name),
            )
        )
    return PropertyCatalog(tuple(sorted(descriptors, key=lambda item: item.field)))


def catalog_from_columns(columns: Iterable[str]) -> PropertyCatalog:
    descriptors = tuple(
        PropertyDescriptor(
            field=f"column:{name}",
            source="column",
            name=str(name),
            dtype="float64",
        )
        for name in dict.fromkeys(str(value).strip().lower() for value in columns)
        if name
    )
    return PropertyCatalog(descriptors)


def merge_catalogs(*catalogs: PropertyCatalog) -> PropertyCatalog:
    descriptors: dict[str, PropertyDescriptor] = {}
    for catalog in catalogs:
        for descriptor in catalog.descriptors:
            if descriptor.field in descriptors:
                raise ValueError(f"duplicate atom property field {descriptor.field!r}")
            descriptors[descriptor.field] = descriptor
    return PropertyCatalog(
        tuple(sorted(descriptors.values(), key=lambda item: item.field))
    )


def coerce_atom_property_color_spec(value: Any) -> AtomPropertyColorSpec | None:
    if value is None or isinstance(value, AtomPropertyColorSpec):
        return value
    if not isinstance(value, Mapping):
        raise TypeError(
            "atom_property_color must be AtomPropertyColorSpec, mapping, or None"
        )
    payload = dict(value)
    fields = payload.get("fields")
    if isinstance(fields, str):
        payload["fields"] = (fields,)
    elif fields is not None:
        payload["fields"] = tuple(fields)
    if payload.get("value_range") is not None:
        payload["value_range"] = tuple(payload["value_range"])
    return AtomPropertyColorSpec(**payload)


def resolve_source_property_context(
    source: Any,
    spec: AtomPropertyColorSpec | Mapping[str, Any],
) -> SourcePropertyContext:
    """Resolve fields and one exact scale over a StructureInput-like source."""

    resolved_spec = coerce_atom_property_color_spec(spec)
    assert resolved_spec is not None
    frames = tuple(getattr(source, "frames", ()) or ())
    if not frames:
        frames = (source,)
    manifest = getattr(source, "property_manifest", None)
    frame_indices = tuple(
        int(getattr(frame, "index", ordinal)) for ordinal, frame in enumerate(frames)
    )

    descriptors_by_field: dict[str, PropertyDescriptor] = {}
    catalogs: list[PropertyCatalog] = []
    for frame in frames:
        catalogs.append(catalog_from_arrays(_frame_atom_arrays(frame)))
    if manifest is not None:
        catalogs.append(manifest.catalog())
    catalog = _compatible_catalog(catalogs)
    descriptors = tuple(catalog.resolve(field) for field in resolved_spec.fields)
    for requested, descriptor in zip(resolved_spec.fields, descriptors):
        descriptors_by_field[requested] = descriptor

    sidecar_values: dict[str, dict[int, np.ndarray]] = {}
    if any(descriptor.source == "sidecar" for descriptor in descriptors):
        if manifest is None:
            raise ValueError(
                "sidecar atom property requested without property_data manifest"
            )
        from .loader.property_sidecar import align_sidecar_property

        source_path = getattr(source, "path", None)
        if source_path is None:
            raise ValueError("sidecar atom properties require a source file path")
        for descriptor in descriptors:
            if descriptor.source != "sidecar" or descriptor.name in sidecar_values:
                continue
            aligned = align_sidecar_property(
                manifest,
                descriptor.name,
                frames,
                source_path=source_path,
            )
            sidecar_values[descriptor.name] = {
                item.frame_index: item.values for item in aligned
            }

    component_names = _resolved_component_names(descriptors)
    units = {descriptor.unit for descriptor in descriptors if descriptor.unit}
    if resolved_spec.unit is None and len(units) > 1:
        raise ValueError(
            "selected atom property fields declare different units; set unit explicitly"
        )
    unit = resolved_spec.unit or (next(iter(units)) if units else None)
    reduced_frames: list[ReducedProperty] = []
    for ordinal, frame in enumerate(frames):
        values: list[np.ndarray] = []
        arrays = _frame_atom_arrays(frame)
        frame_index = frame_indices[ordinal]
        for requested, descriptor in zip(resolved_spec.fields, descriptors):
            if descriptor.source == "array":
                try:
                    raw = arrays[descriptor.name]
                except KeyError as exc:
                    raise ValueError(
                        f"source frame {frame_index} lacks atom property {requested!r}"
                    ) from exc
            elif descriptor.source == "sidecar":
                raw = sidecar_values[descriptor.name][frame_index]
            else:
                raise ValueError(
                    "column: fields are available only through the LAMMPS batch path"
                )
            values.append(np.asarray(raw))
        combined = combine_fields(values)
        reduced, effective_mode = reduce_property_values(
            combined,
            reduction=resolved_spec.reduction,
            component=resolved_spec.component,
            components=component_names,
        )
        reduced_frames.append(
            ReducedProperty(
                values=reduced,
                reduction=effective_mode,
                fields=resolved_spec.fields,
                components=component_names,
                unit=unit,
            )
        )
    scale = resolve_property_scale(
        (frame.values for frame in reduced_frames), resolved_spec
    )
    return SourcePropertyContext(
        spec=resolved_spec,
        frames=tuple(reduced_frames),
        source_frame_indices=frame_indices,
        scale=scale,
        manifest_hash=getattr(manifest, "manifest_hash", None),
    )


def resolve_frame_batch_property_context(
    frames: Sequence[Any],
    spec: AtomPropertyColorSpec | Mapping[str, Any],
    *,
    input_path: str,
    embedded_source: Literal["array", "column"],
    manifest: Any = None,
) -> SourcePropertyContext:
    """Resolve selected fields from contiguous FrameBatch objects."""

    resolved_spec = coerce_atom_property_color_spec(spec)
    assert resolved_spec is not None
    catalogs = [
        catalog_from_arrays(frame.atom_arrays, source="array")
        if embedded_source == "array"
        else PropertyCatalog(
            tuple(
                PropertyDescriptor(
                    field=f"column:{name}",
                    source="column",
                    name=name,
                    dtype=str(np.asarray(values).dtype),
                    shape_tail=tuple(np.asarray(values).shape[1:]),
                )
                for name, values in frame.atom_arrays.items()
            )
        )
        for frame in frames
    ]
    if manifest is not None:
        catalogs.append(manifest.catalog())
    catalog = _compatible_catalog(catalogs)
    descriptors = tuple(catalog.resolve(field) for field in resolved_spec.fields)
    sidecar_values: dict[str, dict[int, np.ndarray]] = {}
    if any(item.source == "sidecar" for item in descriptors):
        if manifest is None:
            raise ValueError(
                "sidecar atom property requested without property_data manifest"
            )
        from .loader.property_sidecar import align_sidecar_property

        for descriptor in descriptors:
            if descriptor.source != "sidecar" or descriptor.name in sidecar_values:
                continue
            aligned = align_sidecar_property(
                manifest, descriptor.name, frames, source_path=input_path
            )
            sidecar_values[descriptor.name] = {
                item.frame_index: item.values for item in aligned
            }
    component_names = _resolved_component_names(descriptors)
    units = {item.unit for item in descriptors if item.unit}
    if resolved_spec.unit is None and len(units) > 1:
        raise ValueError("selected atom property fields declare different units")
    unit = resolved_spec.unit or (next(iter(units)) if units else None)
    reduced_frames = []
    indices = []
    for frame in frames:
        frame_index = int(frame.source_index)
        indices.append(frame_index)
        selected = []
        for descriptor in descriptors:
            if descriptor.source == "sidecar":
                selected.append(sidecar_values[descriptor.name][frame_index])
            else:
                selected.append(frame.atom_arrays[descriptor.name])
        values, effective = reduce_property_values(
            combine_fields(selected),
            reduction=resolved_spec.reduction,
            component=resolved_spec.component,
            components=component_names,
        )
        reduced_frames.append(
            ReducedProperty(
                values=values,
                reduction=effective,
                fields=resolved_spec.fields,
                components=component_names,
                unit=unit,
            )
        )
    scale = resolve_property_scale(
        (item.values for item in reduced_frames), resolved_spec
    )
    return SourcePropertyContext(
        spec=resolved_spec,
        frames=tuple(reduced_frames),
        source_frame_indices=tuple(indices),
        scale=scale,
        manifest_hash=getattr(manifest, "manifest_hash", None),
    )


def reduce_frame_batch_property(
    frame: Any,
    spec: AtomPropertyColorSpec | Mapping[str, Any],
    *,
    input_path: str,
    embedded_source: Literal["array", "column"],
    manifest: Any = None,
    sidecar_data: Mapping[str, Any] | None = None,
) -> ReducedProperty:
    """Reduce one batch frame without resolving a per-frame colour range."""

    resolved_spec = coerce_atom_property_color_spec(spec)
    assert resolved_spec is not None
    embedded = (
        catalog_from_arrays(frame.atom_arrays)
        if embedded_source == "array"
        else PropertyCatalog(
            tuple(
                PropertyDescriptor(
                    field=f"column:{name}",
                    source="column",
                    name=name,
                    dtype=str(np.asarray(values).dtype),
                    shape_tail=tuple(np.asarray(values).shape[1:]),
                )
                for name, values in frame.atom_arrays.items()
            )
        )
    )
    catalog = (
        merge_catalogs(embedded, manifest.catalog())
        if manifest is not None
        else embedded
    )
    descriptors = tuple(catalog.resolve(field) for field in resolved_spec.fields)
    selected = []
    for descriptor in descriptors:
        if descriptor.source == "sidecar":
            from .loader.property_sidecar import align_sidecar_property

            selected.append(
                align_sidecar_property(
                    manifest,
                    descriptor.name,
                    [frame],
                    source_path=input_path,
                    property_values=(sidecar_data or {})
                    .get("properties", {})
                    .get(descriptor.name),
                    frame_ids=(sidecar_data or {}).get("frame_ids"),
                    atom_ids=(sidecar_data or {}).get("atom_ids"),
                )[0].values
            )
        else:
            selected.append(frame.atom_arrays[descriptor.name])
    component_names = _resolved_component_names(descriptors)
    values, effective = reduce_property_values(
        combine_fields(selected),
        reduction=resolved_spec.reduction,
        component=resolved_spec.component,
        components=component_names,
    )
    units = {item.unit for item in descriptors if item.unit}
    if resolved_spec.unit is None and len(units) > 1:
        raise ValueError("selected atom property fields declare different units")
    return ReducedProperty(
        values=values,
        reduction=effective,
        fields=resolved_spec.fields,
        components=component_names,
        unit=resolved_spec.unit or (next(iter(units)) if units else None),
    )


def reduce_property_values(
    values: Any,
    *,
    reduction: PropertyReduction = "auto",
    component: str | int | None = None,
    components: Sequence[str] = (),
) -> tuple[np.ndarray, str]:
    """Reduce an ``(N, ...)`` field to one scalar per atom."""

    array = np.asarray(values)
    if array.ndim < 1:
        raise ValueError("atom property values must begin with an atom dimension")
    trailing = array.shape[1:]
    mode = str(reduction).strip().lower().replace("-", "_")
    if mode == "auto":
        if trailing in {(), (1,)}:
            mode = "scalar"
        elif trailing == (3,):
            mode = "magnitude"
        else:
            raise ValueError(
                f"atom property shape {trailing} is tensor-like; choose component, "
                "trace, mean_normal, or von_mises explicitly"
            )
    numeric = np.asarray(array, dtype=np.float64)
    if mode == "scalar":
        if trailing not in {(), (1,)}:
            raise ValueError(
                f"scalar reduction requires shape (N,) or (N,1), got {array.shape}"
            )
        result = numeric.reshape(len(numeric))
    elif mode == "magnitude":
        if trailing != (3,):
            raise ValueError(
                f"magnitude reduction requires shape (N,3), got {array.shape}"
            )
        result = np.linalg.norm(numeric, axis=1)
    elif mode == "component":
        flattened = numeric.reshape(len(numeric), -1)
        index = _component_index(component, components, flattened.shape[1])
        result = flattened[:, index]
    elif mode in {"trace", "mean_normal", "von_mises"}:
        tensor = _symmetric_tensor(numeric, components)
        if mode == "trace":
            result = np.trace(tensor, axis1=1, axis2=2)
        elif mode == "mean_normal":
            result = np.trace(tensor, axis1=1, axis2=2) / 3.0
        else:
            xx, yy, zz = tensor[:, 0, 0], tensor[:, 1, 1], tensor[:, 2, 2]
            xy, yz, xz = tensor[:, 0, 1], tensor[:, 1, 2], tensor[:, 0, 2]
            result = np.sqrt(
                0.5 * ((xx - yy) ** 2 + (yy - zz) ** 2 + (zz - xx) ** 2)
                + 3.0 * (xy**2 + yz**2 + xz**2)
            )
    else:
        raise ValueError(f"unsupported atom property reduction {reduction!r}")
    return np.ascontiguousarray(result, dtype=np.float32), mode


def combine_fields(values: Sequence[Any]) -> np.ndarray:
    """Combine multiple scalar fields into a component axis."""

    if not values:
        raise ValueError("at least one atom property field is required")
    arrays = [np.asarray(value) for value in values]
    atom_count = len(arrays[0])
    if any(array.ndim < 1 or len(array) != atom_count for array in arrays):
        raise ValueError("all atom property fields must have the same atom dimension")
    if len(arrays) == 1:
        return arrays[0]
    if any(array.shape[1:] not in {(), (1,)} for array in arrays):
        raise ValueError("multiple --color-by fields must each be scalar")
    return np.stack([array.reshape(atom_count) for array in arrays], axis=1)


def resolve_property_scale(
    value_sets: Iterable[Any],
    spec: AtomPropertyColorSpec,
) -> ResolvedPropertyScale:
    """Resolve one exact range over all selected source frames and atoms."""

    finite_count = 0
    missing_count = 0
    lower = np.inf
    upper = -np.inf
    for raw_values in value_sets:
        values = np.asarray(raw_values, dtype=np.float64).reshape(-1)
        finite = np.isfinite(values)
        count = int(np.count_nonzero(finite))
        finite_count += count
        missing_count += int(values.size - count)
        if spec.value_range is None and count:
            selected = values[finite]
            lower = min(lower, float(np.min(selected)))
            upper = max(upper, float(np.max(selected)))
    if finite_count == 0:
        raise ValueError(
            "atom property contains no finite values in the selected frames"
        )
    value_range = spec.value_range or (lower, upper)
    center = spec.center
    if center is not None and not value_range[0] <= center <= value_range[1]:
        raise ValueError("center must lie inside the resolved atom property range")
    lut = build_color_lut(spec.colormap)
    if missing_count:
        warnings.warn(
            f"atom property contains {missing_count} NaN/Inf value(s); "
            f"using missing colour {spec.nan_color}",
            UserWarning,
            stacklevel=2,
        )
    return ResolvedPropertyScale(
        value_range=(float(value_range[0]), float(value_range[1])),
        center=center,
        lut=lut,
        finite_count=finite_count,
        missing_count=missing_count,
        lut_hash=sha256(lut.tobytes()).hexdigest(),
    )


def build_color_lut(colormap: str) -> np.ndarray:
    """Return the exact shared 256-colour RGBA lookup table."""

    try:
        from matplotlib import colormaps

        cmap = colormaps.get_cmap(str(colormap))
    except (KeyError, ValueError) as exc:
        raise ValueError(f"unknown matplotlib colormap {colormap!r}") from exc
    rgba = np.asarray(cmap(np.linspace(0.0, 1.0, 256)), dtype=np.float64)
    return np.ascontiguousarray(
        np.rint(np.clip(rgba, 0.0, 1.0) * 255.0), dtype=np.uint8
    )


def normalize_property_values(
    values: Any,
    *,
    value_range: tuple[float, float],
    center: float | None = None,
) -> np.ndarray:
    """Normalize finite values to [0,1], preserving non-finite sentinels."""

    array = np.asarray(values, dtype=np.float64)
    lower, upper = value_range
    result = np.full(array.shape, np.nan, dtype=np.float32)
    finite = np.isfinite(array)
    if lower == upper:
        result[finite] = 0.5
        return result
    clipped = np.clip(array[finite], lower, upper)
    if center is None:
        result[finite] = (clipped - lower) / (upper - lower)
        return result
    left = clipped <= center
    scaled = np.empty(clipped.shape, dtype=np.float64)
    left_span = center - lower
    right_span = upper - center
    scaled[left] = (
        0.5 if left_span == 0.0 else 0.5 * (clipped[left] - lower) / left_span
    )
    scaled[~left] = (
        0.5 if right_span == 0.0 else 0.5 + 0.5 * (clipped[~left] - center) / right_span
    )
    result[finite] = scaled
    return result


def map_property_colors(
    values: Any,
    scale: ResolvedPropertyScale,
    *,
    nan_color: str,
) -> np.ndarray:
    """Map scalar values to shared uint8 RGBA colours."""

    normalized = normalize_property_values(
        values,
        value_range=scale.value_range,
        center=scale.center,
    )
    finite = np.isfinite(normalized)
    indices = np.zeros(normalized.shape, dtype=np.uint8)
    indices[finite] = np.rint(normalized[finite] * 255.0).astype(np.uint8)
    colors = np.empty((*normalized.shape, 4), dtype=np.uint8)
    colors[finite] = scale.lut[indices[finite]]
    colors[~finite] = _parse_color(nan_color)
    return colors


def rgba_to_hex(values: Any) -> list[str]:
    array = np.asarray(values, dtype=np.uint8).reshape(-1, 4)
    return [f"#{r:02X}{g:02X}{b:02X}{a:02X}" for r, g, b, a in array]


def property_metadata(
    spec: AtomPropertyColorSpec,
    reduced: ReducedProperty,
    scale: ResolvedPropertyScale,
    *,
    manifest_hash: str | None = None,
) -> dict[str, Any]:
    label = spec.label or ", ".join(spec.fields)
    unit = spec.unit if spec.unit is not None else reduced.unit
    return {
        "fields": list(spec.fields),
        "reduction": reduced.reduction,
        "component": spec.component,
        "colormap": spec.colormap,
        "range": list(scale.value_range),
        "center": scale.center,
        "nan_color": spec.nan_color,
        "show_colorbar": spec.show_colorbar,
        "label": label,
        "unit": unit,
        "finite_count": scale.finite_count,
        "missing_count": scale.missing_count,
        "lut_hash": scale.lut_hash,
        "manifest_hash": manifest_hash,
        "range_scope": scale.scope,
        "lut": scale.lut[:, :3].tolist(),
    }


def _component_index(
    component: str | int | None,
    components: Sequence[str],
    width: int,
) -> int:
    if component is None:
        raise ValueError("component reduction requires component=NAME_OR_INDEX")
    try:
        index = int(component)
    except (TypeError, ValueError):
        names = tuple(str(value) for value in components)
        if not names:
            raise ValueError(
                "named component selection requires declared component names"
            )
        try:
            index = names.index(str(component))
        except ValueError as exc:
            raise ValueError(
                f"unknown component {component!r}; choose one of {', '.join(names)}"
            ) from exc
    if index < 0:
        index += width
    if not 0 <= index < width:
        raise ValueError(f"component index {component!r} is outside [0, {width})")
    return index


def _symmetric_tensor(array: np.ndarray, components: Sequence[str]) -> np.ndarray:
    if array.shape[1:] == (3, 3):
        return array
    if array.shape[1:] != (6,):
        raise ValueError(
            "tensor reduction requires shape (N,3,3) or a declared six-component field"
        )
    names = tuple(str(value).lower() for value in components)
    required = {"xx", "yy", "zz", "xy", "yz", "xz"}
    if len(names) != 6 or set(names) != required:
        raise ValueError(
            "six-component tensor reduction requires components declaring exactly "
            "xx, yy, zz, xy, yz, xz in their stored order"
        )
    by_name = {name: array[:, index] for index, name in enumerate(names)}
    tensor = np.empty((len(array), 3, 3), dtype=np.float64)
    tensor[:, 0, 0] = by_name["xx"]
    tensor[:, 1, 1] = by_name["yy"]
    tensor[:, 2, 2] = by_name["zz"]
    tensor[:, 0, 1] = tensor[:, 1, 0] = by_name["xy"]
    tensor[:, 1, 2] = tensor[:, 2, 1] = by_name["yz"]
    tensor[:, 0, 2] = tensor[:, 2, 0] = by_name["xz"]
    return tensor


def _parse_color(value: str) -> np.ndarray:
    try:
        from matplotlib.colors import to_rgba

        rgba = to_rgba(str(value))
    except ValueError as exc:
        raise ValueError(f"invalid colour {value!r}") from exc
    return np.rint(np.asarray(rgba) * 255.0).astype(np.uint8)


def _frame_atom_arrays(frame: Any) -> dict[str, np.ndarray]:
    arrays = getattr(frame, "atom_arrays", None)
    if arrays is None:
        bundle = getattr(frame, "bundle", None)
        arrays = getattr(bundle, "atom_arrays", None)
    return {
        str(name): np.asarray(values) for name, values in dict(arrays or {}).items()
    }


def _compatible_catalog(catalogs: Sequence[PropertyCatalog]) -> PropertyCatalog:
    """Merge frame catalogs while retaining only cross-frame compatible fields."""

    if not catalogs:
        return PropertyCatalog()
    merged: dict[str, PropertyDescriptor] = {}
    for catalog in catalogs:
        for descriptor in catalog.descriptors:
            previous = merged.get(descriptor.field)
            if previous is None:
                merged[descriptor.field] = descriptor
                continue
            if (
                previous.dtype != descriptor.dtype
                or previous.shape_tail != descriptor.shape_tail
                or previous.components != descriptor.components
                or previous.unit != descriptor.unit
            ):
                raise ValueError(
                    f"atom property {descriptor.field!r} changes dtype, shape, "
                    "components, or unit across selected frames"
                )
    frame_catalogs = [
        catalog
        for catalog in catalogs
        if any(
            descriptor.source in {"array", "column"}
            for descriptor in catalog.descriptors
        )
    ]
    if len(frame_catalogs) > 1:
        common = set(item.field for item in frame_catalogs[0].descriptors)
        for catalog in frame_catalogs[1:]:
            common &= {item.field for item in catalog.descriptors}
        merged = {
            field: descriptor
            for field, descriptor in merged.items()
            if descriptor.source not in {"array", "column"} or field in common
        }
    return PropertyCatalog(tuple(sorted(merged.values(), key=lambda item: item.field)))


def _resolved_component_names(
    descriptors: Sequence[PropertyDescriptor],
) -> tuple[str, ...]:
    if len(descriptors) == 1:
        return descriptors[0].components
    return tuple(descriptor.name for descriptor in descriptors)


__all__ = [
    "AtomPropertyColorSpec",
    "PropertyCatalog",
    "PropertyDescriptor",
    "PropertyReduction",
    "ReducedProperty",
    "ResolvedPropertyScale",
    "SourcePropertyContext",
    "build_color_lut",
    "catalog_from_arrays",
    "catalog_from_columns",
    "coerce_atom_property_color_spec",
    "combine_fields",
    "map_property_colors",
    "merge_catalogs",
    "normalize_property_values",
    "property_metadata",
    "reduce_frame_batch_property",
    "reduce_property_values",
    "resolve_property_scale",
    "resolve_frame_batch_property_context",
    "resolve_source_property_context",
    "rgba_to_hex",
]
