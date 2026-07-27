#!/usr/bin/env python3
"""Reproduce the TUI characterization metrics used by the agent benchmark.

This script records current behavior; it does not define scientific ground
truth. Analytic answers come from the committed benchmark oracle manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import locale
import os
import platform
import subprocess
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = ROOT / "scripts" / "tui_benchmark"
FIXTURE_ROOT = BENCHMARK_ROOT / "fixtures"
ORACLE_PATH = BENCHMARK_ROOT / "manifests" / "synthetic_oracles.v1.json"
DEFAULT_OUTPUT = ROOT / "scripts" / "_outputs" / "tui_agent_audit_metrics.json"
PACKAGE_NAMES = (
    "matter-vis",
    "numpy",
    "scipy",
    "gemmi",
    "ase",
    "pymatgen",
    "textual",
    "molcrys-kit",
    "wcwidth",
)
GLYPHS = ("●", "⣿", "⁰", "Å")
IMPLEMENTATION_INPUTS = (
    "crystal_viewer/cli.py",
    "crystal_viewer/math/camera.py",
    "crystal_viewer/tui/compositor.py",
    "crystal_viewer/tui/crystal_ir.py",
    "crystal_viewer/tui/loader_adapter.py",
    "crystal_viewer/tui/renderer.py",
    "crystal_viewer/tui/serializer.py",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_value(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _working_tree_dirty() -> bool | None:
    try:
        output = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return bool(output.strip())


def _working_tree_content_hash() -> str | None:
    """Hash tracked diffs plus untracked paths and bytes for provenance."""
    try:
        diff = subprocess.check_output(
            ["git", "diff", "--binary", "HEAD", "--"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
        )
        untracked_text = subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    digest = hashlib.sha256()
    digest.update(diff)
    for raw_path in sorted(path for path in untracked_text.split(b"\0") if path):
        digest.update(raw_path)
        path = ROOT / os.fsdecode(raw_path)
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _package_record(name: str) -> dict[str, Any] | None:
    try:
        distribution = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return None

    record: dict[str, Any] = {"version": distribution.version}
    direct_url = distribution.read_text("direct_url.json")
    if direct_url:
        try:
            record["direct_url"] = json.loads(direct_url)
        except json.JSONDecodeError:
            record["direct_url"] = {"unparsed": direct_url.strip()}
    return record


def _terminal_widths() -> dict[str, int | None]:
    try:
        from wcwidth import wcswidth
    except ImportError:
        return {glyph: None for glyph in GLYPHS}
    return {glyph: int(wcswidth(glyph)) for glyph in GLYPHS}


def _environment(font: str) -> dict[str, Any]:
    return {
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
        "platform": platform.platform(),
        "locale": locale.setlocale(locale.LC_CTYPE),
        "preferred_encoding": locale.getpreferredencoding(False),
        "unicode_version": unicodedata.unidata_version,
        "packages": {name: _package_record(name) for name in PACKAGE_NAMES},
        "terminal": {
            "TERM": os.environ.get("TERM"),
            "COLORTERM": os.environ.get("COLORTERM"),
            "LANG": os.environ.get("LANG"),
            "LC_ALL": os.environ.get("LC_ALL"),
            "font": font,
            "glyph_display_widths": _terminal_widths(),
        },
    }


def _load_oracles() -> dict[str, Any]:
    return json.loads(ORACLE_PATH.read_text(encoding="utf-8"))


def _case(oracles: dict[str, Any], case_id: str) -> dict[str, Any]:
    return next(case for case in oracles["cases"] if case["case_id"] == case_id)


def _frame_extent(frame: str) -> dict[str, Any]:
    lines = frame.splitlines()
    occupied_rows = [index for index, line in enumerate(lines) if line.strip()]
    occupied_height = (
        occupied_rows[-1] - occupied_rows[0] + 1 if occupied_rows else 0
    )
    return {
        "occupied_bbox_python_characters": [
            max((len(line.rstrip()) for line in lines), default=0),
            occupied_height,
        ],
        "emitted_line_count": len(lines),
    }


def _frame_size_metrics() -> list[dict[str, Any]]:
    from crystal_viewer.math.camera import Camera, project_points
    from crystal_viewer.tui.compositor import _compute_viewport, compose_frame
    from crystal_viewer.tui.loader_adapter import load_for_tui

    structure = load_for_tui(str(FIXTURE_ROOT / "projection_stack_6.cif"))
    camera = Camera.from_view_name("diagonal", structure)
    points, depth = project_points(camera, structure.cart_coords)
    metrics: list[dict[str, Any]] = []
    for width, height in ((12, 6), (20, 8), (30, 10), (40, 12), (80, 24)):
        frame = compose_frame(
            structure,
            camera,
            points,
            depth,
            width=width,
            height=height,
            mono=True,
            label_mode="dot",
            show_cell=False,
        )
        viewport = _compute_viewport(points, [], width, height)
        cells = [viewport.to_grid(float(x), float(y)) for x, y in points]
        counts = Counter(cells)
        metrics.append(
            {
                "requested": [width, height],
                **_frame_extent(frame),
                "unique_atom_center_cells": len(counts),
                "atoms_in_colliding_center_cells": sum(
                    count for count in counts.values() if count > 1
                ),
                "max_atom_centers_per_cell": max(counts.values()),
                "frame_sha256": hashlib.sha256(frame.encode("utf-8")).hexdigest(),
            }
        )
    return metrics


def _zoom_metrics() -> dict[str, Any]:
    from crystal_viewer.math.camera import Camera, project_points
    from crystal_viewer.tui.compositor import compose_frame
    from crystal_viewer.tui.loader_adapter import load_for_tui

    structure = load_for_tui(str(FIXTURE_ROOT / "projection_stack_6.cif"))
    camera = Camera.from_view_name("diagonal", structure)
    points, depth = project_points(camera, structure.cart_coords)
    frames: dict[str, dict[str, Any]] = {}
    for zoom in (0.5, 0.769230769, 1.0, 1.3, 2.0):
        frame = compose_frame(
            structure,
            camera,
            points,
            depth,
            width=80,
            height=24,
            mono=True,
            label_mode="dot",
            zoom=zoom,
            show_cell=False,
        )
        frames[str(zoom)] = {
            "sha256": hashlib.sha256(frame.encode("utf-8")).hexdigest(),
            **_frame_extent(frame),
        }
    return frames


def _lattice_metrics(oracles: dict[str, Any]) -> dict[str, Any]:
    from crystal_viewer.tui.loader_adapter import load_for_tui

    case = _case(oracles, "triclinic_lattice")
    structure = load_for_tui(str(BENCHMARK_ROOT / case["fixture_path"]))
    answer = case["tasks"][0]["answer"]
    matrix = np.asarray(answer["row_matrix"], dtype=float)
    oracle_frac = np.asarray(answer["frac"], dtype=float)
    oracle_cart = np.asarray(answer["cart"], dtype=float)
    atom = structure.atoms[0]
    return {
        "oracle_row_matrix": matrix.tolist(),
        "oracle_frac": oracle_frac.tolist(),
        "oracle_cart": oracle_cart.tolist(),
        "oracle_recomputed_cart": (oracle_frac @ matrix).tolist(),
        "loaded_frac": atom.frac.tolist(),
        "loaded_cart": atom.cart.tolist(),
        "loaded_lattice_vector_norms": np.linalg.norm(
            np.asarray(structure.lattice.vectors), axis=1
        ).tolist(),
        "max_frac_loaded_rows_matrix_cart_error_angstrom": max(
            float(np.linalg.norm(item.frac @ structure.lattice.matrix - item.cart))
            for item in structure.atoms
        ),
    }


def _projection_metrics(oracles: dict[str, Any]) -> dict[str, Any]:
    from crystal_viewer.math.camera import Camera, ProjectionMode, project_points
    from crystal_viewer.tui.loader_adapter import load_for_tui

    case = _case(oracles, "projection_stack_6")
    camera_data = case["tasks"][0]["request"]["camera"]
    structure = load_for_tui(str(BENCHMARK_ROOT / case["fixture_path"]))
    camera = Camera(
        azimuth=float(camera_data["azimuth"]),
        elevation=float(camera_data["elevation"]),
        roll=float(camera_data["roll"]),
        distance=1.0,
        target=np.asarray(camera_data["target"], dtype=float),
        projection=ProjectionMode(camera_data["projection"]),
    )
    points, depth = project_points(camera, structure.cart_coords)
    return {
        atom.label: {"xy": points[index].tolist(), "depth": float(depth[index])}
        for index, atom in enumerate(structure.atoms)
    }


def _disorder_metrics() -> dict[str, Any]:
    from crystal_viewer.loader import build_loaded_crystal
    from crystal_viewer.tui.loader_adapter import load_for_tui

    path = FIXTURE_ROOT / "disorder_70_30.cif"
    structure = load_for_tui(str(path))
    bundle = build_loaded_crystal(
        name="disorder_70_30", cif_path=str(path), title="synthetic"
    )
    return {
        "tui": [
            {
                "label": atom.label,
                "occupancy": atom.occupancy,
                "is_minor": atom.is_minor,
            }
            for atom in structure.atoms
        ],
        "canonical": [
            {
                "label": atom["label"],
                "occupancy": atom["occ"],
                "is_minor": bool(atom.get("_is_minor")),
            }
            for atom in bundle.raw_atoms
        ],
    }


def _formula_unit_metrics() -> dict[str, Any]:
    from crystal_viewer.cli import _apply_display_filter
    from crystal_viewer.loader import build_loaded_crystal
    from crystal_viewer.tui.loader_adapter import load_for_tui

    path = FIXTURE_ROOT / "fu_1_1_3.cif"
    structure = load_for_tui(str(path))
    filtered = _apply_display_filter(structure, "formula_unit")
    bundle = build_loaded_crystal(name="fu_1_1_3", cif_path=str(path), title="synthetic")
    return {
        "tui_source_atoms": structure.n_atoms,
        "tui_filtered_atoms": filtered.n_atoms,
        "tui_filtered_composition": filtered.element_counts(),
        "canonical_per_fu": bundle.molcrys_analysis.per_fu,
        "canonical_formula_unit_atoms": len(bundle.formula_unit_atoms),
    }


def _pbc_bond_metrics() -> dict[str, Any]:
    from crystal_viewer.tui.loader_adapter import load_for_tui

    structure = load_for_tui(str(FIXTURE_ROOT / "pbc_pair.cif"))
    return {
        "bonds": [
            {
                "labels": [structure.atoms[bond.i].label, structure.atoms[bond.j].label],
                "distance_angstrom": bond.distance,
            }
            for bond in structure.bonds
        ]
    }


def _scope_metrics() -> dict[str, Any]:
    from crystal_viewer.tui.loader_adapter import load_for_tui

    structure = load_for_tui(str(FIXTURE_ROOT / "disorder_70_30.cif"))
    display_counts = structure.element_counts()
    visible_atoms = [atom for atom in structure.atoms if not atom.is_minor]
    visible_counts: dict[str, int] = {}
    for atom in visible_atoms:
        visible_counts[atom.element] = visible_counts.get(atom.element, 0) + 1
    return {
        "resolved_display_mode": structure.metadata.get("display_mode"),
        "expanded_atom_count_metadata": structure.metadata.get("source_atom_count"),
        "display_atom_count_metadata": structure.metadata.get("display_atom_count"),
        "display_atom_count": structure.n_atoms,
        "visible_atom_count": len(visible_atoms),
        "display_composition": display_counts,
        "visible_composition": visible_counts,
    }


def _rotation_scale_metrics() -> dict[str, Any]:
    from crystal_viewer.math.camera import Camera, project_points
    from crystal_viewer.tui.compositor import _compute_viewport
    from crystal_viewer.tui.loader_adapter import load_for_tui

    structure = load_for_tui(str(FIXTURE_ROOT / "projection_stack_6.cif"))
    scales: dict[str, float] = {}
    for azimuth in range(0, 360, 30):
        camera = Camera.from_view_name("diagonal", structure)
        camera.azimuth = float(azimuth)
        points, _ = project_points(camera, structure.cart_coords)
        viewport = _compute_viewport(points, [], 80, 24)
        scales[str(azimuth)] = viewport.scale
    values = list(scales.values())
    return {
        "scale_by_azimuth_deg": scales,
        "min_scale": min(values),
        "max_scale": max(values),
        "relative_span": (max(values) - min(values)) / min(values),
    }


def build_payload(font: str = "unspecified") -> dict[str, Any]:
    oracles = _load_oracles()
    fixtures = {
        path.name: {
            "path": path.relative_to(BENCHMARK_ROOT).as_posix(),
            "sha256": _sha256(path),
        }
        for path in sorted(FIXTURE_ROOT.glob("*.cif"))
    }
    input_paths = [
        Path(__file__).resolve(),
        ORACLE_PATH,
        BENCHMARK_ROOT / "manifests" / "task_eligibility.v1.json",
        *sorted((BENCHMARK_ROOT / "schemas").glob("*.schema.json")),
        *(ROOT / relative for relative in IMPLEMENTATION_INPUTS),
    ]
    return {
        "schema": "mattervis.tui-characterization/v1",
        "repository": {
            "commit": _git_value("rev-parse", "HEAD"),
            "working_tree_dirty": _working_tree_dirty(),
            "working_tree_content_sha256": _working_tree_content_hash(),
        },
        "environment": _environment(font),
        "inputs": {
            path.relative_to(ROOT).as_posix(): _sha256(path) for path in input_paths
        },
        "fixtures": fixtures,
        "definitions": {
            "occupied_bbox_python_characters": (
                "Maximum rstripped Python character length and occupied row span; "
                "not terminal display width."
            ),
            "emitted_line_count": "Number of lines emitted by compose_frame.",
            "unique_atom_center_cells": (
                "Distinct terminal character cells returned by Viewport.to_grid "
                "for atom centers."
            ),
            "atoms_in_colliding_center_cells": (
                "Atoms whose terminal character cell contains multiple atom centers."
            ),
        },
        "measurements": {
            "lattice": _lattice_metrics(oracles),
            "projection": _projection_metrics(oracles),
            "zoom": _zoom_metrics(),
            "small_terminal": _frame_size_metrics(),
            "disorder": _disorder_metrics(),
            "formula_unit": _formula_unit_metrics(),
            "pbc_bonds": _pbc_bond_metrics(),
            "observation_scopes": _scope_metrics(),
            "rotation_scale": _rotation_scale_metrics(),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--font",
        default="unspecified",
        help="Terminal font name/version; remains unspecified unless supplied.",
    )
    args = parser.parse_args(argv)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = build_payload(font=args.font)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
