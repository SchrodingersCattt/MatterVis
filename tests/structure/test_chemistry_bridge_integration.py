"""Cross-repository MolCrysKit chemistry-contract smoke test."""

import numpy as np
import pytest
from ase import Atoms

import molcrys_kit
from mat_viewer.structure import molcrys_bridge
from molcrys_kit.io.cif import identify_molecules


@pytest.mark.skipif(
    not hasattr(molcrys_kit, "infer_chemistry"),
    reason="shared gate: requires the MolCrysKit #143 chemistry contract",
)
def test_bridge_consumes_real_mck_atom_ids_bonds_entities_and_stereo() -> None:
    directions = np.asarray(
        [
            [1.0, 1.0, 1.0],
            [1.0, -1.0, -1.0],
            [-1.0, 1.0, -1.0],
            [-1.0, -1.0, 1.0],
        ]
    ) / np.sqrt(3.0)
    positions = np.vstack(
        [
            np.zeros(3),
            directions[0] * 1.94,
            directions[1] * 1.77,
            directions[2] * 1.35,
            directions[3] * 1.09,
        ]
    )
    atoms = Atoms(
        ["C", "Br", "Cl", "F", "H"],
        positions=positions,
        cell=np.eye(3) * 20.0,
        pbc=False,
    )
    crystal = molcrys_kit.MolecularCrystal(
        np.eye(3) * 20.0,
        identify_molecules(atoms),
        pbc=(False, False, False),
    )

    analysis = molcrys_bridge.analyze_crystal(crystal, include_chemistry=True)

    assert analysis.chemistry is not None
    assert [record.atom_id for record in analysis.chemistry.atoms] == [
        "m0:a0",
        "m0:a1",
        "m0:a2",
        "m0:a3",
        "m0:a4",
    ]
    assert analysis.chemistry.entities[0].dimension == 0
    assert len(analysis.chemistry.bonds) == 4
    carbon = analysis.chemistry.atom("m0:a0")
    assert carbon.stereo_descriptor in {"R", "S"}
    assert carbon.cip_order == ("m0:a1", "m0:a2", "m0:a3", "m0:a4")
    entity = analysis.chemistry.entities[0]
    assert entity.name is not None
    assert entity.name.name == "molecular entity CHBrClF"
    assert entity.name.kind == "iupac_composition_description"
    assert entity.line_notation is not None
    assert entity.line_notation.dialect == "OpenSMILES"
    assert entity.line_notation.lossless
    assert analysis.chemistry.crystal_stereo is not None
