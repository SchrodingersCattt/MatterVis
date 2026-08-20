"""Command-line interface for MatterVis.

Subcommands
-----------
inspect       Report bounded structure metadata.
capabilities  Resolve optional dependencies and installation hints.
render        Generate publication-quality figures and animations.
serve         Launch the interactive Dash browser viewer.
tui           Launch or print the terminal viewer.

Usage::

    mat-vis inspect structure.cif --json
    mat-vis capabilities --require png --json
    mat-vis render structure.cif -o figure.png --backend cpu --json
    mat-vis serve --cif structure.cif --port 50001

The module itself imports only the standard library and the lightweight
capability registry.  Renderer, Plotly, Web, Cube, animation, and Textual code
is loaded only after its corresponding command is selected.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
import sys
from pathlib import Path
from typing import Optional

from .capabilities import (
    capabilities,
    requirements_for_render,
    requirements_for_tui,
    resolve_requirements,
)


def _build_render_parser(
    subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    """Build the existing render surface plus explicit agent controls."""

    from .render.cli import _build_render_parser as build_legacy_parser

    parser = build_legacy_parser(subparsers)
    parser.epilog = (
        "Examples:\n"
        "  %(prog)s structure.cif -o figure.png --backend cpu\n"
        "  %(prog)s structure.cif -o figure.svg --backend cpu --view unit_cell\n"
        "  %(prog)s structure.cif -o figure.pdf --backend cpu --style ortep "
        "--ortep-mode ortep_hatch\n"
        "  %(prog)s structure.cif -o figure.html --backend plotly\n"
        "  %(prog)s trajectory.extxyz -o movie.gif --backend cpu "
        "--frame-range 0:100 --stride 5\n"
    )
    # ``None`` lets the agent path distinguish a dormant legacy option from an
    # explicit request. CPU output has no axes unless requested by a future
    # representation, and ORTEP mode is resolved only for ORTEP.
    parser.set_defaults(
        show_axes=None,
        ortep_mode=None,
        ortep_probability=None,
        frame=None,
        fps=None,
        stride=None,
        polyhedron_cutoff=None,
        publication_preset=None,
        publication_style=None,
    )
    for action in parser._actions:
        if action.dest == "camera_distance":
            action.help = (
                "Scene-fit multiplier (default: 1.8); this is not a distance in Å."
            )
        elif action.dest == "camera_position":
            action.help = "Explicit absolute Cartesian camera position in Å."
        elif action.dest == "ortep_mode":
            action.choices = ("ortep_solid", "ortep_axes", "ortep_hatch")
        elif action.dest == "polyhedron":
            action.help = (
                "Add a base-renderer polyhedron from JSON. Required: center, "
                "ligand. Optional: id, level, center_kind, cutoff, "
                "hard_cutoff, fallback_max, color, opacity, edge_opacity."
            )
    parser.add_argument(
        "--backend",
        choices=("cpu", "plotly"),
        default="cpu",
        help="Rendering backend (default: cpu; never selected by fallback).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Resolve requirements only; do not load the structure or create output.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Write exactly one JSON result to stdout; diagnostics go to stderr.",
    )
    parser.add_argument(
        "--aromatic-rings",
        choices=("bonds", "circle", "disk"),
        default="bonds",
        help="Aromatic-ring convention (default: ordinary bonds).",
    )
    parser.add_argument(
        "--missing-adp-policy",
        choices=("error", "sphere"),
        default="error",
        help="ORTEP behavior when ADP data is missing (default: error).",
    )
    return parser


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


def _filter_crystal(crystal, keep_atom_set):
    from .tui.crystal_ir import filter_crystal

    return filter_crystal(crystal, keep_atom_set)


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
# Agent-facing commands
# ---------------------------------------------------------------------------


def _build_inspect_parser(
    subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "inspect",
        help="Inspect a structure without rendering it.",
        description="Report bounded structure, source, and chemistry metadata.",
    )
    parser.add_argument("input", metavar="INPUT")
    parser.add_argument("--input-format", default=None, metavar="FORMAT")
    parser.add_argument("--type-map", nargs="+", default=None, metavar="ELEMENT")
    parser.add_argument("--frame", type=int, default=0)
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Write exactly one JSON object to stdout.",
    )
    return parser


def _build_capabilities_parser(
    subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "capabilities",
        help="Report installed rendering capabilities and exact install hints.",
    )
    parser.add_argument(
        "--require",
        action="append",
        nargs="+",
        default=[],
        metavar="FEATURE",
        help="Resolve one or more requirements (repeatable).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Write exactly one JSON object to stdout.",
    )
    return parser


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if hasattr(value, "items"):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "tolist"):
        return value.tolist()
    return repr(value)


def _emit(payload: dict, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(_json_safe(payload), sort_keys=True, allow_nan=False))
        return
    if payload.get("schema") == "mattervis.capabilities/v1":
        for item in payload["capabilities"]:
            marker = "yes" if item["available"] else "no"
            print(f"{item['name']:<14} {marker:<3} {item['description']}")
            if not item["available"]:
                print(f"  install: {item['install']}")
        return
    if payload.get("schema") == "mattervis.requirements/v1":
        print("available" if payload["available"] else "unavailable")
        print(payload["install"])
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def _fail(message: str, *, json_output: bool, code: int = 2) -> None:
    print(f"Error: {message}", file=sys.stderr)
    if json_output:
        _emit(
            {"schema": "mattervis.error/v1", "ok": False, "error": message},
            json_output=True,
        )
    raise SystemExit(code)


def _flatten_requirements(groups: list[list[str]]) -> list[str]:
    return [value for group in groups for value in group]


def _capabilities_main(args: argparse.Namespace) -> None:
    try:
        requested = _flatten_requirements(args.require)
        payload = (
            resolve_requirements(requested).to_dict() if requested else capabilities()
        )
    except ValueError as exc:
        _fail(str(exc), json_output=args.json_output)
    _emit(payload, json_output=args.json_output)


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _record_count(crystal, method: str) -> int | None:
    function = getattr(crystal, method, None)
    if not callable(function):
        return None
    return len(function())


def _inspect_payload(structure) -> dict:
    selected = structure.frames[0]
    bundle = selected.bundle
    analysis = getattr(bundle, "molcrys_analysis", None)
    crystal = getattr(analysis, "crystal", None)
    metadata = bundle.metadata() if hasattr(bundle, "metadata") else {}
    warnings = list(metadata.get("warnings") or [])
    return {
        "schema": "mattervis.inspect/v1",
        "ok": True,
        "source": {
            "path": str(structure.path),
            "sha256": _file_sha256(Path(structure.path)),
            "input_format": structure.input_format,
            "frame": selected.index,
            "total_frames": structure.total_frames,
        },
        "structure": {
            "site_records": _record_count(crystal, "get_site_records")
            if crystal is not None
            else None,
            "bond_records": _record_count(crystal, "get_bond_records")
            if crystal is not None
            else None,
            "parsed_atoms": len(getattr(bundle, "raw_atoms", ()) or ()),
            "displayed_atoms": len(
                (getattr(bundle, "scene", {}) or {}).get("draw_atoms", ()) or ()
            ),
            "fragments": len(getattr(bundle, "fragment_table", ()) or ()),
            "has_disorder": bool(metadata.get("has_minor")),
        },
        "warnings": warnings,
    }


def _inspect_main(args: argparse.Namespace) -> None:
    from .agent import load_structure

    try:
        context = (
            redirect_stdout(sys.stderr)
            if args.json_output
            else redirect_stdout(sys.stdout)
        )
        with context:
            structure = load_structure(
                args.input,
                input_format=args.input_format,
                type_map=args.type_map,
                frame=args.frame,
            )
        payload = _inspect_payload(structure)
    except Exception as exc:
        _fail(str(exc), json_output=args.json_output)
    _emit(payload, json_output=args.json_output)


def _camera_request(args: argparse.Namespace) -> dict:
    return {
        "projection": args.projection,
        "axis": args.camera_axis or "c",
        "view_direction": args.view_direction,
        "position": args.camera_position,
        "up": args.camera_up,
        "fit_multiplier": args.camera_distance,
    }


def _render_requirements(args: argparse.Namespace) -> tuple[str, ...]:
    required = list(requirements_for_render(args.output, args.backend))
    if (
        Path(args.input).suffix.lower() == ".cube"
        or str(args.input_format).lower() == "cube"
    ):
        required.append("cube")
    return tuple(required)


def _is_animation_output(args: argparse.Namespace) -> bool:
    return Path(args.output).suffix.lower() in {".gif", ".mp4"}


def _render_shading(args: argparse.Namespace) -> str:
    return "flat" if args.material == "flat" else "smooth"


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
    if args.show_axes is not None:
        unsupported.append("--show-axes" if args.show_axes else "--no-axes")
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
        if args.stride is not None and args.stride <= 0:
            raise ValueError("--stride must be greater than zero")
        if args.fps is not None and args.fps <= 0.0:
            raise ValueError("--fps must be greater than zero")
    else:
        if args.frame_range is not None:
            raise ValueError("--frame-range is only valid for GIF/MP4 output")
        if args.stride is not None:
            raise ValueError("--stride is only valid for GIF/MP4 output")
        if args.fps is not None:
            raise ValueError("--fps is only valid for GIF/MP4 output")
    if not args.polyhedron:
        if args.polyhedron_site is not None:
            raise ValueError("--polyhedron-site requires --polyhedron")
        if args.polyhedron_cutoff is not None:
            raise ValueError("--polyhedron-cutoff requires --polyhedron")
    else:
        if args.polyhedron_cutoff is not None and args.polyhedron_cutoff <= 0.0:
            raise ValueError("--polyhedron-cutoff must be greater than zero")
        from .agent_topology import parse_polyhedron_specs

        parse_polyhedron_specs(args.polyhedron)
    _render_ortep_mode(args)


def _render_check_payload(args: argparse.Namespace) -> dict:
    _validate_render_options(args)
    resolution = resolve_requirements(_render_requirements(args))
    return {
        "schema": "mattervis.render-check/v1",
        "ok": resolution.available,
        "check_only": True,
        "backend": args.backend,
        "output_format": Path(args.output).suffix.lower().lstrip("."),
        "camera": _camera_request(args),
        "source": {"path": str(Path(args.input).expanduser().resolve())},
        "requirements": resolution.to_dict(),
        "warnings": [],
    }


def _hex_rgba(value: str) -> tuple[float, float, float, float]:
    text = str(value).strip().lstrip("#")
    if len(text) not in {6, 8}:
        raise ValueError("--background must be #RRGGBB or #RRGGBBAA")
    try:
        channels = [
            int(text[index : index + 2], 16) / 255.0 for index in range(0, len(text), 2)
        ]
    except ValueError as exc:
        raise ValueError("--background must be #RRGGBB or #RRGGBBAA") from exc
    if len(channels) == 3:
        channels.append(1.0)
    return tuple(channels)  # type: ignore[return-value]


def _scene_fit(bundle, *, display: str, show_hydrogen: bool):
    import numpy as np

    scene = getattr(bundle, "scene", {}) or {}
    if all(
        hasattr(bundle, name)
        for name in ("raw_atoms", "scene_cache", "M", "cell", "formula_unit_atoms")
    ):
        from .loader.core import build_bundle_scene

        scene = build_bundle_scene(
            bundle,
            display_mode=display,
            show_hydrogen=show_hydrogen,
        )
    bounds = scene.get("bounds") or {}
    center = np.asarray(bounds.get("center", (0.0, 0.0, 0.0)), dtype=float)
    radius = 0.0
    mins = np.asarray(bounds.get("mins", ()), dtype=float)
    maxs = np.asarray(bounds.get("maxs", ()), dtype=float)
    if mins.shape == (3,) and maxs.shape == (3,) and np.all(np.isfinite((mins, maxs))):
        radius = float(np.linalg.norm((maxs - mins) * 0.5))
        if bounds.get("center") is None:
            center = (mins + maxs) * 0.5
    if radius <= 1.0e-9:
        ranges = np.asarray(bounds.get("ranges", ()), dtype=float)
        if ranges.shape == (3,) and np.all(np.isfinite(ranges)):
            radius = float(np.linalg.norm(ranges * 0.5))
    if center.shape != (3,) or not np.all(np.isfinite(center)):
        center = np.zeros(3, dtype=float)
    return scene, center, max(radius, 1.0)


def _camera_spec(structure, args: argparse.Namespace, *, display: str):
    import numpy as np

    from .render.contracts import CameraSpec

    selected = structure.frames[0]
    matrix = np.asarray(getattr(selected.bundle, "M", np.eye(3)), dtype=float)
    _, target, radius = _scene_fit(
        selected.bundle,
        display=display,
        show_hydrogen=args.show_hydrogen,
    )
    aspect = max(float(args.width) / float(args.height), 1.0e-6)
    ortho_scale = radius * 1.15 / min(aspect, 1.0)

    if args.camera_position is not None:
        position = np.asarray(args.camera_position, dtype=float)
        up = np.asarray(args.camera_up or matrix[1], dtype=float)
        absolute_distance = float(np.linalg.norm(position - target))
        near = max(radius * 1.0e-4, absolute_distance - 1.5 * radius)
        far = max(near * 2.0, absolute_distance + 1.5 * radius)
        return CameraSpec(
            position=tuple(position),
            target=tuple(target),
            up=tuple(up),
            projection=args.projection,
            near=near,
            far=far,
            ortho_scale=ortho_scale,
        )

    if args.view_direction is not None:
        direction = np.asarray(args.view_direction, dtype=float)
    else:
        axis = args.camera_axis or "c"
        if axis.endswith("*"):
            reciprocal = np.linalg.inv(matrix).T
            direction = reciprocal[{"a*": 0, "b*": 1, "c*": 2}[axis]]
        else:
            direction = matrix[{"a": 0, "b": 1, "c": 2}[axis]]
    up = np.asarray(args.camera_up or matrix[1], dtype=float)
    if np.linalg.norm(np.cross(direction, up)) < 1e-10:
        up = np.array([0.0, 1.0, 0.0])
        if np.linalg.norm(np.cross(direction, up)) < 1e-10:
            up = np.array([1.0, 0.0, 0.0])
    up /= np.linalg.norm(up)
    multiplier = float(args.camera_distance)
    if not np.isfinite(multiplier) or multiplier <= 0.0:
        raise ValueError("--camera-distance fit multiplier must be finite and positive")
    if args.projection == "perspective":
        half_fov = np.radians(45.0 * 0.5)
        distance = multiplier * radius / np.sin(half_fov)
    else:
        distance = max(multiplier * radius, 1.5 * radius)
    near = max(radius * 1.0e-4, distance - 1.5 * radius)
    far = distance + 1.5 * radius
    return CameraSpec.looking_along(
        direction,
        target=target,
        up=up,
        distance=distance,
        projection=args.projection,
        ortho_scale=ortho_scale,
        near=near,
        far=far,
    )


def _render_result_payload(result, structure, args: argparse.Namespace, camera) -> dict:
    output = Path(args.output).expanduser().resolve()
    result_payload = {
        name: _json_safe(getattr(result, name))
        for name in (
            "schema",
            "backend",
            "format",
            "width",
            "height",
            "plan_sha256",
            "output_sha256",
            "warnings",
            "metadata",
        )
        if hasattr(result, name)
    }
    return {
        "schema": "mattervis.render-result/v1",
        "ok": True,
        "backend": result_payload.get("backend", args.backend),
        "camera": _json_safe(camera),
        "warnings": result_payload.get("warnings", []),
        "install": resolve_requirements(_render_requirements(args)).install_command,
        "output": {
            "path": str(output),
            "sha256": _file_sha256(output),
            "bytes": output.stat().st_size,
            "format": output.suffix.lower().lstrip("."),
        },
        "source": {
            "path": str(structure.path),
            "sha256": _file_sha256(Path(structure.path)),
            "input_format": structure.input_format,
            "frame": structure.frames[0].index,
            "selected_frames": [frame.index for frame in structure.frames],
        },
        "result": result_payload,
    }


def _display_mode(structure, args: argparse.Namespace) -> str:
    if args.view != "auto":
        return args.view
    return "formula_unit" if structure.input_format == "cif" else "unit_cell"


def _animation_indices(args: argparse.Namespace) -> list[int]:
    from .loader.structure_input import count_structure_frames
    from .render.frame_selection import parse_frame_indices

    count = count_structure_frames(
        args.input,
        input_format=args.input_format,
        type_map=args.type_map,
    )
    indices = parse_frame_indices(
        count,
        args.frame_range,
        args.stride if args.stride is not None else 1,
    )
    if len(indices) < 2:
        raise ValueError(
            "GIF/MP4 output requires at least two selected frames; adjust "
            "--frame-range or --stride"
        )
    return indices


def _agent_render_main(args: argparse.Namespace) -> None:
    try:
        check_payload = _render_check_payload(args)
    except (ValueError, OSError) as exc:
        _fail(str(exc), json_output=args.json_output)
    if args.check:
        _emit(check_payload, json_output=args.json_output)
        if not check_payload["ok"]:
            raise SystemExit(2)
        return
    if not check_payload["ok"]:
        requirements = check_payload["requirements"]
        _fail(
            "required capability is unavailable; install with: "
            + requirements["install"],
            json_output=args.json_output,
        )

    from .agent import load_structure, render
    from .render.contracts import RenderSpec, ViewSpec

    try:
        context = (
            redirect_stdout(sys.stderr)
            if args.json_output
            else redirect_stdout(sys.stdout)
        )
        with context:
            animation = _is_animation_output(args)
            if animation:
                structure = load_structure(
                    args.input,
                    input_format=args.input_format,
                    type_map=args.type_map,
                    frame_indices=_animation_indices(args),
                )
            else:
                structure = load_structure(
                    args.input,
                    input_format=args.input_format,
                    type_map=args.type_map,
                    frame=args.frame if args.frame is not None else 0,
                )
            display = _display_mode(structure, args)
            view = ViewSpec(display=display)
            camera = _camera_spec(structure, args, display=display)
            spec = RenderSpec(
                representation=args.style,
                shading=_render_shading(args),
                ortep_mode=_render_ortep_mode(args),
                backend=args.backend,
                width=args.width,
                height=args.height,
                scale=args.scale,
                background=_hex_rgba(args.background),
                atom_scale=args.atom_scale,
                bond_radius=args.bond_radius,
                show_hydrogen=args.show_hydrogen,
                show_cell=args.show_unit_cell,
                show_labels=args.show_labels,
                aromatic_rings=args.aromatic_rings,
                ortep_probability=(
                    args.ortep_probability
                    if args.ortep_probability is not None
                    else 0.5
                ),
                missing_adp_policy=args.missing_adp_policy,
            )
            from .agent_topology import build_topology_data

            topology_data = build_topology_data(
                structure,
                args.polyhedron,
                site_index=args.polyhedron_site,
                cutoff=(
                    args.polyhedron_cutoff
                    if args.polyhedron_cutoff is not None
                    else 10.0
                ),
            )
            result = render(
                structure,
                output=Path(args.output).expanduser().resolve(),
                backend=args.backend,
                view=view,
                camera=camera,
                render_spec=spec,
                topology_data=topology_data,
                fps=args.fps if args.fps is not None else 12.0,
            )
        payload = _render_result_payload(result, structure, args, camera)
    except Exception as exc:
        _fail(str(exc), json_output=args.json_output)
    _emit(payload, json_output=args.json_output)


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
    _build_inspect_parser(subparsers)
    _build_capabilities_parser(subparsers)
    _build_serve_parser(subparsers)
    _build_tui_parser(subparsers)

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)
    elif args.command == "render":
        _agent_render_main(args)
    elif args.command == "inspect":
        _inspect_main(args)
    elif args.command == "capabilities":
        _capabilities_main(args)
    elif args.command == "serve":
        try:
            resolve_requirements("web").require()
        except Exception as exc:
            _fail(str(exc), json_output=False)
        _serve_main(args)
    elif args.command == "tui":
        try:
            resolve_requirements(
                requirements_for_tui(args.FILE, args.input_format)
            ).require()
        except Exception as exc:
            _fail(str(exc), json_output=False)
        _tui_main(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
