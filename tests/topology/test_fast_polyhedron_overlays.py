from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from mat_viewer.agent_topology import (
    fast_polyhedron_overlays,
    prepare_fast_polyhedron_context,
)
from mat_viewer.render.contracts import LinePrimitive, TriangleMeshPrimitive


def test_fast_polyhedron_context_reuses_cached_shell_selection() -> None:
    frame = SimpleNamespace(
        atomic_numbers=np.array([19, 8, 8, 8, 8, 8, 8], dtype=np.uint8),
        positions=np.array(
            [
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [-1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, -1.0, 0.0],
                [0.0, 0.0, 1.0],
                [0.0, 0.0, -1.0],
            ],
            dtype=float,
        ),
        cell=np.eye(3, dtype=float) * 10.0,
    )

    context = prepare_fast_polyhedron_context(
        frame,
        ['{"center":"K","ligand":"O","level":"atom","cutoff":7.5}'],
    )

    assert context.specs[0].cutoff == 7.5
    assert context.specs[0].center_indices == (0,)
    assert context.specs[0].ligand_indices == (1, 2, 3, 4, 5, 6)

    overlays = fast_polyhedron_overlays(frame, context)

    assert len(overlays) == 2
    assert isinstance(overlays[0], TriangleMeshPrimitive)
    assert isinstance(overlays[1], LinePrimitive)
