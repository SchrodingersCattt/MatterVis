"""CLI adapter for the bounded large-LAMMPS animation path."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import math
from pathlib import Path


def add_fast_animation_arguments(parser: argparse.ArgumentParser) -> None:
    """Register controls shared by the fixed-viewport animation path."""
    parser.add_argument(
        "--repeat",
        nargs=3,
        type=int,
        default=(1, 1, 1),
        metavar=("NX", "NY", "NZ"),
        help="Repeat a LAMMPS animation frame along its three cell vectors.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Bounded CPU frame workers; default is selected from CPU and memory.",
    )
    parser.add_argument(
        "--zoom",
        type=float,
        default=1.0,
        help="Camera zoom multiplier fixed for every selected frame (default: 1).",
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
        help="Write large-animation stage timings and peak memory as JSON.",
    )
    parser.add_argument(
        "--bond-skin",
        type=float,
        default=0.5,
        help="Verlet skin in Angstrom for large bonded animations (default: 0.5).",
    )


def validate_fast_animation_options(
    args: argparse.Namespace,
    *,
    animation: bool,
) -> None:
    """Validate fast-animation controls without changing general render policy."""
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


def _is_fast_lammps_animation(args: argparse.Namespace) -> bool:
    output_suffix = Path(args.output).suffix.lower()
    if output_suffix not in {".gif", ".mp4"} or args.backend != "cpu":
        return False
    input_format = str(args.input_format or "").lower()
    input_suffix = Path(args.input).suffix.lower()
    lammps_dump = input_format in {"lammps-dump", "lammps-dump-text"} or (
        input_suffix in {".dump", ".lammpstrj", ".lammpsdump"}
    )
    return bool(
        lammps_dump
        and args.style in {"ball", "ball_stick"}
        and not args.show_labels
        and not bool(args.show_axes)
        and not args.polyhedron
        and args.vector_overlays is None
    )


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


def render_fast_animation_if_eligible(
    args: argparse.Namespace,
    *,
    install_command: str,
) -> dict | None:
    """Render one eligible large animation, otherwise return None."""
    if not _is_fast_lammps_animation(args):
        return None

    from .fast_animation import render_lammps_animation

    fps = (
        1.0 / args.frame_duration
        if args.frame_duration is not None
        else (args.fps if args.fps is not None else 12.0)
    )
    background = _color8(args.background, alpha=True)
    cell_color = _color8(args.cell_color, alpha=False)
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
        camera_position=(tuple(args.camera_position) if args.camera_position else None),
        camera_up=tuple(args.camera_up) if args.camera_up else None,
        fit_multiplier=args.camera_distance,
        zoom=args.zoom,
        framing_margin=args.framing_margin,
        atom_scale=args.atom_scale,
        background=background,
        show_hydrogen=args.show_hydrogen,
        show_cell=(True if args.show_unit_cell is None else bool(args.show_unit_cell)),
        cell_color=cell_color,
        cell_width_px=args.cell_width,
        bonded=args.style == "ball_stick",
        bond_radius=args.bond_radius,
        bond_skin=args.bond_skin,
        workers=args.workers,
        profile_path=args.profile_json,
    )
    output = result.output
    source = Path(args.input).expanduser().resolve()
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
            "input_format": args.input_format or "lammps-dump-text",
            "selected_frames": list(result.selected_frames),
        },
        "result": {
            "schema": "mattervis.large-animation-profile/v1",
            "backend": "cpu",
            "format": output.suffix.lower().lstrip("."),
            "width": args.width,
            "height": args.height,
            "warnings": [],
            "metadata": result.profile,
        },
    }


__all__ = [
    "add_fast_animation_arguments",
    "render_fast_animation_if_eligible",
    "validate_fast_animation_options",
]
