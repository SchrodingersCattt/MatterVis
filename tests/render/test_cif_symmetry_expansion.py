from __future__ import annotations

import pytest

from mat_viewer.structure.cif_parse import parse_asu


def test_parse_asu_rejects_incomplete_non_p1_symops(tmp_path):
    """molcrys-kit>=0.8.0 refuses identity-only loops on non-P1 declarations.

    Silent auto-expansion was removed from the structure reader so incomplete
    CIFs cannot materialize with the wrong multiplicity. MatterVis surfaces
    that ValueError through ``parse_asu`` / ``load_cif``.
    """
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

    with pytest.raises(ValueError, match="incomplete non-P1"):
        parse_asu(str(cif))
