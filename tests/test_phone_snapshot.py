"""The phone snapshot keeps display changes off the chemistry path."""

import subprocess
import sys
import warnings
from pathlib import Path

import pytest

MOLCRYS = Path(__file__).resolve().parents[2] / "MolCrysKit"
if MOLCRYS.is_dir():
    sys.path.insert(0, str(MOLCRYS))
    for _name in list(sys.modules):
        if _name == "molcrys_kit" or _name.startswith("molcrys_kit."):
            del sys.modules[_name]

from mat_viewer.phone import analyze_cif, display, operate_commit_major

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
molcrys = Path.cwd().parent / "MolCrysKit"
if molcrys.is_dir():
    sys.path.insert(0, str(molcrys))
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
