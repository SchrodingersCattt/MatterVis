"""Command-line interface for MatterVis.

Subcommands
-----------
render  Generate publication-quality figures from CIF files.
serve   Launch the interactive Dash browser viewer.

Usage::

    mat-vis render structure.cif -o figure.png
    mat-vis serve --cif structure.cif --port 50001
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from .tui.crystal_ir import filter_crystal as _filter_crystal

from .render.cli import (
    _apply_camera_overrides,  # noqa: F401 - compatibility re-export
    _build_cli_topology_data,  # noqa: F401 - compatibility re-export
    _build_render_parser,
    _parse_polyhedron_specs,  # noqa: F401 - compatibility re-export
    _render_main,
)
from .render.export import (
    plotly_static_export_available as _plotly_static_export_available,  # noqa: F401
)

# ---------------------------------------------------------------------------
# Serve subcommand
# ---------------------------------------------------------------------------


def _build_serve_parser(
    subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "serve",
        help="Launch the interactive Dash browser viewer.",
        description="Start the MatterVis web application for interactive crystal visualization.",
    )
    p.add_argument("--preset", default=None, help="Preset JSON to load and save.")
    p.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0).")
    p.add_argument(
        "--port", type=int, default=50001, help="Port to expose (default: 50001)."
    )
    p.add_argument(
        "--structure", nargs="*", help="Serve only selected catalog structure(s)."
    )
    p.add_argument(
        "--cif",
        action="append",
        default=[],
        help="CIF path to preload. Repeat for multiple files: --cif a.cif --cif b.cif.",
    )
    p.add_argument(
        "--api-only", action="store_true", help="Reserved for automation mode."
    )
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
_TUI_LABELS = ("auto", "element", "label", "molecule", "dot")
_TUI_DISPLAYS = ("auto", "formula_unit", "unit_cell", "asymmetric_unit")


def _build_tui_parser(
    subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
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
    p.add_argument(
        "FILE",
        help=(
            "Atomistic input: CIF, Cube, POSCAR/CONTCAR, VASP, XYZ/extxyz, "
            "ASE .traj, or LAMMPS dump/data."
        ),
    )
    p.add_argument(
        "--input-format",
        default=None,
        metavar="FORMAT",
        help="ASE format name for ambiguous inputs.",
    )
    p.add_argument(
        "--type-map",
        nargs="+",
        default=None,
        metavar="ELEMENT",
        help="LAMMPS type order, for example --type-map Si O.",
    )
    p.add_argument(
        "--frame",
        type=int,
        default=0,
        help="Trajectory frame index (default: 0; negative indices allowed).",
    )
    p.add_argument(
        "--interaction",
        "--interactive",
        action="store_true",
        default=True,
        dest="interaction",
        help="Launch interactive TUI (default).",
    )
    p.add_argument(
        "--no-interaction",
        "--no-interactive",
        action="store_false",
        dest="interaction",
        help="Print static output to stdout (for LLM/script piping).",
    )
    p.add_argument(
        "--mono",
        action="store_true",
        default=False,
        help="Force monochrome output (no ANSI color codes).",
    )
    p.add_argument(
        "--format",
        choices=_TUI_FORMATS,
        default="ascii",
        help="Non-interactive output format (default: ascii).",
    )
    p.add_argument(
        "--compact",
        action="store_true",
        default=False,
        help="Use single-char dot mode instead of element symbols.",
    )
    p.add_argument(
        "--projection",
        choices=_TUI_PROJECTIONS,
        default="orthographic",
        help="Initial projection mode (default: orthographic).",
    )
    p.add_argument(
        "--width",
        type=int,
        default=None,
        help="Override terminal grid width (auto-detect if omitted).",
    )
    p.add_argument(
        "--height",
        type=int,
        default=None,
        help="Override terminal grid height (auto-detect if omitted).",
    )
    p.add_argument(
        "--view",
        choices=_TUI_VIEWS,
        default="auto",
        help="Initial view direction (default: auto → diagonal).",
    )
    p.add_argument(
        "--label",
        choices=_TUI_LABELS,
        default="auto",
        help="Atom label mode (default: auto). 'element'=Fe/O, 'label'=Fe1/O2, "
        "'molecule'=Fe1⁰/O2¹ (with mol index), 'dot'=● colored.",
    )
    p.add_argument(
        "--display",
        choices=_TUI_DISPLAYS,
        default="auto",
        help="Display mode (default: auto, canonical unit_cell).",
    )
    p.add_argument(
        "--show-minor",
        action="store_true",
        default=False,
        help="Show minor disorder atoms (dimmed). Hidden by default.",
    )
    p.add_argument(
        "--hide-partial",
        action="store_true",
        default=False,
        help="Hide partial-occupancy atoms (occ < 1). Shown by default.",
    )
    p.add_argument(
        "--zoom",
        type=float,
        default=1.0,
        help="Viewport zoom factor (>1 to zoom in). Default: 1.0.",
    )
    p.add_argument(
        "--center",
        default=None,
        help="Center view on atom label (e.g. Fe1) or fractional coords (e.g. 0.5,0.5,0.5).",
    )
    p.add_argument(
        "--azimuth",
        type=float,
        default=None,
        help="Camera azimuth angle in degrees (overrides --view).",
    )
    p.add_argument(
        "--elevation",
        type=float,
        default=None,
        help="Camera elevation angle in degrees (overrides --view).",
    )
    p.add_argument(
        "--roll",
        type=float,
        default=None,
        help="Camera roll angle in degrees.",
    )
    p.add_argument(
        "--no-bonds",
        action="store_true",
        default=False,
        help="Hide bonds.",
    )
    p.add_argument(
        "--no-cell",
        action="store_true",
        default=False,
        help="Hide unit cell edges.",
    )
    return p


def _tui_main(args: argparse.Namespace) -> None:
    """Execute the tui subcommand."""
    filepath = args.FILE
    if not Path(filepath).exists():
        print(f"Error: file not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    from .math.camera import Camera, ProjectionMode, project_points
    from .tui.loader_adapter import load_for_tui

    if args.zoom <= 0:
        print("Error: --zoom must be greater than zero.", file=sys.stderr)
        sys.exit(2)
    if args.width is not None and args.width <= 0:
        print("Error: --width must be greater than zero.", file=sys.stderr)
        sys.exit(2)
    if args.height is not None and args.height <= 0:
        print("Error: --height must be greater than zero.", file=sys.stderr)
        sys.exit(2)

    display_mode = args.display
    crystal = load_for_tui(
        filepath,
        display_mode=display_mode,
        input_format=args.input_format,
        type_map=args.type_map,
        frame=args.frame,
    )

    keep_atom_set = {
        index
        for index, atom in enumerate(crystal.atoms)
        if (not args.hide_partial or atom.occupancy >= 0.99)
    }
    if len(keep_atom_set) != crystal.n_atoms:
        crystal = _filter_crystal(crystal, keep_atom_set)

    # Map --compact to label_mode="dot" for backward compat
    label_mode = args.label
    if args.compact and label_mode in ("auto", "label"):
        label_mode = "dot"

    from dataclasses import replace as _replace

    cam = Camera.from_view_name(args.view, crystal)
    cam = _replace(cam, projection=ProjectionMode(args.projection))

    # Apply explicit camera angles (agent-friendly: override --view)
    if args.azimuth is not None:
        cam = _replace(cam, azimuth=args.azimuth)
    if args.elevation is not None:
        cam = _replace(cam, elevation=args.elevation)
    if args.roll is not None:
        cam = _replace(cam, roll=args.roll)

    # Apply --center if specified
    if args.center:
        cam = _apply_center(cam, args.center, crystal)

    # Apply --zoom
    cam = _replace(cam, viewport_zoom=args.zoom)

    if not args.interaction:
        # Static output mode
        pts_2d, depth = project_points(cam, crystal.cart_coords)

        if args.format == "structured":
            from .tui.serializer import serialize_crystal

            output = serialize_crystal(
                crystal,
                cam,
                pts_2d,
                show_minor=args.show_minor,
            )
        else:
            from .tui.compositor import compose_frame

            output = compose_frame(
                crystal,
                cam,
                pts_2d,
                depth,
                width=args.width,
                height=args.height,
                mono=args.mono,
                label_mode=label_mode,
                show_bonds=not args.no_bonds,
                show_cell=not args.no_cell,
                show_minor=args.show_minor,
                zoom=cam.viewport_zoom,
            )
        print(output)
    else:
        # Interactive TUI mode
        from .tui.app import CrystalTUI

        app = CrystalTUI(
            crystal=crystal,
            mono=args.mono,
            initial_view=args.view,
            camera=cam,
            show_bonds=not args.no_bonds,
            show_cell=not args.no_cell,
            label_mode=label_mode,
            show_minor=args.show_minor,
            initial_level=(
                "molecule"
                if args.display == "auto"
                and crystal.species_map
                and crystal.n_atoms > 64
                else "atom"
            ),
        )
        app.run()


def _apply_center(cam, center_str: str, crystal):
    """Shift camera target to center on a label or fractional coord."""
    from dataclasses import replace as _replace

    import numpy as np

    # Try as atom label first
    for atom in crystal.atoms:
        if atom.label == center_str or atom.display_label == center_str:
            return _replace(cam, target=np.array(atom.cart, dtype=float))

    # Try as fractional coords (x,y,z)
    parts = center_str.split(",")
    if len(parts) == 3 and crystal.lattice is not None:
        try:
            frac = np.array([float(p) for p in parts])
            cart = frac @ crystal.lattice.matrix
            return _replace(cam, target=cart)
        except ValueError:
            pass

    print(
        f"Warning: --center '{center_str}' not found, using default.", file=sys.stderr
    )
    return cam


def _apply_display_filter(crystal, mode: str):
    """Filter crystal atoms to a display subset."""
    if mode == "formula_unit":
        # Keep only atoms belonging to one formula unit (one molecule per species)
        if not crystal.species_map:
            return crystal  # No MCK data, show everything

        # Pick the canonical per-formula-unit count for each species.
        keep_mol_indices: set[int] = set()
        for species_id, mol_indices in crystal.species_map.items():
            count = max(int(crystal.per_formula_unit.get(species_id, 1)), 0)
            keep_mol_indices.update(mol_indices[:count])

        if not keep_mol_indices:
            return crystal

        # Filter atoms
        keep_atom_set = {
            i
            for i, atom in enumerate(crystal.atoms)
            if atom.molecule_index in keep_mol_indices
        }
        return _filter_crystal(crystal, keep_atom_set)

    elif mode == "asymmetric_unit":
        # Keep only one symop copy of each label
        seen_labels: set[str] = set()
        keep_atom_set: set[int] = set()
        for i, atom in enumerate(crystal.atoms):
            if atom.label not in seen_labels:
                seen_labels.add(atom.label)
                keep_atom_set.add(i)
        return _filter_crystal(crystal, keep_atom_set)

    return crystal


# ---------------------------------------------------------------------------
# Top-level CLI router
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> None:
    """MatterVis CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="mat-vis",
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
