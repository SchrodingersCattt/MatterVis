from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from mat_viewer.structure import molcrys_bridge


def _site(global_index: int, local_index: int, position, image_shift=(0, 0, 0)):
    return SimpleNamespace(
        global_index=global_index,
        molecule_index=0,
        local_index=local_index,
        cartesian_position_A=tuple(position),
        image_shift=image_shift,
    )


def test_analysis_consumes_only_public_molcryskit_records(monkeypatch):
    sites = (_site(0, 0, (0.0, 0.0, 0.0)), _site(1, 1, (1.0, 0.0, 0.0)))
    bonds = (
        SimpleNamespace(
            molecule_index=0,
            left_local_index=0,
            right_local_index=1,
            left_global_index=0,
            right_global_index=1,
            left_asym_index=3,
            right_asym_index=4,
            right_image_shift=(1, 0, 0),
            vector_A=(1.0, 0.0, 0.0),
            distance_A=1.0,
        ),
    )
    selection = SimpleNamespace(
        members=(
            SimpleNamespace(species_id="C2_1", molecule_index=0, image_shift=(0, 1, 0)),
        )
    )

    class Analyzer:
        def __init__(self, crystal):
            self.species_map = {"C2_1": [0]}

        def get_simplest_unit(self):
            return {"C2_1": 1}

        def select_formula_unit(self):
            return selection

    ring = SimpleNamespace(
        atom_indices=(0, 1),
        cycle_atom_indices=(1, 0),
        symbols=("C", "C"),
        centroid_A=(0.5, 0.0, 0.0),
        normal=(0.0, 0.0, 1.0),
        plane_rmsd_A=0.0,
        is_planar=True,
        is_aromatic=True,
        size=2,
    )

    class GeometryCache:
        def __init__(self, crystal):
            pass

        def __getitem__(self, molecule_index):
            return SimpleNamespace(rings=lambda: [ring])

    crystal = SimpleNamespace(
        molecules=[object()],
        get_site_records=lambda: list(sites),
        get_bond_records=lambda: list(bonds),
    )
    monkeypatch.setattr(
        molcrys_bridge,
        "_require_molcryskit",
        lambda: {
            "StoichiometryAnalyzer": Analyzer,
            "LocalGeometryCache": GeometryCache,
        },
    )

    analysis = molcrys_bridge.analyze_crystal(crystal)

    assert analysis.mol_indices == [[0, 1]]
    assert analysis.bond_pairs == [(0, 1)]
    assert analysis.bond_records[0]["right_image_shift"] == [1, 0, 0]
    assert analysis.formula_unit_selection is selection
    assert analysis.ring_records[0]["atom_indices"] == (0, 1)
    assert analysis.ring_records[0]["cycle_atom_indices"] == (1, 0)
    assert analysis.ring_records[0]["is_aromatic"] is True


def test_analysis_rebases_bond_shift_to_wrapped_site_images(monkeypatch):
    sites = (
        _site(0, 0, (9.8, 0.0, 0.0)),
        _site(1, 1, (10.2, 0.0, 0.0), image_shift=(1, 0, 0)),
    )
    bond = SimpleNamespace(
        molecule_index=0,
        left_local_index=0,
        right_local_index=1,
        left_global_index=0,
        right_global_index=1,
        left_asym_index=0,
        right_asym_index=1,
        right_image_shift=(0, 0, 0),
        vector_A=(0.4, 0.0, 0.0),
        distance_A=0.4,
    )

    class Analyzer:
        def __init__(self, crystal):
            self.species_map = {"C2_1": [0]}

        def get_simplest_unit(self):
            return {"C2_1": 1}

        def select_formula_unit(self):
            return SimpleNamespace(members=(SimpleNamespace(),))

    class GeometryCache:
        def __init__(self, crystal):
            pass

        def __getitem__(self, molecule_index):
            return SimpleNamespace(rings=lambda: [])

    crystal = SimpleNamespace(
        molecules=[object()],
        get_site_records=lambda: list(sites),
        get_bond_records=lambda: [bond],
    )
    monkeypatch.setattr(
        molcrys_bridge,
        "_require_molcryskit",
        lambda: {
            "StoichiometryAnalyzer": Analyzer,
            "LocalGeometryCache": GeometryCache,
        },
    )

    analysis = molcrys_bridge.analyze_crystal(crystal)

    assert analysis.bond_records[0]["right_image_shift"] == [1, 0, 0]


