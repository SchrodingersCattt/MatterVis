from __future__ import annotations


def repeat_crystal(crystal, *, a: int = 1, b: int = 1, c: int = 1):
    """Return a source-side repeated crystal when MolCrysKit exposes it.

    The display-side repeat remains available as `ops.display.repeat` for fast
    visualization-only image replication.
    """
    if hasattr(crystal, "repeat"):
        return crystal.repeat((int(a), int(b), int(c)))
    raise NotImplementedError("source-side repeat requires a MolecularCrystal repeat API")


__all__ = ["repeat_crystal"]
