import numpy as np

from crystal_viewer.legacy import plot_crystal as pc


def _atom(label, elem, cart, partners):
    return {
        "label": label,
        "elem": elem,
        "cart": np.array(cart, dtype=float),
        "frac": np.array(cart, dtype=float),
        "occ": 0.5,
        "dg": "-1",
        "da": "A",
        "_bond_partners": tuple(partners),
        "_bond_lengths": {},
        "_has_bond_table": True,
    }


def test_duplicate_disorder_labels_keep_nearest_alternative_bonds_only():
    atoms = [
        _atom("C2", "C", [0.0, 0.00, 0.0], ["F4"]),
        _atom("C2", "C", [0.0, 1.00, 0.0], ["F4"]),
        _atom("F4", "F", [1.35, 0.02, 0.0], ["C2"]),
        _atom("F4", "F", [1.35, 1.02, 0.0], ["C2"]),
    ]

    bonds = {tuple(pair) for pair in pc.find_bonds(atoms)}

    assert bonds == {(0, 2), (1, 3)}


def test_parse_asu_keeps_distinct_labels_at_same_position(tmp_path):
    cif = tmp_path / "overlap.cif"
    cif.write_text("""
data_overlap
_cell_length_a 10
_cell_length_b 10
_cell_length_c 10
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_space_group_name_H-M_alt 'P 1'
loop_
_space_group_symop_operation_xyz
'x, y, z'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
C1 C 0.1 0.2 0.3 1
C2 C 0.1 0.2 0.3 1
""")

    atoms, _, _ = pc.parse_asu(str(cif))
    labels = [atom["label"] for atom in atoms]

    assert labels.count("C1") == 1
    assert labels.count("C2") == 1
