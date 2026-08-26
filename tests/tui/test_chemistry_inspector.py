from __future__ import annotations

import asyncio

import numpy as np

from mat_viewer.structure.chemistry_records import (
    AbsoluteStructureRecord,
    AtomChemistryRecord,
    BondChemistryRecord,
    CrystalChemistryRecords,
    CrystalStereoRecord,
    EnantiomerCountRecord,
    EntityChemistryRecord,
    LineNotationRecord,
    NamingRecord,
)
from mat_viewer.tui.chemistry_inspector import format_atom_inspector
from mat_viewer.tui.app import CrystalTUI
from mat_viewer.tui.controller import TerminalViewController
from mat_viewer.tui.crystal_ir import AtomIR, BondIR, CrystalIR


def _chemistry_crystal() -> CrystalIR:
    atom_specs = (
        ("C", "C1", "m0:a0", (0.0, 0.0, 0.0)),
        ("Br", "Br1", "m0:a1", (1.0, 1.0, 1.0)),
        ("Cl", "Cl1", "m0:a2", (1.0, -1.0, -1.0)),
        ("F", "F1", "m0:a3", (-1.0, 1.0, -1.0)),
        ("H", "H1", "m0:a4", (-1.0, -1.0, 1.0)),
    )
    atoms = [
        AtomIR(
            element,
            np.asarray(position, dtype=float),
            np.asarray(position, dtype=float) / 10.0,
            atom_id=atom_id,
            label=label,
            index=index,
            source_index=index,
            display_copy_id=f"copy:{label}",
            occupancy=0.75 if index == 0 else 1.0,
            disorder_group=2 if index == 0 else 0,
        )
        for index, (element, label, atom_id, position) in enumerate(atom_specs)
    ]
    atom_records = tuple(
        AtomChemistryRecord(
            atom_id=atom_id,
            source_index=index,
            entity_id="molecule:0",
            element=element,
            isotope=13 if index == 0 else None,
            formal_charge=0,
            radical_electrons=0,
            implicit_hydrogens=0,
            oxidation_state=None,
            status="inferred",
            stereo_descriptor="R" if index == 0 else None,
            stereo_kind="tetrahedral" if index == 0 else None,
            stereo_status="inferred" if index == 0 else None,
            cip_order=("m0:a1", "m0:a2", "m0:a3", "m0:a4") if index == 0 else (),
            stereo_reason="assigned from CIP order and signed tetrahedral volume"
            if index == 0
            else None,
            evidence=("inferred:unit-test",),
        )
        for index, (element, _label, atom_id, _position) in enumerate(atom_specs)
    )
    chemistry_bonds = tuple(
        BondChemistryRecord(
            atom1_id="m0:a0",
            atom2_id=f"m0:a{index}",
            order=1.0,
            kind="covalent",
            aromatic=False,
            atom2_image_shift=(0, 0, 0),
            stereochemistry=None,
            evidence=("inferred:unit-test",),
        )
        for index in range(1, 5)
    )
    chemistry = CrystalChemistryRecords(
        status="provisional",
        atoms=atom_records,
        bonds=chemistry_bonds,
        entities=(
            EntityChemistryRecord(
                entity_id="molecule:0",
                kind="FiniteChemicalEntity",
                dimension=0,
                atom_ids=tuple(record.atom_id for record in atom_records),
                net_charge=0,
                status="inferred",
                warnings=("bond order is coordinate-derived",),
                evidence=("inferred:unit-test",),
                name=NamingRecord(
                    name="bromochlorofluoromethane",
                    kind="preferred_iupac_name",
                    nomenclature="IUPAC substitutive nomenclature",
                    standard="Blue Book",
                    version="2013",
                    status="provisional",
                    preferred=True,
                    rule_trace=("Select methane as the parent hydride.",),
                ),
                line_notation=LineNotationRecord(
                    value="F[C@H](Cl)Br",
                    dialect="OpenSMILES",
                    version="1.0",
                    lossless=True,
                ),
            ),
        ),
        warnings=("multiple chemical interpretations remain",),
        evidence=("inferred:unit-test",),
        source_names=(("systematic", "test deposited name"),),
        absolute_configuration="ad",
        absolute_structure=(AbsoluteStructureRecord("flack", "0.06(3)", 0.06, 0.03),),
        absolute_structure_details="Parsons quotients",
        crystal_stereo=CrystalStereoRecord(
            classification="enantiopure",
            status="provisional",
            symmetry_category="Sohncke",
            reason="all stereogenic entities have one handedness",
            enantiomer_counts=(
                EnantiomerCountRecord("molecule:0", 1, None, 0),
            ),
            evidence=("inferred:unit-test",),
        ),
        crystal_name=NamingRecord(
            name="bromochlorofluoromethane",
            kind="preferred_iupac_name",
            nomenclature="IUPAC substitutive nomenclature",
            standard="Blue Book",
            version="2013",
            status="provisional",
            preferred=True,
        ),
        alternative_count=1,
    )
    return CrystalIR(
        title="inspector",
        formula="CHBrClF",
        canonical_formula="CHBrClF",
        spacegroup="P 21",
        atoms=atoms,
        bonds=[BondIR(0, index, float(np.sqrt(3.0))) for index in range(1, 5)],
        chemistry=chemistry,
        metadata={
            "rings": (
                {
                    "atom_indices": (0, 1, 2),
                    "size": 3,
                    "is_aromatic": False,
                    "is_planar": True,
                },
            )
        },
    )


