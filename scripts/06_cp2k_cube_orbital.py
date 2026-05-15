"""Render a CP2K/Gaussian cube orbital as Plotly isosurfaces.

Run from the repository root:

    python scripts/06_cp2k_cube_orbital.py --cube /path/to/orbital.cube

The HTML output is always written. PNG export is attempted when Kaleido is
available in the local environment.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from crystal_viewer.cube import build_orbital_panel_figure, default_isovalue, read_cube  # noqa: E402


OUTPUT_DIR = Path(__file__).resolve().parent / "_outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cube", required=True, help="Input .cube file")
    parser.add_argument("--output-prefix", default=None, help="Output file stem or path")
    parser.add_argument("--stride", type=int, default=2, help="Grid stride for interactive rendering")
    parser.add_argument("--percentile", type=float, default=98.5, help="Abs(value) percentile for isovalue")
    parser.add_argument("--isovalue", type=float, default=None, help="Explicit isovalue; overrides percentile")
    parser.add_argument("--no-atoms", action="store_true", help="Hide atom overlay")
    parser.add_argument("--no-mesh", action="store_true", help="Use Plotly Isosurface instead of marching-cubes Mesh3d")
    parser.add_argument("--show-bonds", dest="show_bonds", action="store_true", default=True)
    parser.add_argument("--no-bonds", dest="show_bonds", action="store_false")
    parser.add_argument("--show-cell-box", dest="show_cell_box", action="store_true", default=True)
    parser.add_argument("--no-cell", dest="show_cell_box", action="store_false")
    parser.add_argument("--opacity", type=float, default=1.0)
    parser.add_argument("--positive-color", default="#D55E00")
    parser.add_argument("--negative-color", default="#0072B2")
    parser.add_argument("--atom-mask-radius", type=float, default=None)
    parser.add_argument("--min-volume-voxels", type=int, default=0)
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    cube_path = Path(args.cube)
    output_prefix = Path(args.output_prefix) if args.output_prefix else OUTPUT_DIR / cube_path.stem
    if args.output_prefix and len(output_prefix.parts) == 1:
        output_prefix = OUTPUT_DIR / output_prefix

    cube = read_cube(cube_path)
    isovalue = args.isovalue if args.isovalue is not None else default_isovalue(cube.values, args.percentile)
    print(f"resolved isovalue = {isovalue:g} (percentile={args.percentile:g})")
    fig = build_orbital_panel_figure(
        [cube],
        isovalues=[isovalue],
        percentile=args.percentile,
        stride=args.stride,
        show_atoms=not args.no_atoms,
        show_bonds=args.show_bonds,
        show_cell_box=args.show_cell_box,
        use_mesh=not args.no_mesh,
        opacity=args.opacity,
        positive_color=args.positive_color,
        negative_color=args.negative_color,
        atom_mask_radius=args.atom_mask_radius,
        min_volume_voxels=args.min_volume_voxels,
        titles=[cube_path.name],
    )

    html = output_prefix.with_suffix(".html")
    png = output_prefix.with_suffix(".png")
    html.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(html), include_plotlyjs="cdn", full_html=True)
    print(f"Wrote HTML: {html} ({os.path.getsize(html)} bytes)")

    try:
        fig.write_image(str(png), width=900, height=720, scale=2)
    except Exception as exc:  # pragma: no cover - depends on local Kaleido/Chrome
        print(f"PNG export skipped: {exc}")
    else:
        print(f"Wrote PNG : {png} ({os.path.getsize(png)} bytes)")


if __name__ == "__main__":
    main()
