"""Agent-facing render CLI controls and validation."""

from __future__ import annotations

import argparse
import math
from pathlib import Path


def _add_render_control_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--atom-group",
        action="append",
        nargs="+",
        default=[],
        metavar="TOKEN",
        help=(
            "Selector-based atom rule: SELECTOR KEY=VALUE...; repeat in "
            "later-wins order."
        ),
    )
    parser.add_argument(
        "--bond-group",
        action="append",
        nargs="+",
        default=[],
        metavar="TOKEN",
        help=(
            "Selector-based bond rule: SELECTOR KEY=VALUE...; repeat in "
            "later-wins order."
        ),
    )
    parser.add_argument(
        "--camera-target",
        nargs=3,
        type=float,
        default=None,
        metavar=("X", "Y", "Z"),
        help="Explicit Cartesian look-at target in angstrom.",
    )
    parser.add_argument(
        "--field-of-view",
        type=float,
        default=None,
        metavar="DEG",
        help="Vertical field of view for perspective projection.",
    )
    parser.add_argument(
        "--ortho-scale",
        type=float,
        default=None,
        metavar="ANGSTROM",
        help="Orthographic half-height; overrides automatic scene fitting.",
    )
    parser.add_argument(
        "--camera-clip",
        nargs=2,
        type=float,
        default=None,
        metavar=("NEAR", "FAR"),
        help="Explicit positive camera clipping distances.",
    )
    parser.add_argument(
        "--sphere-detail",
        nargs=2,
        type=int,
        default=(12, 20),
        metavar=("LAT", "LON"),
        help="Sphere/ellipsoid latitude and longitude segments (default: 12 20).",
    )
    parser.add_argument(
        "--cylinder-sides",
        type=int,
        default=12,
        metavar="N",
        help="Bond-cylinder side count (default: 12).",
    )
    parser.add_argument(
        "--display-time",
        choices=("fs", "ps", "ns"),
        default=None,
        metavar="UNIT",
        help="Label every GIF/MP4 frame with physical simulation time.",
    )
    parser.add_argument(
        "--time-step",
        type=float,
        default=None,
        metavar="DT",
        help="MD integrator timestep used for physical-time labels.",
    )
    parser.add_argument(
        "--time-step-unit",
        choices=("fs", "ps", "ns"),
        default="fs",
        metavar="UNIT",
        help="Unit of --time-step (default: fs).",
    )
    parser.add_argument(
        "--dump-frequency",
        type=int,
        default=None,
        metavar="STEPS",
        help="MD steps between stored frames when source step metadata is absent.",
    )
    parser.add_argument(
        "--first-frame-step",
        type=int,
        default=0,
        metavar="STEP",
        help="Simulation step represented by source frame 0 (default: 0).",
    )
    parser.add_argument(
        "--time-position",
        choices=("top-left", "top-right", "bottom-left", "bottom-right"),
        default="top-left",
        help="Corner for the physical-time label (default: top-left).",
    )


def _camera_request(args: argparse.Namespace) -> dict:
    return {
        "projection": args.projection,
        "axis": args.camera_axis or "c",
        "view_direction": args.view_direction,
        "position": args.camera_position,
        "target": args.camera_target,
        "up": args.camera_up,
        "fit_multiplier": args.camera_distance,
        "field_of_view": args.field_of_view,
        "ortho_scale": args.ortho_scale,
        "clip": args.camera_clip,
    }


def _is_animation_output(args: argparse.Namespace) -> bool:
    return Path(args.output).suffix.lower() in {".gif", ".mp4"}


def _render_shading(args: argparse.Namespace) -> str:
    return str(args.shading)


def _render_ortep_mode(args: argparse.Namespace) -> str:
    if args.style != "ortep":
        if args.ortep_mode is not None:
            raise ValueError("--ortep-mode requires --style ortep")
        if args.ortep_probability is not None:
            raise ValueError("--ortep-probability requires --style ortep")
        return "solid"
    modes = {
        None: "axes",
        "ortep_solid": "solid",
        "ortep_axes": "axes",
        "ortep_hatch": "hatch",
    }
    if args.ortep_mode == "ortep_octant":
        raise ValueError(
            "--ortep-mode ortep_octant is not supported by the backend-neutral "
            "renderer; no visual fallback was attempted"
        )
    return modes[args.ortep_mode]


