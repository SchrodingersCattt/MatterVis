"""Argument forwarding for the interactive Web application."""

from __future__ import annotations

import argparse


def serve_main(args: argparse.Namespace) -> None:
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
    if args.input is not None:
        argv.extend(["--input", args.input, "--frame", str(args.frame)])
    if args.input_format is not None:
        argv.extend(["--input-format", args.input_format])
    if args.type_map:
        argv.append("--type-map")
        argv.extend(args.type_map)
    if args.property_data is not None:
        argv.extend(["--property-data", str(args.property_data)])
    if args.color_by:
        argv.append("--color-by")
        argv.extend(args.color_by)
        argv.extend(["--color-reduction", args.color_reduction])
        if args.color_component is not None:
            argv.extend(["--color-component", str(args.color_component)])
        argv.extend(["--colormap", args.colormap, "--nan-color", args.nan_color])
        if args.color_range is not None:
            argv.extend(["--color-range", *map(str, args.color_range)])
        if args.color_center is not None:
            argv.extend(["--color-center", str(args.color_center)])
        if args.color_label is not None:
            argv.extend(["--color-label", args.color_label])
        if args.color_unit is not None:
            argv.extend(["--color-unit", args.color_unit])
        if not args.show_colorbar:
            argv.append("--no-colorbar")

    from .factory import main as factory_main

    factory_main(argv)
