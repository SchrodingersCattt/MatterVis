"""Named structure representations.

A representation chooses geometry. It is independent of ``style["material"]``
(``mesh`` versus the ``flat`` billboard technique) and of ``RenderSpec.shading``
(``smooth`` versus per-face ``flat`` triangle shading). ``flat_illustration``
is a representation. It is not ``material="flat"`` and not ``shading="flat"``.
"""

from __future__ import annotations

# Stable public ids. ``ball`` and ``space_filling`` stay on the compact
# display-radius table. ``vdw`` is a separate id and a separate radius source.
REPRESENTATIONS = frozenset(
    {
        "ball_stick",
        "ball",
        "stick",
        "space_filling",
        "ortep",
        "wireframe",
        "vdw",
        "flat_illustration",
    }
)

# Compact display radii (``colors.ATOM_RADIUS``), not van der Waals radii.
COMPACT_DISPLAY_REPRESENTATIONS = frozenset({"ball", "space_filling"})

# Sphere-only drawings. They do not stroke bonds and do not add the
# cross-boundary bond-endpoint images that stick styles keep.
SPHERE_ONLY_REPRESENTATIONS = frozenset({"ball", "space_filling", "vdw"})

BOND_CAPABLE_REPRESENTATIONS = frozenset(
    {
        "ball_stick",
        "ortep",
        "stick",
        "wireframe",
        "flat_illustration",
    }
)


def normalize_representation(value: object) -> str:
    """Return the canonical representation id."""
    return str(value).strip().lower().replace("-", "_")


def bond_stroke_for(first: str, second: str) -> str | None:
    """Return the bond stroke for a pair of atom representations.

    ``None`` means the pair does not draw a bond. ``flat_illustration``
    uses the same line stroke as wireframe. It does not select
    ``material="flat"``.
    """
    if (
        first not in BOND_CAPABLE_REPRESENTATIONS
        or second not in BOND_CAPABLE_REPRESENTATIONS
    ):
        return None
    if first == second == "wireframe":
        return "wireframe"
    if first == second == "flat_illustration":
        return "flat_illustration"
    return "stick"


def illustration_sphere_metadata(metadata: dict) -> dict:
    """Mark one sphere unlit without clearing vertex normals."""
    return {
        **metadata,
        "_raster_ambient": 1.0,
        "_raster_diffuse": 0.0,
        "_raster_specular": 0.0,
    }


__all__ = [
    "BOND_CAPABLE_REPRESENTATIONS",
    "COMPACT_DISPLAY_REPRESENTATIONS",
    "REPRESENTATIONS",
    "SPHERE_ONLY_REPRESENTATIONS",
    "bond_stroke_for",
    "illustration_sphere_metadata",
    "normalize_representation",
]
