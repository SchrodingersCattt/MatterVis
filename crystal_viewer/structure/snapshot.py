"""Reverse loader bridge: convert rendered scene snapshots to real structures.

This module owns future scene-dict-to-`MolecularCrystal` conversion. Display
operations can remain cheap scene mutations while callers still have a named
place to promote a displayed snapshot into a source-side structure that can be
saved or fed back through `structure.loader`.
"""
from __future__ import annotations

from typing import Any


def molecular_crystal_from_scene(scene: dict[str, Any]):
    """Return a new `MolecularCrystal` represented by a rendered scene.

    The full conversion needs MolCrysKit writer support and provenance mapping
    for transformed scene atoms, so this contract is intentionally explicit
    until that bridge is implemented.
    """
    raise NotImplementedError("scene -> MolecularCrystal snapshot conversion is not implemented yet")


__all__ = ["molecular_crystal_from_scene"]
