"""The shared MolCrysKit pin keeps both parent surfaces.

``2a3ee11e4c708b64691181cc2cbc4b2a3625f0c6`` is the reviewed commit of open
MolCrysKit PR 234 authorized for this pin. It is not a later tip of that
branch. It includes Mercury ``read_mryx`` ``0702ed8d``,
lightweight CIF / phone-site ``a4080132``, and the MRYX bounds on this
commit: a single index validates every structure and keeps only that
structure; ``index=":"`` builds the full list before returning. Labels
are at most 128 characters, a document at most 4,096 structures, and a
structure at most 100,000 stored sites. Synthetic documents exercise
those bounds. They do not certify Mercury. It is not MolCrysKit main.
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
