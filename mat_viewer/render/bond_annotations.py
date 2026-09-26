"""Compile display-only bond annotations without changing scene connectivity."""

from __future__ import annotations

from dataclasses import fields
from typing import Any, Mapping

from .contracts import BondStyle, LinePrimitive
from .geometry import bond_line_primitives


def bond_annotation_primitives(entries: Any) -> tuple[LinePrimitive, ...]:
    """Draw optional world-space lines separate from chemical ``scene['bonds']``.

    Entries require ``id``, ``start``, and ``end``; ``style`` accepts a
    :class:`BondStyle` or a mapping of its fields. ``atom_indices`` is optional
    provenance and never adds a graph edge.
    """
    primitives: list[LinePrimitive] = []
    for entry in entries or ():
        if not isinstance(entry, Mapping):
            raise TypeError("bond annotation must be a mapping")
        identifier = str(entry["id"])
        if not identifier:
            raise ValueError("bond annotation id must be nonempty")
        raw_style = entry.get("style", BondStyle())
        if isinstance(raw_style, BondStyle):
            style = raw_style
        elif isinstance(raw_style, Mapping):
            fields_allowed = {field.name for field in fields(BondStyle)}
            unknown = set(raw_style) - fields_allowed
            if unknown:
                raise ValueError(f"unknown BondStyle fields: {sorted(unknown)}")
            style = BondStyle(**raw_style)
        else:
            raise TypeError("bond annotation style must be BondStyle or a mapping")
        atoms = tuple(int(atom) for atom in entry.get("atom_indices", ()))
        if atoms and len(atoms) != 2:
            raise ValueError("bond annotation atom_indices must contain two atoms")
        primitives.extend(
            bond_line_primitives(
                f"bond-annotation:{identifier}",
                entry["start"],
                entry["end"],
                style.width_px,
                style.color,
                alpha=style.opacity,
                dash=style.dash,
                depth_test=style.depth_test,
                metadata={
                    "kind": "bond_annotation",
                    "display_only": True,
                    "atom_indices": list(atoms),
                },
            )
        )
    return tuple(primitives)