def test_full_inspector_combines_site_bond_entity_stereo_and_crystal_records() -> None:
    text = format_atom_inspector(_chemistry_crystal(), 0)

    assert "ATOM [C1]" in text
    assert "isotope: 13" in text
    assert "occupancy: 0.75  disorder: 2" in text
    assert "FiniteChemicalEntity  dimension=0D" in text
    assert "formula: [13C]HBrClF" in text
    assert "IUPAC name: bromochlorofluoromethane [provisional; PIN]" in text
    assert "line notation [OpenSMILES 1.0; lossless]: F[C@H](Cl)Br" in text
    assert "Br1  covalent order=1" in text
    assert "rings: 3-member non-aromatic planar" in text
    assert "tetrahedral: R [inferred]" in text
    assert "CIP: Br1 > Cl1 > F1 > H1" in text
    assert "space group: P 21" in text
    assert "enantiomer composition: enantiopure [provisional]" in text
    assert "symmetry category: Sohncke" in text
    assert "enantiomer count: molecule:0=1; mirror none=0" in text
    assert "flack: 0.06(3) (value=0.06; su=0.03)" in text
    assert "! multiple chemical interpretations remain" in text


def test_why_and_name_keep_inference_and_source_name_provenance_visible() -> None:
    crystal = _chemistry_crystal()

    why = format_atom_inspector(crystal, 0, view="why")
    name = format_atom_inspector(crystal, 0, view="name")

    assert "crystal chemistry: provisional; source=molcrys_kit" in why
    assert "alternative interpretations retained: 1" in why
    assert "stereo reason: assigned from CIP order" in why
    assert "crystal stereo reason: all stereogenic entities have one handedness" in why
    assert "IUPAC name: bromochlorofluoromethane [provisional; PIN]" in name
    assert "system: IUPAC substitutive nomenclature; Blue Book 2013" in name
    assert "rule: Select methane as the parent hydride." in name
    assert "CIF systematic name: test deposited name" in name


def test_controller_exposes_ascii_inspector_and_main_view_warnings() -> None:
    controller = TerminalViewController(_chemistry_crystal(), mono=True)
    controller.select_atom("C1")

    stereo = controller.inspect_selected(view="stereo")
    observation = controller.observe()

    assert "tetrahedral: R" in stereo
    assert observation.warnings[0].startswith("CHEMISTRY PROVISIONAL")
    assert "multiple chemical interpretations remain" in observation.warnings


def test_missing_chemistry_is_explicitly_indeterminate() -> None:
    crystal = CrystalIR(
        atoms=[
            AtomIR(
                "C",
                np.zeros(3),
                np.zeros(3),
                label="C1",
                display_copy_id="copy:C1",
            )
        ]
    )
    controller = TerminalViewController(crystal, mono=True)
    controller.select_atom("C1")

    assert "chemistry: unavailable" in controller.inspect_selected()
    assert controller.observe().warnings == (
        "CHEMISTRY UNAVAILABLE: MolCrysKit records are not attached",
    )


def test_inspector_commands_return_full_plain_text_and_change_panel_view() -> None:
    app = CrystalTUI(_chemistry_crystal(), mono=True)

    full, selected = app.execute_command(":inspect C1")
    stereo, _ = app.execute_command(":stereo")
    name, _ = app.execute_command(":name")
    why, _ = app.execute_command(":why")

    assert selected is not None and selected.state.selection.label == "C1"
    assert "ATOM [C1]" in full
    assert "tetrahedral: R" in stereo
    assert "CIF systematic name: test deposited name" in name
    assert "alternative interpretations retained: 1" in why


def test_wide_tui_shows_warning_and_side_inspector_that_tracks_selection() -> None:
    async def exercise() -> None:
        app = CrystalTUI(_chemistry_crystal(), mono=True, label_mode="label")
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()

            warning = app.query_one("#chemistry-warning")
            inspector = app.query_one("#inspector")
            assert warning.display is True
            assert "CHEMISTRY PROVISIONAL" in str(warning.render())
            assert inspector.display is True
            assert "ATOM [C1]" in str(inspector.render())
            assert app.screen.has_class("narrow") is False

            await pilot.press("tab")
            await pilot.pause()
            assert "ATOM [Br1]" in str(inspector.render())

    asyncio.run(exercise())


def test_narrow_tui_places_the_same_inspector_in_bottom_layout() -> None:
    async def exercise() -> None:
        app = CrystalTUI(_chemistry_crystal(), mono=True, label_mode="label")
        async with app.run_test(size=(80, 28)) as pilot:
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()

            inspector = app.query_one("#inspector")
            assert app.screen.has_class("narrow") is True
            assert inspector.display is True
            assert "ATOM [C1]" in str(inspector.render())

    asyncio.run(exercise())
