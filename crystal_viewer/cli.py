"""Command-line interface for MatterVis.

Subcommands
-----------
render  Generate publication-quality figures from CIF files.
serve   Launch the interactive Dash browser viewer.

Usage::

    python -m crystal_viewer render structure.cif -o figure.png
    python -m crystal_viewer serve --cif structure.cif --port 50001
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Render subcommand
# ---------------------------------------------------------------------------

_DISPLAY_MODES = ("formula_unit", "unit_cell", "asymmetric_unit", "cluster")
_STYLES = ("ball_stick", "ball", "stick", "ortep", "wireframe")
_MATERIALS = ("mesh", "flat")
_ORTEP_MODES = ("ortep_solid", "ortep_axes", "ortep_octant", "ortep_hatch")
_IMAGE_EXTENSIONS = (".png", ".pdf", ".svg")
_SUPPORTED_EXTENSIONS = _IMAGE_EXTENSIONS + (".html",)


def _build_render_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "render",
        help="Render a CIF file to a publication-quality figure.",
        description=(
            "Load a CIF file and export a static figure. Output format is "
            "inferred from the file extension (.png, .pdf, .svg, .html)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s structure.cif -o fig.png\n"
            "  %(prog)s structure.cif -o fig.pdf --view unit_cell --no-hydrogen\n"
            "  %(prog)s structure.cif -o fig.png --style ortep --ortep-mode ortep_hatch --monochrome\n"
            "  %(prog)s structure.cif -o fig.html --orthogonal --atom-scale 1.2\n"
        ),
    )

    # Positional
    p.add_argument("cif", metavar="CIF", help="Path to the input CIF file.")

    # Output
    p.add_argument(
        "-o", "--output", required=True,
        help="Output file path. Format inferred from extension: .png, .pdf, .svg, .html.",
    )

    # Display mode
    p.add_argument(
        "--view", choices=_DISPLAY_MODES, default="formula_unit",
        help="Display mode (default: formula_unit).",
    )

    # Rendering style
    p.add_argument(
        "--style", choices=_STYLES, default="ball_stick",
        help="Atom/bond rendering style (default: ball_stick).",
    )
    p.add_argument(
        "--material", choices=_MATERIALS, default="mesh",
        help="Surface material (default: mesh).",
    )

    # Projection
    proj = p.add_mutually_exclusive_group()
    proj.add_argument(
        "--orthogonal", dest="projection", action="store_const", const="orthographic",
        help="Use orthographic projection.",
    )
    proj.add_argument(
        "--perspective", dest="projection", action="store_const", const="perspective",
        help="Use perspective projection (default).",
    )
    p.set_defaults(projection="perspective")

    # Boolean display options
    p.add_argument("--show-hydrogen", dest="show_hydrogen", action="store_true", default=False, help="Show hydrogen atoms.")
    p.add_argument("--no-hydrogen", dest="show_hydrogen", action="store_false", help="Hide hydrogen atoms (default).")
    p.add_argument("--show-cell", dest="show_unit_cell", action="store_true", default=True, help="Show unit cell edges (default).")
    p.add_argument("--no-cell", dest="show_unit_cell", action="store_false", help="Hide unit cell edges.")
    p.add_argument("--show-axes", dest="show_axes", action="store_true", default=True, help="Show lattice axes (default).")
    p.add_argument("--no-axes", dest="show_axes", action="store_false", help="Hide lattice axes.")
    p.add_argument("--show-labels", dest="show_labels", action="store_true", default=False, help="Show atom labels.")
    p.add_argument("--no-labels", dest="show_labels", action="store_false", help="Hide atom labels (default).")
    p.add_argument("--monochrome", action="store_true", default=False, help="Render in greyscale.")

    # Numeric parameters
    p.add_argument("--atom-scale", type=float, default=1.0, help="Atom radius scale factor (default: 1.0).")
    p.add_argument("--bond-radius", type=float, default=0.15, help="Bond cylinder radius in Å (default: 0.15).")
    p.add_argument("--camera-distance", type=float, default=1.8, help="Camera eye distance (default: 1.8).")

    # Colors
    p.add_argument("--background", default="#FFFFFF", help="Background hex colour (default: #FFFFFF).")

    # Image dimensions
    p.add_argument("--width", type=int, default=900, help="Image width in pixels (default: 900).")
    p.add_argument("--height", type=int, default=720, help="Image height in pixels (default: 720).")
    p.add_argument("--scale", type=int, default=2, help="Image scale factor / supersampling (default: 2).")

    # ORTEP
    p.add_argument(
        "--ortep-probability", type=float, default=0.5,
        help="ORTEP ellipsoid probability (0.0–1.0, default: 0.5).",
    )
    p.add_argument(
        "--ortep-mode", choices=_ORTEP_MODES, default="ortep_axes",
        help="ORTEP rendering variant (default: ortep_axes).",
    )

    # Config escape-hatch
    p.add_argument(
        "--config", metavar="JSON",
        help="Path to a JSON file with full style overrides. CLI flags take precedence over config values.",
    )

    # View-direction scoring weights
    p.add_argument(
        "--view-weights", metavar="JSON",
        help=(
            'JSON dict of auto-view scoring weight overrides. '
            'Keys: organic_plane, organic_depth, aspect, robust_sep, '
            'close_contact, occlusion, cluster_crowding, elev_pen. '
            'Example: \'{"occlusion": 3.0, "elev_pen": 0.5}\''
        ),
    )

    return p


def _build_style_overrides(args: argparse.Namespace) -> Dict[str, Any]:
    """Collect CLI flags into a style-override dict."""
    overrides: Dict[str, Any] = {}

    # Load config JSON first (CLI flags override it)
    if args.config:
        config_path = Path(args.config)
        if not config_path.exists():
            sys.exit(f"Error: config file not found: {args.config}")
        with open(config_path) as f:
            try:
                config_data = json.load(f)
            except json.JSONDecodeError as exc:
                sys.exit(f"Error: invalid JSON in config file: {exc}")
        # Config may have a nested "style" key or be flat
        if "style" in config_data and isinstance(config_data["style"], dict):
            overrides.update(config_data["style"])
        else:
            overrides.update(config_data)

    # CLI flags override config values
    overrides["display_mode"] = args.view
    overrides["style"] = args.style
    overrides["material"] = args.material
    overrides["projection"] = args.projection
    overrides["show_hydrogen"] = args.show_hydrogen
    overrides["show_unit_cell"] = args.show_unit_cell
    overrides["show_axes"] = args.show_axes
    overrides["show_labels"] = args.show_labels
    overrides["monochrome"] = args.monochrome
    overrides["atom_scale"] = args.atom_scale
    overrides["bond_radius"] = args.bond_radius
    overrides["camera_eye_distance"] = args.camera_distance
    overrides["background"] = args.background
    overrides["ortep_probability"] = args.ortep_probability
    overrides["ortep_mode"] = args.ortep_mode
    # Suppress title in static exports for cleaner publication figures
    overrides["show_title"] = False

    return overrides


def _render_main(args: argparse.Namespace) -> None:
    """Execute the render subcommand."""
    cif_path = Path(args.cif).resolve()
    if not cif_path.exists():
        sys.exit(f"Error: CIF file not found: {args.cif}")

    output_path = Path(args.output).resolve()
    ext = output_path.suffix.lower()
    if ext not in _SUPPORTED_EXTENSIONS:
        sys.exit(
            f"Error: unsupported output format '{ext}'. "
            f"Supported: {', '.join(_SUPPORTED_EXTENSIONS)}"
        )

    # Lazy imports to keep CLI startup fast when just showing --help
    from .loader import build_loaded_crystal, build_bundle_scene
    from .scene import scene_style
    from .renderer import build_figure

    name = cif_path.stem
    print(f"Loading {cif_path.name} ...")

    # Parse view-weights JSON if provided
    view_weights = None
    if args.view_weights:
        try:
            view_weights = json.loads(args.view_weights)
            if not isinstance(view_weights, dict):
                sys.exit("Error: --view-weights must be a JSON object.")
        except json.JSONDecodeError as exc:
            sys.exit(f"Error: invalid JSON in --view-weights: {exc}")

    bundle = build_loaded_crystal(
        name=name,
        cif_path=str(cif_path),
        title=name,
        view_weights=view_weights,
    )

    scene = build_bundle_scene(
        bundle,
        display_mode=args.view,
        show_hydrogen=args.show_hydrogen,
    )

    overrides = _build_style_overrides(args)
    style = scene_style(scene, overrides)

    print(f"Building figure ({args.style}, {args.view}) ...")
    fig = build_figure(scene, style)

    # kaleido ≥1.0 crashes when layout.title is None (it tries .get("text")
    # on it). Ensure a valid title dict is always present for static export.
    fig_dict = fig.to_dict()
    layout = fig_dict.setdefault("layout", {})
    if layout.get("title") is None:
        layout["title"] = {"text": ""}
    import plotly.graph_objects as go
    fig = go.Figure(fig_dict)

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if ext == ".html":
        fig.write_html(str(output_path), include_plotlyjs="cdn", full_html=True)
    else:
        fig.write_image(
            str(output_path),
            width=args.width,
            height=args.height,
            scale=args.scale,
        )

    size = os.path.getsize(output_path)
    print(f"Wrote {ext.lstrip('.')} : {output_path}  ({size:,} bytes)")


# ---------------------------------------------------------------------------
# Serve subcommand
# ---------------------------------------------------------------------------

def _build_serve_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "serve",
        help="Launch the interactive Dash browser viewer.",
        description="Start the MatterVis web application for interactive crystal visualization.",
    )
    p.add_argument("--preset", default=None, help="Preset JSON to load and save.")
    p.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0).")
    p.add_argument("--port", type=int, default=50001, help="Port to expose (default: 50001).")
    p.add_argument("--structure", nargs="*", help="Serve only selected catalog structure(s).")
    p.add_argument(
        "--cif", action="append", default=[],
        help="CIF path to preload. Repeat for multiple files: --cif a.cif --cif b.cif.",
    )
    p.add_argument("--api-only", action="store_true", help="Reserved for automation mode.")
    return p


def _serve_main(args: argparse.Namespace) -> None:
    """Execute the serve subcommand by delegating to the existing Dash app."""
    # Build argv list matching factory._build_parser() expectations
    argv: list[str] = []
    if args.preset is not None:
        argv.extend(["--preset", args.preset])
    argv.extend(["--host", args.host])
    argv.extend(["--port", str(args.port)])
    if args.structure:
        argv.append("--structure")
        argv.extend(args.structure)
    for cif in args.cif:
        argv.extend(["--cif", cif])
    if args.api_only:
        argv.append("--api-only")

    from .app.factory import main as _factory_main
    _factory_main(argv)


# ---------------------------------------------------------------------------
# TUI subcommand
# ---------------------------------------------------------------------------

_TUI_FORMATS = ("ascii", "structured")
_TUI_PROJECTIONS = ("orthographic", "perspective")
_TUI_VIEWS = ("auto", "a", "b", "c", "diagonal", "ab", "ac", "bc")


def _build_tui_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "tui",
        help="Terminal-based crystal structure viewer.",
        description=(
            "View a crystal structure in the terminal. Default is interactive "
            "(Textual TUI). Use --no-interaction for static output suitable "
            "for piping to LLMs or scripts."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s structure.cif\n"
            "  %(prog)s structure.cif --no-interaction --mono\n"
            "  %(prog)s structure.cif --no-interaction --format structured\n"
            "  %(prog)s POSCAR --no-interaction --view c\n"
        ),
    )
    p.add_argument("FILE", help="Crystal structure file (.cif, .vasp, .poscar, .extxyz).")
    p.add_argument(
        "--interaction", "--interactive",
        action="store_true", default=True, dest="interaction",
        help="Launch interactive TUI (default).",
    )
    p.add_argument(
        "--no-interaction", "--no-interactive",
        action="store_false", dest="interaction",
        help="Print static output to stdout (for LLM/script piping).",
    )
    p.add_argument(
        "--mono", action="store_true", default=False,
        help="Force monochrome output (no ANSI color codes).",
    )
    p.add_argument(
        "--format", choices=_TUI_FORMATS, default="ascii",
        help="Non-interactive output format (default: ascii).",
    )
    p.add_argument(
        "--compact", action="store_true", default=False,
        help="Use single-char dot mode instead of element symbols.",
    )
    p.add_argument(
        "--projection", choices=_TUI_PROJECTIONS, default="orthographic",
        help="Initial projection mode (default: orthographic).",
    )
    p.add_argument(
        "--width", type=int, default=None,
        help="Override terminal grid width (auto-detect if omitted).",
    )
    p.add_argument(
        "--height", type=int, default=None,
        help="Override terminal grid height (auto-detect if omitted).",
    )
    p.add_argument(
        "--view", choices=_TUI_VIEWS, default="auto",
        help="Initial view direction (default: auto → diagonal).",
    )
    p.add_argument(
        "--no-bonds", action="store_true", default=False,
        help="Hide bonds.",
    )
    p.add_argument(
        "--no-cell", action="store_true", default=False,
        help="Hide unit cell edges.",
    )
    return p


def _tui_main(args: argparse.Namespace) -> None:
    """Execute the tui subcommand."""
    filepath = args.FILE
    if not Path(filepath).exists():
        print(f"Error: file not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    from .tui.loader_adapter import load_for_tui
    from .math.camera import Camera, project_points

    crystal = load_for_tui(filepath)
    cam = Camera.from_view_name(args.view, crystal)

    if not args.interaction:
        # Static output mode
        pts_2d, depth = project_points(cam, crystal.cart_coords)

        if args.format == "structured":
            from .tui.serializer import serialize_crystal
            output = serialize_crystal(crystal, cam, pts_2d)
        else:
            from .tui.renderer import render_ascii_frame
            output = render_ascii_frame(
                crystal, cam, pts_2d, depth,
                width=args.width, height=args.height,
                mono=args.mono, compact=args.compact,
                show_bonds=not args.no_bonds,
                show_cell=not args.no_cell,
            )
        print(output)
    else:
        # Interactive TUI mode
        from .tui.app import CrystalTUI
        app = CrystalTUI(
            crystal=crystal, mono=args.mono,
            initial_view=args.view,
            show_bonds=not args.no_bonds,
            show_cell=not args.no_cell,
            compact=args.compact,
        )
        app.run()


# ---------------------------------------------------------------------------
# Top-level CLI router
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> None:
    """MatterVis CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="matvis",
        description="MatterVis: publication-quality crystal structure visualization.",
    )
    subparsers = parser.add_subparsers(dest="command")

    _build_render_parser(subparsers)
    _build_serve_parser(subparsers)
    _build_tui_parser(subparsers)

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)
    elif args.command == "render":
        _render_main(args)
    elif args.command == "serve":
        _serve_main(args)
    elif args.command == "tui":
        _tui_main(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
