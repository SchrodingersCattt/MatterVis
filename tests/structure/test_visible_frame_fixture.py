"""The public visible-frame fixture stays a two-atom crystal."""

from pathlib import Path

from mat_viewer.structure.cif_parse import load_cif

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "visible_frame" / "two-atom.cif"


def test_two_atom_fixture_loads_carbon_and_oxygen():
    parsed = load_cif(_FIXTURE)
    symbols = [str(atom.get("element") or atom.get("elem") or atom.get("symbol")) for atom in parsed.atoms]
    assert symbols == ["C", "O"]
