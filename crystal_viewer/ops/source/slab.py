from __future__ import annotations


def generate_slab_crystal(crystal, *args, **kwargs):
    """Return a real slab crystal through MolCrysKit's source-side operation."""
    from molcrys_kit.operations.surface import generate_topological_slab

    return generate_topological_slab(crystal, *args, **kwargs)


__all__ = ["generate_slab_crystal"]
