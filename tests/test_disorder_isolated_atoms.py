"""Every non-H atom that belongs to a disorder PART must have at least one
bond within its own PART.  Isolated (bondless) non-H atoms are a hard fail.

Rule:
  - Within the same disorder PART, atoms bond normally.
  - Between different PARTs, atoms do NOT bond.
  - Ordered atoms (no PART) bond to any atom that is not from a conflicting PART.
  - An isolated non-H atom (zero bonds) is ALWAYS wrong for a molecular crystal.
"""
from __future__ import annotations

from pathlib import Path

import pytest

CIF = Path(__file__).resolve().parent.parent / "_tmp" / "GAGCIF01.cif"
pytestmark = pytest.mark.skipif(not CIF.exists(), reason="GAGCIF01.cif not available")


def _load_scene(display_mode: str) -> dict:
    from crystal_viewer.loader import build_loaded_crystal, build_bundle_scene

    bundle = build_loaded_crystal(
        name="GAGCIF01", cif_path=str(CIF), title="test"
    )
    return build_bundle_scene(bundle, display_mode=display_mode)


def _isolated_non_h(scene: dict) -> list[dict]:
    """Return list of non-H atoms that have zero bonds."""
    atoms = scene["draw_atoms"]
    bonds = scene["bonds"]
    bonded: set[int] = set()
    for b in bonds:
        bonded.add(b["i"])
        bonded.add(b["j"])
    isolated = []
    for i, a in enumerate(atoms):
        if a.get("elem") == "H":
            continue
        if i not in bonded:
            isolated.append(
                {
                    "idx": i,
                    "label": a.get("label", "?"),
                    "elem": a.get("elem", "?"),
                    "is_minor": a.get("is_minor", False),
                    "dg": a.get("_mv_auto_disorder_group", ""),
                }
            )
    return isolated


@pytest.mark.parametrize("mode", ["formula_unit", "unit_cell"])
def test_no_isolated_non_h_atoms(mode: str):
    """Every non-H atom must participate in at least one bond."""
    scene = _load_scene(mode)
    isolated = _isolated_non_h(scene)
    if isolated:
        lines = [f"  {a['label']} ({a['elem']}) minor={a['is_minor']} dg={a['dg']}" for a in isolated]
        pytest.fail(
            f"{len(isolated)} isolated non-H atoms in display_mode={mode}:\n"
            + "\n".join(lines)
        )


def test_disorder_parts_bond_within_not_across():
    """Bonds exist within each PART; no bond crosses between different PARTs."""
    scene = _load_scene("formula_unit")
    atoms = scene["draw_atoms"]
    bonds = scene["bonds"]

    cross_part_bonds = []
    for b in bonds:
        ai, aj = atoms[b["i"]], atoms[b["j"]]
        dg_i = ai.get("_mv_auto_disorder_group", "")
        dg_j = aj.get("_mv_auto_disorder_group", "")
        da_i = ai.get("_mv_auto_disorder_assembly", "")
        da_j = aj.get("_mv_auto_disorder_assembly", "")
        # Both disordered in the same assembly but different groups → cross-part
        if dg_i and dg_j and da_i == da_j and dg_i != dg_j:
            cross_part_bonds.append(
                f"  {ai.get('label')}(dg={dg_i}) — {aj.get('label')}(dg={dg_j})"
            )

    if cross_part_bonds:
        pytest.fail(
            f"{len(cross_part_bonds)} cross-PART bonds found:\n"
            + "\n".join(cross_part_bonds[:20])
        )


def test_disorder_atoms_are_tagged():
    """The CIF has disorder — loader must tag minor/major atoms."""
    scene = _load_scene("formula_unit")
    atoms = scene["draw_atoms"]
    n_minor = sum(1 for a in atoms if a.get("is_minor"))
    n_major_dg = sum(
        1 for a in atoms
        if not a.get("is_minor") and a.get("_mv_auto_disorder_group")
    )
    assert n_minor > 0, "GAGCIF01 has disorder — expected minor atoms"
    assert n_major_dg > 0, "GAGCIF01 has disorder — expected major-tagged atoms"
