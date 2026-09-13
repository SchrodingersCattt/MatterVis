"""Command-line options for continuous per-atom property colouring."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import AtomPropertyColorSpec, build_color_lut


def add_atom_property_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--property-data",
        type=Path,
        default=None,
        metavar="MANIFEST",
        help="JSON + mmap NPY atom-property sidecar manifest.",
    )
    parser.add_argument(
        "--color-by",
        nargs="+",
        default=None,
        metavar="FIELD",
        help="Per-atom field(s), qualified as array:, column:, or sidecar:.",
    )
    parser.add_argument(
        "--color-reduction",
        choices=(
            "auto",
            "scalar",
            "magnitude",
            "component",
            "trace",
            "mean_normal",
            "von_mises",
        ),
        default="auto",
    )
    parser.add_argument("--color-component", default=None, metavar="NAME_OR_INDEX")
    parser.add_argument("--colormap", default="viridis", metavar="NAME")
    parser.add_argument(
        "--color-range", nargs=2, type=float, default=None, metavar=("MIN", "MAX")
    )
    parser.add_argument("--color-center", type=float, default=None, metavar="VALUE")
    parser.add_argument("--nan-color", default="#BDBDBD", metavar="COLOR")
    parser.add_argument("--color-label", default=None, metavar="TEXT")
    parser.add_argument("--color-unit", default=None, metavar="UNIT")
    parser.add_argument(
        "--no-colorbar", action="store_false", dest="show_colorbar", default=True
    )


def atom_property_spec(args: argparse.Namespace) -> AtomPropertyColorSpec | None:
    if not getattr(args, "color_by", None):
        return None
    component = getattr(args, "color_component", None)
    if component is not None:
        try:
            component = int(component)
        except ValueError:
            pass
    spec = AtomPropertyColorSpec(
        fields=tuple(args.color_by),
        reduction=args.color_reduction,
        component=component,
        colormap=args.colormap,
        value_range=None if args.color_range is None else tuple(args.color_range),
        center=args.color_center,
        nan_color=args.nan_color,
        show_colorbar=bool(args.show_colorbar),
        label=args.color_label,
        unit=args.color_unit,
    )
    build_color_lut(spec.colormap)
    return spec
