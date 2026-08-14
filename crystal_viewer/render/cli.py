"""Implementation of the MatterVis render command."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict

_DISPLAY_MODES = ("formula_unit", "unit_cell", "asymmetric_unit", "cluster")
_STYLES = ("ball_stick", "ball", "stick", "ortep", "wireframe")
_MATERIALS = ("mesh", "flat")
_ORTEP_MODES = ("ortep_solid", "ortep_axes", "ortep_octant", "ortep_hatch")
_IMAGE_EXTENSIONS = (".png", ".pdf", ".svg")
_SUPPORTED_EXTENSIONS = _IMAGE_EXTENSIONS + (".html",)
_CAMERA_AXES = ("a", "b", "c", "a*", "b*", "c*")


def _build_render_parser(
    subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
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
        "-o",
        "--output",
        required=True,
        help="Output file path. Format inferred from extension: .png, .pdf, .svg, .html.",
    )

    # Display mode
    p.add_argument(
        "--view",
        choices=_DISPLAY_MODES,
        default="formula_unit",
        help="Display mode (default: formula_unit).",
    )

    # Rendering style
    p.add_argument(
        "--style",
        choices=_STYLES,
        default="ball_stick",
        help="Atom/bond rendering style (default: ball_stick).",
    )
    p.add_argument(
        "--material",
        choices=_MATERIALS,
        default="mesh",
        help="Surface material (default: mesh).",
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
        help="Show lattice axes (default).",
    )
    p.add_argument(
        "--no-axes", dest="show_axes", action="store_false", help="Hide lattice axes."
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
        help="Built-in publication style selected entirely from the CLI.",
    )
    p.add_argument(
        "--publication-option",
        action="append",
        default=[],
        metavar="PATH=VALUE",
        help=(
            "Override any publication-style field without a config file. Repeat as "
            "needed, for example materials.8.main.alpha=0.34. Values accept JSON "
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
    from ..app.normalizers import _normalize_polyhedron_spec

    specs: list[dict[str, Any]] = []
    existing_ids: set[str] = set()
    for index, raw in enumerate(raw_specs):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"polyhedron {index + 1}: invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"polyhedron {index + 1}: expected a JSON object")
        normalized_payload = dict(payload)
        if (
            "center_species" not in normalized_payload
            and "center" in normalized_payload
        ):
            normalized_payload["center_species"] = normalized_payload.pop("center")
        if (
            "ligand_species" not in normalized_payload
            and "ligand" in normalized_payload
        ):
            normalized_payload["ligand_species"] = normalized_payload.pop("ligand")
        spec = _normalize_polyhedron_spec(
            normalized_payload,
            fallback_color="#7c5cbf",
            existing_ids=existing_ids,
        )
        if spec is None:
            raise ValueError(f"polyhedron {index + 1}: center and ligand are required")
        if not spec.get("ligand_species"):
            raise ValueError(f"polyhedron {index + 1}: ligand is required")
        specs.append(spec)
    return specs


def _parse_publication_options(
    preset: str,
    raw_options: list[str],
    *,
    site_styles: list[list[str]] | None = None,
    legend_entries: list[list[str]] | None = None,
    panel_labels: list[list[str]] | None = None,
    legend_footer: str | None = None,
) -> dict[str, Any]:
    publication: dict[str, Any] = {"preset": preset}
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
    if not args.polyhedron:
        return None
    from ..app.backend_topology import compute_topology_geometry

    specs = _parse_polyhedron_specs(args.polyhedron)
    fragments = scene.get("fragment_table") or []
    if not fragments:
        raise ValueError("polyhedra require a scene with at least one fragment")
    site_index = args.polyhedron_site
    if site_index is None:
        site_index = next(
            (
                int(fragment["index"])
                for spec in specs
                for fragment in fragments
                if (
                    str(spec.get("center_species"))
                    in {str(elem) for elem in (fragment.get("elem_set") or [])}
                    if spec.get("level") == "atom"
                    else (fragment.get("formula") or fragment.get("species"))
                    == spec.get("center_species")
                )
            ),
            int(fragments[0]["index"]),
        )
    topology_data = compute_topology_geometry(
        bundle=bundle,
        scene=scene,
        effective_specs=specs,
        site_index=int(site_index),
        cutoff=float(args.polyhedron_cutoff),
    )
    if topology_data is None:
        raise ValueError(f"no topology fragment found for site {site_index}")
    if not any(
        overlay.get("hull", {}).get("simplices")
        for result in topology_data.get("spec_results") or []
        for overlay in result.get("overlays") or []
    ):
        details = "; ".join(topology_data.get("warnings") or [])
        raise ValueError(
            f"no drawable polyhedron found{': ' + details if details else ''}"
        )
    paint_by_id = {
        str(spec["id"]): {
            "color": str(spec.get("color") or "#7c5cbf"),
            "opacity": float(spec.get("opacity", 0.55)),
            "edge_opacity": float(spec.get("edge_opacity", 0.90)),
            "edge_width": float(spec.get("edge_width", 3.0)),
            "flatshading": bool(spec.get("flatshading", True)),
        }
        for spec in specs
    }
    topology_data = dict(topology_data)
    topology_data["spec_results"] = [
        {
            **result,
            **paint_by_id.get(
                str(result.get("spec_id")),
                {
                    "color": "#7c5cbf",
                    "opacity": 0.55,
                    "edge_opacity": 0.90,
                    "edge_width": 3.0,
                    "flatshading": True,
                },
            ),
        }
        for result in topology_data.get("spec_results") or []
    ]
    return topology_data


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
    if not math.isfinite(args.camera_distance) or args.camera_distance <= 0:
        sys.exit("Error: --camera-distance must be finite and greater than zero.")

    # Lazy imports to keep CLI startup fast when just showing --help
    from ..loader import build_bundle_scene, build_loaded_crystal
    from ..renderer import (
        build_figure,
        build_publication_figure,
        build_static_publication_figure,
        render,
    )
    from ..scene import scene_style
    from .export import (
        plotly_static_export_available,
        save_flat_ortep_fallback,
    )

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
    try:
        _apply_camera_overrides(scene, overrides, args)
    except ValueError as exc:
        sys.exit(f"Error: invalid camera parameters: {exc}")
    style = scene_style(scene, overrides)

    try:
        topology_data = _build_cli_topology_data(bundle, scene, args)
    except ValueError as exc:
        sys.exit(f"Error: invalid polyhedron parameters: {exc}")
    if topology_data is not None:
        style["topology_enabled"] = True
    if args.publication_layout and topology_data is None:
        sys.exit(
            "Error: --publication-layout requires at least one --polyhedron specification."
        )

    print(f"Building figure ({args.style}, {args.view}) ...")

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if ext == ".html":
        # HTML is always the interactive Plotly path, including flat ORTEP.
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
    else:
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
            if args.publication_layout:
                sys.exit(
                    "Error: publication layout requires Plotly/Kaleido static export "
                    f"({unavailable_reason})."
                )
            print(
                "Plotly/Kaleido static export is unavailable "
                f"({unavailable_reason}).",
                file=sys.stderr,
            )
            print(
                "Falling back to Matplotlib flat ORTEP output; "
                "the visual style differs from the requested Plotly rendering.",
                file=sys.stderr,
            )
            save_flat_ortep_fallback(
                scene,
                overrides,
                output_path,
                width=args.width,
                height=args.height,
                scale=args.scale,
            )
        else:
            try:
                result.save(
                    str(output_path),
                    width=args.width,
                    height=args.height,
                    scale=args.scale,
                )
            except Exception as exc:
                if result.plotly_figure is None:
                    raise
                if args.publication_layout:
                    sys.exit(
                        "Error: publication layout static export failed "
                        f"({type(exc).__name__}: {exc})."
                    )
                print(
                    "Plotly/Kaleido static export is unavailable "
                    f"({type(exc).__name__}: {exc}).",
                    file=sys.stderr,
                )
                print(
                    "Falling back to Matplotlib flat ORTEP output; "
                    "the visual style differs from the requested Plotly rendering.",
                    file=sys.stderr,
                )
                save_flat_ortep_fallback(
                    scene,
                    overrides,
                    output_path,
                    width=args.width,
                    height=args.height,
                    scale=args.scale,
                )

    size = os.path.getsize(output_path)
    print(f"Wrote {ext.lstrip('.')} : {output_path}  ({size:,} bytes)")
