"""CLI adapter for workload-driven array batch rendering."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path

import numpy as np

from .frame_selection import parse_frame_indices
from .renderer_selection import RendererDecision, select_renderer


@dataclass(frozen=True, slots=True)
class WorkloadInspection:
    frame_indices: tuple[int, ...]
    atom_frames: int
    lammps_dump: bool


def add_batch_render_arguments(parser: argparse.ArgumentParser) -> None:
    """Register array-renderer controls shared by static and animated output."""

    parser.add_argument(
        "--renderer",
        choices=("auto", "batch", "general"),
        default="auto",
        help="Renderer selection; auto uses atom count times frame count.",
    )
    parser.add_argument(
        "--repeat",
        nargs=3,
        type=int,
        default=(1, 1, 1),
        metavar=("NX", "NY", "NZ"),
        help="Repeat each periodic frame along its three cell vectors.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Bounded CPU frame workers; default follows CPU and memory.",
    )
    parser.add_argument(
        "--zoom",
        type=float,
        default=1.0,
        help="Camera zoom multiplier shared by every selected frame.",
    )
    parser.add_argument(
        "--framing-margin",
        type=float,
        default=1.12,
        help="Shared viewport margin multiplier (default: 1.12).",
    )
    parser.add_argument(
        "--frame-duration",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Seconds per animation frame; mutually exclusive with --fps.",
    )
    parser.add_argument(
        "--profile-json",
        type=Path,
        default=None,
        metavar="PATH",
        help="Write batch-render timings and peak memory as JSON.",
    )
    parser.add_argument(
        "--bond-skin",
        type=float,
        default=0.5,
        help="Verlet skin in Angstrom for bonded animations (default: 0.5).",
    )


def validate_batch_render_options(args: argparse.Namespace, *, animation: bool) -> None:
    if args.frame_duration is not None:
        if not animation:
            raise ValueError("--frame-duration is only valid for GIF/MP4 output")
        if args.frame_duration <= 0.0 or not math.isfinite(args.frame_duration):
            raise ValueError("--frame-duration must be finite and greater than zero")
        if args.fps is not None:
            raise ValueError("--frame-duration and --fps are mutually exclusive")
    if any(value <= 0 for value in args.repeat):
        raise ValueError("--repeat values must be positive")
    if args.workers is not None and args.workers <= 0:
        raise ValueError("--workers must be positive")
    if not math.isfinite(args.bond_skin) or args.bond_skin <= 0.0:
        raise ValueError("--bond-skin must be finite and greater than zero")
    for value, flag in (
        (args.zoom, "--zoom"),
        (args.framing_margin, "--framing-margin"),
    ):
        if value <= 0.0 or not math.isfinite(value):
            raise ValueError(f"{flag} must be finite and greater than zero")


def _is_lammps_dump(args: argparse.Namespace) -> bool:
    value = str(args.input_format or "").lower()
    return value in {"lammps-dump", "lammps-dump-text"} or Path(
        args.input
    ).suffix.lower() in {".dump", ".lammpstrj", ".lammpsdump"}


def _selected_indices(args: argparse.Namespace, frame_count: int) -> list[int]:
    if Path(args.output).suffix.lower() in {".gif", ".mp4"}:
        return parse_frame_indices(
            frame_count,
            args.frame_range,
            args.stride if args.stride is not None else 1,
        )
    frame = 0 if args.frame is None else int(args.frame)
    frame = frame + frame_count if frame < 0 else frame
    if not 0 <= frame < frame_count:
        raise ValueError(
            f"frame {args.frame} is out of range for {frame_count} frame(s)"
        )
    return [frame]


def inspect_workload(args: argparse.Namespace) -> WorkloadInspection:
    """Measure atom-frames without constructing render scenes or meshes."""

    repeat_factor = int(np.prod(args.repeat))
    lammps_dump = _is_lammps_dump(args)
    if lammps_dump:
        from ..loader.lammps_batch import index_lammps_dump

        index = index_lammps_dump(args.input)
        selected = _selected_indices(args, len(index))
        atom_frames = sum(index.records[item].natoms for item in selected)
    else:
        from ..loader.structure_input import (
            count_structure_frames,
            iter_atomistic_frames,
            load_structure_input,
        )

        frame_count = count_structure_frames(
            args.input, input_format=args.input_format, type_map=args.type_map
        )
        selected = _selected_indices(args, frame_count)
        suffix = Path(args.input).suffix.lower()
        resolved = str(args.input_format or "").lower()
        if resolved in {"cif", "cube"} or suffix in {".cif", ".cube"}:
            loaded = load_structure_input(
                args.input,
                input_format=args.input_format,
                type_map=args.type_map,
                frame_indices=selected,
            )
            atom_frames = sum(len(item.bundle.raw_atoms) for item in loaded.frames)
        else:
            atom_frames = sum(
                len(frame.atoms)
                for frame, _ in iter_atomistic_frames(
                    args.input,
                    input_format=args.input_format,
                    type_map=args.type_map,
                    frame_indices=selected,
                )
            )
    return WorkloadInspection(
        frame_indices=tuple(selected),
        atom_frames=int(atom_frames * repeat_factor),
        lammps_dump=lammps_dump,
    )


def renderer_decision(
    args: argparse.Namespace,
) -> tuple[RendererDecision, WorkloadInspection]:
    workload = inspect_workload(args)
    decision = select_renderer(
        args.renderer,
        atom_frames=workload.atom_frames,
        backend=args.backend,
        representation=args.style,
        output_format=Path(args.output).suffix.lower().lstrip("."),
    )
    return decision, workload


def _color8(value: str, *, alpha: bool) -> tuple[int, ...]:
    text = str(value).strip().lstrip("#")
    if len(text) not in {6, 8}:
        raise ValueError("expected #RRGGBB or #RRGGBBAA colour")
    channels = tuple(
        int(text[index : index + 2], 16) for index in range(0, len(text), 2)
    )
    if alpha:
        return channels if len(channels) == 4 else channels + (255,)
    return channels[:3]


def _result_payload(args, result, decision, *, install_command: str) -> dict:
    output = result.output
    source = Path(args.input).expanduser().resolve()
    metadata = dict(result.profile)
    metadata["renderer_selection"] = decision.to_dict()
    if args.profile_json is not None:
        profile_path = Path(args.profile_json).expanduser().resolve()
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return {
        "schema": "mattervis.render-result/v1",
        "ok": True,
        "backend": "cpu",
        "camera": asdict(result.camera),
        "warnings": [],
        "install": install_command,
        "output": {
            "path": str(output),
            "sha256": result.output_sha256,
            "bytes": output.stat().st_size,
            "format": output.suffix.lower().lstrip("."),
        },
        "source": {
            "path": str(source),
            "bytes": source.stat().st_size,
            "input_format": args.input_format or "auto",
            "selected_frames": list(result.selected_frames),
        },
        "result": {
            "schema": "mattervis.batch-render-profile/v1",
            "backend": "cpu",
            "format": output.suffix.lower().lstrip("."),
            "width": args.width,
            "height": args.height,
            "warnings": [],
            "metadata": metadata,
        },
    }


def render_batch_if_selected(
    args: argparse.Namespace, *, install_command: str
) -> dict | None:
    """Render through canonical arrays when workload selection chooses batch."""

    decision, workload = renderer_decision(args)
    if decision.selected != "batch":
        return None
    fps = (
        1.0 / args.frame_duration
        if args.frame_duration is not None
        else (args.fps if args.fps is not None else 12.0)
    )
    background = _color8(args.background, alpha=True)
    cell_color = _color8(args.cell_color, alpha=False)
    animation = Path(args.output).suffix.lower() in {".gif", ".mp4"}
    has_overlay_layers = bool(
        args.show_labels
        or args.show_axes
        or args.vector_overlays is not None
        or args.display_time is not None
        or args.frame_field
    )
    if workload.lammps_dump and animation and not has_overlay_layers:
        from ..properties.cli import atom_property_spec
        from .fast_animation import render_lammps_animation

        result = render_lammps_animation(
            args.input,
            args.output,
            input_format=args.input_format,
            type_map=tuple(args.type_map) if args.type_map else None,
            frame_range=args.frame_range,
            stride=args.stride if args.stride is not None else 1,
            repeat=tuple(args.repeat),
            width=args.width,
            height=args.height,
            scale=args.scale,
            fps=fps,
            projection=args.projection,
            camera_axis=args.camera_axis,
            view_direction=tuple(args.view_direction) if args.view_direction else None,
            camera_position=(
                tuple(args.camera_position) if args.camera_position else None
            ),
            camera_up=tuple(args.camera_up) if args.camera_up else None,
            fit_multiplier=args.camera_distance,
            zoom=args.zoom,
            framing_margin=args.framing_margin,
            ortho_scale=args.ortho_scale,
            atom_scale=args.atom_scale,
            background=background,
            show_hydrogen=args.show_hydrogen,
            show_cell=(
                True if args.show_unit_cell is None else bool(args.show_unit_cell)
            ),
            cell_color=cell_color,
            cell_width_px=args.cell_width,
            bonded=args.style == "ball_stick",
            bond_radius=args.bond_radius,
            bond_skin=args.bond_skin,
            workers=args.workers,
            profile_path=args.profile_json,
            atom_property_color=(atom_property_spec(args) if args.color_by else None),
            property_data=args.property_data,
        )
    else:
        from ..cli import _load_vector_overlays
        from ..properties.cli import atom_property_spec
        from .batch_pipeline import render_array_input
        from .cli_controls import _animation_time_from_args, _frame_annotation_from_args

        result = render_array_input(
            args.input,
            args.output,
            input_format=args.input_format,
            type_map=tuple(args.type_map) if args.type_map else None,
            frame_indices=workload.frame_indices,
            repeat=tuple(args.repeat),
            width=args.width,
            height=args.height,
            scale=args.scale,
            fps=fps,
            projection=args.projection,
            camera_axis=args.camera_axis,
            view_direction=tuple(args.view_direction) if args.view_direction else None,
            camera_position=(
                tuple(args.camera_position) if args.camera_position else None
            ),
            camera_up=tuple(args.camera_up) if args.camera_up else None,
            fit_multiplier=args.camera_distance,
            zoom=args.zoom,
            framing_margin=args.framing_margin,
            ortho_scale=args.ortho_scale,
            atom_scale=args.atom_scale,
            background=background,
            show_hydrogen=args.show_hydrogen,
            show_cell=args.show_unit_cell,
            show_axes=bool(args.show_axes),
            show_labels=args.show_labels,
            cell_color=cell_color,
            cell_width_px=args.cell_width,
            bonded=args.style == "ball_stick",
            bond_radius=args.bond_radius,
            bond_skin=args.bond_skin,
            vector_overlays=_load_vector_overlays(args.vector_overlays),
            polyhedron_specs=tuple(args.polyhedron),
            polyhedron_site=args.polyhedron_site,
            polyhedron_cutoff=args.polyhedron_cutoff,
            animation_time=_animation_time_from_args(args),
            frame_annotation=_frame_annotation_from_args(args),
            profile_path=args.profile_json,
            atom_property_color=(atom_property_spec(args) if args.color_by else None),
            property_data=args.property_data,
        )
    return _result_payload(args, result, decision, install_command=install_command)


# Compatibility aliases for the first performance prototype.
add_fast_animation_arguments = add_batch_render_arguments
validate_fast_animation_options = validate_batch_render_options
render_fast_animation_if_eligible = render_batch_if_selected


__all__ = [
    "WorkloadInspection",
    "add_batch_render_arguments",
    "inspect_workload",
    "render_batch_if_selected",
    "renderer_decision",
    "validate_batch_render_options",
]
