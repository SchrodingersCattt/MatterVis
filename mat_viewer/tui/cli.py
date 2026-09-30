"""Command-line integration for static and stateful terminal views."""

from __future__ import annotations

import argparse


def add_session_arguments(parser: argparse.ArgumentParser) -> None:
    """Add machine-readable terminal rendering and session options."""
    parser.add_argument(
        "--charset",
        choices=("unicode", "ascii7"),
        default="unicode",
        help="Static/session glyph set (default: unicode; ascii7 implies monochrome).",
    )
    parser.add_argument(
        "--session-format",
        choices=("jsonl",),
        default=None,
        help="Keep one stateful session on stdin/stdout using newline-delimited JSON.",
    )


def run_session(args: argparse.Namespace, crystal, camera, *, label_mode: str) -> bool:
    """Run the requested machine session and report whether it handled the CLI."""
    if args.session_format != "jsonl":
        return False
    from .session import TerminalSession, run_jsonl_session

    session = TerminalSession(
        crystal,
        camera=camera,
        width=args.width or 80,
        height=args.height or 24,
        mono=True if args.charset == "ascii7" else args.mono,
        charset=args.charset,
        show_bonds=not args.no_bonds,
        show_cell=not args.no_cell,
        label_mode=label_mode,
        show_minor=args.show_minor,
        display_level=getattr(args, "level", "auto"),
    )
    run_jsonl_session(session)
    return True


def compose_static_frame(
    args: argparse.Namespace,
    crystal,
    camera,
    points,
    depth,
    *,
    label_mode: str,
    display_level: str = "auto",
) -> str:
    """Render static output with the same charset choices as agent sessions."""
    from .compositor import compose_frame

    return compose_frame(
        crystal,
        camera,
        points,
        depth,
        width=args.width,
        height=args.height,
        mono=True if args.charset == "ascii7" else args.mono,
        charset=args.charset,
        label_mode=label_mode,
        show_bonds=not args.no_bonds,
        show_cell=not args.no_cell,
        show_minor=args.show_minor,
        display_level=display_level,
        zoom=camera.viewport_zoom,
    )


def render_static_output(args, crystal, camera, points, depth, *, label_mode: str) -> str:
    """Render one bounded static CLI response, including its short header."""
    if args.format == "structured":
        from .serializer import serialize_crystal

        return serialize_crystal(
            crystal,
            camera,
            points,
            show_minor=args.show_minor,
            display_level=getattr(args, "level", "auto"),
        )

    display_level = getattr(args, "level", "auto")
    frame = compose_static_frame(
        args,
        crystal,
        camera,
        points,
        depth,
        label_mode=label_mode,
        display_level=display_level,
    )
    from .observation import build_static_header

    header = build_static_header(
        crystal,
        camera,
        display_level=display_level,
        label_mode=label_mode,
        show_bonds=not args.no_bonds,
        show_cell=not args.no_cell,
        show_minor=args.show_minor,
        mono=True if args.charset == "ascii7" else args.mono,
        width=args.width or 80,
        height=args.height or 24,
    )
    return f"{header}\n{frame}"


__all__ = [
    "add_session_arguments",
    "compose_static_frame",
    "render_static_output",
    "run_session",
]
