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
import warnings
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
METHODS = ("enumerate", "random")
MAX_CANDIDATES = 16
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_SEED = 2**32 - 1
_MCK_LOGGER = "molcrys_kit"
_NOISE_WARNINGS = (DeprecationWarning, PendingDeprecationWarning, FutureWarning)


class CandidateRequestError(ValueError):
    """The request is invalid or exceeds a phone bound. Nothing was returned."""


class CandidatePayloadError(CandidateRequestError):
    """The serialized result would exceed ``max_payload_bytes``."""


class _LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


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
) -> dict[str, Any]:
    """Return up to ``count`` ordered disorder candidates for one CIF.

    ``method="enumerate"`` is MolCrysKit's deterministic enumeration with
    ``count`` as a top-N cap; ``seed`` must be ``None``. ``method="random"``
    is occupancy-weighted sampling and requires an explicit ``seed``; its
    samples may repeat, which is reported through ``duplicate_of`` rather
    than hidden. MolCrysKit may return fewer candidates than requested; the
    shortfall is reported as a warning and ``actual_count``, never padded.

    ``independent_assemblies`` defaults to ``False`` (MolCrysKit's legacy
    semantics): separate assemblies are *not* assumed independent. Pass
    ``True`` only when the source confirms it. ``coupled`` is forwarded
    unchanged. Invalid input, more than ``max_candidates`` requested, or a
    result larger than ``max_payload_bytes`` raises
    :class:`CandidateRequestError`; the result is never truncated.

    The source file is read-only here and every candidate is committed from a
    fresh session registered from the original source, so candidates are
    siblings, not a chain. Not thread-safe while MolCrysKit logging is being
    captured.
    """
    method, count, seed, coupled, independent_assemblies = _validate(
        method, count, seed, coupled, independent_assemblies, max_candidates, max_payload_bytes
    )
    source_path = Path(path)
    source_info, before = _fingerprint_source(source_path)

    from molcrys_kit.analysis.disorder.process import (
        generate_ordered_replicas_from_disordered_sites,
    )

    from mat_viewer.phone.snapshot import _read

    captured: list[dict[str, str]] = []
    handler = _LogCapture()
    logger = logging.getLogger(_MCK_LOGGER)
    logger.addHandler(handler)
    try:
        with warnings.catch_warnings(record=True) as caught:
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
    first_seen: dict[tuple[int, ...], str] = {}
    for index, (replica, kept) in enumerate(replicas):
        kept_indices = [int(item) for item in kept]
        candidate_id = f"candidate-{index + 1:03d}"
        key = tuple(kept_indices)
        duplicate_of = first_seen.get(key)
        first_seen.setdefault(key, candidate_id)
        snapshot, cif, fingerprint = _commit_candidate(
            source, replica, kept_indices, source_path, method, seed, index
        )
        candidates.append(
            {
                "candidate_id": candidate_id,
                "index": index,
                "kept_indices": kept_indices,
                "duplicate_of": duplicate_of,
                "fingerprint": fingerprint,
                "snapshot": snapshot,
                "cif": cif,
            }
        )

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

    after = _fingerprint_source(source_path)[1]
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


def _validate(method, count, seed, coupled, independent_assemblies, max_candidates, max_payload_bytes):
    if not isinstance(method, str) or method not in METHODS:
        hint = " (use operate_commit_major for the single optimal candidate)" if method == "optimal" else ""
        raise CandidateRequestError(f"method must be one of {list(METHODS)}, got {method!r}{hint}")
    if not _is_int(max_candidates) or max_candidates < 1 or max_candidates > MAX_CANDIDATES:
        raise CandidateRequestError(f"max_candidates must be an integer in 1..{MAX_CANDIDATES}")
    if not _is_int(max_payload_bytes) or not 0 < max_payload_bytes <= MAX_PAYLOAD_BYTES:
        raise CandidateRequestError(f"max_payload_bytes must be an integer in 1..{MAX_PAYLOAD_BYTES}")
    if not _is_int(count) or count < 1:
        raise CandidateRequestError(f"count must be a positive integer, got {count!r}")
    if count > max_candidates:
        raise CandidateRequestError(
            f"count {count} exceeds the phone limit of {max_candidates} candidates"
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
    return method, count, seed, coupled, independent_assemblies


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _fingerprint_source(path: Path) -> tuple[dict[str, Any], str]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise CandidateRequestError(f"cannot read source CIF: {exc}") from exc
    if size > MAX_SOURCE_BYTES:
        raise CandidateRequestError(
            f"source CIF is {size} bytes, above the {MAX_SOURCE_BYTES} byte phone limit"
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"path": str(path), "sha256": digest, "bytes": size}, digest


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
