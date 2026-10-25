"""Opt-in model-facing terminal observations.

The legacy session payload is unchanged. ``observation_profile="model"``
keeps the picture, camera, display toggles, selection identities, and
state fingerprint, and drops fields that can carry chemistry warnings,
formulas, or source titles.

The profile does not change action semantics or the renderer. A native
molecule picture can still draw manifested species labels because the
built-in compositor reads ``CrystalIR``. Untrusted renderers do not
receive that object; they receive :class:`RenderRequest`.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

MODEL_SCOPE_KEYS = (
    "display_level",
    "source_site_atom_count",
    "expanded_atom_count",
    "display_atom_count",
    "visible_atom_count",
    "visible_marker_count",
)
MODEL_TITLE = "display"


def sanitize_model_observation(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached observation without chemistry-bearing metadata."""
    cleaned = copy.deepcopy(dict(payload))
    cleaned["title"] = MODEL_TITLE
    cleaned["warnings"] = []
    cleaned["observation_profile"] = "model"
    scope = cleaned.get("scope")
    if isinstance(scope, Mapping):
        cleaned["scope"] = {key: scope[key] for key in MODEL_SCOPE_KEYS if key in scope}
    return cleaned


__all__ = ["MODEL_SCOPE_KEYS", "MODEL_TITLE", "sanitize_model_observation"]
