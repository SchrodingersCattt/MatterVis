from __future__ import annotations

import numpy as np
import pytest

from mat_viewer.tui.controller import TerminalViewController
from mat_viewer.tui.crystal_ir import AtomIR, CrystalIR


def _crystal() -> CrystalIR:
    atoms = [
        AtomIR(
            "C", np.array([-2.0, 0.0, 0.0]), np.zeros(3), label="Q001", index=0,
            source_index=1, display_copy_id="Q001/source:1/image:0,0,0",
            source_molecule_index=4, molecule_index=0, display_fragment_id="A0",
        ),
        AtomIR(
            "C", np.array([2.0, 0.0, 0.0]), np.zeros(3), label="Q001", index=1,
            source_index=1, display_copy_id="Q001/source:1/image:1,0,0",
            source_molecule_index=4, molecule_index=1, display_fragment_id="A1",
        ),
        AtomIR(
            "O", np.array([2.0, 1.0, 0.0]), np.zeros(3), label="Q002", index=2,
            source_index=2, display_copy_id="Q002/source:2/image:0,0,0",
            source_molecule_index=4, molecule_index=1, display_fragment_id="A1",
            occupancy=0.3, disorder_group=2, is_minor=True,
        ),
    ]
    return CrystalIR(
        title="focus",
        formula="C2O",
        atoms=atoms,
        n_molecules=1,
        species_map={"CO_1": [0, 1]},
        source_molecules={4: (1, 2)},
        source_molecule_species={4: "CO_1"},
        per_formula_unit={"CO_1": 1},
    )


def test_focus_label_resolves_every_displayed_periodic_copy() -> None:
    controller = TerminalViewController(_crystal(), width=50, height=16)

    observation = controller.focus_atom("Q001")

    assert observation.state.focus.kind == "atom"
    assert observation.state.focus.matched_copy_ids == (
        "Q001/source:1/image:0,0,0",
        "Q001/source:1/image:1,0,0",
    )
    assert observation.state.focus.hidden_copy_ids == ()


def test_focus_exact_display_copy_has_one_target() -> None:
    controller = TerminalViewController(_crystal(), width=50, height=16)

    observation = controller.focus_atom("Q001/source:1/image:1,0,0")

    assert observation.state.focus.matched_copy_ids == ("Q001/source:1/image:1,0,0",)


def test_hidden_minor_focus_is_atomic() -> None:
    controller = TerminalViewController(_crystal(), width=50, height=16, show_minor=False)
    before = controller.state

    with pytest.raises(ValueError, match="no visible"):
        controller.focus_atom("Q002")

    assert controller.state == before


def test_molecule_level_atom_focus_frames_visible_owning_molecule() -> None:
    controller = TerminalViewController(
        _crystal(), width=50, height=16, display_level="molecule", show_minor=True,
    )

    observation = controller.focus_atom("Q001/source:1/image:1,0,0")

    assert observation.state.focus.framed_copy_ids == (
        "Q001/source:1/image:1,0,0",
        "Q002/source:2/image:0,0,0",
    )


def test_focus_keeps_all_context_in_rendered_frame() -> None:
    controller = TerminalViewController(_crystal(), width=50, height=16, label_mode="label")

    frame = controller.focus_atom("Q001/source:1/image:1,0,0").frame

    assert "Q001" in frame


def test_save_restore_round_trips_camera_display_focus_and_viewport() -> None:
    controller = TerminalViewController(_crystal(), width=50, height=16)
    controller.focus_atom("Q001")
    controller.set_camera(azimuth=50.0, zoom=1.4)
    controller.set_display(label_mode="dot", show_bonds=False)
    saved = controller.save_view("candidate")
    controller.clear_focus()
    controller.set_camera(azimuth=110.0, zoom=2.0)
    controller.set_display(label_mode="element", show_bonds=True)

    restored = controller.restore_view("candidate")

    assert restored.state.camera.as_dict() == saved.state.camera.as_dict()
    assert restored.state.display.as_dict() == saved.state.display.as_dict()
    assert restored.state.focus.as_dict() == saved.state.focus.as_dict()
    assert controller.list_views()[0].name == "candidate"


def test_inspect_atom_reports_ambiguity_provenance_and_not_minor() -> None:
    controller = TerminalViewController(_crystal(), show_minor=False)

    result = controller.inspect_atom(["Q001", "Q002"])

    assert result["count"] == 3
    first = result["atoms"][0]
    minor = result["atoms"][-1]
    assert first["source_index"] == 1
    assert first["display_copy_id"] == "Q001/source:1/image:0,0,0"
    assert minor["occupancy"] == pytest.approx(0.3)
    assert minor["partial_occupancy"] is True
    assert minor["render_classification"] == "minor"
    assert minor["hidden_reason"] == "minor_filtered"
    assert minor["classification_provenance_available"] is False


def test_inspect_molecule_uses_retained_molcryskit_provenance() -> None:
    controller = TerminalViewController(_crystal(), show_minor=True)

    result = controller.inspect_molecule({"source_molecule_index": 4})

    molecule = result["molecules"][0]
    assert molecule["grouping_source"] == "molcrys_kit.mol_indices"
    assert molecule["species_id"] == "CO_1"
    assert molecule["per_formula_unit"] == 1
    assert molecule["source_member_indices"] == [1, 2]
    assert molecule["complete"] is True
    assert {item["display_fragment_id"] for item in molecule["display_instances"]} == {"A0", "A1"}


def test_atom_reference_mapping_respects_declared_namespace() -> None:
    controller = TerminalViewController(_crystal())

    with pytest.raises(TypeError, match="label reference"):
        controller.focus_atom({"label": 1})
    with pytest.raises(TypeError, match="source_index reference"):
        controller.focus_atom({"source_index": "1"})
    with pytest.raises(TypeError, match="display_copy_id reference"):
        controller.focus_atom({"display_copy_id": 1})