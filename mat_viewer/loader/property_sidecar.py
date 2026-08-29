"""Strict JSON-manifest and memory-mapped NPY atom-property sidecars."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from ..properties import PropertyCatalog, PropertyDescriptor

SIDECAR_SCHEMA = "mattervis.atom-properties/v1"


@dataclass(frozen=True, slots=True)
class SidecarProperty:
    name: str
    path: Path
    dtype: str
    shape: tuple[int, ...]
    unit: str | None
    components: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AtomPropertyManifest:
    """Validated metadata; property payloads remain unopened until requested."""

    path: Path
    manifest_hash: str
    source_sha256: str | None
    frame_key: str | None
    frame_ids_path: Path | None
    atom_key: str
    atom_ids_path: Path | None
    properties: Mapping[str, SidecarProperty]

    def catalog(self) -> PropertyCatalog:
        descriptors = tuple(
            PropertyDescriptor(
                field=f"sidecar:{item.name}",
                source="sidecar",
                name=item.name,
                dtype=item.dtype,
                shape_tail=_property_shape_tail(
                    item.shape,
                    has_frames=self.frame_ids_path is not None,
                ),
                components=item.components,
                unit=item.unit,
            )
            for item in sorted(self.properties.values(), key=lambda value: value.name)
        )
        return PropertyCatalog(descriptors)

    def open_property(self, name: str) -> np.ndarray:
        try:
            item = self.properties[str(name)]
        except KeyError as exc:
            choices = ", ".join(sorted(self.properties))
            raise ValueError(
                f"sidecar has no property {name!r}; choose one of {choices}"
            ) from exc
        return _load_npy(item.path, mmap=True)

    def frame_ids(self) -> np.ndarray | None:
        if self.frame_ids_path is None:
            return None
        return _load_npy(self.frame_ids_path, mmap=True)

    def atom_ids(self) -> np.ndarray | None:
        if self.atom_ids_path is None:
            return None
        return _load_npy(self.atom_ids_path, mmap=True)


@dataclass(frozen=True, slots=True)
class AlignedSidecarFrame:
    frame_index: int
    values: np.ndarray


def load_atom_property_manifest(path: str | Path) -> AtomPropertyManifest:
    """Validate a v1 manifest without loading property payload arrays."""

    manifest_path = Path(path).expanduser().resolve()
    if not manifest_path.is_file():
        raise FileNotFoundError(f"atom property manifest not found: {manifest_path}")
    raw_bytes = manifest_path.read_bytes()
    try:
        payload = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid atom property manifest JSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SIDECAR_SCHEMA:
        raise ValueError(f"atom property manifest schema must be {SIDECAR_SCHEMA!r}")

    source = _mapping(payload.get("source"), "source", required=False)
    source_sha = source.get("sha256")
    if source_sha is not None:
        source_sha = str(source_sha).lower()
        if len(source_sha) != 64 or any(
            char not in "0123456789abcdef" for char in source_sha
        ):
            raise ValueError("source.sha256 must be null or a 64-character SHA-256")

    frames = _mapping(payload.get("frames"), "frames", required=False)
    frame_key = str(frames.get("key", "")).strip() or None
    frame_ids_path = _relative_npy(manifest_path, frames.get("ids"), "frames.ids")
    if bool(frame_key) != bool(frame_ids_path):
        raise ValueError("frames.key and frames.ids must be provided together")

    atoms = _mapping(payload.get("atoms"), "atoms")
    atom_key = str(atoms.get("key", "")).strip().lower()
    if atom_key not in {"id", "label", "row"}:
        raise ValueError("atoms.key must be 'id', 'label', or 'row'")
    atom_ids_path = _relative_npy(manifest_path, atoms.get("ids"), "atoms.ids")
    if atom_key == "row":
        if atom_ids_path is not None:
            raise ValueError("row-aligned sidecars must not provide atoms.ids")
        if source_sha is None:
            raise ValueError("row-aligned sidecars require source.sha256")
    elif atom_ids_path is None:
        raise ValueError(f"{atom_key}-aligned sidecars require atoms.ids")

    raw_properties = _mapping(payload.get("properties"), "properties")
    if not raw_properties:
        raise ValueError("atom property manifest contains no properties")
    properties: dict[str, SidecarProperty] = {}
    for raw_name, raw_spec in raw_properties.items():
        name = str(raw_name).strip()
        if not name or ":" in name:
            raise ValueError(f"invalid sidecar property name {raw_name!r}")
        spec = _mapping(raw_spec, f"properties.{name}")
        values_path = _relative_npy(
            manifest_path,
            spec.get("values"),
            f"properties.{name}.values",
            required=True,
        )
        assert values_path is not None
        dtype, shape = _npy_metadata(values_path)
        if not shape:
            raise ValueError(
                f"sidecar property {name!r} must begin with an atom dimension"
            )
        components = tuple(str(value) for value in (spec.get("components") or ()))
        properties[name] = SidecarProperty(
            name=name,
            path=values_path,
            dtype=str(dtype),
            shape=shape,
            unit=None if spec.get("unit") is None else str(spec.get("unit")),
            components=components,
        )

    if frame_ids_path is not None:
        frame_dtype, frame_shape = _npy_metadata(frame_ids_path)
        if frame_dtype.hasobject or frame_shape.__len__() != 1:
            raise ValueError(
                "frames.ids must be a non-object NPY array with shape (F,)"
            )
    if atom_ids_path is not None:
        atom_dtype, atom_shape = _npy_metadata(atom_ids_path)
        if atom_dtype.hasobject or len(atom_shape) not in {1, 2}:
            raise ValueError(
                "atoms.ids must be a non-object NPY array with shape (N,) or (F,N)"
            )

    return AtomPropertyManifest(
        path=manifest_path,
        manifest_hash=sha256(raw_bytes).hexdigest(),
        source_sha256=source_sha,
        frame_key=frame_key,
        frame_ids_path=frame_ids_path,
        atom_key=atom_key,
        atom_ids_path=atom_ids_path,
        properties=properties,
    )


def align_sidecar_property(
    manifest: AtomPropertyManifest,
    property_name: str,
    source_frames: Sequence[Any],
    *,
    source_path: str | Path,
    property_values: np.ndarray | None = None,
    frame_ids: np.ndarray | None = None,
    atom_ids: np.ndarray | None = None,
) -> tuple[AlignedSidecarFrame, ...]:
    """Return mmap-backed property views reordered to each source frame."""

    values = (
        manifest.open_property(property_name)
        if property_values is None
        else property_values
    )
    frame_ids = manifest.frame_ids() if frame_ids is None else frame_ids
    atom_ids = manifest.atom_ids() if atom_ids is None else atom_ids
    if manifest.atom_key == "row":
        actual_hash = _file_sha256(Path(source_path))
        if actual_hash != manifest.source_sha256:
            raise ValueError(
                "row-aligned atom property sidecar source SHA-256 does not match input"
            )
    frame_lookup = (
        _unique_lookup(frame_ids, "sidecar frame IDs")
        if frame_ids is not None
        else None
    )
    aligned: list[AlignedSidecarFrame] = []
    for ordinal, frame in enumerate(source_frames):
        frame_index = int(getattr(frame, "index", ordinal))
        source_atom_ids = _source_atom_keys(frame, manifest.atom_key)
        atom_count = len(source_atom_ids)
        sidecar_frame = None
        if frame_lookup is not None:
            source_frame_id = _source_frame_key(frame, manifest.frame_key)
            try:
                sidecar_frame = frame_lookup[_hashable(source_frame_id)]
            except KeyError as exc:
                raise ValueError(
                    f"source frame {frame_index} has {manifest.frame_key}={source_frame_id!r}, "
                    "which is absent from sidecar frames.ids"
                ) from exc
        property_values = _property_frame_values(
            values,
            sidecar_frame=sidecar_frame,
            atom_count=atom_count,
            has_frames=frame_ids is not None,
            property_name=property_name,
        )
        if manifest.atom_key == "row":
            if len(property_values) != atom_count:
                raise ValueError(
                    f"row-aligned property {property_name!r} has {len(property_values)} atoms; "
                    f"source frame {frame_index} has {atom_count}"
                )
            reordered = property_values
        else:
            assert atom_ids is not None
            property_atom_ids = _atom_ids_for_frame(atom_ids, sidecar_frame)
            order = strict_alignment_order(
                source_atom_ids,
                property_atom_ids,
                key_name=manifest.atom_key,
            )
            if len(property_values) != len(property_atom_ids):
                raise ValueError(
                    f"property {property_name!r} atom dimension does not match atoms.ids"
                )
            reordered = property_values[order]
        aligned.append(
            AlignedSidecarFrame(
                frame_index=frame_index,
                values=np.asarray(reordered),
            )
        )
    return tuple(aligned)


def strict_alignment_order(
    source_ids: Iterable[Any],
    property_ids: Iterable[Any],
    *,
    key_name: str,
) -> np.ndarray:
    """Map sidecar rows to source order with exact set and uniqueness checks."""

    source = [_hashable(value) for value in np.asarray(source_ids).reshape(-1)]
    sidecar = [_hashable(value) for value in np.asarray(property_ids).reshape(-1)]
    source_lookup = _unique_lookup(source, f"source {key_name} values")
    sidecar_lookup = _unique_lookup(sidecar, f"sidecar {key_name} values")
    source_set = set(source_lookup)
    sidecar_set = set(sidecar_lookup)
    missing = source_set - sidecar_set
    extra = sidecar_set - source_set
    if missing or extra:
        raise ValueError(
            f"sidecar {key_name} set differs from source: "
            f"missing={_short_values(missing)}, extra={_short_values(extra)}"
        )
    return np.asarray([sidecar_lookup[value] for value in source], dtype=np.int64)


def _source_frame_key(frame: Any, key: str | None) -> Any:
    if not key:
        return getattr(frame, "index", 0)
    info = dict(getattr(frame, "info", {}) or {})
    if key in info:
        return info[key]
    if key == "timestep" and hasattr(frame, "timestep"):
        return getattr(frame, "timestep")
    bundle = getattr(frame, "bundle", None)
    frame_info = dict(getattr(bundle, "frame_info", {}) or {})
    if key in frame_info:
        return frame_info[key]
    if key in {"frame", "index"}:
        return getattr(frame, "index", 0)
    raise ValueError(f"source frame lacks sidecar frame key {key!r}")


def _source_atom_keys(frame: Any, key: str) -> np.ndarray:
    arrays = dict(getattr(frame, "atom_arrays", {}) or {})
    if key == "row":
        if hasattr(frame, "natoms"):
            return np.arange(int(frame.natoms), dtype=np.int64)
        bundle = getattr(frame, "bundle", None)
        atoms = tuple(
            getattr(bundle, "raw_atoms", ()) or getattr(bundle, "atoms", ()) or ()
        )
        count = len(atoms)
        if not count and arrays:
            count = len(next(iter(arrays.values())))
        return np.arange(count, dtype=np.int64)
    candidates = (key, "ids") if key == "id" else (key, "labels")
    if key == "id" and getattr(frame, "atom_ids", None) is not None:
        return np.asarray(frame.atom_ids)
    for candidate in candidates:
        if candidate in arrays:
            return np.asarray(arrays[candidate])
    if key == "label":
        bundle = getattr(frame, "bundle", None)
        atoms = tuple(
            getattr(bundle, "raw_atoms", ()) or getattr(bundle, "atoms", ()) or ()
        )
        if atoms:
            return np.asarray(
                [
                    atom.get("label")
                    if isinstance(atom, Mapping)
                    else getattr(atom, "label", None)
                    for atom in atoms
                ]
            )
    raise ValueError(f"source frame lacks per-atom {key!r} values required by sidecar")


def _property_frame_values(
    values: np.ndarray,
    *,
    sidecar_frame: int | None,
    atom_count: int,
    has_frames: bool,
    property_name: str,
) -> np.ndarray:
    if has_frames:
        if values.ndim < 2 or sidecar_frame is None:
            raise ValueError(
                f"trajectory sidecar property {property_name!r} must have shape (F,N,...)"
            )
        return values[sidecar_frame]
    if values.ndim < 1 or values.shape[0] != atom_count:
        raise ValueError(
            f"static sidecar property {property_name!r} must have shape (N,...); "
            f"expected N={atom_count}, got {values.shape}"
        )
    return values


def _atom_ids_for_frame(atom_ids: np.ndarray, sidecar_frame: int | None) -> np.ndarray:
    if atom_ids.ndim == 1:
        return atom_ids
    if sidecar_frame is None:
        raise ValueError("per-frame atoms.ids requires frames.ids")
    return atom_ids[sidecar_frame]


def _unique_lookup(values: Iterable[Any] | None, context: str) -> dict[Any, int]:
    if values is None:
        return {}
    lookup: dict[Any, int] = {}
    duplicates: list[Any] = []
    for index, raw_value in enumerate(values):
        value = _hashable(raw_value)
        if value in lookup:
            duplicates.append(value)
        else:
            lookup[value] = index
    if duplicates:
        raise ValueError(
            f"{context} contain duplicate values: {_short_values(duplicates)}"
        )
    return lookup


def _hashable(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    return value


def _short_values(values: Iterable[Any]) -> list[Any]:
    return sorted(values, key=repr)[:8]


def _mapping(value: Any, name: str, *, required: bool = True) -> dict[str, Any]:
    if value is None and not required:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"manifest {name} must be an object")
    return dict(value)


def _relative_npy(
    manifest_path: Path,
    value: Any,
    name: str,
    *,
    required: bool = False,
) -> Path | None:
    if value is None:
        if required:
            raise ValueError(f"manifest {name} is required")
        return None
    relative = Path(str(value))
    if relative.is_absolute():
        raise ValueError(f"manifest {name} must be relative to the manifest")
    target = (manifest_path.parent / relative).resolve()
    try:
        target.relative_to(manifest_path.parent.resolve())
    except ValueError as exc:
        raise ValueError(f"manifest {name} escapes the manifest directory") from exc
    if target.suffix.lower() != ".npy" or not target.is_file():
        raise ValueError(f"manifest {name} must name an existing .npy file")
    return target


def _npy_metadata(path: Path) -> tuple[np.dtype, tuple[int, ...]]:
    try:
        array = _load_npy(path, mmap=True)
    except ValueError as exc:
        raise ValueError(f"unsafe or invalid NPY file {path.name!r}: {exc}") from exc
    if array.dtype.hasobject:
        raise ValueError(
            f"NPY file {path.name!r} uses object/pickle data, which is forbidden"
        )
    return array.dtype, tuple(int(value) for value in array.shape)


def _load_npy(path: Path, *, mmap: bool) -> np.ndarray:
    return np.load(path, mmap_mode="r" if mmap else None, allow_pickle=False)


def _property_shape_tail(
    shape: tuple[int, ...], *, has_frames: bool
) -> tuple[int, ...]:
    start = 2 if has_frames else 1
    return tuple(shape[start:])


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


__all__ = [
    "AlignedSidecarFrame",
    "AtomPropertyManifest",
    "SIDECAR_SCHEMA",
    "SidecarProperty",
    "align_sidecar_property",
    "load_atom_property_manifest",
    "strict_alignment_order",
]
