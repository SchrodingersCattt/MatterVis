"""JSON-safe coordination-shell shape labels.

``classify_shell`` is exhaustive for the small registered coordination
polyhedra. Applying that residual sweep to a periodic shell with hundreds of
image neighbours is combinatorial, so oversized shells are labelled from the
nearest registered-size cage while the true coordination number is retained.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from molcrys_kit.analysis.shape import classify_shell
from molcrys_kit.structures.polyhedra import all_ideal_polyhedra

_MAX_REGISTERED_CN = max(int(cn) for cn in all_ideal_polyhedra())


def _classify_shell_payload(
    shell_coords: list | np.ndarray,
    center: list | np.ndarray,
) -> dict[str, Any]:
    """Run ``classify_shell`` defensively and return a JSON-safe payload.

    ``classify_shell`` raises on degenerate inputs (CN < 1, all points
    collinear, etc.) which we don't want to bubble up to the renderer
    text-panel call site; an empty / partial shell should just degrade to
    ``primary_label = None`` rather than crash the whole topology card.
    """
    if len(np.asarray(shell_coords)) == 0:
        return _empty_shape_payload()
    coords_arr = np.asarray(shell_coords, dtype=float)
    classification_coords = coords_arr
    bounded_sample = False
    sample_indices: list[int] = []
    if len(coords_arr) > _MAX_REGISTERED_CN:
        # A shell can contain many periodic image neighbours when the source
        # cell is small.  The registered shape catalogue cannot describe an
        # arbitrary CN, and classify_shell's residual decomposition would
        # enumerate C(N, k) subsets.  Fit the nearest registered-size local
        # cage with the rigid path instead; retain the true CN below so the
        # caller can distinguish the approximation from a complete shell.
        center_arr = np.asarray(center, dtype=float)
        distances = np.linalg.norm(coords_arr - center_arr, axis=1)
        sample_indices = np.argsort(distances, kind="mergesort")[
            :_MAX_REGISTERED_CN
        ].astype(int).tolist()
        classification_coords = coords_arr[sample_indices]
        bounded_sample = True
    try:
        # Delegate the shell label policy to molcrys_kit's current
        # classifier. Earlier MatterVis pinned ``max_strip=0`` to force a
        # rigid CN=N CShM match, but that bypassed MCK's newer core/residual
        # decomposition and mislabelled registered derived shells such as
        # EAP-4 AX11 (tricapped cube) as rigid CN=11 alternatives. Leaving
        # ``max_strip`` unset keeps MV aligned with upstream MCK labels while
        # still returning a JSON-safe payload for the renderer/scripts.
        result = classify_shell(
            classification_coords,
            center=center,
            n_random_inits=4,
            top_k=3,
            max_strip=0 if bounded_sample else None,
        )
    except Exception as exc:
        return {**_empty_shape_payload(), "error": str(exc)}
    payload = _sanitize_shape_payload(result)
    if bounded_sample:
        payload["coordination_number"] = len(coords_arr)
        payload["diagnostics"] = {
            **(payload.get("diagnostics") or {}),
            "search_mode": "bounded_local_cage",
            "sample_size": len(classification_coords),
            "sample_indices": sample_indices,
            "max_registered_cn": _MAX_REGISTERED_CN,
        }
    return payload


# Fields under ``shape["topology"]`` (and inside each candidate's
# ``core.topology``) carry molcrys_kit's polyhedron-registry namedtuples
# (``FaceInfo``, ``EdgeInfo``, etc.) that are not JSON-serialisable and
# not actionable for any downstream consumer in this repo. Strip them so
# ``analyze_topology`` keeps its "JSON-safe payload" contract.
_SHAPE_DROP_KEYS = ("topology",)


def _sanitize_shape_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = {
        key: value for key, value in payload.items() if key not in _SHAPE_DROP_KEYS
    }
    if isinstance(cleaned.get("core"), dict):
        cleaned["core"] = {
            key: value
            for key, value in cleaned["core"].items()
            if key not in _SHAPE_DROP_KEYS
        }
    cleaned["candidates"] = [
        _sanitize_candidate(item) for item in cleaned.get("candidates") or []
    ]
    cleaned["alternatives"] = [
        _sanitize_candidate(item) for item in cleaned.get("alternatives") or []
    ]
    if isinstance(cleaned.get("best_match"), dict):
        cleaned["best_match"] = _sanitize_candidate(cleaned["best_match"])
    return cleaned


def _sanitize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    cleaned = {
        key: value for key, value in candidate.items() if key not in _SHAPE_DROP_KEYS
    }
    if isinstance(cleaned.get("core"), dict):
        cleaned["core"] = {
            key: value
            for key, value in cleaned["core"].items()
            if key not in _SHAPE_DROP_KEYS
        }
    return cleaned


def _empty_shape_payload() -> dict[str, Any]:
    return {
        "coordination_number": 0,
        "primary_label": None,
        "label_modifier": None,
        "label_source": None,
        "confidence_gap": None,
        "cshm_value": None,
        "core": None,
        "residuals": [],
        "structural_description": "",
        "alternatives": [],
        "best_match": None,
        "candidates": [],
    }
