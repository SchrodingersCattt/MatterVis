"""The phone snapshot keeps display changes off the chemistry path."""

import json
import subprocess
import sys
import warnings
from pathlib import Path

import numpy as np
import pytest

from mat_viewer.phone import (
    analyze_cif,
    display,
    operate_commit_major,
    operate_commit_major_with_cif,
)

CIF = """data_phone
_cell_length_a 10
_cell_length_b 10
_cell_length_c 10
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_space_group_IT_number 1
_space_group_name_H-M_alt 'P 1'
loop_
_space_group_symop_operation_xyz
x,y,z
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
_atom_site_disorder_assembly
_atom_site_disorder_group
C1 C 0.10 0.20 0.30 0.70 A 1
C1B C 0.40 0.20 0.30 0.30 A 2
"""


def test_display_hides_the_other_disorder_group(tmp_path: Path):
    path = tmp_path / "disorder.cif"
    path.write_text(CIF, encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        snapshot = analyze_cif(path)
    assert snapshot["revision"] == 1
    assert {site["alpha"] for site in snapshot["sites"]} == {site["occupancy"] for site in snapshot["sites"]}
    shown = display(snapshot, disorder_group=1)
    visible = [site for site in shown["sites"] if site["visible"]]
    assert visible
    assert all(site["disorder_group"] == "1" for site in visible)
    assert any(not site["visible"] for site in shown["sites"])


def test_operate_commits_a_new_revision(tmp_path: Path):
    path = tmp_path / "disorder.cif"
    path.write_text(CIF, encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        committed = operate_commit_major(path)
    assert committed["revision"] == 2
    assert committed["operation"] == "commit_disorder_candidate"
    assert len(committed["sites"]) == 1


@pytest.mark.timeout(180)
def test_operate_does_not_import_desktop_chemistry(tmp_path: Path):
    path = tmp_path / "disorder.cif"
    path.write_text(CIF, encoding="utf-8")
    script = r"""
import sys
from pathlib import Path
blocked = ("ase", "scipy", "matplotlib", "pymatgen", "spglib", "PIL")

class Block:
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] in blocked:
            raise ImportError(name)
        return None

sys.meta_path.insert(0, Block())
import warnings
from mat_viewer.phone import operate_commit_major
with warnings.catch_warnings():
    warnings.simplefilter("ignore", UserWarning)
    committed = operate_commit_major(sys.argv[1])
assert committed["revision"] == 2
assert len(committed["sites"]) == 1
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


MULTI_CIF = """data_phone_multi
_cell_length_a 9.0
_cell_length_b 11.0
_cell_length_c 12.0
_cell_angle_alpha 90
_cell_angle_beta 100
_cell_angle_gamma 90
_space_group_IT_number 1
_space_group_name_H-M_alt 'P 1'
loop_
_space_group_symop_operation_xyz
x,y,z
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
_atom_site_disorder_assembly
_atom_site_disorder_group
C1 C 0.10 0.20 0.30 0.70 A 1
C1B C 0.30 0.20 0.30 0.30 A 2
N1 N 0.60 0.70 0.20 0.60 B 1
N1B N 0.60 0.90 0.20 0.40 B 2
O1 O 0.80 0.50 0.70 1.00 . .
"""


def _write(directory: Path, text: str = MULTI_CIF, name: str = "multi.cif") -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def _quiet(function, *args):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return function(*args)


def _assert_same_structure(left: dict, right: dict) -> None:
    """Same cell, symbols, occupancies and periodic fractional positions."""
    assert np.allclose(left["cell"], right["cell"], atol=1e-4)
    key = lambda site: (site["symbol"], round(site["occupancy"], 4))  # noqa: E731
    assert sorted(map(key, left["sites"])) == sorted(map(key, right["sites"]))
    inverse_left = np.linalg.inv(np.array(left["cell"]))
    inverse_right = np.linalg.inv(np.array(right["cell"]))
    for site in left["sites"]:
        frac = np.array(site["position"]) @ inverse_left
        candidates = [
            np.array(other["position"]) @ inverse_right
            for other in right["sites"]
            if key(other) == key(site)
        ]
        assert candidates, site
        best = min(
            np.abs((frac - cand) - np.round(frac - cand)).max() for cand in candidates
        )
        assert best < 1e-4, (site["symbol"], frac, best)


def test_analyze_keeps_every_disorder_group_and_occupancy(tmp_path: Path):
    snapshot = _quiet(analyze_cif, _write(tmp_path))
    assert snapshot["revision"] == 1
    assert len(snapshot["sites"]) == 5
    groups = {(s["disorder_assembly"], s["disorder_group"]) for s in snapshot["sites"]}
    assert {("A", "1"), ("A", "2"), ("B", "1"), ("B", "2")} <= groups
    assert sorted(s["occupancy"] for s in snapshot["sites"]) == [0.3, 0.4, 0.6, 0.7, 1.0]


def test_with_cif_snapshot_equals_operate_commit_major(tmp_path: Path):
    path = _write(tmp_path)
    plain = _quiet(operate_commit_major, path)
    paired = _quiet(operate_commit_major_with_cif, path)
    assert set(paired) == {"snapshot", "cif"}
    assert paired["snapshot"] == plain
    assert paired["snapshot"]["revision"] == 2
    assert paired["snapshot"]["operation"] == "commit_disorder_candidate"
    # One component per disorder assembly plus the ordered oxygen.
    assert sorted(s["symbol"] for s in paired["snapshot"]["sites"]) == ["C", "N", "O"]
    assert isinstance(paired["cif"], str) and paired["cif"].endswith("\n")
    assert "_atom_site_fract_x" in paired["cif"]


def test_with_cif_operates_exactly_once_through_effect_session(tmp_path: Path, monkeypatch):
    import molcrys_kit.analysis.disorder.process as process

    from mat_viewer.effects import EffectSession

    calls = {"operate": 0, "generate": 0}
    real_operate = EffectSession.operate
    real_generate = process.generate_ordered_replicas_from_disordered_sites

    def counting_operate(self, *args, **kwargs):
        calls["operate"] += 1
        return real_operate(self, *args, **kwargs)

    def counting_generate(*args, **kwargs):
        calls["generate"] += 1
        return real_generate(*args, **kwargs)

    monkeypatch.setattr(EffectSession, "operate", counting_operate)
    monkeypatch.setattr(
        process, "generate_ordered_replicas_from_disordered_sites", counting_generate
    )
    result = _quiet(operate_commit_major_with_cif, _write(tmp_path))
    assert calls == {"operate": 1, "generate": 1}
    assert result["snapshot"]["revision"] == 2


def test_exported_cif_reimports_as_the_committed_structure(tmp_path: Path):
    path = _write(tmp_path)
    original = _quiet(analyze_cif, path)
    paired = _quiet(operate_commit_major_with_cif, path)
    exported = _write(tmp_path, paired["cif"], name="committed.cif")
    again = _quiet(analyze_cif, exported)
    # analyze -> commit -> export -> reimport keeps cell, symbols,
    # positions and occupancies of the committed structure.
    _assert_same_structure(paired["snapshot"], again)
    assert len(again["sites"]) == 3 < len(original["sites"])
    assert np.allclose(again["cell"], original["cell"], atol=1e-4)


def test_phone_path_exports_cif_without_desktop_dependencies(tmp_path: Path):
    path = _write(tmp_path)
    script = r"""
import json, sys, warnings
from pathlib import Path
blocked = ("ase", "scipy", "matplotlib", "pymatgen", "spglib", "PIL")

class Block:
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] in blocked:
            raise ImportError(name)
        return None

sys.meta_path.insert(0, Block())
from mat_viewer.phone import analyze_cif, operate_commit_major_with_cif
with warnings.catch_warnings():
    warnings.simplefilter("ignore", UserWarning)
    result = operate_commit_major_with_cif(sys.argv[1])
    Path(sys.argv[2]).write_text(result["cif"], encoding="utf-8")
    again = analyze_cif(sys.argv[2])
loaded = sorted(m for m in sys.modules if m.split(".")[0] in blocked)
print(json.dumps({"snapshot": result["snapshot"], "again": again, "loaded": loaded}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(path), str(tmp_path / "phone.cif")],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["loaded"] == []
    _assert_same_structure(payload["snapshot"], payload["again"])


class _Crystal:
    """Minimal stand-in exposing the public MolCrysKit record accessors."""

    lattice = np.diag([10.0, 10.0, 10.0])

    def __init__(self, bonds):
        self._bonds = bonds

    def get_site_records(self):
        return []

    def get_bond_records(self):
        return self._bonds


def _bond(left, right, shift, vector, role="covalent"):
    from molcrys_kit.structures.records import BondRecord

    return BondRecord(
        molecule_index=0,
        left_local_index=left,
        right_local_index=right,
        left_global_index=left,
        right_global_index=right,
        left_asym_index=None,
        right_asym_index=None,
        right_image_shift=shift,
        vector_A=vector,
        distance_A=float(np.linalg.norm(vector)),
        role=role,
    )


def test_bonds_keep_canonical_vector_shift_and_role():
    from mat_viewer.phone.snapshot import _project

    records = [
        _bond(0, 1, (1, 0, 0), (-1.5, 0.0, 0.0)),  # periodic image
        _bond(2, 2, (0, 0, 1), (0.0, 0.0, 4.0)),  # self-image
        _bond(0, 3, (0, -1, 0), (0.0, 2.0, 0.0), role="coordination"),
    ]
    bonds = _project(_Crystal(records))["bonds"]
    assert len(bonds) == 3
    for bond, record in zip(bonds, records):
        assert bond["left"] == record.left_global_index
        assert bond["right"] == record.right_global_index
        assert bond["role"] == record.role
        assert bond["vector_A"] == [float(v) for v in record.vector_A]
        assert bond["right_image_shift"] == list(record.right_image_shift)
    assert bonds[0]["right_image_shift"] == [1, 0, 0]
    assert bonds[1]["left"] == bonds[1]["right"]
    assert bonds[1]["right_image_shift"] == [0, 0, 1]
    assert [b["role"] for b in bonds] == ["covalent", "covalent", "coordination"]
    assert json.loads(json.dumps(bonds)) == bonds


def test_bond_fields_serialize_without_desktop_dependencies():
    script = r"""
import json, sys
blocked = ("ase", "scipy", "matplotlib", "pymatgen", "spglib", "PIL")

class Block:
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] in blocked:
            raise ImportError(name)
        return None

sys.meta_path.insert(0, Block())
from molcrys_kit.structures.records import BondRecord
from mat_viewer.phone.snapshot import _project

class C:
    lattice = [[10, 0, 0], [0, 10, 0], [0, 0, 10]]
    def get_site_records(self):
        return []
    def get_bond_records(self):
        return [BondRecord(0, 0, 0, 0, 0, None, None, (0, 0, 1), (0.0, 0.0, 4.0), 4.0, "covalent")]

text = json.dumps(_project(C()))
loaded = sorted(m for m in sys.modules if m.split(".")[0] in blocked)
print(json.dumps({"snapshot": json.loads(text), "loaded": loaded}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["loaded"] == []
    bond = payload["snapshot"]["bonds"][0]
    assert bond["vector_A"] == [0.0, 0.0, 4.0]
    assert bond["right_image_shift"] == [0, 0, 1]
