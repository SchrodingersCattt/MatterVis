"""Shared scoped observation summary for terminal status and serializers."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .crystal_ir import CrystalIR


def _counts(atoms) -> dict[str, int]:
    counts: dict[str, int] = {}
    for atom in atoms:
        counts[atom.element] = counts.get(atom.element, 0) + 1
    return counts


def _formula(counts: dict[str, int]) -> str:
    def sort_key(element: str) -> tuple[int, str]:
        if element == "C":
            return (0, element)
        if element == "H":
            return (1, element)
        return (2, element)

    return "".join(
        element if count == 1 else f"{element}{count}"
        for element, count in sorted(counts.items(), key=lambda item: sort_key(item[0]))
    )


def build_scope_summary(
    crystal: "CrystalIR",
    *,
    show_minor: bool,
    display_level: str = "atom",
    visible_marker_count: int | None = None,
) -> dict[str, Any]:
    """Return unambiguous canonical, display, visible, and marker scopes."""
    display_counts = crystal.element_counts()
    visible_atoms = [
        atom for atom in crystal.atoms if show_minor or not atom.is_minor
    ]
    visible_counts = _counts(visible_atoms)
    if visible_marker_count is None:
        visible_marker_count = (
            sum(len(indices) for indices in crystal.species_map.values())
            if display_level == "molecule"
            else len(visible_atoms)
        )

    canonical_counts = dict(crystal.canonical_composition or display_counts)
    return {
        "display_mode": crystal.metadata.get("display_mode", "structure"),
        "display_level": display_level,
        "source_site_atom_count": crystal.source_site_atom_count,
        "expanded_atom_count": crystal.expanded_atom_count,
        "display_atom_count": crystal.n_atoms,
        "visible_atom_count": len(visible_atoms),
        "visible_marker_count": int(visible_marker_count),
        "canonical_formula": crystal.canonical_formula or _formula(canonical_counts),
        "canonical_composition": canonical_counts,
        "display_formula": _formula(display_counts),
        "display_composition": display_counts,
        "visible_formula": _formula(visible_counts),
        "visible_composition": visible_counts,
    }
