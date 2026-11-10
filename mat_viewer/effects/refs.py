"""Identity tokens for authoritative structures and display instances.

A displayed periodic copy is not a source atom. ``SiteRef`` names a site
inside one committed revision. ``DisplayInstanceRef`` adds the periodic
image and a display-instance id that must never be written back as a
source primary key.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StructureRef:
    """Authoritative scientific coordinate: structure, revision, and frame."""

    id: str
    revision: int
    frame_ref: str = "0"

    def __post_init__(self) -> None:
        if not str(self.id).strip():
            raise ValueError("structure id is required")
        if int(self.revision) < 1:
            raise ValueError("structure revision starts at 1")
        object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "revision", int(self.revision))
        object.__setattr__(self, "frame_ref", str(self.frame_ref))

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "revision": self.revision, "frame_ref": self.frame_ref}


@dataclass(frozen=True, slots=True)
class SiteRef:
    """One authoritative site inside a structure revision."""

    structure_id: str
    revision: int
    site_id: str
    frame_ref: str = "0"

    def to_dict(self) -> dict[str, object]:
        return {
            "structure_id": self.structure_id,
            "revision": self.revision,
            "frame_ref": self.frame_ref,
            "site_id": self.site_id,
        }


@dataclass(frozen=True, slots=True)
class DisplayInstanceRef:
    """One drawn periodic instance of a source site."""

    structure_id: str
    revision: int
    site_id: str
    image: tuple[int, int, int]
    instance_id: str
    frame_ref: str = "0"
    symmetry_op: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "structure_id": self.structure_id,
            "revision": self.revision,
            "frame_ref": self.frame_ref,
            "site_id": self.site_id,
            "image": list(self.image),
            "instance_id": self.instance_id,
            "symmetry_op": self.symmetry_op,
        }


def parse_image(value) -> tuple[int, int, int]:
    """Return an integer lattice image, defaulting a missing value to zero."""

    if value in (None, ""):
        return (0, 0, 0)
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("image shift must be three integers")
    try:
        image = tuple(int(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ValueError("image shift must be three integers") from exc
    return image  # type: ignore[return-value]


__all__ = [
    "DisplayInstanceRef",
    "SiteRef",
    "StructureRef",
    "parse_image",
]
