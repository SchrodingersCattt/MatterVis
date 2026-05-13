from __future__ import annotations

import argparse
import gc
import json
import time
from pathlib import Path
from statistics import mean, median
from typing import Any, Callable

import numpy as np

from crystal_viewer.loader import build_bundle_scene, build_loaded_crystal
from crystal_viewer.renderer import _cached_atom_bond_meshes, build_figure, style_from_controls
from crystal_viewer.topology import (
    analyze_topology,
    classify_fragments,
    planarity_analysis,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CIF = ROOT / "scripts" / "data" / "DAP-4.cif"


def _time_call(fn: Callable[[], Any], *, repeat: int = 5, warmup: int = 1) -> dict[str, Any]:
    for _ in range(max(0, warmup)):
        fn()
    gc.collect()
    samples = []
    last = None
    for _ in range(max(1, repeat)):
        start = time.perf_counter()
        last = fn()
        samples.append(time.perf_counter() - start)
    return {
        "mean_s": mean(samples),
        "median_s": median(samples),
        "min_s": min(samples),
        "max_s": max(samples),
        "repeat": len(samples),
        "last_type": type(last).__name__,
    }


def _first_topology_site(bundle) -> int:
    fragments = classify_fragments(bundle)
    for fragment in fragments:
        if fragment.get("type") != "X":
            return int(fragment["index"])
    return int(fragments[0]["index"]) if fragments else 0


def _first_ligand_formula(bundle) -> str | None:
    for fragment in classify_fragments(bundle):
        if fragment.get("type") == "X":
            return fragment.get("formula") or fragment.get("species")
    for fragment in classify_fragments(bundle):
        if int(fragment["index"]) != _first_topology_site(bundle):
            return fragment.get("formula") or fragment.get("species")
    return None


def _clear_bundle_perf_caches(bundle) -> None:
    for attr in (
        "_analyze_topology_cache",
        "_topology_state_cache",
    ):
        cache = getattr(bundle, attr, None)
        if isinstance(cache, dict):
            cache.clear()


def _clear_scene_mesh_cache(scene: dict) -> None:
    cache = scene.get("_mesh_trace_cache")
    if isinstance(cache, dict):
        cache.clear()


def bench_planarity(*, repeat: int = 5) -> dict[str, Any]:
    rng = np.random.default_rng(20260501)
    out: dict[str, Any] = {}
    for cn in (8, 10, 12, 14):
        coords = rng.normal(size=(cn, 3))
        result = _time_call(lambda coords=coords: planarity_analysis(coords), repeat=repeat)
        result["best_rms"] = planarity_analysis(coords)["best_rms"]
        out[f"cn_{cn}"] = result
    return out


def bench_atom_mesh(scene: dict, style: dict, *, repeat: int = 3) -> dict[str, Any]:
    def run():
        _clear_scene_mesh_cache(scene)
        return _cached_atom_bond_meshes(scene, style, use_fast=False)

    result = _time_call(run, repeat=repeat, warmup=0)
    payload = run()
    result["atom_count"] = len(scene.get("draw_atoms", []))
    result["bond_count"] = len(scene.get("bonds", []))
    result["trace_count"] = sum(len(v) for v in payload.values())
    return result


def bench_style_toggle(scene: dict, style: dict, *, repeat: int = 10) -> dict[str, Any]:
    build_figure(scene, style)
    toggled = dict(style)
    toggled["show_labels"] = not bool(style.get("show_labels", False))
    toggled["show_axes"] = not bool(style.get("show_axes", False))
    toggled["show_unit_cell"] = not bool(style.get("show_unit_cell", False))
    toggled["show_minor_only"] = not bool(style.get("show_minor_only", False))

    result = _time_call(lambda: build_figure(scene, toggled), repeat=repeat, warmup=1)
    result["mesh_cache_entries"] = len(scene.get("_mesh_trace_cache") or {})
    return result


def bench_topology_full(bundle, center_index: int, cutoff: float = 10.0, *, repeat: int = 3) -> dict[str, Any]:
    ligand = _first_ligand_formula(bundle)

    def run():
        _clear_bundle_perf_caches(bundle)
        return analyze_topology(
            bundle,
            center_index=center_index,
            cutoff=cutoff,
            ligand_species=[ligand] if ligand else None,
        )

    result = _time_call(run, repeat=repeat, warmup=0)
    payload = run()
    result["coordination_number"] = int(payload.get("coordination_number", 0))
    result["neighbor_pool_size"] = int(payload.get("neighbor_pool_size", 0))
    return result


def build_benchmark_payload(cif_path: Path, *, repeat: int = 3) -> dict[str, Any]:
    bundle = build_loaded_crystal(
        name=cif_path.stem,
        cif_path=str(cif_path),
        title=cif_path.stem,
        source="perf",
    )
    center_index = _first_topology_site(bundle)
    scene = build_bundle_scene(bundle, display_mode="unit_cell", show_hydrogen=False, preset={})
    style = dict(scene.get("style", {}))
    style.update(style_from_controls(1.0, 0.12, 0.35, 0.12, []))
    return {
        "cif": str(cif_path),
        "fragment_count": len(classify_fragments(bundle)),
        "atom_count_unit_cell": len(scene.get("draw_atoms", [])),
        "benchmarks": {
            "planarity": bench_planarity(repeat=repeat),
            "atom_mesh_unit_cell": bench_atom_mesh(scene, style, repeat=max(1, min(3, repeat))),
            "style_toggle_patch_path": bench_style_toggle(scene, style, repeat=max(1, repeat)),
            "topology_full": bench_topology_full(bundle, center_index, repeat=max(1, min(3, repeat))),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run MatterVis developer performance benchmarks.")
    parser.add_argument("--cif", default=str(DEFAULT_CIF), help="CIF path to benchmark.")
    parser.add_argument("--repeat", type=int, default=3, help="Timing repeats per benchmark.")
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    args = parser.parse_args(argv)

    payload = build_benchmark_payload(Path(args.cif), repeat=args.repeat)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
