from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def write_lammps_sidecar_trajectory(tmp_path: Path) -> tuple[Path, Path]:
    """Write two LAMMPS frames and a deliberately reordered velocity sidecar."""

    source = tmp_path / "velocity.dump"
    source.write_text(
        """ITEM: TIMESTEP
10
ITEM: NUMBER OF ATOMS
2
ITEM: BOX BOUNDS pp pp pp
0 5
0 5
0 5
ITEM: ATOMS id element x y z
2 O 1 0 0
1 H 0 0 0
ITEM: TIMESTEP
20
ITEM: NUMBER OF ATOMS
2
ITEM: BOX BOUNDS pp pp pp
0 5
0 5
0 5
ITEM: ATOMS id element x y z
1 H 0.2 0 0
2 O 1.2 0 0
""",
        encoding="utf-8",
    )

    # Sidecar frame order differs from the dump. Atom order also differs in
    # both frames, so a passing test proves timestep and ID alignment.
    np.save(tmp_path / "timesteps.npy", np.asarray([20, 10], dtype=np.int64))
    np.save(
        tmp_path / "atom_ids.npy",
        np.asarray([[2, 1], [1, 2]], dtype=np.int64),
    )
    np.save(
        tmp_path / "velocity.npy",
        np.asarray(
            [
                [[0.0, 0.0, 4.0], [0.0, 3.0, 0.0]],
                [[1.0, 0.0, 0.0], [0.0, 2.0, 0.0]],
            ],
            dtype=np.float32,
        ),
    )
    manifest = tmp_path / "properties.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "mattervis.atom-properties/v1",
                "source": {"sha256": None},
                "frames": {"key": "timestep", "ids": "timesteps.npy"},
                "atoms": {"key": "id", "ids": "atom_ids.npy"},
                "properties": {
                    "velocity": {
                        "values": "velocity.npy",
                        "unit": "angstrom/ps",
                        "components": ["x", "y", "z"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return source, manifest
