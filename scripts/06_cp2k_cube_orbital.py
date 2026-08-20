"""Render a Cube file through MatterVis's canonical structure pipeline.

Examples from the repository root:

    python scripts/06_cp2k_cube_orbital.py density.cube -o density.png
    python scripts/06_cp2k_cube_orbital.py density.cube -o density.html --backend plotly

Cube mesh extraction requires ``matter-vis[cube]``. The Plotly example also
requires ``matter-vis[plotly]``; no backend or output fallback is attempted.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from mat_viewer.agent import load_structure, render


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cube", help="Input .cube file")
    parser.add_argument("-o", "--output", required=True, help="Output path")
    parser.add_argument(
        "--backend",
        choices=("cpu", "plotly"),
        default="cpu",
        help="Explicit renderer backend (default: cpu)",
    )
    parser.add_argument("--width", type=int, default=900)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--scale", type=int, default=2)
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    source = load_structure(Path(args.cube))
    result = render(
        source,
        output=Path(args.output),
        backend=args.backend,
        render_spec={
            "backend": args.backend,
            "width": args.width,
            "height": args.height,
            "scale": args.scale,
        },
    )
    print(f"Wrote {result.output} ({result.output_sha256})")


if __name__ == "__main__":
    main()
