"""Implementation of the MatterVis render command."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict

from .frame_selection import parse_frame_indices as _parse_frame_indices

_DISPLAY_MODES = ("auto", "formula_unit", "unit_cell", "asymmetric_unit", "cluster")
_STYLES = ("ball_stick", "ball", "space_filling", "stick", "ortep", "wireframe")
_SHADINGS = ("smooth", "flat")
_ORTEP_MODES = ("ortep_solid", "ortep_axes", "ortep_octant", "ortep_hatch")
_IMAGE_EXTENSIONS = (".png", ".pdf", ".svg")
_ANIMATION_EXTENSIONS = (".gif", ".mp4")
_SUPPORTED_EXTENSIONS = _IMAGE_EXTENSIONS + (".html",) + _ANIMATION_EXTENSIONS
_CAMERA_AXES = ("a", "b", "c", "a*", "b*", "c*")


def _build_render_parser(
    subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "render",
        help="Render an atomistic structure or trajectory.",
        description=(
            "Load an atomistic structure or trajectory and export a static figure "
            "or animation. Output format is inferred from the extension."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s structure.cif -o fig.png\n"
            "  %(prog)s structure.cif -o fig.pdf --view unit_cell --no-hydrogen\n"
            "  %(prog)s structure.cif -o fig.png --style ortep --ortep-mode ortep_hatch --monochrome\n"
            "  %(prog)s structure.cif -o fig.html --orthogonal --atom-scale 1.2\n"
            "  %(prog)s POSCAR -o fig.png --view unit_cell\n"
            "  %(prog)s run.dump --type-map Si O -o run.gif --stride 10\n"
        ),
    )

    # Positional
    p.add_argument(
        "input",
        metavar="INPUT",
        help=(
            "Atomistic input: CIF, Cube, POSCAR/CONTCAR, VASP, XYZ/extxyz, "
            "ASE .traj, or LAMMPS dump/data."
        ),
    )
    p.add_argument(
        "--input-format",
        default=None,
        metavar="FORMAT",
        help="ASE format name for ambiguous inputs (for example lammps-data).",
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
        help="Frame index for a static output (default: 0; negative indices allowed).",
    )
    p.add_argument(
        "--frame-range",
        default=None,
        metavar="START:STOP[:STEP]",
        help="Frame slice for GIF/MP4 output; defaults to all frames.",
    )
    p.add_argument(
        "--stride",
        type=int,
        default=1,
        help="Keep every Nth selected animation frame (default: 1).",
    )
    p.add_argument(
        "--fps",
        type=float,
        default=12.0,
        help="Animation frames per second (default: 12).",
    )

    # Output
    p.add_argument(
        "-o",
        "--output",
        required=True,
        help="Output path: .png, .pdf, .svg, .html, .gif, or .mp4.",
    )

    # Display mode
    p.add_argument(
        "--view",
        choices=_DISPLAY_MODES,
        default="auto",
        help="Display mode (default: CIF formula_unit; other inputs unit_cell).",
    )

    # Rendering style
    p.add_argument(
        "--style",
        choices=_STYLES,
        default="ball_stick",
        help="Atom/bond rendering style (default: ball_stick).",
    )
    p.add_argument(
        "--shading",
        choices=_SHADINGS,
        default="smooth",
        help="Surface shading for mesh geometry (default: smooth).",
    )

    # Projection
    proj = p.add_mutually_exclusive_group()
    proj.add_argument(
        "--orthogonal",
        dest="projection",
        action="store_const",
        const="orthographic",
        help="Use orthographic projection.",
    )
    proj.add_argument(
        "--perspective",
        dest="projection",
        action="store_const",
        const="perspective",
        help="Use perspective projection.",
    )
    p.set_defaults(projection="orthographic")

    # Camera. Explicit Cartesian controls override the default +c alignment.
    camera_direction = p.add_mutually_exclusive_group()
    camera_direction.add_argument(
        "--camera-axis",
        choices=_CAMERA_AXES,
        default=None,
        help="Align the camera with a real or reciprocal lattice axis (default: c).",
    )
    camera_direction.add_argument(
        "--view-direction",
        nargs=3,
        type=float,
        metavar=("X", "Y", "Z"),
        help="Cartesian direction from the scene toward the camera.",
    )
    camera_direction.add_argument(
        "--camera-position",
        nargs=3,
        type=float,
        metavar=("X", "Y", "Z"),
        help="Explicit Plotly camera eye position relative to the scene centre.",
    )
    p.add_argument(
        "--camera-up",
        nargs=3,
        type=float,
        metavar=("X", "Y", "Z"),
        help="Preferred Cartesian screen-up direction.",
    )

    # Boolean display options
    p.add_argument(
        "--show-hydrogen",
        dest="show_hydrogen",
        action="store_true",
        default=False,
        help="Show hydrogen atoms.",
    )
    p.add_argument(
        "--no-hydrogen",
        dest="show_hydrogen",
        action="store_false",
        help="Hide hydrogen atoms (default).",
    )
    p.add_argument(
        "--show-cell",
        dest="show_unit_cell",
        action="store_true",
        default=True,
        help="Show unit cell edges (default).",
    )
    p.add_argument(
        "--no-cell",
        dest="show_unit_cell",
        action="store_false",
        help="Hide unit cell edges.",
    )
    p.add_argument(
        "--show-axes",
        dest="show_axes",
        action="store_true",
        default=True,
        help="Show a camera-projected lattice-axis compass.",
    )
    p.add_argument(
        "--no-axes", dest="show_axes", action="store_false", help="Hide lattice axes."
    )
    p.add_argument(
        "--cell-color",
        default="#333333",
        metavar="COLOR",
        help="Unit-cell edge colour (default: #333333).",
    )
    p.add_argument(
        "--cell-width",
        type=float,
        default=2.0,
        metavar="PX",
        help="Unit-cell edge width in pixels (default: 2.0).",
    )
    p.add_argument(
        "--show-labels",
        dest="show_labels",
        action="store_true",
        default=False,
        help="Show atom labels.",
    )
    p.add_argument(
        "--no-labels",
        dest="show_labels",
        action="store_false",
        help="Hide atom labels (default).",
    )
    p.add_argument(
        "--monochrome", action="store_true", default=False, help="Render in greyscale."
    )

    # Numeric parameters
    p.add_argument(
        "--atom-scale",
        type=float,
        default=1.0,
        help="Atom radius scale factor (default: 1.0).",
    )
    p.add_argument(
        "--bond-radius",
        type=float,
        default=0.15,
        help="Bond cylinder radius in Å (default: 0.15).",
    )
    p.add_argument(
        "--camera-distance",
        type=float,
        default=1.8,
        help="Camera eye distance (default: 1.8).",
    )

    # Colors
    p.add_argument(
        "--background",
        default="#FFFFFF",
        help="Background hex colour (default: #FFFFFF).",
    )

    # Image dimensions
    p.add_argument(
        "--width", type=int, default=900, help="Image width in pixels (default: 900)."
    )
    p.add_argument(
        "--height", type=int, default=720, help="Image height in pixels (default: 720)."
    )
    p.add_argument(
        "--scale",
        type=int,
        default=2,
        help="Image scale factor / supersampling (default: 2).",
    )

    # ORTEP
    p.add_argument(
        "--ortep-probability",
        type=float,
        default=0.5,
        help="ORTEP ellipsoid probability (0.0–1.0, default: 0.5).",
    )
    p.add_argument(
        "--ortep-mode",
        choices=_ORTEP_MODES,
        default="ortep_axes",
        help="ORTEP rendering variant (default: ortep_axes).",
    )

    # Config escape-hatch
    p.add_argument(
        "--config",
        metavar="JSON",
        help="Path to a JSON file with full style overrides. CLI flags take precedence over config values.",
    )

    # View-direction scoring weights
    p.add_argument(
        "--view-weights",
        metavar="JSON",
        help=(
            "JSON dict of auto-view scoring weight overrides. "
            "Keys: organic_plane, organic_depth, aspect, robust_sep, "
            "close_contact, occlusion, cluster_crowding, elev_pen. "
            'Example: \'{"occlusion": 3.0, "elev_pen": 0.5}\''
        ),
    )

    p.add_argument(
        "--polyhedron",
        action="append",
        default=[],
        metavar="JSON",
        help=(
            "Add a polyhedron overlay from a JSON object. Repeat for multiple overlays. "
            "Required keys: center, ligand. Optional: level=atom|molecule, "
            "center_kind, cutoff, hard_cutoff, fallback_max, color, opacity, "
            "edge_opacity, edge_width, flatshading, name."
        ),
    )
    p.add_argument(
        "--polyhedron-site",
        type=int,
        default=None,
        metavar="INDEX",
        help="Displayed fragment index used as the primary polyhedron analysis anchor.",
    )
    p.add_argument(
        "--polyhedron-cutoff",
        type=float,
        default=10.0,
        metavar="ANGSTROM",
        help="Default polyhedron search cutoff in Å (default: 10.0).",
    )
    p.add_argument(
        "--publication-layout",
        action="store_true",
        default=False,
        help="Compose a main structure view with isolated representative polyhedron panels.",
    )
    p.add_argument(
        "--publication-preset",
        choices=("dense_coordination",),
        default="dense_coordination",
        help="Built-in publication layout selected entirely from the CLI.",
    )
    p.add_argument(
        "--publication-style",
        choices=("blender",),
        default="blender",
        help="Built-in publication material and lighting style (default: blender).",
    )
    p.add_argument(
        "--publication-option",
        action="append",
        default=[],
        metavar="PATH=VALUE",
        help=(
            "Override the selected publication preset or style without a config file. "
            "Repeat as needed, for example materials.8.main.alpha=0.40. Values accept JSON "
            "scalars or arrays; other text is kept as a string."
        ),
    )
    p.add_argument(
        "--publication-site-style",
        action="append",
        nargs=5,
        default=[],
        metavar=("ELEMENTS", "COLORS", "WEIGHTS", "LABEL", "RADIUS"),
        help=(
            "Add a mixed or single site style. ELEMENTS, COLORS, and WEIGHTS are "
            "comma-separated; repeat for multiple crystallographic sites."
        ),
    )
    p.add_argument(
        "--publication-legend-entry",
        action="append",
        nargs=2,
        default=[],
        metavar=("COLORS", "LABEL"),
        help="Add a legend row from comma-separated COLORS and a LABEL.",
    )
    p.add_argument(
        "--publication-panel-label",
        action="append",
        nargs=2,
        default=[],
        metavar=("SPEC_ID", "LABEL"),
        help="Set the representative-panel label for one polyhedron SPEC_ID.",
    )
    p.add_argument(
        "--publication-legend-footer",
        default=None,
        metavar="TEXT",
        help="Set the publication legend footer.",
    )
    p.add_argument(
        "--title",
        default=None,
        metavar="TEXT",
        help="Title for --publication-layout (default: structure title).",
    )
    p.add_argument(
        "--subtitle",
        default=None,
        metavar="TEXT",
        help="Optional subtitle for --publication-layout.",
    )

    return p


def _parse_polyhedron_specs(raw_specs: list[str]) -> list[dict[str, Any]]:
    """Compatibility delegate to the backend-neutral strict parser."""
    from ..agent_topology import parse_polyhedron_specs

    return parse_polyhedron_specs(raw_specs)


def _parse_publication_options(
    preset: str,
    raw_options: list[str],
    *,
    publication_style: str = "blender",
    site_styles: list[list[str]] | None = None,
    legend_entries: list[list[str]] | None = None,
    panel_labels: list[list[str]] | None = None,
    legend_footer: str | None = None,
) -> dict[str, Any]:
    publication: dict[str, Any] = {
        "preset": preset,
        "style": publication_style,
    }
    for raw in raw_options:
        if "=" not in raw:
            raise ValueError(f"publication option {raw!r} must use PATH=VALUE syntax")
        raw_path, raw_value = raw.split("=", 1)
        keys = raw_path.split(".")
        if not raw_path or any(not key for key in keys):
            raise ValueError(f"invalid publication option path: {raw_path!r}")
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            value = raw_value

        target = publication
        for key in keys[:-1]:
            current = target.get(key)
            if current is None:
                current = {}
                target[key] = current
            if not isinstance(current, dict):
                raise ValueError(
                    f"publication option path collides at {key!r}: {raw_path!r}"
                )
            target = current
        target[keys[-1]] = value

    parsed_site_styles: list[dict[str, Any]] = []
    for elements, colors, weights, label, radius in site_styles or []:
        element_values = [value for value in elements.split(",") if value]
        color_values = [value for value in colors.split(",") if value]
        try:
            weight_values = [float(value) for value in weights.split(",") if value]
            radius_value = float(radius)
        except ValueError as exc:
            raise ValueError("site-style weights and radius must be numeric") from exc
        if not element_values or len(element_values) != len(color_values):
            raise ValueError("site-style ELEMENTS and COLORS must have equal lengths")
        if len(weight_values) != len(element_values):
            raise ValueError("site-style WEIGHTS must match ELEMENTS")
        parsed_site_styles.append(
            {
                "elements": element_values,
                "colors": color_values,
                "weights": weight_values,
                "label": label,
                "radius": radius_value,
            }
        )
    if parsed_site_styles:
        publication["site_styles"] = parsed_site_styles

    parsed_legend_entries: list[dict[str, Any]] = []
    for colors, label in legend_entries or []:
        color_values = [value for value in colors.split(",") if value]
        if not color_values:
            raise ValueError("legend-entry COLORS must not be empty")
        parsed_legend_entries.append({"colors": color_values, "label": label})
    if parsed_legend_entries or legend_footer is not None:
        publication["legend"] = {}
        if parsed_legend_entries:
            publication["legend"]["entries"] = parsed_legend_entries
        if legend_footer is not None:
            publication["legend"]["footer"] = legend_footer

    if panel_labels:
        publication["specs"] = {
            spec_id: {"panel_label": label} for spec_id, label in panel_labels
        }
    return {"publication": publication}


def _build_cli_topology_data(
    bundle, scene: dict, args: argparse.Namespace
) -> dict[str, Any] | None:
    from types import SimpleNamespace

    from ..agent_topology import build_topology_data

    class _SceneBundleProxy:
        def __init__(self, base, active_scene: dict) -> None:
            self._base = base
            self.scene = active_scene

        def __getattr__(self, name: str):
            return getattr(self._base, name)

    structure = SimpleNamespace(
        frames=(SimpleNamespace(bundle=_SceneBundleProxy(bundle, scene)),)
    )
    return build_topology_data(
        structure,
        args.polyhedron,
        site_index=args.polyhedron_site,
        cutoff=float(args.polyhedron_cutoff),
    )


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

    # CLI publication arguments override config values without requiring a file.
    try:
        publication_override = _parse_publication_options(
            args.publication_preset,
            args.publication_option,
            publication_style=args.publication_style,
            site_styles=args.publication_site_style,
            legend_entries=args.publication_legend_entry,
            panel_labels=args.publication_panel_label,
            legend_footer=args.publication_legend_footer,
        )
    except ValueError as exc:
        sys.exit(f"Error: invalid publication parameters: {exc}")
    existing_publication = overrides.get("publication")
    if isinstance(existing_publication, dict):
        existing_publication.update(publication_override["publication"])
    else:
        overrides.update(publication_override)

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


def _apply_camera_overrides(
    scene: Dict[str, Any],
    overrides: Dict[str, Any],
    args: argparse.Namespace,
) -> None:
    """Apply deterministic CLI camera controls to Plotly and flat renderers."""
    import numpy as np

    from ..math.rotation import axis_camera_basis, normalize_vector, orthogonalise_up

    up_hint = np.asarray(args.camera_up or [0.0, 1.0, 0.0], dtype=float)
    if args.camera_position is not None:
        eye = np.asarray(args.camera_position, dtype=float)
        view_direction = normalize_vector(eye, name="camera position")
        up = orthogonalise_up(view_direction, up_hint)
    elif args.view_direction is not None:
        view_direction = normalize_vector(args.view_direction, name="view direction")
        up = orthogonalise_up(view_direction, up_hint)
        eye = view_direction * float(args.camera_distance)
    else:
        axis = args.camera_axis or "c"
        matrix = np.asarray(scene.get("M"), dtype=float)
        if matrix.shape == (3, 3) and np.all(np.isfinite(matrix)):
            basis = axis_camera_basis(matrix, axis)
            view_direction = basis[2]
            up = (
                basis[1]
                if args.camera_up is None
                else orthogonalise_up(view_direction, up_hint)
            )
        elif axis == "c":
            view_direction = np.array([0.0, 0.0, 1.0], dtype=float)
            up = orthogonalise_up(view_direction, up_hint)
        else:
            raise ValueError(f"camera axis {axis!r} requires a valid lattice matrix")
        eye = view_direction * float(args.camera_distance)

    scene["view_direction"] = np.asarray(view_direction, dtype=float)
    scene["up"] = np.asarray(up, dtype=float)
    overrides["camera"] = {
        "eye": {axis: float(eye[index]) for index, axis in enumerate("xyz")},
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "up": {axis: float(up[index]) for index, axis in enumerate("xyz")},
        "projection": {"type": args.projection},
    }


def _prepare_frame(bundle, args: argparse.Namespace, overrides: dict[str, Any]):
    from ..loader import build_bundle_scene
    from ..scene import scene_style

    source_metadata = dict(getattr(bundle, "scene", {}) or {})
    include_boundary_replicas = any(source_metadata.get("pbc", [True, True, True]))
    scene = build_bundle_scene(
        bundle,
        display_mode=args.view,
        show_hydrogen=args.show_hydrogen,
        include_boundary_replicas=include_boundary_replicas,
    )
    _apply_camera_overrides(scene, overrides, args)
    style = scene_style(scene, overrides)
    topology_data = _build_cli_topology_data(bundle, scene, args)
    if topology_data is not None:
        style["topology_enabled"] = True
    return scene, style, topology_data


def _save_static_output(
    bundle,
    scene: dict[str, Any],
    style: dict[str, Any],
    topology_data: dict[str, Any] | None,
    args: argparse.Namespace,
    output_path: Path,
    *,
    allow_style_fallback: bool = False,
) -> dict[str, Any]:
    from ..renderer import (
        build_figure,
        build_publication_figure,
        build_static_publication_figure,
        render,
    )
    from .export import plotly_static_export_available

    if allow_style_fallback:
        raise ValueError(
            "visual style fallback has been removed; request an explicit backend "
            "and representation"
        )

    ext = output_path.suffix.lower()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if ext == ".html":
        if args.publication_layout:
            fig = build_publication_figure(
                scene,
                style,
                topology_data,
                title=args.title,
                subtitle=args.subtitle,
                width=args.width,
                height=args.height,
            )
        else:
            fig = build_figure(scene, style, topology_data=topology_data)
        fig.write_html(str(output_path), include_plotlyjs="cdn", full_html=True)
        return {"backend": "plotly-html", "fallback_reason": None}

    result = render(scene, style) if topology_data is None else None
    if args.publication_layout:
        from .api import FigureResult

        result = FigureResult(
            mpl_fig=build_static_publication_figure(
                scene,
                style,
                topology_data,
                title=args.title,
                subtitle=args.subtitle,
                width=args.width,
                height=args.height,
            ),
            mpl_save_kwargs={"bbox_inches": None},
        )
    elif topology_data is not None:
        from .api import FigureResult

        result = FigureResult(
            plotly_fig=build_figure(scene, style, topology_data=topology_data)
        )

    export_available, unavailable_reason = plotly_static_export_available()
    if result.plotly_figure is not None and not export_available:
        raise RuntimeError(
            "Plotly/Kaleido static export is unavailable and no fallback is "
            f"permitted ({unavailable_reason})"
        )

    try:
        result.save(
            str(output_path),
            width=args.width,
            height=args.height,
            scale=args.scale,
        )
        return {
            "backend": (
                "plotly-kaleido"
                if result.plotly_figure is not None
                else "matplotlib-flat-ortep"
            ),
            "fallback_reason": None,
        }
    except Exception as exc:
        if result.plotly_figure is not None:
            raise RuntimeError(
                "Plotly/Kaleido static export failed and no fallback is permitted "
                f"({type(exc).__name__}: {exc})"
            ) from exc
        raise


def _render_main(args: argparse.Namespace) -> None:
    """Execute the render subcommand through the shared structure IO."""
    input_path = Path(args.input).resolve()
    if not input_path.exists():
        sys.exit(f"Error: structure file not found: {args.input}")

    output_path = Path(args.output).resolve()
    ext = output_path.suffix.lower()
    if ext not in _SUPPORTED_EXTENSIONS:
        sys.exit(
            f"Error: unsupported output format {ext!r}. "
            f"Supported: {', '.join(_SUPPORTED_EXTENSIONS)}"
        )
    if not math.isfinite(args.camera_distance) or args.camera_distance <= 0:
        sys.exit("Error: --camera-distance must be finite and greater than zero.")
    if not math.isfinite(args.fps) or args.fps <= 0:
        sys.exit("Error: --fps must be finite and greater than zero.")

    from ..loader import count_structure_frames, load_structure_input
    from ..renderer import uniform_viewport

    print(f"Loading {input_path.name} ...")
    if ext in _ANIMATION_EXTENSIONS:
        if args.publication_layout:
            sys.exit("Error: publication layouts cannot be exported as animations.")
        try:
            frame_count = count_structure_frames(
                input_path,
                input_format=args.input_format,
                type_map=args.type_map,
            )
            indices = _parse_frame_indices(
                frame_count,
                args.frame_range,
                args.stride,
            )
        except (FileNotFoundError, ValueError) as exc:
            sys.exit(f"Error: {exc}")
        if len(indices) < 2:
            sys.exit("Error: animation output requires at least two selected frames.")
    else:
        if args.frame_range is not None:
            sys.exit("Error: --frame-range is only valid for GIF/MP4 output.")
        indices = [args.frame]

    if ext in _ANIMATION_EXTENSIONS:
        structure = None
        input_format = args.input_format or (
            "cif" if input_path.suffix.lower() == ".cif" else "ase-auto"
        )
    else:
        try:
            structure = load_structure_input(
                input_path,
                input_format=args.input_format,
                type_map=args.type_map,
                frame_indices=indices,
            )
        except (FileNotFoundError, ValueError) as exc:
            sys.exit(f"Error: {exc}")
        input_format = structure.input_format

    args.view = (
        "formula_unit"
        if args.view == "auto" and input_format == "cif"
        else "unit_cell"
        if args.view == "auto"
        else args.view
    )
    overrides = _build_style_overrides(args)

    if ext in _ANIMATION_EXTENSIONS:
        from .animation import save_streaming_from_cli

        print(
            f"Building streaming animation ({len(indices)} frames, "
            f"{args.style}, {args.view}) ..."
        )
        try:
            export_info = save_streaming_from_cli(
                input_path,
                indices,
                args,
                overrides,
                output_path,
            )
        except Exception as exc:
            sys.exit(f"Error: animation export failed ({type(exc).__name__}: {exc}).")
        size = os.path.getsize(output_path)
        print(f"Wrote {ext.lstrip('.')} : {output_path}  ({size:,} bytes)")
        print(f"Effective backend: {export_info['backend']}")
        if export_info.get("fallback_reason"):
            print(f"Fallback reason: {export_info['fallback_reason']}", file=sys.stderr)
        return

    prepared = []
    for selected in structure.frames:
        frame_overrides = dict(overrides)
        try:
            scene, style, topology_data = _prepare_frame(
                selected.bundle,
                args,
                frame_overrides,
            )
        except ValueError as exc:
            sys.exit(f"Error: invalid render parameters: {exc}")
        prepared.append((selected.bundle, scene, style, topology_data))

    if args.publication_layout and prepared[0][3] is None:
        sys.exit(
            "Error: --publication-layout requires at least one --polyhedron specification."
        )

    if len(prepared) > 1:
        from .animation import save_prepared_from_cli

        uniform_viewport(
            [scene for _, scene, _, _ in prepared],
            style=prepared[0][2],
            shared_center=True,
        )
        print(
            f"Building animation ({len(prepared)} frames, "
            f"{args.style}, {args.view}) ..."
        )
        try:
            export_info = save_prepared_from_cli(prepared, args, output_path)
        except Exception as exc:
            sys.exit(f"Error: animation export failed ({type(exc).__name__}: {exc}).")
    else:
        bundle, scene, style, topology_data = prepared[0]
        print(f"Building figure ({args.style}, {args.view}) ...")
        try:
            export_info = _save_static_output(
                bundle,
                scene,
                style,
                topology_data,
                args,
                output_path,
            )
        except Exception as exc:
            sys.exit(f"Error: static export failed ({type(exc).__name__}: {exc}).")

    size = os.path.getsize(output_path)
    print(f"Wrote {ext.lstrip('.')} : {output_path}  ({size:,} bytes)")
    print(f"Effective backend: {export_info['backend']}")
    if export_info.get("fallback_reason"):
        print(f"Fallback reason: {export_info['fallback_reason']}", file=sys.stderr)
