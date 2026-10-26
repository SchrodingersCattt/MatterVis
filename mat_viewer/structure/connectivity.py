"""Read MolCrysKit bond role and connectivity regime for display.

MatterVis does not classify elements. These helpers only copy the public
fields ``BondRecord.role`` and ``MolecularCrystal.get_connectivity_report()``.
"""

from __future__ import annotations


def bond_role(record) -> str:
    """Return ``covalent`` or ``coordination`` from one neighbor-list edge.

    Records built before ``role`` existed, and test doubles, stay on the
    covalent default BondRecord uses.
    """
    role = getattr(record, "role", None)
    if role in {"covalent", "coordination"}:
        return str(role)
    return "covalent"


def connectivity_fields(crystal) -> tuple[str | None, int | None]:
    """Return ``(regime, max_rank)`` when the crystal exposes the report."""
    getter = getattr(crystal, "get_connectivity_report", None)
    if not callable(getter):
        return None, None
    report = getter()
    regime = getattr(report, "regime", None)
    rank = getattr(report, "max_rank", None)
    if regime not in {"molecular", "extended"}:
        return None, None
    try:
        rank_value = int(rank)
    except (TypeError, ValueError):
        return str(regime), None
    return str(regime), rank_value
