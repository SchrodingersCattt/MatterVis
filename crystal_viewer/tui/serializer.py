"""Structured serializer for LLM/agent consumption.

Outputs a YAML-like text representation of a crystal structure
that is both human-readable and machine-parseable. This is the
primary "agent interface" — the ASCII art is a visual supplement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .crystal_ir import CrystalIR
    from ..math.camera import Camera


def serialize_crystal(
    crystal: "CrystalIR",
    camera: "Camera",
    pts_2d: np.ndarray,
    *,
    include_art: bool = True,
    art_width: int = 60,
    art_height: int = 25,
    show_minor: bool = True,
) -> str:
    """Serialize a crystal structure for agent/LLM consumption.

    Output format is a structured text block with:
    - Crystal identity (formula, spacegroup, source)
    - Unit cell parameters
    - Atom table with coordinates
    - Neighbor/bond connectivity
    - Optional ASCII art view

    Parameters
    ----------
    crystal : CrystalIR
        The crystal structure.
    camera : Camera
        Camera used for the view (metadata).
    pts_2d : np.ndarray
        Projected 2D coordinates (for art generation).
    include_art : bool
        Whether to append ASCII art at the end.
    art_width, art_height : int
        Dimensions for the ASCII art block.

    Returns
    -------
    str
        Structured text output.
    """
    lines: list[str] = []
    visible_indices = {
        index
        for index, atom in enumerate(crystal.atoms)
        if show_minor or not atom.is_minor
    }
    visible_atoms = [
        atom for index, atom in enumerate(crystal.atoms) if index in visible_indices
    ]

    counts: dict[str, int] = {}
    for atom in visible_atoms:
        counts[atom.element] = counts.get(atom.element, 0) + 1

    def _formula_sort_key(element: str) -> tuple[int, str]:
        if element == "C":
            return (0, element)
        if element == "H":
            return (1, element)
        return (2, element)

    formula = "".join(
        element if count == 1 else f"{element}{count}"
        for element, count in sorted(counts.items(), key=lambda item: _formula_sort_key(item[0]))
    )

    # ── Header ──────────────────────────────────────────────────────────
    lines.append("crystal:")
    lines.append(f"  formula: {formula}")
    if crystal.spacegroup:
        lines.append(f"  spacegroup: {crystal.spacegroup}")
    lines.append(f"  n_atoms: {len(visible_atoms)}")
    lines.append(f"  source: {crystal.source_path}")
    lines.append("")

    # ── Unit cell ───────────────────────────────────────────────────────
    if crystal.lattice is not None:
        lat = crystal.lattice
        lines.append("cell:")
        lines.append(f"  a: {lat.a:.4f}")
        lines.append(f"  b: {lat.b:.4f}")
        lines.append(f"  c: {lat.c:.4f}")
        lines.append(f"  alpha: {lat.alpha:.2f}")
        lines.append(f"  beta: {lat.beta:.2f}")
        lines.append(f"  gamma: {lat.gamma:.2f}")
        lines.append(f"  volume: {lat.volume:.2f}")
        lines.append("")

    # ── Composition ─────────────────────────────────────────────────────
    if counts:
        lines.append("composition:")
        for elem, n in sorted(counts.items()):
            lines.append(f"  {elem}: {n}")
        lines.append("")

    # ── Atom table ──────────────────────────────────────────────────────
    lines.append("atoms:")
    # Build neighbor map from bonds
    neighbors: dict[int, list[tuple[str, float]]] = {}
    for bond in crystal.bonds:
        if bond.i not in visible_indices or bond.j not in visible_indices:
            continue
        neighbors.setdefault(bond.i, []).append(
            (crystal.atoms[bond.j].element, bond.distance)
        )
        neighbors.setdefault(bond.j, []).append(
            (crystal.atoms[bond.i].element, bond.distance)
        )

    for atom in visible_atoms:
        lines.append(f"  - label: {atom.label}")
        lines.append(f"    element: {atom.element}")
        lines.append(
            f"    frac: [{atom.frac[0]:.4f}, {atom.frac[1]:.4f}, {atom.frac[2]:.4f}]"
        )
        lines.append(
            f"    cart: [{atom.cart[0]:.3f}, {atom.cart[1]:.3f}, {atom.cart[2]:.3f}]"
        )
        if atom.molecule_index >= 0:
            lines.append(f"    molecule: {atom.molecule_index}")
        if atom.is_minor:
            lines.append("    disorder: minor")
        elif atom.disorder_group != 0:
            lines.append(f"    disorder_group: {atom.disorder_group}")
        # Coordination info
        nbrs = neighbors.get(atom.index, [])
        if nbrs:
            cn = len(nbrs)
            nbr_elems = [e for e, _ in nbrs]
            lines.append(f"    coordination: {cn}")
            # Summarize neighbors by element
            nbr_counts: dict[str, int] = {}
            for e in nbr_elems:
                nbr_counts[e] = nbr_counts.get(e, 0) + 1
            nbr_str = ", ".join(f"{e}×{n}" for e, n in sorted(nbr_counts.items()))
            lines.append(f"    neighbors: [{nbr_str}]")
        lines.append("")

    # ── Bond summary ────────────────────────────────────────────────────
    visible_bonds = [
        bond
        for bond in crystal.bonds
        if bond.i in visible_indices and bond.j in visible_indices
    ]
    if visible_bonds:
        lines.append("bonds:")
        # Group by element pair
        bond_groups: dict[tuple[str, str], list[float]] = {}
        for bond in visible_bonds:
            e1 = crystal.atoms[bond.i].element
            e2 = crystal.atoms[bond.j].element
            key = tuple(sorted([e1, e2]))
            bond_groups.setdefault(key, []).append(bond.distance)

        for (e1, e2), dists in sorted(bond_groups.items()):
            avg_d = np.mean(dists)
            lines.append(
                f"  {e1}-{e2}: count={len(dists)}, "
                f"avg={avg_d:.3f}Å, "
                f"range=[{min(dists):.3f}, {max(dists):.3f}]Å"
            )
        lines.append("")

    # ── View metadata ───────────────────────────────────────────────────
    lines.append("view:")
    lines.append(f"  projection: {camera.projection.value}")
    lines.append(f"  azimuth: {camera.azimuth:.1f}")
    lines.append(f"  elevation: {camera.elevation:.1f}")
    lines.append("")

    # ── ASCII art ───────────────────────────────────────────────────────
    if include_art and len(pts_2d) > 0:
        from .compositor import compose_frame
        from ..math.camera import project_points

        _, depth = project_points(camera, crystal.cart_coords)
        art = compose_frame(
            crystal, camera, pts_2d, depth,
            width=art_width, height=art_height,
            mono=True, label_mode="label",
            show_bonds=True, show_cell=True,
            show_minor=show_minor,
        )
        lines.append("  art: |")
        for art_line in art.split("\n"):
            lines.append(f"    {art_line}")

    return "\n".join(lines)