def test_analysis_copies_public_chemistry_and_stereo_records(monkeypatch):
    source_evidence = SimpleNamespace(
        source="inferred",
        method="golden_method",
        detail="human checked",
    )
    chemical_atoms = (
        SimpleNamespace(
            atom_id="m0:a0",
            element="C",
            isotope=None,
            formal_charge=0,
            radical_electrons=0,
            implicit_hydrogens=1,
            oxidation_state=None,
            evidence=(source_evidence,),
        ),
        SimpleNamespace(
            atom_id="m0:a1",
            element="F",
            isotope=None,
            formal_charge=0,
            radical_electrons=0,
            implicit_hydrogens=0,
            oxidation_state=None,
            evidence=(source_evidence,),
        ),
    )

    class FiniteChemicalEntity:
        entity_id = "molecule:0"
        atoms = chemical_atoms
        bonds = (
            SimpleNamespace(
                atom1_id="m0:a0",
                atom2_id="m0:a1",
                order=1.0,
                kind="covalent",
                aromatic=False,
                atom2_image_shift=(0, 0, 0),
                stereochemistry=None,
                evidence=(source_evidence,),
            ),
        )
        embedding = object()
        dimension = 0
        net_charge = 0
        status = "inferred"
        warnings = ()
        evidence = (source_evidence,)

    entity = FiniteChemicalEntity()
    chemistry = SimpleNamespace(
        atom_ids_by_global_index=("m0:a0", "m0:a1"),
        components=(entity,),
        status="inferred",
        warnings=("coordinate-derived bond order",),
        evidence=(source_evidence,),
    )
    stereo = SimpleNamespace(
        descriptors=(
            SimpleNamespace(
                center_atom_id="m0:a0",
                descriptor="R",
                kind="tetrahedral",
                status="inferred",
                cip_order=("m0:a1", "m0:a0:implicit-H"),
                reason="golden orientation",
            ),
        ),
        warnings=(),
    )
    crystal_stereo = SimpleNamespace(
        classification="enantiopure",
        status="provisional",
        symmetry_category="Sohncke",
        reason="all stereogenic entities have one handedness",
        enantiomer_counts=(
            SimpleNamespace(
                representative_entity_id="molecule:0",
                count=1,
                mirror_entity_id=None,
                mirror_count=0,
            ),
        ),
        relationships=(),
        warnings=("tetrahedral scope only",),
        evidence=(source_evidence,),
    )
    sites = (
        SimpleNamespace(
            site_id="m0:a0",
            global_index=0,
            molecule_index=0,
            local_index=0,
            cartesian_position_A=(0.0, 0.0, 0.0),
            image_shift=(0, 0, 0),
        ),
        SimpleNamespace(
            site_id="m0:a1",
            global_index=1,
            molecule_index=0,
            local_index=1,
            cartesian_position_A=(1.0, 0.0, 0.0),
            image_shift=(0, 0, 0),
        ),
    )

    class Analyzer:
        species_map = {"CF_1": [0]}

        def __init__(self, crystal):
            pass

        def get_simplest_unit(self):
            return {"CF_1": 1}

        def select_formula_unit(self):
            return SimpleNamespace(members=(SimpleNamespace(),))

    class GeometryCache:
        def __init__(self, crystal):
            pass

        def __getitem__(self, molecule_index):
            return SimpleNamespace(rings=lambda: [])

    crystal = SimpleNamespace(
        molecules=[object()],
        chemistry=None,
        metadata={
            "cif_chemistry": {
                "chemical_name_systematic": "test systematic name",
                "chemical_name_common": "test common name",
                "chemical_absolute_configuration": "ad",
                "absolute_structure": {
                    "flack": {
                        "raw": "0.06(3)",
                        "value": 0.06,
                        "standard_uncertainty": 0.03,
                    },
                    "details": "Parsons quotients",
                },
            }
        },
        get_site_records=lambda: list(sites),
        get_bond_records=lambda: [],
    )
    monkeypatch.setattr(
        molcrys_bridge,
        "_require_molcryskit",
        lambda: {
            "StoichiometryAnalyzer": Analyzer,
            "LocalGeometryCache": GeometryCache,
            "infer_chemistry": lambda value: chemistry,
            "assign_stereochemistry": lambda value, embedding: stereo,
            "analyze_crystal_stereochemistry": lambda value, **kwargs: crystal_stereo,
        },
    )

    analysis = molcrys_bridge.analyze_crystal(crystal)

    assert analysis.chemistry.source == "molcrys_kit"
    assert analysis.chemistry.warnings == (
        "coordinate-derived bond order",
        "tetrahedral scope only",
    )
    assert analysis.chemistry.entities[0].dimension == 0
    assert analysis.chemistry.bonds[0].kind == "covalent"
    carbon = analysis.chemistry.atom("m0:a0")
    assert carbon.source_index == 0
    assert carbon.stereo_descriptor == "R"
    assert carbon.cip_order == ("m0:a1", "m0:a0:implicit-H")
    assert carbon.evidence == ("inferred:golden_method (human checked)",)
    assert analysis.chemistry.source_names == (
        ("systematic", "test systematic name"),
        ("common", "test common name"),
    )
    assert analysis.chemistry.absolute_configuration == "ad"
    assert analysis.chemistry.absolute_structure[0].raw == "0.06(3)"
    assert analysis.chemistry.absolute_structure[0].standard_uncertainty == pytest.approx(0.03)
    assert analysis.chemistry.absolute_structure_details == "Parsons quotients"
    assert analysis.chemistry.crystal_stereo.classification == "enantiopure"
    assert analysis.chemistry.crystal_stereo.symmetry_category == "Sohncke"
    assert analysis.chemistry.crystal_stereo.enantiomer_counts[0].mirror_count == 0
    assert "tetrahedral scope only" in analysis.chemistry.warnings


