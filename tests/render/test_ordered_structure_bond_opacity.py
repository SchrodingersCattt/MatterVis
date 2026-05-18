from __future__ import annotations

from pathlib import Path

import pytest

from crystal_viewer.loader import build_loaded_crystal
from crystal_viewer.presets import DEFAULT_STYLE
from crystal_viewer.renderer import build_figure


# DAP-4 and MPEP are fully ordered (no `_atom_site_disorder_assembly`
# rows that the loader needs to resolve via MolCrysKit). PEP and HPEP
# both carry Olex2 explicit assembly+group disorder, so they DO have
# minor atoms / bonds in the scene -- we still want to assert their
# major-major bonds (the chemically real backbone) stay opaque.
FULLY_ORDERED_CIFS = ("DAP-4", "MPEP")
PARTIALLY_DISORDERED_CIFS = ("PEP", "HPEP")
ALL_CIFS = FULLY_ORDERED_CIFS + PARTIALLY_DISORDERED_CIFS


def _bond_traces(fig):
    for trace in fig.to_dict().get("data", []):
        meta = trace.get("meta") if isinstance(trace.get("meta"), dict) else {}
        if meta.get("mv_role") != "bond":
            continue
        yield meta, trace


@pytest.mark.parametrize("name", FULLY_ORDERED_CIFS)
def test_fully_ordered_catalog_structures_have_no_minor_atoms(name: str):
    cif_path = Path("scripts/data") / f"{name}.cif"
    if not cif_path.exists():
        pytest.skip(f"{cif_path} is not present")
    bundle = build_loaded_crystal(name=name, cif_path=str(cif_path), title=name)
    scene = bundle.scene
    assert all(not atom.get("is_minor", False) for atom in scene.get("draw_atoms", []))
    assert all(not bond.get("is_minor", False) for bond in scene.get("bonds", []))


@pytest.mark.parametrize("name", ALL_CIFS)
@pytest.mark.parametrize("disorder", ("outline_rings", "opacity"))
def test_major_major_bonds_render_opaque(name: str, disorder: str):
    """Major-to-major bonds are the chemically real backbone and must
    render fully opaque regardless of the disorder mode. Minor (or
    minor-to-minor) bonds may legitimately fade under
    ``disorder='opacity'``; this test does NOT pin those."""
    cif_path = Path("scripts/data") / f"{name}.cif"
    if not cif_path.exists():
        pytest.skip(f"{cif_path} is not present")

    bundle = build_loaded_crystal(name=name, cif_path=str(cif_path), title=name)
    scene = bundle.scene

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
    major_opacities = [
        float(trace.get("opacity", 1.0))
        for meta, trace in _bond_traces(fig)
        if not bool(meta.get("mv_minor", False))
    ]

    assert major_opacities, "expected at least one major-major bond trace"
    assert all(o >= 0.999 for o in major_opacities), major_opacities
