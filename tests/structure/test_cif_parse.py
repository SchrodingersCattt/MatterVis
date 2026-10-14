from __future__ import annotations

from mat_viewer.structure.cif_parse import _normalise_cif_text, load_cif


def _charged_cif() -> str:
    return """data_ionic
_cell_length_a 5
_cell_length_b 5
_cell_length_c 5
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_space_group_name_H-M_alt 'P 1'
_chemical_formula_moiety 'Fe3+ O2-'
loop_
_space_group_symop_operation_xyz
'x,y,z'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
Fe1 Fe3+ 0 0 0 1
O1 O2- 0.2 0.2 0.2 1
"""


def test_cif_type_symbol_normalisation_does_not_rewrite_formula_metadata():
    text = _charged_cif()
    normalised = _normalise_cif_text(text)
    assert "_chemical_formula_moiety 'Fe3+ O2-'" in normalised
    assert "Fe1 Fe 0 0 0 1" in normalised
    assert "O1 O 0.2 0.2 0.2 1" in normalised


def test_load_cif_accepts_ionic_type_symbols(tmp_path):
    source = tmp_path / "ionic.cif"
    source.write_text(_charged_cif(), encoding="utf-8")

    parsed = load_cif(source)

    assert [atom["elem"] for atom in parsed.atoms] == ["Fe", "O"]
    assert [atom["label"] for atom in parsed.atoms] == ["Fe1", "O1"]
