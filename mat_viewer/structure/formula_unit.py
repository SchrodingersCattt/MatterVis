"""Compatibility facade for MolCrysKit formula-unit selection.

MatterVis intentionally owns no molecule clustering or stoichiometric
heuristics. The public compatibility function below materialises the
``FormulaUnitSelection`` returned by MolCrysKit and preserves the historical
``(atoms, indices)`` return shape for older callers.
"""

from __future__ import annotations

from . import molcrys_bridge


def select_formula_unit(atoms, M, cell=None, *, analysis=None):
    """Return MolCrysKit's compact formula unit in the legacy tuple shape."""
    del cell  # Cell chemistry is represented by ``M`` and the MCK analysis.
    selected = molcrys_bridge.select_formula_unit(atoms, M, analysis=analysis)
    return selected, list(range(len(selected)))


__all__ = ["select_formula_unit"]
