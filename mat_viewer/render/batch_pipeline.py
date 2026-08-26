"""Format-neutral array batch rendering for static images and animations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from ..loader.frame_batch import FrameBatch, frame_batch_from_ase, frame_box_corners
from ..loader.lammps_batch import (
    index_lammps_dump,
    read_lammps_frame,
    repeat_frame,
)
from ..loader.structure_input import load_atomistic_input, load_structure_input
from .animation_time import draw_time_label, resolve_animation_times
from .contracts import CameraSpec, TextPrimitive
from .cpu.batch import NUMBA_AVAILABLE, render_frame_batch, warm_batch_renderer
from .cpu.raster import composite_primitives
from .frame_annotations import resolve_frame_annotations


@dataclass(frozen=True, slots=True)
class BatchPipelineResult:
    output: Path
    output_sha256: str
    camera: CameraSpec
    selected_frames: tuple[int, ...]
    profile: dict[str, Any]


def _is_lammps_dump(path: str | Path, input_format: str | None) -> bool:
    value = str(input_format or "").lower()
    return value in {"lammps-dump", "lammps-dump-text"} or Path(
        path
    ).suffix.lower() in {
        ".dump",
        ".lammpstrj",
        ".lammpsdump",
    }


def _bundle_to_frame(bundle: Any, *, source_index: int) -> FrameBatch:
    from ase.data import atomic_numbers

    atoms = list(bundle.raw_atoms or bundle.scene.get("draw_atoms") or ())
    positions = np.asarray(
        [item.get("cart", item.get("position")) for item in atoms], dtype=float
    )
    numbers = np.asarray(
        [atomic_numbers[str(item.get("elem", item.get("element")))] for item in atoms],
        dtype=np.uint8,
    )
    matrix = np.asarray(bundle.M, dtype=float)
    metadata = bundle.scene.get("metadata", {})
    pbc = np.asarray(metadata.get("pbc", [True, True, True]), dtype=bool)
    if bool(metadata.get("synthetic_cell", False)):
        pbc[:] = False
    return FrameBatch(
        positions=positions,
        atomic_numbers=numbers,
        atom_ids=np.arange(1, len(positions) + 1),
        origin=np.zeros(3),
        cell=matrix,
        pbc=pbc,
        timestep=source_index,
        source_index=source_index,
        info={"frame_index": source_index, **dict(bundle.frame_info)},
    )


def load_frame_batches(
    path: str | Path,
    *,
    input_format: str | None,
    type_map: Sequence[str] | None,
    frame_indices: Sequence[int],
    repeat: tuple[int, int, int],
) -> tuple[FrameBatch, ...]:
    """Load any supported format into the one renderer-facing array class."""

    if _is_lammps_dump(path, input_format):
        dump = index_lammps_dump(path)
        frames = tuple(
            read_lammps_frame(dump, index, type_map=type_map) for index in frame_indices
        )
    else:
        resolved = str(input_format or "").lower()
        suffix = Path(path).suffix.lower()
        if resolved in {"cif", "cube"} or suffix in {".cif", ".cube"}:
            loaded = load_structure_input(
                path,
                input_format=input_format,
                type_map=type_map,
                frame_indices=frame_indices,
            )
            frames = tuple(
                _bundle_to_frame(item.bundle, source_index=item.index)
                for item in loaded.frames
            )
        else:
            loaded = load_atomistic_input(
                path,
                input_format=input_format,
                type_map=type_map,
                frame_indices=frame_indices,
            )
            frames = tuple(
                frame_batch_from_ase(item.atoms, source_index=item.index)
                for item in loaded.frames
            )
    if repeat != (1, 1, 1):
        frames = tuple(repeat_frame(frame, repeat) for frame in frames)
    return frames


def fit_shared_frame_camera(
    frames: Sequence[FrameBatch],
    *,
    width: int,
    height: int,
    projection: str,
    camera_axis: str | None,
    view_direction: tuple[float, float, float] | None,
    camera_position: tuple[float, float, float] | None,
    camera_up: tuple[float, float, float] | None,
    fit_multiplier: float,
    zoom: float,
    framing_margin: float,
) -> CameraSpec:
    """Fit one immutable viewport to the union of selected frame bounds."""

    if not frames:
        raise ValueError("cannot fit a camera without frames")
    points = np.concatenate([frame_box_corners(frame) for frame in frames], axis=0)
    lower, upper = np.min(points, axis=0), np.max(points, axis=0)
    target = (lower + upper) * 0.5
    radius = max(float(np.linalg.norm(points - target, axis=1).max()), 1.0)
    matrix = frames[0].cell
    if abs(float(np.linalg.det(matrix))) < 1.0e-12:
        matrix = np.eye(3)
    if view_direction is not None:
        direction = np.asarray(view_direction, dtype=float)
    elif camera_position is not None:
        direction = np.asarray(camera_position, dtype=float) - target
    else:
        axis = camera_axis or "a"
        if axis.endswith("*"):
            direction = np.linalg.inv(matrix).T[{"a*": 0, "b*": 1, "c*": 2}[axis]]
        else:
            direction = matrix[{"a": 0, "b": 1, "c": 2}[axis]]
    norm = float(np.linalg.norm(direction))
    if norm <= 1.0e-12:
        raise ValueError("camera direction must be non-zero")
    direction /= norm
    up = np.asarray(camera_up if camera_up is not None else matrix[1], dtype=float)
    if np.linalg.norm(up) <= 1.0e-12 or np.linalg.norm(np.cross(direction, up)) < 1e-10:
        up = np.asarray([0.0, 1.0, 0.0])
        if np.linalg.norm(np.cross(direction, up)) < 1e-10:
            up = np.asarray([0.0, 0.0, 1.0])
    up /= np.linalg.norm(up)
    forward = -direction
    right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    screen_up = np.cross(right, forward)
    relative = points - target
    aspect = width / height
    half_width = float(np.max(np.abs(relative @ right)))
    half_height = float(np.max(np.abs(relative @ screen_up)))
    ortho_scale = max(half_height, half_width / aspect, 0.5) * framing_margin / zoom
    if camera_position is not None:
        position = np.asarray(camera_position, dtype=float)
        distance = float(np.linalg.norm(position - target))
    elif projection == "perspective":
        distance = max(
            fit_multiplier
            * max(half_height, half_width / aspect)
            * framing_margin
            / (math.tan(math.radians(22.5)) * zoom),
            1.5 * radius,
        )
        position = target + direction * distance
    else:
        distance = max(fit_multiplier * radius, 1.5 * radius)
        position = target + direction * distance
    near = max(radius * 1.0e-4, distance - 1.5 * radius)
    far = max(near * 2.0, distance + 1.5 * radius)
    return CameraSpec(
        position=tuple(position),
        target=tuple(target),
        up=tuple(up),
        projection=projection,
        near=near,
        far=far,
        ortho_scale=ortho_scale,
    )


def _atom_label_primitives(frame: FrameBatch) -> list[TextPrimitive]:
    from ase.data import chemical_symbols

    identifiers = frame.atom_ids
    return [
        TextPrimitive(
            semantic_id=f"label:{index}",
            position=tuple(position),
            text=f"{chemical_symbols[int(number)]}{int(identifiers[index]) if identifiers is not None else index + 1}",
            offset_px=(5.0, -5.0),
            metadata={"kind": "atom_label", "atom_index": index},
        )
        for index, (position, number) in enumerate(
            zip(frame.positions, frame.atomic_numbers)
        )
    ]


def _render_one(
    frame: FrameBatch,
    camera: CameraSpec,
    *,
    width: int,
    height: int,
    scale: int,
    atom_scale: float,
    background: tuple[int, int, int, int],
    show_hydrogen: bool,
    show_cell: bool | None,
    show_axes: bool,
    show_labels: bool,
    cell_color: tuple[int, int, int],
    cell_width_px: float,
    bonds: Any,
    bond_radius: float,
    overlay_primitives: Iterable[Any],
) -> np.ndarray:
    rendered = render_frame_batch(
        frame,
        camera,
        width=width * scale,
        height=height * scale,
        atom_scale=atom_scale,
        background=background,
        show_hydrogen=show_hydrogen,
        show_cell=bool(np.any(frame.pbc)) if show_cell is None else show_cell,
        cell_color=cell_color,
        cell_width_px=cell_width_px * scale,
        bonds=bonds,
        bond_radius=bond_radius,
    )
    primitives = list(overlay_primitives)
    if show_labels:
        primitives.extend(_atom_label_primitives(frame))
    metadata: dict[str, Any] = {}
    if show_axes:
        from .compass_overlay import attach_lattice_compass_metadata

        attach_lattice_compass_metadata(metadata, [], frame.cell)
    rgba, _ = composite_primitives(
        rendered.rgba,
        rendered.depth,
        camera,
        primitives,
        metadata=metadata,
    )
    if scale != 1:
        from PIL import Image

        rgba = np.asarray(
            Image.fromarray(rgba).resize(
                (width, height), resample=Image.Resampling.LANCZOS
            )
        )
    return rgba


def render_array_input(
    input_path: str | Path,
    output_path: str | Path,
    *,
    input_format: str | None,
    type_map: Sequence[str] | None,
    frame_indices: Sequence[int],
    repeat: tuple[int, int, int],
    width: int,
    height: int,
    scale: int,
    fps: float,
    projection: str,
    camera_axis: str | None,
    view_direction: tuple[float, float, float] | None,
    camera_position: tuple[float, float, float] | None,
    camera_up: tuple[float, float, float] | None,
    fit_multiplier: float,
    zoom: float,
    framing_margin: float,
    atom_scale: float,
    background: tuple[int, int, int, int],
    show_hydrogen: bool,
    show_cell: bool | None,
    show_axes: bool,
    show_labels: bool,
    cell_color: tuple[int, int, int],
    cell_width_px: float,
    bonded: bool,
    bond_radius: float,
    bond_skin: float,
    vector_overlays: Any = None,
    polyhedron_specs: Sequence[str] = (),
    polyhedron_site: int | None = None,
    polyhedron_cutoff: float = 10.0,
    animation_time: Any = None,
    frame_annotation: Any = None,
    profile_path: str | Path | None = None,
) -> BatchPipelineResult:
    """Render any supported input after conversion to canonical arrays."""

    if not NUMBA_AVAILABLE:
        raise RuntimeError("batch renderer requires numba")
    output = Path(output_path).expanduser().resolve()
    if output.suffix.lower() not in {".png", ".gif", ".mp4"}:
        raise ValueError("batch renderer output must be PNG, GIF, or MP4")
    frames = load_frame_batches(
        input_path,
        input_format=input_format,
        type_map=type_map,
        frame_indices=frame_indices,
        repeat=repeat,
    )
    camera = fit_shared_frame_camera(
        frames,
        width=width,
        height=height,
        projection=projection,
        camera_axis=camera_axis,
        view_direction=view_direction,
        camera_position=camera_position,
        camera_up=camera_up,
        fit_multiplier=fit_multiplier,
        zoom=zoom,
        framing_margin=framing_margin,
    )
    overlay_primitives = ()
    if vector_overlays:
        from .overlay.vectors import vector_primitives

        overlay_primitives = tuple(
            vector_primitives(vector_overlays, lattice=frames[0].cell)
        )
    if polyhedron_specs:
        if len(frames) != 1:
            raise ValueError("animated polyhedron overlays are not supported")
        from ..agent import load_structure, prepare_render
        from ..agent_topology import build_topology_data
        from .contracts import RenderSpec

        structure = load_structure(
            input_path,
            input_format=input_format,
            type_map=type_map,
            frame=frame_indices[0],
        )
        topology_data = build_topology_data(
            structure,
            list(polyhedron_specs),
            site_index=polyhedron_site,
            cutoff=polyhedron_cutoff,
        )
        plan = prepare_render(
            structure,
            camera=camera,
            render_spec=RenderSpec(
                representation="ball",
                width=width * scale,
                height=height * scale,
                show_cell=False,
                show_axes=False,
                show_labels=False,
            ),
            topology_data=topology_data,
        )
        overlay_primitives = tuple(overlay_primitives) + tuple(
            primitive
            for viewport in plan.viewports
            for primitive in viewport.primitives
            if primitive.semantic_id.startswith("polyhedron:")
        )
    time_series = (
        resolve_animation_times(frames, animation_time) if animation_time else None
    )
    annotation_series = (
        resolve_frame_annotations(frames, frame_annotation)
        if frame_annotation
        else None
    )
    tracker = None
    if bonded:
        from molcrys_kit.structures import VerletBondTracker

        tracker = VerletBondTracker(skin=bond_skin)
    warm_batch_renderer()
    output.parent.mkdir(parents=True, exist_ok=True)
    writer = None
    if output.suffix.lower() == ".gif":
        from .fast_animation import _gif_writer

        writer = _gif_writer(output, fps=fps)
    elif output.suffix.lower() == ".mp4":
        from .fast_animation import _mp4_writer

        writer = _mp4_writer(output, width=width, height=height, fps=fps)
    try:
        for ordinal, frame in enumerate(frames):
            bonds = (
                tracker.update(
                    frame.positions,
                    frame.atomic_numbers,
                    cell=frame.cell,
                    pbc=frame.pbc,
                )
                if tracker is not None
                else None
            )
            rgba = _render_one(
                frame,
                camera,
                width=width,
                height=height,
                scale=scale,
                atom_scale=atom_scale,
                background=background,
                show_hydrogen=show_hydrogen,
                show_cell=show_cell,
                show_axes=show_axes,
                show_labels=show_labels,
                cell_color=cell_color,
                cell_width_px=cell_width_px,
                bonds=bonds,
                bond_radius=bond_radius,
                overlay_primitives=overlay_primitives,
            )
            if time_series or annotation_series:
                from PIL import Image

                image = Image.fromarray(rgba)
                if time_series:
                    image = draw_time_label(
                        image,
                        time_series.labels[ordinal],
                        time_series.spec.position,
                    )
                if annotation_series:
                    image = draw_time_label(
                        image,
                        annotation_series.labels[ordinal],
                        annotation_series.spec.position,
                    )
                rgba = np.asarray(image)
            if writer is None:
                from PIL import Image

                Image.fromarray(rgba).save(output)
            elif output.suffix.lower() == ".mp4":
                writer.send(np.ascontiguousarray(rgba[:, :, :3]))
            else:
                writer.append_data(np.ascontiguousarray(rgba[:, :, :3]))
    finally:
        if writer is not None:
            writer.close()
    profile = {
        "schema": "mattervis.batch-render-profile/v1",
        "source": str(Path(input_path).expanduser().resolve()),
        "selected_frames": list(frame_indices),
        "atom_frames": int(sum(frame.natoms for frame in frames)),
        "shared_viewport": True,
        "camera": asdict(camera),
    }
    if profile_path is not None:
        destination = Path(profile_path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(profile, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return BatchPipelineResult(
        output=output,
        output_sha256=sha256(output.read_bytes()).hexdigest(),
        camera=camera,
        selected_frames=tuple(frame_indices),
        profile=profile,
    )


__all__ = [
    "BatchPipelineResult",
    "fit_shared_frame_camera",
    "load_frame_batches",
    "render_array_input",
]
