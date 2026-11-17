"""The shared MolCrysKit pin keeps both parent surfaces.

``9616debf`` adds the lightweight CIF compatibility reader.
``0702ed8d`` adds ``read_mryx``. A pin of either parent alone fails here.
"""

from __future__ import annotations


def test_shared_pin_has_mercury_reader_and_lightweight_cif():
    import molcrys_kit.compat.cifparse as cifparse
    import molcrys_kit.io as io
    from molcrys_kit.structures.records import BondRecord

    assert "read_mryx" in io.__all__
    assert "read_mryx_atoms" in io.__all__
    assert callable(io.read_mryx)
    assert callable(io.read_mryx_atoms)
    assert hasattr(cifparse, "CifParser")
    assert "role" in BondRecord.__annotations__