def test_formula_unit_materialises_mck_image_shift():
    raw_atoms = [
        {"elem": "C", "cart": np.array([0.0, 0.0, 0.0]), "frac": np.zeros(3)},
        {
            "elem": "C",
            "cart": np.array([1.0, 0.0, 0.0]),
            "frac": np.array([0.1, 0.0, 0.0]),
        },
    ]
    selection = SimpleNamespace(
        members=(
            SimpleNamespace(species_id="C2_1", molecule_index=0, image_shift=(0, 1, 0)),
        )
    )
    analysis = SimpleNamespace(
        formula_unit_selection=selection,
        mol_indices=[[0, 1]],
        mol_cart_positions=[np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])],
        site_records=(
            SimpleNamespace(global_index=0),
            SimpleNamespace(global_index=1),
        ),
        bond_records=[],
    )

    atoms = molcrys_bridge.select_formula_unit(
        raw_atoms, np.diag([10.0, 20.0, 30.0]), analysis=analysis
    )

    assert np.asarray(atoms[0]["cart"]) == pytest.approx([0.0, 20.0, 0.0])
    assert np.asarray(atoms[1]["cart"]) == pytest.approx([1.0, 20.0, 0.0])
    assert [atom["_source_index"] for atom in atoms] == [0, 1]
    assert all(atom["_formula_image_shift"] == [0, 1, 0] for atom in atoms)
