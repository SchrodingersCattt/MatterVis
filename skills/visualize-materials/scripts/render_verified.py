#!/usr/bin/env python3
"""Execute a literal mat-vis render, validate its output, and write evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _option(command: list[str], *names: str, default: str | None = None) -> str | None:
    for index, value in enumerate(command):
        if value in names and index + 1 < len(command):
            return command[index + 1]
    return default


def _has(command: list[str], name: str) -> bool:
    return name in command


def _parse_command(command: list[str]) -> dict[str, Any]:
    if len(command) < 4 or Path(command[0]).name != "mat-vis" or command[1] != "render":
        raise ValueError("command must begin with the literal mat-vis render CLI")
    output = _option(command, "-o", "--output")
    if not output:
        raise ValueError("mat-vis render command must include -o/--output")
    style = _option(command, "--style", default="ball_stick")
    material = _option(command, "--material", default="mesh")
    view = _option(command, "--view", default="formula_unit")
    projection = "perspective" if _has(command, "--perspective") else "orthographic"
    camera = {
        "axis": _option(command, "--camera-axis", default="c"),
        "distance": _option(command, "--camera-distance", default="1.8"),
    }
    for name, key in (
        ("--view-direction", "view_direction"),
        ("--camera-position", "position"),
        ("--camera-up", "up"),
    ):
        if name in command:
            index = command.index(name)
            camera[key] = command[index + 1 : index + 4]
    return {
        "input": Path(command[2]).resolve(),
        "output": Path(output).resolve(),
        "requested": {
            "display": view,
            "style": style,
            "material": material,
            "projection": projection,
            "backend": (
                "matplotlib-flat-ortep"
                if style == "ortep" and material == "flat"
                else "plotly-kaleido"
            ),
            "camera": camera,
            "show_hydrogen": _has(command, "--show-hydrogen"),
            "show_cell": not _has(command, "--no-cell"),
            "show_axes": not _has(command, "--no-axes"),
            "show_labels": _has(command, "--show-labels"),
            "width": int(_option(command, "--width", default="900") or 900),
            "height": int(_option(command, "--height", default="720") or 720),
            "scale": int(_option(command, "--scale", default="2") or 2),
            "background": _option(command, "--background", default="#FFFFFF"),
        },
    }


def _background_rgb(value: str | None) -> tuple[int, int, int]:
    color = (value or "#FFFFFF").lstrip("#")
    if len(color) == 3:
        color = "".join(character * 2 for character in color)
    if len(color) != 6:
        raise ValueError(f"unsupported background color: {value!r}")
    try:
        return tuple(int(color[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError as exc:
        raise ValueError(f"unsupported background color: {value!r}") from exc


def _crop_png(
    path: Path,
    *,
    background: tuple[int, int, int],
    padding: int,
) -> dict[str, Any]:
    before = analyze_png(path, background=background)
    bbox = before.get("foreground_bbox")
    if before["blank"] or bbox is None:
        return {
            "applied": False,
            "reason": "blank image has no crop box",
            "original_dimensions": [before["width"], before["height"]],
            "rescaled": False,
        }
    left = max(0, bbox[0] - padding)
    top = max(0, bbox[1] - padding)
    right = min(before["width"], bbox[2] + padding)
    bottom = min(before["height"], bbox[3] + padding)
    crop_box = [left, top, right, bottom]
    with Image.open(path) as image:
        cropped = image.crop(tuple(crop_box))
        cropped.save(path)
    return {
        "applied": crop_box != [0, 0, before["width"], before["height"]],
        "original_dimensions": [before["width"], before["height"]],
        "crop_box": crop_box,
        "cropped_dimensions": [right - left, bottom - top],
        "padding": padding,
        "background": list(background),
        "rescaled": False,
    }


def _classify_backend(
    parsed: dict[str, Any],
    stdout: str,
    stderr: str,
) -> tuple[dict[str, str], str | None, list[str]]:
    combined = f"{stdout}\n{stderr}"
    requested = parsed["requested"]
    fallback_marker = "Falling back to Matplotlib flat ORTEP output"
    warnings: list[str] = []
    if fallback_marker in combined:
        warnings.append("export")
        return (
            {
                "display": requested["display"],
                "style": "ortep",
                "material": "flat",
                "projection": "orthographic",
                "backend": "matplotlib-flat-ortep",
                "evidence": "captured CLI fallback message",
            },
            next(
                (
                    line.strip()
                    for line in stderr.splitlines()
                    if "Plotly/Kaleido static export is unavailable" in line
                ),
                "Plotly/Kaleido export failure",
            ),
            warnings,
        )
    suffix = parsed["output"].suffix.lower()
    backend = "plotly-html" if suffix == ".html" else requested["backend"]
    return (
        {
            "display": requested["display"],
            "style": requested["style"],
            "material": requested["material"],
            "projection": requested["projection"],
            "backend": backend,
            "evidence": "CLI dispatch contract and absence of captured fallback",
        },
        None,
        warnings,
    )


def _verify_signature(
    path: Path, *, background: tuple[int, int, int]
) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return analyze_png(path, background=background)
    head = path.read_bytes()[:4096]
    if suffix == ".pdf":
        valid = head.startswith(b"%PDF")
    elif suffix == ".svg":
        valid = b"<svg" in head.lower()
    elif suffix == ".html":
        valid = b"plotly" in head.lower()
    else:
        valid = False
    return {"format": suffix.lstrip(".").upper(), "signature_valid": valid}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--min-bbox-coverage", type=float, default=0.0)
    parser.add_argument("--crop-padding", type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not 0.0 <= args.min_bbox_coverage <= 1.0:
        parser.error("--min-bbox-coverage must be between 0 and 1")
    if args.crop_padding is not None and args.crop_padding < 0:
        parser.error("--crop-padding must be non-negative")
    try:
        parsed = _parse_command(command)
    except ValueError as exc:
        parser.error(str(exc))

    args.manifest = args.manifest.resolve()
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    stdout_log = args.manifest.with_suffix(".stdout.log")
    stderr_log = args.manifest.with_suffix(".stderr.log")
    run = subprocess.run(command, check=False, capture_output=True, text=True)
    stdout_log.write_text(run.stdout, encoding="utf-8")
    stderr_log.write_text(run.stderr, encoding="utf-8")
    sys.stdout.write(run.stdout)
    sys.stderr.write(run.stderr)

    effective, fallback_reason, warning_classes = _classify_backend(
        parsed, run.stdout, run.stderr
    )
    try:
        background = _background_rgb(parsed["requested"]["background"])
    except ValueError as exc:
        parser.error(str(exc))
    input_path = parsed["input"]
    output_path = parsed["output"]
    manifest: dict[str, Any] = {
        "schema": "mattervis.render-evidence.v1",
        "status": "failed",
        "command": shlex.join(command),
        "exit_code": run.returncode,
        "input": {
            "path": str(input_path),
            "sha256": sha256(input_path) if input_path.is_file() else None,
        },
        "output": {"path": str(output_path)},
        "logs": {
            "stdout": str(stdout_log),
            "stdout_sha256": sha256(stdout_log),
            "stderr": str(stderr_log),
            "stderr_sha256": sha256(stderr_log),
        },
        "requested": parsed["requested"],
        "effective": effective,
        "fallback_reason": fallback_reason,
        "warning_classes": warning_classes,
        "visual_acceptance": "pending",
    }

    failure: str | None = None
    if run.returncode != 0:
        failure = f"mat-vis exited with {run.returncode}"
    elif not output_path.is_file() or output_path.stat().st_size == 0:
        failure = "output is missing or empty"
    else:
        crop = None
        try:
            if output_path.suffix.lower() == ".png" and args.crop_padding is not None:
                crop = _crop_png(
                    output_path,
                    background=background,
                    padding=args.crop_padding,
                )
            checks = _verify_signature(output_path, background=background)
        except Exception as exc:
            checks = {"verification_error": f"{type(exc).__name__}: {exc}"}
            failure = "output verification failed"
        manifest["output"].update(
            {
                "bytes": output_path.stat().st_size,
                "sha256": sha256(output_path),
                **checks,
            }
        )
        if crop is not None:
            manifest["output"]["crop"] = crop
        if checks.get("blank") is True:
            failure = "PNG is blank or has too few foreground pixels"
        if checks.get("signature_valid") is False:
            failure = "output signature is invalid"
        bbox = checks.get("foreground_bbox")
        if bbox and args.min_bbox_coverage:
            width = checks["width"]
            height = checks["height"]
            coverage = {
                "x": (bbox[2] - bbox[0]) / width,
                "y": (bbox[3] - bbox[1]) / height,
            }
            manifest["output"]["bbox_coverage"] = coverage
            if min(coverage.values()) < args.min_bbox_coverage:
                failure = (
                    "foreground bounding box does not meet "
                    f"{args.min_bbox_coverage:.2f} coverage"
                )

    if fallback_reason and effective["backend"] != parsed["requested"]["backend"]:
        failure = "effective backend does not match requested visual language"
    manifest["failure"] = failure
    manifest["status"] = "ok" if failure is None else "failed"
    args.manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"render_manifest={args.manifest}")
    if failure:
        print(f"verification_failed={failure}", file=sys.stderr)
        return run.returncode or 4
    print(f"effective_backend={effective['backend']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
