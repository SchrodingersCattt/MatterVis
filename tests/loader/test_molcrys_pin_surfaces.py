"""The shared MolCrysKit pin keeps both parent surfaces.

``a54a922eb2d11dca3fafac2d63a19c603541ca4d`` is the reviewed head of open
MolCrysKit PR 234. It includes lightweight CIF commit ``a4080132`` and
Mercury ``read_mryx`` commit ``0702ed8d``. It is not MolCrysKit main.
``read_mryx`` imports ``defusedxml`` from the desktop extra, so a core-only
install of this commit fails here.
"""

from __future__ import annotations


def test_shared_pin_has_mercury_reader_and_lightweight_cif():
    import defusedxml
    import molcrys_kit.compat.cifparse as cifparse
    import molcrys_kit.io as io
    from molcrys_kit.structures.records import BondRecord

    assert defusedxml.__name__ == "defusedxml"
    assert "read_mryx" in io.__all__
    assert "read_mryx_atoms" in io.__all__
    assert callable(io.read_mryx)
    assert callable(io.read_mryx_atoms)
    assert hasattr(cifparse, "CifParser")
    assert "role" in BondRecord.__annotations__
