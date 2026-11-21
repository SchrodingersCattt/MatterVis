"""Phone-facing Analysis, Display, and Operate snapshots.

The GPU on the phone draws this payload. This module does not rasterize.

Every snapshot is a stateless transport payload with ``schema``
``mattervis.phone.snapshot.v1``. It is not an EffectSession
``StructureRef``. ``analyze_cif`` does not open a session and does not
set ``revision``. Commit helpers build a temporary ``EffectSession`` and
discard it before returning. That session's output revision is stored
only as ``provenance["effect_session_revision"]`` and cannot be passed
back as ``expected_revision``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

SNAPSHOT_SCHEMA = "mattervis.phone.snapshot.v1"
_COMMIT_OPERATION = "commit_disorder_candidate"
# CIF marks an ordered site with one of these disorder-group tokens.
_ORDERED_DISORDER_GROUPS = frozenset({"0", ".", ""})


def analyze_cif(path: str | Path) -> dict[str, Any]:
    """Read one CIF into an immutable transport snapshot.

    The result carries ``schema`` and ``source``. It has no ``revision``
    and is not a ``StructureRef`` a client can address.
    """
    crystal = _read(path)
    snapshot = _project(crystal)
    snapshot["source"] = str(path)
    return snapshot


def display(
    snapshot: dict[str, Any],
    *,
    disorder_group: str | int | None = None,
    disorder_selection: Mapping[str, str | int] | None = None,
) -> dict[str, Any]:
    """Change only the view. Occupancy stays the atom alpha.

    ``disorder_group`` is a global display filter. The same group id is
    applied to every assembly; it is not a choice of one assembly. CIF
    group numbers repeat across assemblies, so a precise view uses
    ``disorder_selection``, a mapping of assembly id to the group kept
    visible in that assembly (``{"A": 1, "B": 2}``). A site whose
    assembly is in the map is visible only when its group is the
    selected one or the site is ordered. Assemblies absent from the
    map stay fully visible, so a selection for assembly A does not hide
    assembly B. Ordered groups
    (``"0"``, ``"."``, ``""``) stay visible under either filter. Passing
    both arguments raises ``ValueError``.

    The request is recorded on ``display`` as ``disorder_group`` and
    ``disorder_selection`` (unused form is ``None``; group ids are
    strings). A global filter also sets ``disorder_group_scope`` to
    ``"global"``.
    """
    if disorder_group is not None and disorder_selection is not None:
        raise ValueError(
            "pass disorder_group or disorder_selection, not both; "
            "disorder_group is a global display filter"
        )
    selected = None if disorder_group is None else str(disorder_group)
    selection = _normalize_disorder_selection(disorder_selection)
    sites = []
    for site in snapshot["sites"]:
        item = dict(site)
        item["visible"] = _site_visible(site, selected=selected, selection=selection)
        item["alpha"] = float(site["occupancy"])
        sites.append(item)
    shown = dict(snapshot)
    shown["sites"] = sites
    shown["display"] = {
        "disorder_group": selected,
        "disorder_group_scope": "global" if selected is not None else None,
        "disorder_selection": selection,
    }
    return shown


def operate_commit_major(path: str | Path) -> dict[str, Any]:
    """Commit the major disorder component through a temporary EffectSession.

    The session is discarded before this returns. Its output revision is
    ``provenance["effect_session_revision"]`` and cannot be passed back
    as ``expected_revision``.
    """
    snapshot, _ = _commit_major(path)
    return snapshot


def operate_commit_major_with_cif(path: str | Path) -> dict[str, Any]:
    """Commit the major disorder component once and also export it as CIF.

    Returns ``{"snapshot": ..., "cif": ...}``. ``snapshot`` is exactly what
    :func:`operate_commit_major` returns. ``cif`` is MolCrysKit's public P1
    ``write_cif`` text for the same committed crystal object that produced
    the snapshot: one EffectSession operation, no second chemistry run, and
    no reconstruction from display coordinates. The session is discarded
    before this returns; ``provenance["effect_session_revision"]`` is not a
    ``StructureRef`` revision.
    """
    snapshot, committed = _commit_major(path)
    from molcrys_kit.io import write_cif

    text = write_cif(committed, representation="p1")
    if not isinstance(text, str):
        raise TypeError("MolCrysKit CIF writer returned non-text output")
    return {"snapshot": snapshot, "cif": text if text.endswith("\n") else text + "\n"}


def _commit_major(path: str | Path) -> tuple[dict[str, Any], Any]:
    """Run the single commit operation; return its snapshot and crystal."""
    from molcrys_kit.analysis.disorder.process import (
        generate_ordered_replicas_from_disordered_sites,
    )

    from mat_viewer.effects import EffectSession

    source = _read(path)
    replicas = generate_ordered_replicas_from_disordered_sites(
        str(path),
        method="optimal",
        generate_count=1,
        return_kept_indices=True,
    )
    if not replicas:
        raise ValueError("disorder generation produced no candidate")
    major, kept = replicas[0]
    session = EffectSession()
    ref = session.register(source, structure_id="phone")
    committed = session.operate(
        "commit_disorder_candidate",
        {
            "crystal": major,
            "kept_indices": [int(index) for index in kept],
            "method": "optimal",
        },
        structure_id=ref.id,
        expected_revision=ref.revision,
    )
    saved = session.checkout(committed.output_ref)
    snapshot = _stamp_commit(_project(saved), path, committed.output_ref.revision)
    return snapshot, saved


def _stamp_commit(
    snapshot: dict[str, Any],
    path: str | Path,
    effect_session_revision: int,
) -> dict[str, Any]:
    """Attach source and discarded-session provenance.

    ``effect_session_revision`` belongs to the temporary EffectSession
    that produced this snapshot. That session is discarded when the
    public call returns, so the number is not a StructureRef the client
    can pass back as ``expected_revision``.
    """
    snapshot.pop("revision", None)
    snapshot["source"] = str(path)
    snapshot["operation"] = _COMMIT_OPERATION
    snapshot["provenance"] = {
        "operation": _COMMIT_OPERATION,
        "effect_session_revision": int(effect_session_revision),
    }
    return snapshot


def _normalize_disorder_selection(
    selection: Mapping[str, str | int] | None,
) -> dict[str, str] | None:
    if selection is None:
        return None
    if not isinstance(selection, Mapping):
        raise TypeError("disorder_selection must map assembly id to disorder group")
    normalized: dict[str, str] = {}
    for assembly, group in selection.items():
        if not isinstance(assembly, str):
            raise TypeError("disorder_selection keys must be assembly ids")
        if isinstance(group, bool) or not isinstance(group, (str, int)):
            raise TypeError("disorder_selection values must be disorder group ids")
        normalized[assembly] = str(group)
    return normalized


def _site_visible(
    site: Mapping[str, Any],
    *,
    selected: str | None,
    selection: Mapping[str, str] | None,
) -> bool:
    group = str(site["disorder_group"])
    if group in _ORDERED_DISORDER_GROUPS:
        return True
    if selection is not None:
        chosen = selection.get(str(site["disorder_assembly"]))
        return chosen is None or group == chosen
    if selected is not None:
        return group == selected
    return True


def _read(path: str | Path, **kwargs):
    from molcrys_kit.io.cif import read_mol_crystal

    return read_mol_crystal(str(path), attach_chemistry=False, **kwargs)


def _project(crystal) -> dict[str, Any]:
    sites = []
    for record in crystal.get_site_records():
        occupancy = float(record.occupancy)
        sites.append(
            {
                "site_id": str(record.site_id),
                "symbol": str(record.symbol),
                "position": [float(value) for value in record.cartesian_position_A],
                # Canonical MolCrysKit SiteRecord provenance, passed through
                # unchanged. ``position`` is the contiguous-molecule Cartesian
                # site; ``fractional_position`` corresponds to it, and
                # ``image_shift`` is the lattice translation applied to make
                # the molecule contiguous. Unit-cell wrapping must use
                # ``fractional_position - floor(fractional_position)``;
                # ``image_shift`` is per-site provenance, not a general
                # whole-molecule or home-cell wrapping shortcut.
                "fractional_position": [float(value) for value in record.fractional_position],
                "image_shift": [int(value) for value in record.image_shift],
                "occupancy": occupancy,
                "alpha": occupancy,
                "disorder_group": str(record.disorder_group),
                "disorder_assembly": record.disorder_assembly or ".",
                "visible": True,
            }
        )
    bonds = [
        {
            "left": int(record.left_global_index),
            "right": int(record.right_global_index),
            "role": str(record.role),
            # Canonical MolCrysKit periodic-bond provenance, passed through
            # unchanged. A renderer must draw ``vector_A`` from ``left``;
            # right-site minus left-site is wrong for periodic-image bonds.
            "vector_A": [float(value) for value in record.vector_A],
            "right_image_shift": [int(value) for value in record.right_image_shift],
        }
        for record in crystal.get_bond_records()
    ]
    return {
        "schema": SNAPSHOT_SCHEMA,
        "cell": [[float(value) for value in row] for row in crystal.lattice],
        "sites": sites,
        "bonds": bonds,
    }
