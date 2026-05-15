from __future__ import annotations

from pathlib import Path

import pytest

from crystal_viewer.loader import build_loaded_crystal
from crystal_viewer.presets import DEFAULT_STYLE
from crystal_viewer.renderer import build_figure


ORDERED_CIFS = ("DAP-4", "PEP", "MPEP", "HPEP")


def _bond_trace_opacities(fig):
    for trace in fig.to_dict().get("data", []):
        meta = trace.get("meta") if isinstance(trace.get("meta"), dict) else {}
        if meta.get("mv_role") != "bond":
            continue
        yield trace.get("name") or trace.get("type"), float(trace.get("opacity", 1.0))


@pytest.mark.parametrize("name", ORDERED_CIFS)
@pytest.mark.parametrize("disorder", ("outline_rings", "opacity"))
def test_ordered_catalog_structures_render_bonds_opaque(name: str, disorder: str):
    cif_path = Path("scripts/data") / f"{name}.cif"
    if not cif_path.exists():
        pytest.skip(f"{cif_path} is not present")

    bundle = build_loaded_crystal(name=name, cif_path=str(cif_path), title=name)
    scene = bundle.scene

    assert all(not atom.get("is_minor", False) for atom in scene.get("draw_atoms", []))
    assert all(not bond.get("is_minor", False) for bond in scene.get("bonds", []))

    style = {
        **DEFAULT_STYLE,
        **scene.get("style", {}),
        "disorder": disorder,
        "show_axes": False,
        "show_axis_key": False,
        "show_labels": False,
        "topology_enabled": False,
    }
    fig = build_figure(scene, style)
    opacities = list(_bond_trace_opacities(fig))

    assert opacities, "expected at least one rendered bond trace"
    assert all(opacity >= 0.999 for _name, opacity in opacities), opacities
