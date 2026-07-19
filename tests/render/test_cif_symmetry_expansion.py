from __future__ import annotations

from crystal_viewer.structure.cif_parse import parse_asu


def test_parse_asu_expands_space_group_when_symops_missing(tmp_path):
    cif = tmp_path / "srtio3_minimal.cif"
    cif.write_text(
        """data_srtio3
_cell_length_a 3.905
_cell_length_b 3.905
_cell_length_c 3.905
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_space_group_IT_number 221
_symmetry_space_group_name_H-M 'P m -3 m'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
Sr1 Sr 0 0 0 1
Ti1 Ti 0.5 0.5 0.5 1
O1 O 0.5 0.5 0 1
""",
        encoding="utf-8",
    )

    atoms, _cell, _M = parse_asu(str(cif))

    assert len(atoms) == 5
    assert [atom["elem"] for atom in atoms].count("O") == 3
