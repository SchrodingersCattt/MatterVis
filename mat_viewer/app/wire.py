"""Small JSON boundary helpers for browser-facing view events."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def json_safe_wire(value: Any, *, drop_private: bool = False) -> Any:
    """Return a detached value accepted by Dash's strict JSON encoder.

    Plotly and topology builders keep useful in-process caches on otherwise
    public dictionaries.  Those caches can have tuple keys (and are never
    needed by a browser), while numeric arrays may contain NaN sentinels.
    Convert those values at the transport boundary instead of leaking Python
    implementation details into ``view-update-store`` or the WebSocket.
    """

    if isinstance(value, np.ndarray):
        return json_safe_wire(value.tolist(), drop_private=drop_private)
    if isinstance(value, np.generic):
        return json_safe_wire(value.item(), drop_private=drop_private)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            if drop_private and text_key.startswith("_"):
                continue
            result[text_key] = json_safe_wire(item, drop_private=drop_private)
        return result
    if isinstance(value, (list, tuple)):
        return [json_safe_wire(item, drop_private=drop_private) for item in value]
    return value


__all__ = ["json_safe_wire"]
