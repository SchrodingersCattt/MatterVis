"""Periodic materialization reports the cell the caller supplied."""

from __future__ import annotations

import numpy as np

from effect_builders import f1_ordered_nonorthogonal
from mat_viewer.effects import EffectSession
from mat_viewer.effects.errors import UnsupportedOperation, ValidationError


def test_periodic_materialization_collapses_declared_images_into_the_requested_cell():
    session = EffectSession()
    ref = session.register(f1_ordered_nonorthogonal(), structure_id="cell")
    lattice = np.asarray(session.snapshot(ref).lattice, dtype=float)
    result = session.operate(
        "materialize_selection",
        {
            "instances": [
                {"site_id": "m0:a0", "image": [0, 0, 0]},
                {"site_id": "m0:a0", "image": [1, 0, 0]},
            ],
            "pbc": True,
            "cell": lattice.tolist(),
            "equivalences": [[
                {"site_id": "m0:a0", "image": [0, 0, 0]},
                {"site_id": "m0:a0", "image": [1, 0, 0]},
            ]],
        },
        structure_id=ref.id,
        expected_revision=1,
    )
    output = session.snapshot(result.output_ref)
    assert output.pbc == (True, True, True)
    assert np.allclose(output.lattice, lattice)
    assert output.n_sites == 1
    assert session._frames[(ref.id, "0")][-1].provenance["pbc"] == [True, True, True]


def test_periodic_request_without_a_cell_does_not_commit_a_cluster():
    session = EffectSession()
    ref = session.register(f1_ordered_nonorthogonal(), structure_id="cell")
    try:
        session.operate(
            "materialize_selection",
            {"instances": [{"site_id": "m0:a0", "image": [0, 0, 0]}], "pbc": True},
            structure_id=ref.id,
            expected_revision=1,
        )
    except UnsupportedOperation:
        pass
    else:
        raise AssertionError("periodic request without a cell succeeded")
    assert session.current_ref("cell").revision == 1
    skew = np.eye(3) * 10.0
    try:
        session.operate(
            "materialize_selection",
            {
                "instances": [
                    {"site_id": "m0:a0", "image": [0, 0, 0]},
                    {"site_id": "m0:a0", "image": [1, 0, 0]},
                ],
                "pbc": True,
                "cell": skew.tolist(),
                "equivalences": [[
                    {"site_id": "m0:a0", "image": [0, 0, 0]},
                    {"site_id": "m0:a0", "image": [1, 0, 0]},
                ]],
            },
            structure_id=ref.id,
            expected_revision=1,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("non-equivalent images were committed as a finite cluster")
    assert session.current_ref("cell").revision == 1
    assert session.counters.commits == 0
