"""Structured serializer for LLM/agent consumption.

Outputs a YAML-like text representation of a crystal structure
that is both human-readable and machine-parseable. This is the
primary "agent interface" — the ASCII art is a visual supplement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from .summary import build_scope_summary
from .text import terminal_text


STRUCTURED_ATOM_LIMIT = 48

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
    display_level: str = "auto",
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

    from .compositor import resolve_display_level
    from .compositor import structure_is_framework_like

    resolved_level = resolve_display_level(
        display_level,
        atom_count=len(crystal.atoms),
        molecule_count=sum(len(indices) for indices in crystal.species_map.values()),
        framework_like=structure_is_framework_like(crystal, show_minor=show_minor),
    )
    summary = build_scope_summary(
        crystal,
        show_minor=show_minor,
        display_level=resolved_level,
    )
    counts = summary["visible_composition"]

    # ── Header ──────────────────────────────────────────────────────────
    lines.append("crystal:")
    lines.append(f"  formula: {terminal_text(summary['visible_formula'])}")
    if crystal.spacegroup:
        lines.append(f"  spacegroup: {terminal_text(crystal.spacegroup)}")
    lines.append(f"  n_atoms: {len(visible_atoms)}")
    lines.append(f"  source: {terminal_text(crystal.source_path)}")
    lines.append("")

    lines.append("observation:")
    lines.append(f"  display_mode: {summary['display_mode']}")
    lines.append(f"  display_level: {summary['display_level']}")
    for field in (
        "source_site_atom_count",
        "expanded_atom_count",
        "display_atom_count",
        "visible_atom_count",
        "visible_marker_count",
    ):
        value = summary[field]
        if value is not None:
            lines.append(f"  {field}: {value}")
    lines.append(f"  canonical_formula: {summary['canonical_formula']}")
    lines.append(f"  display_formula: {summary['display_formula']}")
    lines.append(f"  visible_formula: {summary['visible_formula']}")
    if resolved_level == "molecule" and structure_is_framework_like(
        crystal, show_minor=show_minor
    ):
        lines.append("  overview: framework_skeleton")
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

    # ── Scoped composition ──────────────────────────────────────────────
    lines.append("canonical_composition:")
    for elem, n in sorted(summary["canonical_composition"].items()):
        lines.append(f"  {elem}: {n}")
    lines.append("")

    lines.append("display_composition:")
    for elem, n in sorted(summary["display_composition"].items()):
        lines.append(f"  {elem}: {n}")
    lines.append("")

    if counts:
        lines.append("visible_composition:")
        for elem, n in sorted(counts.items()):
            lines.append(f"  {elem}: {n}")
        lines.append("")
        lines.append("composition:")
        for elem, n in sorted(counts.items()):
            lines.append(f"  {elem}: {n}")
        lines.append("")

    # ── Molecule overview ──────────────────────────────────────────────
    # A whole-cell atom dump is technically parseable but unusable in a
    # terminal.  Keep one compact row per species and a bounded atom sample;
    # callers can use the stateful session's select/focus actions to inspect
    # the omitted instances without asking for another giant payload.
    if len(visible_atoms) > STRUCTURED_ATOM_LIMIT:
        lines.append("molecules:")
        for species_id, molecule_indices in sorted(crystal.species_map.items()):
            groups = [
                [
                    atom
                    for atom in visible_atoms
                    if atom.molecule_index == molecule_index
                ]
                for molecule_index in molecule_indices
            ]
            sizes = [len(group) for group in groups if group]
            if sizes:
                size_text = str(sizes[0]) if len(set(sizes)) == 1 else "variable"
                lines.append(
                    f"  - species: {terminal_text(_display_species_name(species_id))}"
                )
                lines.append(f"    instances: {len(molecule_indices)}")
                lines.append(f"    atoms_per_instance: {size_text}")
        lines.append("")

    # ── Atom table ──────────────────────────────────────────────────────
    lines.append("atoms:")
    # Build neighbor map from bonds
    def _bond_stat_distance(bond) -> float:
        value = bond.minimum_image_distance
        return float(bond.distance if value is None else value)

    neighbors: dict[int, list[tuple[str, float]]] = {}
    for bond in crystal.bonds:
        if bond.i not in visible_indices or bond.j not in visible_indices:
            continue
        neighbors.setdefault(bond.i, []).append(
            (crystal.atoms[bond.j].element, _bond_stat_distance(bond))
        )
        neighbors.setdefault(bond.j, []).append(
            (crystal.atoms[bond.i].element, _bond_stat_distance(bond))
        )

    atom_rows = visible_atoms
    if len(visible_atoms) > STRUCTURED_ATOM_LIMIT:
        atom_rows = _representative_atoms(
            crystal,
            visible_atoms,
            limit=STRUCTURED_ATOM_LIMIT,
        )
        lines.append(
            f"  # sample of {len(atom_rows)} atoms; "
            f"{len(visible_atoms) - len(atom_rows)} omitted from this view"
        )

    for atom in atom_rows:
        lines.append(f"  - label: {terminal_text(atom.label)}")
        lines.append(f"    element: {terminal_text(atom.element)}")
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
            bond_groups.setdefault(key, []).append(_bond_stat_distance(bond))

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
            mono=True, label_mode="auto",
            show_bonds=True, show_cell=True,
            show_minor=show_minor,
            display_level=resolved_level,
        )
        lines.append("  art: |")
        for art_line in art.split("\n"):
            lines.append(f"    {art_line}")

    return "\n".join(lines)


def _display_species_name(species_id: str) -> str:
    """Hide the graph discriminator used by MolCrysKit in compact labels."""
    safe = terminal_text(species_id)
    base, separator, suffix = safe.rpartition("_")
    if separator and base and suffix.isdigit():
        return base
    return safe


def _representative_atoms(crystal, visible_atoms, *, limit: int):
    """Choose a stable, chemically diverse bounded atom sample."""
    selected = []
    selected_indices = set()
    # Include a small slice from the first displayed instance of every species.
    for species_id, molecule_indices in crystal.species_map.items():
        first_molecule = next(iter(molecule_indices), None)
        if first_molecule is None:
            continue
        for atom in visible_atoms:
            if atom.molecule_index == first_molecule:
                selected.append(atom)
                selected_indices.add(atom.index)
                if len(selected) >= limit:
                    return selected[:limit]
    # Fill any remaining slots in manifested order, which is deterministic.
    for atom in visible_atoms:
        if atom.index in selected_indices:
            continue
        selected.append(atom)
        if len(selected) >= limit:
            break
    return selected
