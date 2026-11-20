"""GIF/MP4 frames drawn from :class:`FrameBatch` arrays on one GPU session.

One frame is encoded before the next is submitted.  Ball and ball-and-stick
use the arrays directly.  Other representations keep the planned primitives
and the same session.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np

from ..loader.frame_batch import FrameBatch, frame_box_corners
from .contracts import RENDER_RESULT_SCHEMA, CameraSpec, RenderResult
from .gpu import (
    DrawPacket,
    GPUUnsupportedError,
    GpuSession,
    LoweredPlan,
    render_animation,
)
from .gpu_packets import lowered_plan_digest


_CELL_EDGES = (
    (0, 1),
    (0, 2),
    (0, 3),
    (1, 4),
    (1, 5),
    (2, 4),
    (2, 6),
    (3, 5),
    (3, 6),
    (4, 7),
    (5, 7),
    (6, 7),
)


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, bytes)):
        return bool(value)
    try:
        return len(value) > 0
    except TypeError:
        return True


def frame_batch_fast_path(
    source: Any,
    *,
    representation: str,
    view: Any = None,
    render_spec: Any = None,
    topology_data: Any = None,
    cell_overlays: Any = None,
    vector_overlays: Any = None,
    atom_groups: Any = None,
    bond_groups: Any = None,
    atom_property_color: Any = None,
) -> bool:
    """Return whether one session can draw these frames from stored arrays.

    The fast path reads ``FrameBatch`` positions directly. It does not apply
    a display mode, atom or bond groups, overlays, topology, labels, axes,
    or property colours. Empty group lists are not a request. Structure
    frames stay on the planned renderer so the displayed atom set matches
    the CPU path.
    """

    if representation not in {"ball", "ball_stick"}:
        return False
    frames = tuple(getattr(source, "frames", ()) or ())
    if not frames or not isinstance(frames[0], FrameBatch):
        return False
    if view is not None or atom_property_color is not None:
        return False
    if any(
        _nonempty(item)
        for item in (
            topology_data,
            cell_overlays,
            vector_overlays,
            atom_groups,
            bond_groups,
        )
    ):
        return False
    if bool(getattr(render_spec, "show_labels", False)) or bool(
        getattr(render_spec, "show_axes", False)
    ):
        return False
    return True


def _rgb(value: Any) -> tuple[float, float, float, float]:
    text = str(value).strip().lstrip("#")
    if len(text) == 6:
        channels = tuple(int(text[index : index + 2], 16) / 255.0 for index in (0, 2, 4))
        return (*channels, 1.0)  # type: ignore[return-value]
    raise GPUUnsupportedError(f"GPU frame batch could not read colour {value!r}")


def _frames(source: Any) -> tuple[FrameBatch, ...]:
    frames = tuple(getattr(source, "frames", ()) or ())
    if frames and isinstance(frames[0], FrameBatch):
        return frames  # type: ignore[return-value]
    from .batch_pipeline import _bundle_to_frame

    converted: list[FrameBatch] = []
    for frame in frames:
        bundle = getattr(frame, "bundle", None)
        if bundle is None:
            raise GPUUnsupportedError(
                "GPU ball animation requires FrameBatch structure frames"
            )
        index = int(getattr(frame, "index", len(converted)))
        converted.append(_bundle_to_frame(bundle, source_index=index))
    if len(converted) < 2:
        raise ValueError("animation output requires at least two selected frames")
    return tuple(converted)


def lowered_from_frame(
    frame: FrameBatch,
    camera: CameraSpec,
    *,
    width: int,
    height: int,
    background: tuple[float, float, float, float],
    atom_scale: float,
    bond_radius: float,
    show_hydrogen: bool,
    show_cell: bool,
    cell_color: str,
    cell_width_px: float,
    bonded: bool,
    tracker: Any,
    min_atom_radius: float | None = None,
    shading: str = "smooth",
) -> LoweredPlan:
    """Build instanced sphere and bond packets from one frame's arrays."""

    from .cpu.batch import element_style_tables

    colors, radii = element_style_tables(
        atom_scale,
        bond_radius=bond_radius,
        min_atom_radius=min_atom_radius,
    )
    shading_mode = str(shading).strip().lower()
    if shading_mode not in {"smooth", "flat"}:
        raise GPUUnsupportedError(
            "GPU FrameBatch shading must be 'smooth' or 'flat', "
            f"got {shading!r}"
        )
    flat = shading_mode == "flat"
    packets: list[DrawPacket] = []
    for index, (position, number) in enumerate(
        zip(frame.positions, frame.atomic_numbers, strict=True)
    ):
        atomic_number = int(number)
        if not show_hydrogen and atomic_number == 1:
            continue
        if frame.atom_colors is not None:
            rgb = tuple(float(channel) / 255.0 for channel in frame.atom_colors[index])
        else:
            rgb = tuple(float(channel) / 255.0 for channel in colors[atomic_number])
        packets.append(
            DrawPacket(
                kind="sphere",
                semantic_id=f"atom:{index}",
                viewport_id="main",
                vertices=np.asarray([position], dtype=np.float32),
                indices=None,
                rgba=(*rgb, 1.0),
                rect=(0.0, 0.0, 1.0, 1.0),
                metadata={"radius": float(radii[atomic_number]), "flat": flat},
            )
        )
    if bonded:
        bonds = tracker.update(
            frame.positions,
            frame.atomic_numbers,
            cell=frame.cell,
            pbc=frame.pbc,
        )
        for pair, vector in zip(bonds.pairs, bonds.vectors, strict=True):
            first = int(pair[0])
            second = int(pair[1])
            if not show_hydrogen and (
                int(frame.atomic_numbers[first]) == 1
                or int(frame.atomic_numbers[second]) == 1
            ):
                continue
            start = np.asarray(frame.positions[first], dtype=np.float64)
            end = start + np.asarray(vector, dtype=np.float64)
            mid = (start + end) * 0.5
            for endpoint, atom_index, name in (
                (start, first, "a"),
                (end, second, "b"),
            ):
                if frame.atom_colors is not None:
                    rgb = tuple(
                        float(channel) / 255.0 for channel in frame.atom_colors[atom_index]
                    )
                else:
                    rgb = tuple(
                        float(channel) / 255.0
                        for channel in colors[int(frame.atomic_numbers[atom_index])]
                    )
                packets.append(
                    DrawPacket(
                        kind="cylinder",
                        semantic_id=f"bond:{first}:{second}:{name}",
                        viewport_id="main",
                        vertices=np.asarray([endpoint, mid], dtype=np.float32),
                        indices=None,
                        rgba=(*rgb, 1.0),
                        rect=(0.0, 0.0, 1.0, 1.0),
                        metadata={"radius": float(bond_radius), "flat": flat},
                    )
                )
    if show_cell and (frame.natoms or bool(np.any(frame.pbc))):
        corners = frame_box_corners(frame)
        segments = [[corners[start], corners[end]] for start, end in _CELL_EDGES]
        packets.append(
            DrawPacket(
                kind="line",
                semantic_id="unit-cell",
                viewport_id="main",
                vertices=np.asarray(segments, dtype=np.float32).reshape(-1, 3),
                indices=None,
                rgba=_rgb(cell_color),
                rect=(0.0, 0.0, 1.0, 1.0),
                metadata={
                    "kind": "unit_cell",
                    "depth_test": False,
                    "width_px": float(cell_width_px),
                },
            )
        )
    digest = lowered_plan_digest(
        LoweredPlan(
            width=int(width),
            height=int(height),
            background=background,
            packets=tuple(packets),
            plan_sha256="",
            cameras={"main": camera},
        )
    )
    return LoweredPlan(
        width=int(width),
        height=int(height),
        background=background,
        packets=tuple(packets),
        plan_sha256=digest,
        cameras={"main": camera},
    )


