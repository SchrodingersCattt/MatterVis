from __future__ import annotations


def expand_crystal_by_symmetry(crystal, *args, **kwargs):
    """Return a source-side symmetry-expanded crystal when upstream supports it."""
    method = getattr(crystal, "expand_by_symmetry", None)
    if method is None:
        raise NotImplementedError("source-side symmetry expansion requires a MolecularCrystal API")
    return method(*args, **kwargs)


__all__ = ["expand_crystal_by_symmetry"]
