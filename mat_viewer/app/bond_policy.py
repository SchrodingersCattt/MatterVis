"""Apply global bond policy changes to already loaded backend bundles."""

from __future__ import annotations

from typing import Any


def reload_bond_policy(backend: Any, config: Any) -> None:
    """Rebuild every populated bundle from its canonical atom/cell payload.

    A config reload must update catalog, uploaded, and directly supplied
    structures alike.  Their cached MolCrysKit analysis owns the bond graph,
    so clearing scene caches alone leaves stale connectivity behind.
    """
    from ..loader.bundle_builder import build_loaded_crystal_from_atoms
    from ..structure.bonds import normalize_bond_thresholds, validate_bond_scale

    configured = config.mck_overrides
    bond_scale = configured.get("bond_scale")
    if bond_scale is not None:
        bond_scale = validate_bond_scale(bond_scale)
    configured_thresholds = configured.get("bond_thresholds")
    bond_thresholds = (
        normalize_bond_thresholds(configured_thresholds)
        if configured_thresholds
        else None
    )

    with backend._bundle_lock:
        for name, bundle in list(backend.bundles.items()):
            # Pending/placeholder bundles have no chemistry to rebuild. Their
            # derived caches still need clearing to avoid stale figures.
            if not getattr(bundle, "raw_atoms", None) or getattr(
                bundle, "M", None
            ) is None or getattr(bundle, "cell", None) is None:
                bundle.scene_cache.clear()
                bundle.fragment_table_cache.clear()
                getattr(bundle, "_transformed_scene_cache", {}).clear()
                continue

            # Reuse parsed atoms instead of re-reading a CIF. This covers
            # direct/in-memory and uploaded bundles whose path is provenance.
            rebuilt = build_loaded_crystal_from_atoms(
                name=bundle.name,
                source_path=bundle.cif_path,
                raw_atoms=bundle.raw_atoms,
                cell=bundle.cell,
                M=bundle.M,
                title=bundle.title,
                preset=backend.preset,
                source=bundle.source,
                bond_scale=bond_scale,
                bond_thresholds=bond_thresholds,
            )

            # Preserve format-adapter payload while replacing all chemistry
            # and scene-derived fields in place for active scene references.
            old_values = dict(vars(bundle))
            bundle.__dict__.update(vars(rebuilt))
            for key in (
                "cube_data",
                "frame_info",
                "atom_arrays",
                "pymatgen_structure",
            ):
                if key in old_values and old_values[key] is not None:
                    bundle.__dict__[key] = old_values[key]
            for key, value in old_values.items():
                if key.startswith("_") and key not in bundle.__dict__:
                    bundle.__dict__[key] = value
    backend._invalidate_figure_cache()
    backend._bump_version()


__all__ = ["reload_bond_policy"]