def render_gpu_animation(
    source: Any,
    output: str | Path,
    *,
    view: Any = None,
    camera: CameraSpec | None = None,
    render_spec: Any = None,
    fps: float = 12.0,
    topology_data: Any = None,
    cell_overlays: Any = None,
    vector_overlays: Any = None,
    atom_groups: Any = None,
    bond_groups: Any = None,
    time_spec: Any = None,
    annotation_spec: Any = None,
    atom_property_color: Any = None,
) -> RenderResult:
    """Encode an animation with one GPU session and at most one frame in flight."""

    representation = str(getattr(render_spec, "representation", "ball_stick"))
    batch_options = dict(
        representation=representation,
        view=view,
        render_spec=render_spec,
        topology_data=topology_data,
        cell_overlays=cell_overlays,
        vector_overlays=vector_overlays,
        atom_groups=atom_groups,
        bond_groups=bond_groups,
        atom_property_color=atom_property_color,
    )
    frames = tuple(getattr(source, "frames", ()) or ())
    batch_frames = bool(frames) and isinstance(frames[0], FrameBatch)
    if not frame_batch_fast_path(source, **batch_options):
        if batch_frames and representation in {"ball", "ball_stick"}:
            raise GPUUnsupportedError(
                "GPU FrameBatch animation draws only the stored spheres, bonds, "
                "and cell. Display mode, groups, overlays, topology, axes, and "
                "property colors need planned structure frames; text is "
                "unsupported on the GPU."
            )
        return render_animation(
            source,
            output,
            view=view,
            camera=camera,
            render_spec=render_spec,
            topology_data=topology_data,
            cell_overlays=cell_overlays,
            vector_overlays=vector_overlays,
            atom_groups=atom_groups,
            bond_groups=bond_groups,
            fps=fps,
            time_spec=time_spec,
            annotation_spec=annotation_spec,
            atom_property_color=atom_property_color,
        )
    if len(frames) < 2:
        raise ValueError("animation output requires at least two selected frames")
    frames = _frames(source)
    if camera is None:
        raise GPUUnsupportedError("GPU ball animation requires a camera")
    if not np.isfinite(fps) or float(fps) <= 0.0:
        raise ValueError("animation fps must be finite and positive")
    width = int(getattr(render_spec, "width", 900))
    height = int(getattr(render_spec, "height", 720))
    background = tuple(float(channel) for channel in getattr(
        render_spec, "background", (1.0, 1.0, 1.0, 1.0)
    ))
    from .animation_time import (
        coerce_animation_time_spec,
        draw_time_label,
        resolve_animation_times,
    )
    from .fast_animation import _gif_writer, _mp4_writer
    from .frame_annotations import (
        coerce_frame_annotation_spec,
        resolve_frame_annotations,
    )

    resolved_time = coerce_animation_time_spec(time_spec)
    resolved_annotation = coerce_frame_annotation_spec(annotation_spec)
    if resolved_time is not None and resolved_annotation is not None:
        raise ValueError("physical-time and generic frame annotations cannot be combined")
    time_series = resolve_animation_times(frames, resolved_time) if resolved_time else None
    annotation_series = (
        resolve_frame_annotations(frames, resolved_annotation) if resolved_annotation else None
    )
    from PIL import Image
    from io import BytesIO

    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.suffix.lower() == ".mp4":
        writer = _mp4_writer(destination, width=width, height=height, fps=float(fps))
    else:
        writer = _gif_writer(destination, fps=float(fps))
    tracker = None
    if representation == "ball_stick":
        from molcrys_kit.structures import VerletBondTracker

        tracker = VerletBondTracker(skin=0.5)
    frame_digests: list[str] = []
    try:
        with GpuSession() as session:
            for ordinal, frame in enumerate(frames):
                lowered = lowered_from_frame(
                    frame,
                    camera,
                    width=width,
                    height=height,
                    background=background,  # type: ignore[arg-type]
                    atom_scale=float(getattr(render_spec, "atom_scale", 1.0)),
                    bond_radius=float(getattr(render_spec, "bond_radius", 0.15)),
                    show_hydrogen=bool(getattr(render_spec, "show_hydrogen", True)),
                    show_cell=bool(getattr(render_spec, "show_cell", True)),
                    min_atom_radius=getattr(render_spec, "min_atom_radius", None),
                    cell_color=str(getattr(render_spec, "cell_color", "#333333")),
                    cell_width_px=float(getattr(render_spec, "cell_width_px", 2.0)),
                    bonded=tracker is not None,
                    tracker=tracker,
                    shading=str(getattr(render_spec, "shading", "smooth")),
                )
                frame_digests.append(lowered.plan_sha256)
                png = session.render_lowered(lowered)
                with Image.open(BytesIO(png)) as image:
                    frame_image = image.convert("RGB")
                series = annotation_series or time_series
                if series is not None:
                    frame_image = draw_time_label(
                        frame_image, series.labels[ordinal], series.spec.position
                    )
                rgb = np.ascontiguousarray(np.asarray(frame_image.convert("RGB")))
                if destination.suffix.lower() == ".mp4":
                    writer.send(rgb)
                else:
                    writer.append_data(rgb)
    finally:
        writer.close()
    data = destination.read_bytes()
    return RenderResult(
        schema=RENDER_RESULT_SCHEMA,
        backend="gpu",
        format=destination.suffix.lower().lstrip("."),
        width=width,
        height=height,
        plan_sha256=hashlib.sha256("\n".join(frame_digests).encode()).hexdigest(),
        output_sha256=hashlib.sha256(data).hexdigest(),
        output=destination,
        data=None,
        warnings=(),
        metadata={
            "requested_backend": "gpu",
            "actual_backend": "gpu",
            "frame_batch": True,
            "in_flight_limit": 1,
            "frame_count": len(frames),
            "fallback": None,
        },
    )
