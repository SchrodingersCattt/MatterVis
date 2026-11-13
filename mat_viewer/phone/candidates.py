"""Bounded, ordered disorder candidates for the phone Operate workflow.

This module only orchestrates. Disorder solving is MolCrysKit's
``generate_ordered_replicas_from_disordered_sites`` (called exactly once per
request); each returned replica is committed through its own
:class:`mat_viewer.effects.EffectSession` and projected with the same
``_project`` the single-candidate API uses. Nothing here rasterizes and
nothing imports the desktop stack.

Contract (JSON-serializable, ``schema_version`` 1)::

    {
      "schema_version": 1,
      "method": "enumerate" | "random",
      "requested_count": int,          # the caller's count, not a promise
      "seed": int | None,              # explicit; required for "random"
      "coupled": bool,
      "independent_assemblies": bool,
      "actual_count": int,             # len(candidates); may be < requested
      "unique_count": int,             # distinct kept_indices sets
      "source": {"path", "sha256", "bytes"},
      "candidates": [{
          "candidate_id": "candidate-001",   # position in MCK return order
          "index": 0,
          "kept_indices": [int, ...],         # MCK source-atom indices
          "duplicate_of": None | "candidate-00N",
          "fingerprint": str,                 # EffectSession commit fingerprint
          "snapshot": {...},                  # as operate_commit_major
          "cif": "..."                        # P1 CIF of the SAME crystal
      }, ...],
      "warnings": [{"source", "category", "message"}, ...]
    }
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import warnings
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
METHODS = ("enumerate", "random")
# Defaults are conservative *starting points*, not measured phone limits; they
# were not benchmarked on a device. Each is a keyword argument and may be raised
# per call up to the hard ceiling (the largest size we are willing to buffer in
# one synchronous call). Exceeding any bound raises; nothing is truncated.
MAX_CANDIDATES = 16
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
HARD_MAX_CANDIDATES = 64
HARD_MAX_SOURCE_BYTES = 64 * 1024 * 1024
HARD_MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
MAX_SEED = 2**32 - 1
_MCK_LOGGER = "molcrys_kit"
_NOISE_WARNINGS = (DeprecationWarning, PendingDeprecationWarning, FutureWarning)
# ``warnings.catch_warnings`` swaps process-global state, so concurrent
# enter/exit pairs can leave a stale "always" filter behind. Calls are
# serialized around that window only; commit/CIF work runs outside it.
_CAPTURE_LOCK = threading.RLock()


class CandidateRequestError(ValueError):
    """The request is invalid or exceeds a phone bound. Nothing was returned."""


class CandidatePayloadError(CandidateRequestError):
    """The serialized result would exceed ``max_payload_bytes``."""


class _LogCapture(logging.Handler):
    """Collect WARNING+ records emitted by the thread that owns this capture.

    A record from any other thread is not attributed to this request.
    """

    def __init__(self, thread_id: int) -> None:
        super().__init__(level=logging.WARNING)
        self.thread_id = thread_id
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread == self.thread_id:
            self.records.append(record)


class _WarningCapture:
    """Record warnings raised by the owning thread; pass others through.

    Installed as ``warnings.showwarning`` inside ``catch_warnings``. Warnings
    from other threads go to the previous handler unchanged.
    """

    def __init__(self, thread_id: int, previous) -> None:
        self.thread_id = thread_id
        self.previous = previous
        self.items: list[warnings.WarningMessage] = []

    def __call__(self, message, category, filename, lineno, file=None, line=None) -> None:
        if threading.get_ident() == self.thread_id:
            self.items.append(warnings.WarningMessage(message, category, filename, lineno, file, line))
        else:
            self.previous(message, category, filename, lineno, file, line)


def generate_disorder_candidates(
    path: str | Path,
    *,
    method: str = "enumerate",
    count: int,
    seed: int | None = None,
    coupled: bool = False,
    independent_assemblies: bool = False,
    max_candidates: int = MAX_CANDIDATES,
    max_payload_bytes: int = MAX_PAYLOAD_BYTES,
    max_source_bytes: int | None = None,
) -> dict[str, Any]:
    """Return up to ``count`` ordered disorder candidates for one CIF.

    ``method="enumerate"`` is MolCrysKit's deterministic enumeration with
    ``count`` as a top-N cap; ``seed`` must be ``None``. ``method="random"``
    is occupancy-weighted sampling and requires an explicit ``seed``; its
    samples may repeat, which is reported through ``duplicate_of`` rather
    than hidden. Identity is the *set* of ``kept_indices``: the same atoms in
    a different order are the same candidate, while ``kept_indices`` itself,
    candidate order and ids are reported exactly as MolCrysKit returned them.
    MolCrysKit may return fewer candidates than requested; the shortfall is
    reported as a warning and ``actual_count``, never padded.

    ``independent_assemblies`` defaults to ``False`` (MolCrysKit's legacy
    semantics): separate assemblies are *not* assumed independent. Pass
    ``True`` only when the source confirms it. ``coupled`` is forwarded
    unchanged.

    Bounds (never truncating; each violation raises
    :class:`CandidateRequestError`): ``max_candidates`` (default 16, up to
    64), ``max_source_bytes`` (default 2 MiB, up to 64 MiB) and
    ``max_payload_bytes`` (default 8 MiB, up to 64 MiB). The defaults are
    conservative and have not been measured on a phone; raise them per call
    when the device can take it. The payload bound is checked after each
    candidate is built, so an oversized request stops early.

    The source file is read-only here and every candidate is committed from a
    fresh session registered from the original source, so candidates are
    siblings, not a chain.

    Concurrency: calls may run from several threads. MolCrysKit log records
    and Python warnings are captured only from the calling thread, and all
    captured state is restored on error. Because ``warnings.catch_warnings``
    mutates process-global state, the MolCrysKit generation step is
    serialized between concurrent calls (commit and CIF writing are not).
    Limitation: other threads that emit warnings during that window see the
    ``always`` filter and are delivered to the previous handler, not
    swallowed; Python cannot isolate warning filters per thread before 3.14.
    """
    method, count, seed, coupled, independent_assemblies, max_source_bytes = _validate(
        method,
        count,
        seed,
        coupled,
        independent_assemblies,
        max_candidates,
        max_payload_bytes,
        max_source_bytes,
    )
    source_path = Path(path)
    source_info, before = _fingerprint_source(source_path, max_source_bytes)

    from molcrys_kit.analysis.disorder.process import (
        generate_ordered_replicas_from_disordered_sites,
    )

    from mat_viewer.phone.snapshot import _read

    captured: list[dict[str, str]] = []
    thread_id = threading.get_ident()
    handler = _LogCapture(thread_id)
    logger = logging.getLogger(_MCK_LOGGER)
    with _CAPTURE_LOCK:
        logger.addHandler(handler)
        try:
            with warnings.catch_warnings():
                recorder = _WarningCapture(thread_id, warnings.showwarning)
                warnings.showwarning = recorder
                warnings.simplefilter("always")
                source = _read(source_path)
                replicas = generate_ordered_replicas_from_disordered_sites(
                    str(source_path),
                    method=method,
                    generate_count=count,
                    random_seed=seed,
                    return_kept_indices=True,
                    coupled=coupled,
                    independent_assemblies=independent_assemblies,
                )
        finally:
            logger.removeHandler(handler)
    caught = recorder.items
    captured.extend(
        {"source": "molcrys_kit", "category": record.levelname, "message": record.getMessage()}
        for record in handler.records
    )
    captured.extend(
        {"source": "molcrys_kit", "category": item.category.__name__, "message": str(item.message)}
        for item in caught
        if not issubclass(item.category, _NOISE_WARNINGS)
    )

    if not replicas:
        raise CandidateRequestError("disorder generation produced no candidate")
    if len(replicas) > count:
        raise CandidateRequestError(
            f"MolCrysKit returned {len(replicas)} candidates for count={count}"
        )

    candidates: list[dict[str, Any]] = []
    first_seen: dict[frozenset[int], str] = {}
    running_bytes = 0
    for index, (replica, kept) in enumerate(replicas):
        kept_indices = [int(item) for item in kept]  # provenance: exact MCK order
        candidate_id = f"candidate-{index + 1:03d}"
        key = frozenset(kept_indices)  # identity: the set of source atoms kept
        duplicate_of = first_seen.get(key)
        first_seen.setdefault(key, candidate_id)
        snapshot, cif, fingerprint = _commit_candidate(
            source, replica, kept_indices, source_path, method, seed, index
        )
        candidate = {
            "candidate_id": candidate_id,
            "index": index,
            "kept_indices": kept_indices,
            "duplicate_of": duplicate_of,
            "fingerprint": fingerprint,
            "snapshot": snapshot,
            "cif": cif,
        }
        # Lower bound of the final payload; fail as soon as it is exceeded.
        running_bytes += len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) + 2
        if running_bytes > max_payload_bytes:
            raise CandidatePayloadError(
                f"candidate payload exceeds the {max_payload_bytes} byte limit after "
                f"{index + 1} of {len(replicas)} candidates; request fewer candidates or "
                "raise max_payload_bytes"
            )
        candidates.append(candidate)

    warnings_out = _dedupe(captured)
    if len(candidates) < count:
        warnings_out.append(
            {
                "source": "mat_viewer",
                "category": "FewerCandidates",
                "message": (
                    f"requested {count} candidates but MolCrysKit returned "
                    f"{len(candidates)}; none were fabricated"
                ),
            }
        )
    unique = len(first_seen)
    if unique < len(candidates):
        warnings_out.append(
            {
                "source": "mat_viewer",
                "category": "DuplicateCandidates",
                "message": (
                    f"{len(candidates) - unique} of {len(candidates)} candidates repeat an "
                    "earlier kept_indices set (random sampling is with replacement)"
                ),
            }
        )

    after = _fingerprint_source(source_path, max_source_bytes)[1]
    if after != before:
        raise CandidateRequestError("source file changed while candidates were generated")

    result = {
        "schema_version": SCHEMA_VERSION,
        "method": method,
        "requested_count": count,
        "seed": seed,
        "coupled": coupled,
        "independent_assemblies": independent_assemblies,
        "actual_count": len(candidates),
        "unique_count": unique,
        "source": source_info,
        "candidates": candidates,
        "warnings": warnings_out,
    }
    size = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    if size > max_payload_bytes:
        raise CandidatePayloadError(
            f"candidate payload is {size} bytes, above the {max_payload_bytes} byte limit; "
            "request fewer candidates"
        )
    return result


def _validate(
    method, count, seed, coupled, independent_assemblies, max_candidates, max_payload_bytes,
    max_source_bytes=None,
):
    if not isinstance(method, str) or method not in METHODS:
        hint = " (use operate_commit_major for the single optimal candidate)" if method == "optimal" else ""
        raise CandidateRequestError(f"method must be one of {list(METHODS)}, got {method!r}{hint}")
    if not _is_int(max_candidates) or not 1 <= max_candidates <= HARD_MAX_CANDIDATES:
        raise CandidateRequestError(f"max_candidates must be an integer in 1..{HARD_MAX_CANDIDATES}")
    if not _is_int(max_payload_bytes) or not 0 < max_payload_bytes <= HARD_MAX_PAYLOAD_BYTES:
        raise CandidateRequestError(
            f"max_payload_bytes must be an integer in 1..{HARD_MAX_PAYLOAD_BYTES}"
        )
    if max_source_bytes is None:
        max_source_bytes = MAX_SOURCE_BYTES
    if not _is_int(max_source_bytes) or not 0 < max_source_bytes <= HARD_MAX_SOURCE_BYTES:
        raise CandidateRequestError(
            f"max_source_bytes must be an integer in 1..{HARD_MAX_SOURCE_BYTES}"
        )
    if not _is_int(count) or count < 1:
        raise CandidateRequestError(f"count must be a positive integer, got {count!r}")
    if count > max_candidates:
        raise CandidateRequestError(
            f"count {count} exceeds the phone limit of {max_candidates} candidates "
            f"(raise max_candidates, at most {HARD_MAX_CANDIDATES})"
        )
    if method == "random":
        if seed is None:
            raise CandidateRequestError("method 'random' requires an explicit integer seed")
        if not _is_int(seed) or not 0 <= seed <= MAX_SEED:
            raise CandidateRequestError(f"seed must be an integer in 0..{MAX_SEED}, got {seed!r}")
    elif seed is not None:
        raise CandidateRequestError("seed is only valid with method 'random'")
    for name, value in (("coupled", coupled), ("independent_assemblies", independent_assemblies)):
        if not isinstance(value, bool):
            raise CandidateRequestError(f"{name} must be a bool, got {value!r}")
    return method, count, seed, coupled, independent_assemblies, max_source_bytes


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _fingerprint_source(path: Path, max_bytes: int) -> tuple[dict[str, Any], str]:
    try:
        size = path.stat().st_size
        if size > max_bytes:
            raise CandidateRequestError(
                f"source CIF is {size} bytes, above the {max_bytes} byte phone limit "
                f"(raise max_source_bytes, at most {HARD_MAX_SOURCE_BYTES})"
            )
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)  # bounded: a file that grew is still caught
    except OSError as exc:
        raise CandidateRequestError(f"cannot read source CIF: {exc}") from exc
    if len(data) > max_bytes:
        raise CandidateRequestError(
            f"source CIF is above the {max_bytes} byte phone limit "
            f"(raise max_source_bytes, at most {HARD_MAX_SOURCE_BYTES})"
        )
    digest = hashlib.sha256(data).hexdigest()
    return {"path": str(path), "sha256": digest, "bytes": len(data)}, digest


def _commit_candidate(source, replica, kept_indices, path, method, seed, index):
    """Commit one replica in its own session registered from the source."""
    from molcrys_kit.io import write_cif

    from mat_viewer.effects import EffectSession
    from mat_viewer.phone.snapshot import _project

    session = EffectSession()
    ref = session.register(source, structure_id="phone")
    committed = session.operate(
        "commit_disorder_candidate",
        {
            "crystal": replica,
            "kept_indices": kept_indices,
            "method": method,
            "seed": seed,
            "candidate_index": index,
        },
        structure_id=ref.id,
        expected_revision=ref.revision,
    )
    saved = session.checkout(committed.output_ref)
    snapshot = _project(saved)
    snapshot["revision"] = int(committed.output_ref.revision)
    snapshot["source"] = str(path)
    snapshot["operation"] = "commit_disorder_candidate"
    text = write_cif(saved, representation="p1")
    if not isinstance(text, str):
        raise TypeError("MolCrysKit CIF writer returned non-text output")
    return snapshot, text if text.endswith("\n") else text + "\n", str(committed.fingerprint)


def _dedupe(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    out = []
    for item in items:
        key = (item["category"], item["message"])
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out
