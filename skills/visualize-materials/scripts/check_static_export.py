#!/usr/bin/env python3
"""Probe Plotly/Kaleido at production-like dimensions and reject blank PNGs."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


def analyze_png(
    path: Path,
    *,
    background: tuple[int, int, int] = (255, 255, 255),
    tolerance: int = 8,
) -> dict[str, Any]:
    with Image.open(path) as source:
        image = source.convert("RGBA")
        canvas = Image.new("RGBA", image.size, (*background, 255))
        rgb = np.asarray(Image.alpha_composite(canvas, image).convert("RGB"))
    delta = np.max(np.abs(rgb.astype(np.int16) - np.asarray(background)), axis=2)
    foreground = delta > tolerance
    count = int(foreground.sum())
    total = int(foreground.size)
    minimum = max(64, int(total * 1e-6))
    if count:
        ys, xs = np.where(foreground)
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
    else:
        bbox = None
    return {
        "format": "PNG",
        "width": int(rgb.shape[1]),
        "height": int(rgb.shape[0]),
        "foreground_pixels": count,
        "foreground_fraction": count / total,
        "foreground_bbox": bbox,
        "blank": count < minimum,
    }


def _probe_figure():
    import plotly.graph_objects as go

    figure = go.Figure()
    vertices = np.asarray(
        [
            [-0.45, -0.45, -0.45],
            [0.45, -0.45, -0.45],
            [0.45, 0.45, -0.45],
            [-0.45, 0.45, -0.45],
            [-0.45, -0.45, 0.45],
            [0.45, -0.45, 0.45],
            [0.45, 0.45, 0.45],
            [-0.45, 0.45, 0.45],
        ],
        dtype=float,
    )
    triangles = (
        [0, 0, 4, 4, 0, 0, 1, 1, 2, 2, 3, 3],
        [1, 2, 5, 6, 1, 5, 2, 6, 3, 7, 0, 4],
        [2, 3, 6, 7, 5, 4, 6, 5, 7, 6, 4, 7],
    )
    colors = ("#4CB17A", "#8F50C2", "#3D90CE")
    for index in range(36):
        shift = np.asarray(
            [
                (index % 6) - 2.5,
                ((index // 6) % 3) - 1.0,
                (index // 18) - 0.5,
            ]
        )
        points = vertices + shift
        figure.add_trace(
            go.Mesh3d(
                x=points[:, 0],
                y=points[:, 1],
                z=points[:, 2],
                i=triangles[0],
                j=triangles[1],
                k=triangles[2],
                color=colors[index % len(colors)],
                opacity=0.55,
                flatshading=True,
                showscale=False,
            )
        )
    figure.update_layout(
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        showlegend=False,
        margin=dict(l=0, r=0, t=0, b=0),
        scene=dict(
            bgcolor="#FFFFFF",
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            zaxis=dict(visible=False),
            camera=dict(projection=dict(type="orthographic")),
        ),
    )
    return figure


def _browser_diagnostics() -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        from choreographer.browsers.chromium import Chromium

        browser = Chromium.find_browser(skip_local=False)
    except Exception as exc:
        result["browser_detection_error"] = f"{type(exc).__name__}: {exc}"
        return result
    result["browser"] = browser
    if not browser:
        return result
    try:
        version = subprocess.run(
            [browser, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        result["browser_version_exit_code"] = version.returncode
        result["browser_version"] = (version.stdout or version.stderr).strip()
    except Exception as exc:
        result["browser_version_error"] = f"{type(exc).__name__}: {exc}"
    try:
        linked = subprocess.run(
            ["ldd", browser],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        result["missing_shared_libraries"] = [
            line.strip() for line in linked.stdout.splitlines() if "not found" in line
        ]
    except Exception as exc:
        result["ldd_error"] = f"{type(exc).__name__}: {exc}"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, default=2400)
    parser.add_argument("--height", type=int, default=1800)
    parser.add_argument("--scale", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    if min(args.width, args.height, args.scale) <= 0:
        parser.error("width, height, and scale must be positive")

    temporary = args.output is None
    path = args.output or Path(tempfile.gettempdir()) / "mattervis-static-probe.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _probe_figure().write_image(
            str(path),
            width=args.width,
            height=args.height,
            scale=args.scale,
        )
        stats = analyze_png(path)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    **_browser_diagnostics(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2
    finally:
        if temporary and not args.keep and path.exists():
            path.unlink()

    summary = {
        "ok": not stats["blank"],
        "requested_width": args.width,
        "requested_height": args.height,
        "requested_scale": args.scale,
        "effective_width": args.width * args.scale,
        "effective_height": args.height * args.scale,
        **stats,
        **_browser_diagnostics(),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
