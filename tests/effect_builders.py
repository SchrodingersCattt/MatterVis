"""Import fixture builders without making ``tests`` a package."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "fixtures" / "effect_contracts" / "builders.py"
_SPEC = importlib.util.spec_from_file_location("effect_contract_builders", _PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"cannot load effect fixture builders from {_PATH}")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

f1_ordered_nonorthogonal = _MODULE.f1_ordered_nonorthogonal
f2_hydrogen_pbc = _MODULE.f2_hydrogen_pbc
f3_octahedron = _MODULE.f3_octahedron
f4_disorder = _MODULE.f4_disorder
f5_host = _MODULE.f5_host
f6_frames = _MODULE.f6_frames
WATER = _MODULE.WATER
F1_LATTICE = _MODULE.F1_LATTICE