def _validate_render_options(args: argparse.Namespace) -> None:
    """Reject legacy flags that the backend-neutral path cannot honour."""

    unsupported: list[str] = []
    if args.monochrome:
        unsupported.append("--monochrome")
    if args.config is not None:
        unsupported.append("--config")
    if args.view_weights is not None:
        unsupported.append("--view-weights")
    if args.publication_layout:
        unsupported.append("--publication-layout")
    if args.publication_preset is not None:
        unsupported.append("--publication-preset")
    if args.publication_style is not None:
        unsupported.append("--publication-style")
    for dest, flag in (
        ("publication_option", "--publication-option"),
        ("publication_site_style", "--publication-site-style"),
        ("publication_legend_entry", "--publication-legend-entry"),
        ("publication_panel_label", "--publication-panel-label"),
    ):
        if getattr(args, dest):
            unsupported.append(flag)
    for dest, flag in (
        ("publication_legend_footer", "--publication-legend-footer"),
        ("title", "--title"),
        ("subtitle", "--subtitle"),
    ):
        if getattr(args, dest) is not None:
            unsupported.append(flag)
    if unsupported:
        raise ValueError(
            "unsupported by the backend-neutral render command: "
            + ", ".join(unsupported)
        )

    animation = _is_animation_output(args)
    if animation:
        if args.backend != "cpu":
            raise ValueError("GIF/MP4 output requires --backend cpu")
        if args.frame is not None:
            raise ValueError(
                "--frame is for static output; use --frame-range for GIF/MP4"
            )
        if args.polyhedron:
            raise ValueError(
                "animated --polyhedron overlays are not yet supported; no static "
                "overlay was silently reused across frames"
            )
        if args.vector_overlays is not None:
            raise ValueError(
                "animated --vector-overlays are not yet supported; use static output"
            )
        if args.stride is not None and args.stride <= 0:
            raise ValueError("--stride must be greater than zero")
        if args.fps is not None and args.fps <= 0.0:
            raise ValueError("--fps must be greater than zero")
        if args.time_step is not None and (
            not math.isfinite(args.time_step) or args.time_step <= 0.0
        ):
            raise ValueError("--time-step must be finite and greater than zero")
        if args.dump_frequency is not None and args.dump_frequency <= 0:
            raise ValueError("--dump-frequency must be greater than zero")
        if args.display_time is None and (
            args.time_step is not None
            or args.dump_frequency is not None
            or args.first_frame_step != 0
            or args.time_position != "top-left"
        ):
            raise ValueError(
                "--time-step, --dump-frequency, --first-frame-step, and "
                "--time-position require --display-time"
            )
    else:
        if args.frame_range is not None:
            raise ValueError("--frame-range is only valid for GIF/MP4 output")
        if args.stride is not None:
            raise ValueError("--stride is only valid for GIF/MP4 output")
        if args.fps is not None:
            raise ValueError("--fps is only valid for GIF/MP4 output")
        if (
            args.display_time is not None
            or args.time_step is not None
            or args.dump_frequency is not None
            or args.first_frame_step != 0
            or args.time_position != "top-left"
        ):
            raise ValueError("physical-time options are only valid for GIF/MP4 output")
    if not args.polyhedron:
        if args.polyhedron_site is not None:
            raise ValueError("--polyhedron-site requires --polyhedron")
        if args.polyhedron_cutoff is not None:
            raise ValueError("--polyhedron-cutoff requires --polyhedron")
    else:
        if args.polyhedron_cutoff is not None and args.polyhedron_cutoff <= 0.0:
            raise ValueError("--polyhedron-cutoff must be greater than zero")
        from ..agent_topology import parse_polyhedron_specs

        parse_polyhedron_specs(args.polyhedron)
    if not math.isfinite(args.cell_width) or args.cell_width <= 0.0:
        raise ValueError("--cell-width must be finite and greater than zero")
    if args.field_of_view is not None:
        if not 0.0 < args.field_of_view < 179.0:
            raise ValueError("--field-of-view must lie in (0, 179)")
        if args.projection != "perspective":
            raise ValueError("--field-of-view requires --perspective")
    if args.ortho_scale is not None:
        if not math.isfinite(args.ortho_scale) or args.ortho_scale <= 0.0:
            raise ValueError("--ortho-scale must be finite and greater than zero")
        if args.projection != "orthographic":
            raise ValueError("--ortho-scale requires --orthogonal")
    if args.camera_clip is not None:
        near, far = args.camera_clip
        if not 0.0 < near < far:
            raise ValueError("--camera-clip must satisfy 0 < NEAR < FAR")
    latitude, longitude = args.sphere_detail
    if latitude < 2 or longitude < 3:
        raise ValueError("--sphere-detail must be at least 2 3")
    if args.cylinder_sides < 3:
        raise ValueError("--cylinder-sides must be at least 3")
    _render_ortep_mode(args)
    _style_groups(args)


def _style_groups(args: argparse.Namespace) -> tuple[list[dict], list[dict]]:
    from .cli_groups import parse_group_arguments

    return (
        parse_group_arguments(args.atom_group, kind="atom"),
        parse_group_arguments(args.bond_group, kind="bond"),
    )


def _animation_time_from_args(args: argparse.Namespace):
    if args.display_time is None:
        return None
    from .animation_time import AnimationTimeSpec

    return AnimationTimeSpec(
        display_unit=args.display_time,
        time_step=args.time_step,
        time_step_unit=args.time_step_unit,
        dump_frequency=args.dump_frequency,
        first_frame_step=args.first_frame_step,
        position=args.time_position,
    )
