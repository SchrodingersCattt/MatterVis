from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .renderer_common import *

def _traces_to_dicts(traces) -> list[dict]:
    """Materialise a list of plotly trace objects (or pre-built dicts)
    into raw dicts so we can pass them to ``go.Figure(data=...)`` as a
    single batch instead of going through ``add_trace`` one at a time."""
    out = []
    for tr in traces:
        if isinstance(tr, dict):
            out.append(_round_coord_arrays(dict(tr)))
        else:
            out.append(_round_coord_arrays(tr.to_plotly_json()))
    return out


def _trace_to_json_safe_dict(trace) -> dict:
    payload = trace if isinstance(trace, dict) else trace.to_plotly_json()
    return _round_coord_arrays(_json_safe_plotly(payload))


def _json_safe_plotly(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, dict):
        return {key: _json_safe_plotly(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe_plotly(item) for item in value]
    return value


# 0.001 Å (0.1 pm) is two orders of magnitude below the smallest atomic
# feature anyone visualises in this app, so rounding mesh vertex / line
# coordinates here is a free win on payload size: full f64 stringifies
# to ~17 chars, three-decimal floats to ~6, ie ~3x smaller for any
# vertex-heavy trace (atoms, bonds, hull edges, polyhedron meshes).
_COORD_KEYS = ("x", "y", "z")
_COORD_ROUND_DECIMALS = 3
_INDEX_KEYS = ("i", "j", "k")


def _round_coord_arrays(trace_dict: dict) -> dict:
    """Quantise vertex coordinates of a Plotly trace dict in place and
    return it.

    Three things happen here, all aimed at trimming the figure JSON
    Dash ships to the browser on every ``update_view``:

    1. Plain Python lists of floats get rounded to ``_COORD_ROUND_DECIMALS``
       (only really shrinks JSON-encoded coords, since ``17 chars -> ~6``).
    2. Numpy float arrays get downcast to ``float32``. Plotly serialises
       numpy arrays as base64 ``bdata``; ``f8`` doubles take 8 B / coord,
       ``f4`` floats take 4 B -- a flat 50% cut on every Mesh3d vertex
       array. Three-decimal rounding (~1 mÅ) is well inside f32's ~7
       significant decimal digits, so this is lossless w.r.t. the
       quantisation step (and far below any visible feature).
    3. Index arrays (``i``/``j``/``k``) get cast to the smallest int
       dtype that fits, so a ~10 k-vertex sphere mesh's faces ride as
       ``i2`` (2 B) or ``i4`` instead of ``i8`` (8 B) -- another ~75%
       drop on the index payload.
    """

    for key in _COORD_KEYS:
        seq = trace_dict.get(key)
        if isinstance(seq, list) and seq and isinstance(seq[0], (int, float)):
            trace_dict[key] = [
                round(float(v), _COORD_ROUND_DECIMALS) if isinstance(v, (int, float)) else v
                for v in seq
            ]
        elif isinstance(seq, np.ndarray) and seq.dtype.kind == "f" and seq.dtype.itemsize > 4:
            trace_dict[key] = np.ascontiguousarray(seq, dtype=np.float32)
    for key in _INDEX_KEYS:
        seq = trace_dict.get(key)
        if isinstance(seq, list) and seq and isinstance(seq[0], (int, float)):
            trace_dict[key] = [int(v) for v in seq]
        elif isinstance(seq, np.ndarray) and seq.dtype.kind in ("i", "u"):
            n = int(seq.max(initial=0)) + 1
            if n < 32_768:
                target = np.int16
            elif n < 2_147_483_648:
                target = np.int32
            else:
                target = seq.dtype
            if seq.dtype != target:
                trace_dict[key] = np.ascontiguousarray(seq, dtype=target)
    return trace_dict

__all__ = [name for name in globals() if not name.startswith("__")]
