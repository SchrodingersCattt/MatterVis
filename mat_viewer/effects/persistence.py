"""Lossless effect-session persistence and strict legacy migration.

Internal files store the scientific snapshot payload, not a CIF. CIF
export is a separate, lossy projection. Legacy scene files are copied
to a backup and rewritten as view configuration; migration does not
commit a structure.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from typing import Any

import numpy as np
from ase import Atoms
from molcrys_kit.constants.config import (
    KEY_ASSEMBLY,
    KEY_ATOM_ID,
    KEY_DISORDER_GROUP,
    KEY_LABEL,
    KEY_OCCUPANCY,
)
from molcrys_kit.structures.molecule import CrystalMolecule

from .errors import MigrationError, PersistenceError
from .materialize import crystal_from_molecules
from .properties import normalize_properties
from .refs import StructureRef
from .session import EffectSession
from .snapshot import canonical_metadata, snapshot_from_crystal

SCHEMA = "mattervis.effects/v1"
LEGACY_SCENE_VERSION = 1


def session_to_payload(session: EffectSession) -> dict[str, Any]:
    structures: dict[str, Any] = {}
    with session._lock:
        for (structure_id, frame_ref), revisions in session._frames.items():
            frame_key = f"{structure_id}\n{frame_ref}"
            structures[frame_key] = {
                "structure_id": structure_id,
                "frame_ref": frame_ref,
                "cursor": session._cursor[(structure_id, frame_ref)],
                "next_revision": session._next_revision.get(
                    (structure_id, frame_ref),
                    max(item.ref.revision for item in revisions) + 1,
                ),
                "revisions": [
                    _revision_payload(
                        item,
                        viewer_name=session._viewer_names.get(
                            (structure_id, frame_ref, item.ref.revision)
                        ),
                    )
                    for item in revisions
                ],
            }
        requests = {
            key: value.to_dict()
            for key, value in session._requests.items()
        }
    return {"schema": SCHEMA, "structures": structures, "requests": requests}


def save_session(session: EffectSession, path: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    payload = session_to_payload(session)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def load_session(path: str) -> EffectSession:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise PersistenceError(f"corrupt session file: {path}") from exc
    except OSError as exc:
        raise PersistenceError(f"cannot read session file: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise PersistenceError(f"unknown effect-session schema in {path}")
    session = EffectSession()
    for frame in payload.get("structures", {}).values():
        structure_id = str(frame["structure_id"])
        frame_ref = str(frame["frame_ref"])
        built = []
        for item in frame["revisions"]:
            crystal = _crystal_from_payload(item["crystal"])
            ref = StructureRef(structure_id, int(item["revision"]), frame_ref)
            properties = normalize_properties(item.get("properties"))
            provenance = dict(item.get("provenance") or {})
            snapshot = snapshot_from_crystal(crystal, ref=ref, properties=properties, provenance=provenance)
            if snapshot.fingerprint != item["fingerprint"]:
                raise PersistenceError(
                    f"revision {structure_id}@{item['revision']} does not reload to its stored fingerprint"
                )
            from .session import _Revision

            built.append(
                _Revision(
                    ref=ref,
                    crystal=crystal,
                    properties=properties,
                    provenance=provenance,
                    fingerprint=snapshot.fingerprint,
                    snapshot=snapshot,
                )
            )
        key = (structure_id, frame_ref)
        session._frames[key] = built
        session._cursor[key] = int(frame["cursor"])
        derived = max(item.ref.revision for item in built) + 1
        session._next_revision[key] = max(int(frame.get("next_revision") or derived), derived)
        for item, revision in zip(frame["revisions"], built):
            session._viewer_names[(revision.ref.id, revision.ref.frame_ref, revision.ref.revision)] = str(
                item.get("viewer_name") or f"{structure_id}@{revision.ref.revision}"
            )
    from .session import CommitResult

    for key, body in (payload.get("requests") or {}).items():
        if not isinstance(body, dict) or "output" not in body or "input" not in body:
            continue
        result = CommitResult(
            input_ref=StructureRef(body["input"]["id"], int(body["input"]["revision"]), body["input"].get("frame_ref", "0")),
            output_ref=StructureRef(body["output"]["id"], int(body["output"]["revision"]), body["output"].get("frame_ref", "0")),
            command=str(body.get("command") or ""),
            request_id=body.get("request_id"),
            candidate_id=str(body.get("candidate_id") or ""),
            mappings=list(body.get("mappings") or []),
            diagnostics=list(body.get("diagnostics") or []),
            warnings=list(body.get("property_warnings") or []),
            fingerprint=str(body.get("fingerprint") or ""),
            request_fingerprint=str(body.get("request_fingerprint") or ""),
            viewer_name=body.get("viewer_name"),
        )
        stored = session._frames.get((result.output_ref.id, result.output_ref.frame_ref), [])
        match = next((item for item in stored if item.ref.revision == result.output_ref.revision), None)
        if match is None or match.fingerprint != result.fingerprint:
            continue
        session._requests[str(key)] = result
    return session


def migrate_legacy_session(
    path: str,
    *,
    known_structures: set[str] | None = None,
    output_path: str | None = None,
) -> dict[str, Any]:
    """Migrate a legacy scene file into view configuration.

    The original file is copied to ``path + '.bak'`` once. A second call
    on the migrated output is a no-op and does not commit structures.
    """

    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise MigrationError(f"corrupt session file: {path}") from exc
    if not isinstance(payload, dict):
        raise MigrationError("session file must be a JSON object")
    schema = payload.get("schema")
    if schema in {SCHEMA, "mattervis.session-migration/v1"}:
        return {"status": "already-migrated", "structural_commits": 0, "path": path}
    if schema not in (None,):
        raise MigrationError(f"unknown schema: {schema}")
    if payload.get("version") not in (None, LEGACY_SCENE_VERSION) or "scenes" not in payload:
        raise MigrationError("unrecognized legacy session shape")
    scenes = payload.get("scenes")
    if not isinstance(scenes, list):
        raise MigrationError("legacy scenes must be a list")
    migrated_scenes = []
    for scene in scenes:
        if not isinstance(scene, dict):
            raise MigrationError("legacy scene entry is not an object")
        structure_name = str(scene.get("structure_name") or "")
        if not structure_name:
            raise MigrationError(f"scene {scene.get('id')!r} has no structure reference")
        if known_structures is not None and structure_name not in known_structures:
            raise MigrationError(f"unresolved structure reference: {structure_name}")
        state = dict(scene.get("state_patch") or {})
        migrated_scenes.append(
            {
                "id": scene.get("id"),
                "label": scene.get("label"),
                "structure_name": structure_name,
                "view": {
                    "transforms": state.get("transforms") or [],
                    "display_mode": state.get("display_mode"),
                    "display_options": state.get("display_options") or [],
                    "polyhedron_specs": state.get("polyhedron_specs") or [],
                    "disorder_resolve": state.get("disorder_resolve"),
                    "disorder_replicas": state.get("disorder_replicas") or [],
                    "camera": scene.get("camera"),
                    "atom_groups": state.get("atom_groups") or [],
                    "bond_groups": state.get("bond_groups") or [],
                },
            }
        )
    backup = path + ".bak"
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
    destination = output_path or path
    document = {
        "schema": "mattervis.session-migration/v1",
        "structural_commits": 0,
        "source_backup": os.path.basename(backup),
        "scenes": migrated_scenes,
    }
    temporary = destination + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, destination)
    return {"status": "migrated", "structural_commits": 0, "path": destination, "backup": backup, "scenes": len(migrated_scenes)}


def export_structure(snapshot: Mapping[str, Any] | Any, crystal, *, fmt: str) -> dict[str, Any]:
    """Export one explicit structure revision.

    ``internal`` is lossless. CIF warns when site properties or a
    non-periodic cluster cannot be fully represented.
    """

    from ..loader.cif_export import crystal_to_cif

    kind = str(fmt or "internal").lower()
    if kind == "internal":
        payload = snapshot.to_dict() if hasattr(snapshot, "to_dict") else dict(snapshot)
        return {"format": "internal", "text": json.dumps(payload, indent=2), "warnings": []}
    if kind != "cif":
        raise PersistenceError(f"unsupported structure export format: {fmt}")
    warnings = []
    properties = snapshot.properties if hasattr(snapshot, "properties") else ()
    if properties and any(properties):
        warnings.append("CIF export omits MatterVis site and structure property arrays; the internal snapshot keeps them")
    pbc = tuple(snapshot.pbc) if hasattr(snapshot, "pbc") else tuple(bool(value) for value in crystal.pbc)
    if pbc != (True, True, True):
        warnings.append(f"CIF export may not preserve PBC {list(pbc)}; the internal snapshot keeps the flags")
    text = crystal_to_cif(crystal)
    return {"format": "cif", "text": text, "warnings": warnings}


def _revision_payload(revision, viewer_name: str | None = None) -> dict[str, Any]:
    payload = {
        "revision": revision.ref.revision,
        "fingerprint": revision.fingerprint,
        "properties": revision.properties,
        "provenance": revision.provenance,
        "crystal": _crystal_payload(revision.crystal),
    }
    if viewer_name:
        payload["viewer_name"] = viewer_name
    return payload


_REPRESENTABLE_ARRAYS = {
    KEY_OCCUPANCY,
    KEY_DISORDER_GROUP,
    KEY_ASSEMBLY,
    KEY_LABEL,
    KEY_ATOM_ID,
    "numbers",
    "positions",
    "image_shift",
}


def _crystal_payload(crystal) -> dict[str, Any]:
    if getattr(crystal, "extra_arrays", None):
        raise PersistenceError("crystal.extra_arrays is not representable")
    if getattr(crystal, "chemistry", None) not in (None, {}, []):
        raise PersistenceError("crystal.chemistry is not representable")
    if getattr(crystal, "disorder_provenance", None) not in (None, {}, []):
        raise PersistenceError("crystal.disorder_provenance is not representable")
    moiety = getattr(crystal, "formula_moiety", None)
    if moiety is not None and not isinstance(moiety, str):
        raise PersistenceError("crystal.formula_moiety is not representable")
    molecules = []
    for molecule in crystal.molecules:
        info = getattr(molecule, "info", None) or {}
        if info:
            names = ", ".join(sorted(str(key) for key in info))
            raise PersistenceError(f"crystal molecule info is not representable: {names}")
        unknown = sorted(set(molecule.arrays) - _REPRESENTABLE_ARRAYS)
        if unknown:
            raise PersistenceError("crystal site array is not representable: " + ", ".join(unknown))
        payload = {
            "symbols": list(molecule.get_chemical_symbols()),
            "positions": np.asarray(molecule.positions, dtype=float).tolist(),
            "occupancy": [float(value) for value in molecule.arrays[KEY_OCCUPANCY]],
            "disorder_group": [int(value) for value in molecule.arrays[KEY_DISORDER_GROUP]],
            "assembly": [str(value) for value in molecule.arrays[KEY_ASSEMBLY]],
            "label": [str(value) for value in molecule.arrays[KEY_LABEL]],
            "site_id": [str(value) for value in molecule.arrays[KEY_ATOM_ID]],
        }
        if "image_shift" in molecule.arrays:
            payload["image_shift"] = np.asarray(molecule.arrays["image_shift"]).tolist()
        molecules.append(payload)
    body = {
        "lattice": np.asarray(crystal.lattice, dtype=float).tolist(),
        "pbc": [bool(value) for value in crystal.pbc],
        "molecules": molecules,
        "metadata": canonical_metadata(getattr(crystal, "metadata", None)),
    }
    if moiety is not None:
        body["formula_moiety"] = moiety
    return body


def _crystal_from_payload(payload: Mapping[str, Any]):
    lattice = np.asarray(payload["lattice"], dtype=float)
    pbc = tuple(bool(value) for value in payload["pbc"])
    molecules = []
    for molecule in payload["molecules"]:
        atoms = Atoms(
            symbols=list(molecule["symbols"]),
            positions=np.asarray(molecule["positions"], dtype=float),
            cell=lattice,
            pbc=list(pbc),
        )
        count = len(atoms)
        atoms.set_array(KEY_OCCUPANCY, np.asarray(molecule["occupancy"], dtype=float))
        atoms.set_array(KEY_DISORDER_GROUP, np.asarray(molecule["disorder_group"], dtype=int))
        atoms.set_array(KEY_ASSEMBLY, np.array(molecule["assembly"]))
        atoms.set_array(KEY_LABEL, np.array(molecule["label"]))
        atoms.set_array(KEY_ATOM_ID, np.array(molecule["site_id"], dtype=str))
        if "image_shift" in molecule:
            atoms.set_array("image_shift", np.asarray(molecule["image_shift"]))
        if len(atoms.arrays[KEY_ATOM_ID]) != count:
            raise PersistenceError("persisted site ids do not match atom count")
        molecules.append(CrystalMolecule(atoms, check_pbc=False))
    crystal = crystal_from_molecules(
        lattice,
        molecules,
        pbc=pbc,
        metadata=canonical_metadata(payload.get("metadata")),
    )
    if payload.get("formula_moiety") is not None:
        crystal.formula_moiety = str(payload["formula_moiety"])
    return crystal


__all__ = [
    "SCHEMA",
    "export_structure",
    "load_session",
    "migrate_legacy_session",
    "save_session",
    "session_to_payload",
]
