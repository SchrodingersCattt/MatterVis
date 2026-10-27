"""Single scientific session: snapshots, candidates, and atomic commits.

Display and Analysis receive snapshots. Only ``commit`` advances a
structure revision. Candidates stay out of the committed history until
that boundary succeeds.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from threading import RLock
from typing import Any
from uuid import uuid4

from .analysis import AnalysisResult, analyze, canonical_query, query_key
from .errors import (
    InjectedFailure,
    OperationRejected,
    RevisionConflict,
    ValidationError,
)
from .operations import IMPLEMENTED_COMMANDS, run_operation
from .properties import apply_property_policy, normalize_properties
from .refs import StructureRef
from .snapshot import ScientificSnapshot, snapshot_from_crystal


@dataclass
class _Revision:
    ref: StructureRef
    crystal: Any
    properties: dict[str, Any]
    provenance: dict[str, Any]
    fingerprint: str
    snapshot: ScientificSnapshot


@dataclass
class Candidate:
    candidate_id: str
    command: str
    input_ref: StructureRef
    request_id: str | None
    status: str
    mappings: list[dict[str, Any]]
    diagnostics: list[str]
    provenance: dict[str, Any]
    fingerprint: str
    crystal: Any
    properties: dict[str, Any]
    index_map: list[int | None]
    invalid_structure_fields: set[str]
    property_policy: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "command": self.command,
            "input": self.input_ref.to_dict(),
            "request_id": self.request_id,
            "status": self.status,
            "mappings": self.mappings,
            "diagnostics": list(self.diagnostics),
            "provenance": self.provenance,
            "fingerprint": self.fingerprint,
            "n_sites": int(sum(len(molecule) for molecule in self.crystal.molecules)),
        }


@dataclass
class CommitResult:
    input_ref: StructureRef
    output_ref: StructureRef
    command: str
    request_id: str | None
    candidate_id: str
    mappings: list[dict[str, Any]]
    diagnostics: list[str]
    warnings: list[dict[str, str]]
    fingerprint: str
    idempotent_replay: bool = False
    viewer_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "committed",
            "command": self.command,
            "input": self.input_ref.to_dict(),
            "output": self.output_ref.to_dict(),
            "request_id": self.request_id,
            "candidate_id": self.candidate_id,
            "mappings": self.mappings,
            "diagnostics": list(self.diagnostics),
            "property_warnings": list(self.warnings),
            "fingerprint": self.fingerprint,
            "idempotent_replay": self.idempotent_replay,
            "viewer_name": self.viewer_name,
        }


@dataclass
class Counters:
    structure_builds: int = 0
    scientific_computations: int = 0
    cache_hits: int = 0
    commits: int = 0
    rejected_commits: int = 0
    display_edits: int = 0


@dataclass
class EffectSession:
    """Authoritative structure revisions and their effect services."""

    counters: Counters = field(default_factory=Counters)
    _lock: RLock = field(default_factory=RLock)
    _frames: dict[tuple[str, str], list[_Revision]] = field(default_factory=dict)
    _cursor: dict[tuple[str, str], int] = field(default_factory=dict)
    _candidates: dict[str, Candidate] = field(default_factory=dict)
    _requests: dict[str, CommitResult] = field(default_factory=dict)
    _results: dict[str, AnalysisResult] = field(default_factory=dict)
    _analysis_cache: dict[str, str] = field(default_factory=dict)
    _viewer_names: dict[tuple[str, str, int], str] = field(default_factory=dict)

    def register(
        self,
        crystal,
        *,
        structure_id: str,
        frame_ref: str | int | None = "0",
        properties: Mapping[str, Any] | None = None,
        provenance: Mapping[str, Any] | None = None,
        viewer_name: str | None = None,
    ) -> StructureRef:
        """Register an imported crystal as revision 1 without copying later edits into it."""

        with self._lock:
            key = (str(structure_id), str(frame_ref))
            if key in self._frames:
                return self._frames[key][self._cursor[key]].ref
            ref = StructureRef(structure_id, 1, str(frame_ref))
            revision = self._make_revision(
                crystal.copy(),
                ref=ref,
                properties=normalize_properties(properties),
                provenance={"origin": "import", **dict(provenance or {})},
            )
            self._frames[key] = [revision]
            self._cursor[key] = 0
            self._viewer_names[(ref.id, ref.frame_ref, ref.revision)] = viewer_name or ref.id
            self.counters.structure_builds += 1
            return ref

    def has(self, structure_id: str, frame_ref: str | int | None = "0") -> bool:
        return (str(structure_id), str(frame_ref)) in self._frames

    def current_ref(self, structure_id: str, frame_ref: str | int | None = "0") -> StructureRef:
        with self._lock:
            return self._current(structure_id, frame_ref).ref

    def snapshot(self, ref: StructureRef | None = None, *, structure_id: str | None = None, frame_ref: str | int | None = None) -> ScientificSnapshot:
        with self._lock:
            revision = self._resolve(ref, structure_id=structure_id, frame_ref=frame_ref)
            return revision.snapshot

    def checkout(self, ref: StructureRef):
        """Return a private crystal copy. Callers cannot alias the stored object."""

        with self._lock:
            return self._resolve(ref).crystal.copy()

    def fingerprint(self, ref: StructureRef | None = None, *, structure_id: str | None = None, frame_ref: str | int | None = None) -> str:
        return self.snapshot(ref, structure_id=structure_id, frame_ref=frame_ref).fingerprint

    def note_display_edit(self) -> None:
        """Record a view-only edit. This cannot advance a structure revision."""

        with self._lock:
            self.counters.display_edits += 1

    def analyze(self, ref: StructureRef, query: Mapping[str, Any]) -> AnalysisResult:
        with self._lock:
            revision = self._resolve(ref)
            query = canonical_query(query)
            key = f"{revision.ref.revision}:{query_key(revision.snapshot, query)}"
            cached = self._analysis_cache.get(key)
            if cached is not None:
                self.counters.cache_hits += 1
                return self._results[cached]
            self.counters.scientific_computations += 1
            result = analyze(revision.snapshot, query, result_id=f"analysis-{uuid4().hex[:12]}")
            self._results[result.result_id] = result
            self._analysis_cache[key] = result.result_id
            return result

    def result(self, result_id: str) -> AnalysisResult:
        with self._lock:
            try:
                return self._results[result_id]
            except KeyError as exc:
                raise KeyError(result_id) from exc

    def bind_result(self, result: AnalysisResult, ref: StructureRef) -> AnalysisResult:
        """Refuse to attach a result computed for a different revision or frame."""

        if result.ref.id != ref.id or result.ref.revision != ref.revision or result.ref.frame_ref != ref.frame_ref:
            raise RevisionConflict(
                "analysis result "
                f"{result.ref.id}@{result.ref.revision} frame {result.ref.frame_ref} "
                f"cannot bind to {ref.id}@{ref.revision} frame {ref.frame_ref}"
            )
        return result

    def preview(self, command: str, params: Mapping[str, Any] | None = None, **kwargs) -> Candidate:
        return self.operate(command, params, preview=True, **kwargs)

    def operate(
        self,
        command: str,
        params: Mapping[str, Any] | None = None,
        *,
        structure_id: str,
        expected_revision: int,
        frame_ref: str | int | None = "0",
        request_id: str | None = None,
        preview: bool = False,
        property_policy: Mapping[str, Any] | None = None,
        inject_failure: str | None = None,
        viewer_name: str | None = None,
    ) -> Candidate | CommitResult:
        with self._lock:
            if request_id and not preview and request_id in self._requests and inject_failure is None:
                replay = self._requests[request_id]
                replay.idempotent_replay = True
                self._restore_request(replay)
                return replay
            if inject_failure == "validate":
                self.counters.rejected_commits += 1
                raise InjectedFailure("injected validation failure")
            current = self._current(structure_id, frame_ref)
            if int(expected_revision) != current.ref.revision:
                self.counters.rejected_commits += 1
                raise RevisionConflict(
                    f"expected revision {expected_revision} but {current.ref.id} is at {current.ref.revision}"
                )
            if inject_failure == "construct":
                self.counters.rejected_commits += 1
                raise InjectedFailure("injected construction failure")
            outcome = run_operation(command, current.crystal, params or {})
            transformed, warnings = apply_property_policy(
                current.properties,
                property_policy,
                command=command,
                index_map=outcome.index_map,
                invalid_structure_fields=set(outcome.invalid_structure_fields),
            )
            candidate = Candidate(
                candidate_id=f"cand-{uuid4().hex[:12]}",
                command=command,
                input_ref=current.ref,
                request_id=request_id,
                status="preview" if preview else "ready",
                mappings=outcome.mappings,
                diagnostics=list(outcome.diagnostics),
                provenance=dict(outcome.provenance),
                fingerprint="",
                crystal=outcome.crystal,
                properties=transformed,
                index_map=list(outcome.index_map),
                invalid_structure_fields=set(outcome.invalid_structure_fields),
                property_policy=dict(property_policy or {}),
            )
            probe = snapshot_from_crystal(
                candidate.crystal,
                ref=StructureRef(current.ref.id, current.ref.revision, current.ref.frame_ref),
                properties=transformed,
                provenance=candidate.provenance,
            )
            candidate.fingerprint = probe.fingerprint
            self._candidates[candidate.candidate_id] = candidate
            if preview or inject_failure == "cancel":
                if inject_failure == "cancel":
                    candidate.status = "cancelled"
                return candidate
            if candidate.status == "cancelled":
                raise OperationRejected("cancelled candidates cannot be committed")
            return self._commit_locked(candidate, warnings=list(warnings), inject_failure=inject_failure, viewer_name=viewer_name)

    def commit_candidate(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        request_id: str | None = None,
        inject_failure: str | None = None,
        viewer_name: str | None = None,
    ) -> CommitResult:
        with self._lock:
            candidate = self._candidates.get(candidate_id)
            if candidate is None:
                raise KeyError(candidate_id)
            if candidate.status == "cancelled":
                raise OperationRejected("cancelled candidates cannot be committed")
            if candidate.status == "committed" and candidate.request_id and candidate.request_id in self._requests:
                replay = self._requests[candidate.request_id]
                replay.idempotent_replay = True
                return replay
            current = self._current(candidate.input_ref.id, candidate.input_ref.frame_ref)
            if current.ref.revision != candidate.input_ref.revision or int(expected_revision) != current.ref.revision:
                self.counters.rejected_commits += 1
                raise RevisionConflict(
                    f"candidate {candidate_id} was built at revision {candidate.input_ref.revision} "
                    f"but the structure is now at {current.ref.revision}"
                )
            if request_id:
                candidate.request_id = request_id
            return self._commit_locked(candidate, warnings=[], inject_failure=inject_failure, viewer_name=viewer_name)

    def cancel(self, candidate_id: str) -> Candidate:
        with self._lock:
            candidate = self._candidates[candidate_id]
            candidate.status = "cancelled"
            return candidate

    def undo(self, structure_id: str, frame_ref: str | int | None = "0") -> StructureRef:
        with self._lock:
            key = (str(structure_id), str(frame_ref))
            cursor = self._cursor[key]
            if cursor <= 0:
                raise ValidationError("no structural operation to undo")
            self._cursor[key] = cursor - 1
            return self._frames[key][self._cursor[key]].ref

    def redo(self, structure_id: str, frame_ref: str | int | None = "0") -> StructureRef:
        with self._lock:
            key = (str(structure_id), str(frame_ref))
            cursor = self._cursor[key]
            if cursor + 1 >= len(self._frames[key]):
                raise ValidationError("no structural operation to redo")
            self._cursor[key] = cursor + 1
            return self._frames[key][self._cursor[key]].ref

    def revisions(self, structure_id: str, frame_ref: str | int | None = "0") -> list[StructureRef]:
        with self._lock:
            key = (str(structure_id), str(frame_ref))
            limit = self._cursor[key] + 1
            return [item.ref for item in self._frames[key][:limit]]

    def viewer_name(self, ref: StructureRef) -> str:
        return self._viewer_names.get((ref.id, ref.frame_ref, ref.revision), ref.id)

    def capabilities(self) -> dict[str, Any]:
        return {
            "schema": "mattervis.operate/v1",
            "commands": [
                {"name": name, "implemented": True, "effect": "operate"}
                for name in IMPLEMENTED_COMMANDS
            ],
            "workflows": [
                {
                    "name": "delete_periodic_copy",
                    "implemented": True,
                    "steps": ["build_supercell", "delete_sites"],
                    "commits": 1,
                }
            ],
            "aliases_rejected": ["operate supercell", "supercell"],
        }

    def _commit_locked(self, candidate: Candidate, *, warnings, inject_failure, viewer_name) -> CommitResult:
        if inject_failure == "persist":
            self.counters.rejected_commits += 1
            raise InjectedFailure("injected persistence failure")
        if inject_failure == "commit":
            self.counters.rejected_commits += 1
            raise InjectedFailure("injected commit-boundary failure")
        key = (candidate.input_ref.id, candidate.input_ref.frame_ref)
        current = self._frames[key][self._cursor[key]]
        if current.ref != candidate.input_ref:
            self.counters.rejected_commits += 1
            raise RevisionConflict("structure revision changed before commit")
        # Drop redo entries so a new commit cannot fork an invisible branch.
        del self._frames[key][self._cursor[key] + 1 :]
        next_revision = current.ref.revision + 1
        ref = StructureRef(current.ref.id, next_revision, current.ref.frame_ref)
        provenance = {
            "command": candidate.command,
            "parent": current.ref.to_dict(),
            "candidate_id": candidate.candidate_id,
            "request_id": candidate.request_id,
            **candidate.provenance,
        }
        revision = self._make_revision(
            candidate.crystal.copy(),
            ref=ref,
            properties=candidate.properties,
            provenance=provenance,
        )
        self._frames[key].append(revision)
        self._cursor[key] = len(self._frames[key]) - 1
        name = viewer_name or f"{ref.id}@{ref.revision}"
        self._viewer_names[(ref.id, ref.frame_ref, ref.revision)] = name
        candidate.status = "committed"
        result = CommitResult(
            input_ref=candidate.input_ref,
            output_ref=ref,
            command=candidate.command,
            request_id=candidate.request_id,
            candidate_id=candidate.candidate_id,
            mappings=candidate.mappings,
            diagnostics=list(candidate.diagnostics),
            warnings=list(warnings),
            fingerprint=revision.fingerprint,
            viewer_name=name,
        )
        if candidate.request_id:
            self._requests[candidate.request_id] = result
        self.counters.commits += 1
        self.counters.structure_builds += 1
        return result

    def _restore_request(self, result: CommitResult) -> None:
        key = (result.output_ref.id, result.output_ref.frame_ref)
        for index, revision in enumerate(self._frames.get(key, ())):
            if revision.ref.revision == result.output_ref.revision and revision.fingerprint == result.fingerprint:
                self._cursor[key] = index
                return

    def _current(self, structure_id: str, frame_ref: str | int | None) -> _Revision:
        key = (str(structure_id), str(frame_ref))
        try:
            return self._frames[key][self._cursor[key]]
        except KeyError as exc:
            raise KeyError(f"unknown structure {structure_id} frame {frame_ref}") from exc

    def _resolve(self, ref: StructureRef | None, *, structure_id: str | None = None, frame_ref: str | int | None = None) -> _Revision:
        if ref is None:
            if structure_id is None:
                raise ValidationError("a structure reference is required")
            return self._current(structure_id, "0" if frame_ref is None else frame_ref)
        key = (ref.id, ref.frame_ref)
        for revision in self._frames.get(key, ()):
            if revision.ref.revision == ref.revision:
                return revision
        raise KeyError(f"unknown revision {ref.id}@{ref.revision} frame {ref.frame_ref}")

    def _make_revision(self, crystal, *, ref: StructureRef, properties: dict[str, Any], provenance: dict[str, Any]) -> _Revision:
        snapshot = snapshot_from_crystal(crystal, ref=ref, properties=properties, provenance=provenance)
        return _Revision(
            ref=ref,
            crystal=crystal,
            properties=properties,
            provenance=dict(provenance),
            fingerprint=snapshot.fingerprint,
            snapshot=snapshot,
        )


__all__ = ["Candidate", "CommitResult", "Counters", "EffectSession"]
