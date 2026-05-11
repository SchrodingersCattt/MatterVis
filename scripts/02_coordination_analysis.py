"""Coordination-shell analysis with full topology scores.

Run from the repository root:

    python scripts/02_coordination_analysis.py

For DAP-4 we ask: which X (perchlorate) anions sit closest to the first A-site
diaminopropane cation? The script writes:

* ``02_coordination_render.png``      - the structure with hull/spokes overlayed
* ``02_coordination_histogram.png``   - the neighbour-distance bar chart
* ``02_coordination_summary.json``    - a compact summary of every score
* ``02_coordination_full.json``       - the raw ``analyze_topology`` output

See ``docs/scores.md`` for the meaning of every score surfaced here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from crystal_viewer.loader import build_loaded_crystal  # noqa: E402
from crystal_viewer.renderer import (  # noqa: E402
    build_figure,
    topology_histogram_figure,
)
from crystal_viewer.scene import scene_style  # noqa: E402
from crystal_viewer.topology import analyze_topology  # noqa: E402


HERE = Path(__file__).resolve().parent
CIF = HERE / "data" / "DAP-4.cif"
OUTPUT_DIR = HERE / "_outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def first_fragment_of_type(bundle, frag_type: str):
    for frag in bundle.topology_fragment_table:
        if frag.get("type") == frag_type:
            return frag
    raise SystemExit(f"No fragment of type {frag_type!r} found.")


def _fmt(v, digits=3, suffix=""):
    if v is None:
        return "n/a"
    return f"{v:.{digits}f}{suffix}"


def print_scores(topology: dict) -> None:
    gap = topology["gap_info"]
    shape = topology["shape"]
    plan = topology["planarity"]
    prism = topology["prism_analysis"]

    print("--- topology scores (see docs/scores.md) ---")
    print(f"  coordination_number : {topology['coordination_number']}")
    print(f"  gap_value (Å)       : {_fmt(gap.get('gap_value'))}")
    print(f"  pool size / cutoff  : {topology['neighbor_pool_size']} / {topology['cutoff']:.2f} Å")
    print(f"  shell distances (Å) : {[round(d, 3) for d in topology['distances']]}")
    if shape.get("primary_label"):
        modifier = shape.get("label_modifier") or ""
        cshm = shape.get("cshm_value")
        modifier_str = f"{modifier} " if modifier else ""
        print(
            f"  shape               : {modifier_str}{shape['primary_label']}  "
            f"(CShM = {_fmt(cshm, 2)})"
        )
        for rank, entry in enumerate(shape.get("candidates", [])[1:], start=2):
            print(
                f"     rank {rank}: {entry['name']}  (CShM = {_fmt(entry.get('cshm'), 2)})"
            )
        if shape.get("structural_description"):
            print(f"  description         : {shape['structural_description']}")
    else:
        print(f"  shape               : n/a  (no library entry for CN={topology['coordination_number']})")
    print(f"  planarity best RMS  : {_fmt(plan.get('best_rms'), 3, ' Å')}  "
          f"indices={plan.get('best_indices')}  (group_size={plan.get('group_size')})")
    print(f"  prism/antiprism     : {prism.get('classification') or 'n/a'}  "
          f"twist={_fmt(prism.get('twist_deg'), 2, '°')}")


def build_summary(topology: dict) -> dict:
    gap = topology["gap_info"]
    shape = topology["shape"]
    plan = topology["planarity"]
    prism = topology["prism_analysis"]
    return {
        "structure": "DAP-4",
        "center_label": topology["center_label"],
        "center_type": topology["center_type"],
        "cutoff_A": topology["cutoff"],
        "neighbor_pool_size": topology["neighbor_pool_size"],
        "coordination_number": topology["coordination_number"],
        "gap_value_A": gap.get("gap_value"),
        "gap_index": gap.get("gap_index"),
        "shell_distances_A": [round(d, 4) for d in topology["distances"]],
        "shape": {
            "primary_label": shape.get("primary_label"),
            "label_modifier": shape.get("label_modifier"),
            "cshm_value": (
                round(shape["cshm_value"], 3)
                if shape.get("cshm_value") is not None
                else None
            ),
            "candidates": [
                {
                    "name": entry["name"],
                    "cshm": round(entry.get("cshm", 0.0), 3),
                }
                for entry in shape.get("candidates", [])
            ],
            "structural_description": shape.get("structural_description"),
        },
        "planarity": {
            "best_rms_A": plan.get("best_rms"),
            "best_indices": plan.get("best_indices"),
            "group_size": plan.get("group_size"),
        },
        "prism_analysis": {
            "classification": prism.get("classification"),
            "twist_deg": prism.get("twist_deg"),
        },
    }


def main() -> None:
    bundle = build_loaded_crystal(name="DAP-4", cif_path=str(CIF), title="DAP-4")

    target = first_fragment_of_type(bundle, "A")
    print(f"Center fragment: {target['label']} ({target['species']}, "
          f"index={target['index']}, heavy_atoms={target['heavy_atom_count']})")

    topology = analyze_topology(bundle, center_index=target["index"], cutoff=8.0)
    print_scores(topology)

    style = scene_style(
        bundle.scene,
        {
            "show_unit_cell": False,
            "show_axes": False,
            "show_labels": False,
            "atom_scale": 1.0,
            "bond_radius": 0.16,
            "topology_enabled": True,
        },
    )
    fig = build_figure(bundle.scene, style, topology_data=topology)
    shape = topology["shape"]
    if shape.get("primary_label"):
        modifier = shape.get("label_modifier") or ""
        prefix = f"{modifier} " if modifier else ""
        shape_text = f"{prefix}{shape['primary_label']}"
    else:
        shape_text = "n/a"
    fig.update_layout(
        title=dict(
            text=(
                f"DAP-4 · {target['label']} ({target['species']}) · "
                f"CN={topology['coordination_number']} · shape: {shape_text}"
            ),
            x=0.5,
        )
    )

    png = OUTPUT_DIR / "02_coordination_render.png"
    fig.write_image(str(png), width=900, height=720, scale=2)
    print(f"\nWrote {png}")

    hist = topology_histogram_figure(topology)
    hist.update_layout(
        title=dict(
            text=f"{target['label']}: neighbour distances  (gap = {_fmt(topology['gap_info'].get('gap_value'), 3, ' Å')})",
            x=0.5,
        )
    )
    hist_png = OUTPUT_DIR / "02_coordination_histogram.png"
    hist.write_image(str(hist_png), width=900, height=420, scale=2)
    print(f"Wrote {hist_png}")

    summary_path = OUTPUT_DIR / "02_coordination_summary.json"
    summary_path.write_text(json.dumps(build_summary(topology), indent=2))
    print(f"Wrote {summary_path}")

    full_path = OUTPUT_DIR / "02_coordination_full.json"
    # ``build_figure`` decorates the topology dict with renderer-internal
    # caches (``_background_dict_cache`` etc.) that use tuple keys -- those
    # are not JSON-safe and we don't want them in the dev dump anyway, so
    # strip any underscore-prefixed key before serialising.
    full_path.write_text(json.dumps(_strip_private(topology), indent=2, default=float))
    print(f"Wrote {full_path}")


def _strip_private(value):
    if isinstance(value, dict):
        return {
            key: _strip_private(item)
            for key, item in value.items()
            if not (isinstance(key, str) and key.startswith("_"))
        }
    if isinstance(value, (list, tuple)):
        return [_strip_private(item) for item in value]
    return value


if __name__ == "__main__":
    main()
