"""End-to-end loader, scene, figure, and scientific-signature benchmark."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import gc
import hashlib
import importlib.metadata
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import plotly

try:
    import resource as _resource
except ImportError:  # Windows does not provide the POSIX resource module.
    _resource = None

from mat_viewer import perf_log
from mat_viewer.loader import build_bundle_scene, build_loaded_crystal
from mat_viewer.renderer import build_figure, style_from_controls

from .oracle import build_oracle_signature

SCHEMA = "mattervis.perf.pipeline/v1"


def _peak_rss_mib() -> float | None:
    """Return peak resident memory when the platform exposes it."""
    if _resource is None:
        return None
    peak = float(_resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss)
    divisor = 1024.0**2 if sys.platform == "darwin" else 1024.0
    return peak / divisor


@contextmanager
def _pipeline_events_enabled():
    names = ("MATTERVIS_BOND_PERF_EVENTS", "MATTERVIS_SCENE_PERF_EVENTS")
    previous = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ[name] = "1"
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _git_revision(root: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _package_provenance(package: str) -> dict[str, Any]:
    try:
        distribution = importlib.metadata.distribution(package)
    except importlib.metadata.PackageNotFoundError:
        return {"version": None, "direct_url": None}
    direct_url = None
    try:
        direct_url = json.loads(distribution.read_text("direct_url.json") or "null")
    except (TypeError, ValueError):
        pass
    return {"version": distribution.version, "direct_url": direct_url}


def _timed(call: Callable[[], Any], *, repeat: int = 1) -> tuple[dict[str, Any], Any]:
    samples = []
    result = None
    for _ in range(max(1, repeat)):
        gc.collect()
        started = time.perf_counter()
        result = call()
        samples.append((time.perf_counter() - started) * 1000.0)
    return ({
        "repeat": len(samples),
        "mean_ms": statistics.mean(samples),
        "median_ms": statistics.median(samples),
        "min_ms": min(samples),
        "max_ms": max(samples),
    }, result)


def _scene_timings(bundle, display_mode: str, *, repeat: int) -> tuple[dict[str, Any], Any]:
    scene_cache = getattr(bundle, "scene_cache", None)
    if isinstance(scene_cache, dict):
        # The loader historically populated ``(display_mode, hydrogen)``
        # while current bundle calls use a four-field style key. Clear every
        # matching display/hydrogen entry so this first call is genuinely cold.
        for key in list(scene_cache):
            if isinstance(key, tuple) and key[:2] == (display_mode, False):
                scene_cache.pop(key, None)
    fragment_cache = getattr(bundle, "fragment_table_cache", None)
    if isinstance(fragment_cache, dict):
        fragment_cache.pop(("scene", display_mode, False), None)
    cold, scene = _timed(
        lambda: build_bundle_scene(bundle, display_mode=display_mode, show_hydrogen=False, preset={}),
    )
    warm, _ = _timed(
        lambda: build_bundle_scene(bundle, display_mode=display_mode, show_hydrogen=False, preset={}),
        repeat=repeat,
    )
    return {"cold": cold, "warm": warm}, scene


def build_pipeline_report(
    cif_path: Path,
    *,
    repeat: int = 1,
    include_unit_cell: bool = True,
    include_figure: bool = True,
) -> dict[str, Any]:
    cif_path = Path(cif_path).resolve()
    content = cif_path.read_bytes()
    root = Path(__file__).resolve().parents[2]
    event_cursor = perf_log.latest_seq()

    with _pipeline_events_enabled():
        loader_timing, bundle = _timed(
            lambda: build_loaded_crystal(
                name=cif_path.stem,
                cif_path=str(cif_path),
                title=cif_path.stem,
                source="upload",
            ),
            repeat=1,
        )

        formula_timing, formula_scene = _scene_timings(bundle, "formula_unit", repeat=repeat)
    scenes: dict[str, Any] = {
        "formula_unit": {
            "timing": formula_timing,
            "atoms": len(formula_scene.get("draw_atoms") or []),
            "bonds": len(formula_scene.get("bonds") or []),
        }
    }
    oracle_scene = formula_scene
    if include_unit_cell:
        with _pipeline_events_enabled():
            unit_timing, unit_scene = _scene_timings(bundle, "unit_cell", repeat=repeat)
        scenes["unit_cell"] = {
            "timing": unit_timing,
            "atoms": len(unit_scene.get("draw_atoms") or []),
            "bonds": len(unit_scene.get("bonds") or []),
        }
        oracle_scene = unit_scene

    figure = None
    figure_report = None
    if include_figure:
        style = dict(oracle_scene.get("style") or {})
        style.update(style_from_controls(1.0, 0.12, 0.35, 0.12, []))
        mesh_cache = oracle_scene.get("_mesh_trace_cache")
        if isinstance(mesh_cache, dict):
            mesh_cache.clear()
        figure_cold, figure = _timed(lambda: build_figure(oracle_scene, style))
        figure_warm, figure = _timed(lambda: build_figure(oracle_scene, style), repeat=repeat)
        encode_timing, encoded = _timed(figure.to_json, repeat=repeat)
        figure_report = {
            "assembly": {"cold": figure_cold, "warm": figure_warm},
            "json_encode": encode_timing,
            "json_bytes": len(encoded.encode("utf-8")),
            "traces": len(figure.data),
        }

    return {
        "schema": SCHEMA,
        "fixture": {
            "path": str(cif_path),
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
        "environment": {
            "mattervis_revision": _git_revision(root),
            "python": sys.version,
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "numpy": np.__version__,
            "plotly": plotly.__version__,
            "molcrys_kit": _package_provenance("molcrys-kit"),
        },
        "loader": {
            "timing": loader_timing,
            "raw_atoms": len(bundle.raw_atoms),
            "formula_unit_atoms": len(bundle.formula_unit_atoms),
        },
        "scenes": scenes,
        "figure": figure_report,
        "peak_rss_mib": _peak_rss_mib(),
        "events": perf_log.recent(limit=1000, since_seq=event_cursor),
        "oracle": build_oracle_signature(bundle, scene=oracle_scene, figure=figure),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark the MatterVis file-to-figure pipeline.")
    parser.add_argument("cif", type=Path, help="CIF file to benchmark.")
    parser.add_argument("--repeat", type=int, default=1, help="Scene/figure timing repeats (default: 1).")
    parser.add_argument("--skip-unit-cell", action="store_true", help="Benchmark formula-unit scene only.")
    parser.add_argument("--skip-figure", action="store_true", help="Skip figure assembly and JSON encoding.")
    parser.add_argument("--output", type=Path, help="Optional JSON output path.")
    args = parser.parse_args(argv)
    report = build_pipeline_report(
        args.cif,
        repeat=max(1, args.repeat),
        include_unit_cell=not args.skip_unit_cell,
        include_figure=not args.skip_figure,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
