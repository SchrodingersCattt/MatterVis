"""Lossless effect-session persistence and strict legacy migration.

Internal files store the scientific snapshot payload, not a CIF. CIF
export is a separate, lossy projection. Legacy scene files are copied
to a backup and rewritten as view configuration; migration does not
commit a structure.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
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

from .errors import MigrationError, PersistenceError, ValidationError
from .materialize import crystal_from_molecules
from .properties import normalize_properties
from .refs import StructureRef
from .session import EffectSession
from .snapshot import canonical_metadata, snapshot_from_crystal, to_jsonable

SCHEMA = "mattervis.effects/v1"
LEGACY_SCENE_VERSION = 1
# flock is per open-file description. A second descriptor in the thread
# that already holds the lock waits on itself, so re-entry is counted here.
_SESSION_LOCKS = threading.local()


@contextmanager
def session_file_lock(path: str) -> Iterator[None]:
    """Exclusive lock for one effect-session file.

    Every process that commits or flushes that file takes this lock
    first. The thread that already holds it may re-enter. The lock is
    released on the way out, including when the body raises.
    """

    key = os.path.abspath(path)
    held = getattr(_SESSION_LOCKS, "depths", None)
    if held is None:
        held = {}
        _SESSION_LOCKS.depths = held
    depth = int(held.get(key, 0))
    if depth:
        held[key] = depth + 1
        try:
            yield
        finally:
            held[key] = depth
        return
    directory = os.path.dirname(key)
    if directory:
        os.makedirs(directory, exist_ok=True)
    descriptor = os.open(key + ".lock", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        held[key] = 1
        try:
            yield
        finally:
            held[key] = 0
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
            held.pop(key, None)


def adopt_committed(memory: EffectSession, disk: EffectSession) -> bool:
    """Copy committed frames from ``disk`` when that file is ahead.

    Candidates stay in ``memory``. A frame is adopted only when the
    disk cursor, or the next revision at the same cursor, is ahead of
    memory. An older file does not replace a newer in-memory commit.
    """

    changed = False
    with memory._lock, disk._lock:
        adopted: list[tuple[str, str]] = []
        for key in set(disk._frames):
            disk_rank = (
                int(disk._cursor.get(key, -1)),
                int(disk._next_revision.get(key, 0)),
            )
            if key not in memory._frames:
                memory_rank = (-1, -1)
            else:
                memory_rank = (
                    int(memory._cursor.get(key, -1)),
                    int(memory._next_revision.get(key, 0)),
                )
            if disk_rank <= memory_rank:
                continue
            structure_id, frame_ref = key
            for revision_key in list(memory._viewer_names):
                if revision_key[0] == structure_id and revision_key[1] == frame_ref:
                    memory._viewer_names.pop(revision_key, None)
            memory._frames[key] = list(disk._frames[key])
            memory._cursor[key] = disk._cursor[key]
            memory._next_revision[key] = disk._next_revision[key]
            for revision_key, name in disk._viewer_names.items():
                if revision_key[0] == structure_id and revision_key[1] == frame_ref:
                    memory._viewer_names[revision_key] = name
            adopted.append(key)
            changed = True
        if adopted:
            adopted_keys = set(adopted)
            for request_key, result in list(memory._requests.items()):
                frame_key = (result.output_ref.id, result.output_ref.frame_ref)
                if frame_key in adopted_keys:
                    memory._requests.pop(request_key, None)
            for request_key, result in disk._requests.items():
                frame_key = (result.output_ref.id, result.output_ref.frame_ref)
                if frame_key in adopted_keys:
                    memory._requests[request_key] = result
    return changed


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


def _crystal_payload(crystal) -> dict[str, Any]:
    try:
        if getattr(crystal, "extra_arrays", None):
            to_jsonable(crystal.extra_arrays)
        chemistry = None if getattr(crystal, "chemistry", None) is None else to_jsonable(crystal.chemistry)
        disorder = None if getattr(crystal, "disorder_provenance", None) is None else to_jsonable(crystal.disorder_provenance)
        metadata = canonical_metadata(getattr(crystal, "metadata", None))
    except ValidationError as exc:
        raise PersistenceError(str(exc)) from exc
    moiety = getattr(crystal, "formula_moiety", None)
    if moiety is not None and not isinstance(moiety, str):
        raise PersistenceError("crystal.formula_moiety is not representable")
    molecules = []
    for molecule in crystal.molecules:
        try:
            info = to_jsonable(dict(getattr(molecule, "info", None) or {}))
            arrays = {
                str(name): to_jsonable(np.asarray(values).tolist())
                for name, values in molecule.arrays.items()
                if name not in {"positions", "numbers"}
            }
        except ValidationError as exc:
            raise PersistenceError(str(exc)) from exc
        molecules.append(
            {
                "symbols": list(molecule.get_chemical_symbols()),
                "positions": np.asarray(molecule.positions, dtype=float).tolist(),
                "info": info,
                "arrays": arrays,
                "graph_edges": _graph_edges_payload(molecule),
            }
        )
    body = {
        "lattice": np.asarray(crystal.lattice, dtype=float).tolist(),
        "pbc": [bool(value) for value in crystal.pbc],
        "molecules": molecules,
        "metadata": metadata,
        "chemistry": chemistry,
        "disorder_provenance": disorder,
    }
    if moiety is not None:
        body["formula_moiety"] = moiety
    if getattr(crystal, "extra_arrays", None):
        body["extra_arrays"] = to_jsonable(crystal.extra_arrays)
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
        arrays = molecule.get("arrays")
        if arrays is None:
            arrays = {
                KEY_OCCUPANCY: molecule["occupancy"],
                KEY_DISORDER_GROUP: molecule["disorder_group"],
                KEY_ASSEMBLY: molecule["assembly"],
                KEY_LABEL: molecule["label"],
                KEY_ATOM_ID: molecule["site_id"],
            }
            if "image_shift" in molecule:
                arrays["image_shift"] = molecule["image_shift"]
        for name, values in arrays.items():
            atoms.set_array(str(name), _array_from_json(values))
        if KEY_ATOM_ID not in atoms.arrays or len(atoms.arrays[KEY_ATOM_ID]) != count:
            raise PersistenceError("persisted site ids do not match atom count")
        wrapped = CrystalMolecule(atoms, check_pbc=False)
        wrapped.info.update(dict(molecule.get("info") or {}))
        if "graph_edges" in molecule:
            _restore_graph_edges(wrapped, molecule.get("graph_edges") or [])
        molecules.append(wrapped)
    crystal = crystal_from_molecules(
        lattice,
        molecules,
        pbc=pbc,
        metadata=canonical_metadata(payload.get("metadata")),
    )
    if payload.get("formula_moiety") is not None:
        crystal.formula_moiety = str(payload["formula_moiety"])
    _restore_json_field(crystal, "chemistry", payload.get("chemistry"))
    _restore_json_field(crystal, "disorder_provenance", payload.get("disorder_provenance"))
    if payload.get("extra_arrays"):
        _restore_json_field(crystal, "extra_arrays", payload.get("extra_arrays"))
    return crystal


def _restore_json_field(crystal, name: str, value) -> None:
    if value is None:
        return
    try:
        setattr(crystal, name, value)
    except AttributeError:
        setattr(crystal, f"_persisted_{name}_json", value)


def _graph_edges_payload(molecule) -> list[dict[str, Any]]:
    """Neighbor-list edges that a position rebuild does not reproduce.

    A periodic network can keep an edge whose finite embedding is longer
    than the contact perceived from the saved coordinates. The edge list
    is that graph, not a second bond heuristic.
    """

    graph = getattr(molecule, "graph", None)
    if graph is None:
        return []
    edges = []
    for left, right, data in graph.edges(data=True):
        item: dict[str, Any] = {"left": int(left), "right": int(right)}
        if data and "vector" in data:
            item["vector"] = [float(value) for value in np.asarray(data["vector"], dtype=float).tolist()]
        if data and "image_shift" in data:
            item["image_shift"] = [int(value) for value in np.asarray(data["image_shift"], dtype=int).tolist()]
        if data and "distance" in data:
            item["distance"] = float(data["distance"])
        edges.append(item)
    edges.sort(key=lambda item: (item["left"], item["right"], tuple(item.get("image_shift") or ())))
    return edges


def _restore_graph_edges(molecule, edges) -> None:
    graph = molecule.graph
    graph.remove_edges_from(list(graph.edges()))
    for item in edges:
        attrs = {}
        if "vector" in item:
            attrs["vector"] = np.asarray(item["vector"], dtype=float)
        if "image_shift" in item:
            attrs["image_shift"] = np.asarray(item["image_shift"], dtype=int)
        if "distance" in item:
            attrs["distance"] = float(item["distance"])
        graph.add_edge(int(item["left"]), int(item["right"]), **attrs)


def _array_from_json(values):
    array = np.asarray(values)
    if array.dtype.kind in {"U", "O"}:
        return np.array(values)
    return array


__all__ = [
    "SCHEMA",
    "adopt_committed",
    "export_structure",
    "load_session",
    "migrate_legacy_session",
    "save_session",
    "session_file_lock",
    "session_to_payload",
]
