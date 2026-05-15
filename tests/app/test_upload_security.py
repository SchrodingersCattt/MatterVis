"""Regression: ``backend.add_uploaded_file_bytes`` must not let a
client-controlled filename escape the upload directory.

Pre-fix, ``os.path.join(upload_dir, filename)`` happily accepted
``../../tmp/evil.cif`` (walks one level up) or ``/etc/passwd``
(``os.path.join`` drops the prefix when the second arg is absolute),
giving an unauthenticated POST to ``/api/v2/upload`` an arbitrary-file
write primitive on the server.

DO NOT REMOVE -- this guards a remote arbitrary-file-write bug.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from crystal_viewer.app import ViewerBackend
from crystal_viewer.presets import default_preset_path


_VALID_CIF = b"""data_minimal
_cell_length_a 10.0
_cell_length_b 10.0
_cell_length_c 10.0
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
C1 C 0.0 0.0 0.0 1.0
"""


@pytest.fixture
def backend(tmp_path: Path) -> ViewerBackend:
    return ViewerBackend(preset_path=default_preset_path(), root_dir=str(tmp_path))


@pytest.fixture
def upload_dir() -> Path:
    return Path(os.path.realpath(os.path.join(tempfile.gettempdir(), "crystal_viewer_uploads")))


def test_relative_traversal_is_neutralised(backend: ViewerBackend, upload_dir: Path) -> None:
    bundle = backend.add_uploaded_file_bytes(_VALID_CIF, "../../escape_attempt.cif")
    written = Path(bundle.cif_path).resolve()
    assert str(written).startswith(str(upload_dir)), (
        f"upload escaped its directory: {written} not under {upload_dir}"
    )
    assert ".." not in written.name


def test_absolute_path_is_neutralised(tmp_path: Path, backend: ViewerBackend, upload_dir: Path) -> None:
    target = tmp_path / "evil.cif"
    assert not target.exists()
    bundle = backend.add_uploaded_file_bytes(_VALID_CIF, str(target))
    written = Path(bundle.cif_path).resolve()
    assert str(written).startswith(str(upload_dir))
    assert not target.exists(), f"absolute attacker path got written: {target}"


def test_pure_dotdot_filename_falls_back_to_default(backend: ViewerBackend, upload_dir: Path) -> None:
    bundle = backend.add_uploaded_file_bytes(_VALID_CIF, "../..")
    written = Path(bundle.cif_path).resolve()
    assert str(written).startswith(str(upload_dir))
    # secure_filename collapses ".." to "" so the helper falls back to
    # ``upload.cif`` -- assert we ended up with a real .cif under the
    # upload dir, not e.g. an empty filename or an unrelated path.
    assert written.suffix == ".cif"


def test_normal_filename_still_works(backend: ViewerBackend, upload_dir: Path) -> None:
    bundle = backend.add_uploaded_file_bytes(_VALID_CIF, "my_struct.cif")
    written = Path(bundle.cif_path).resolve()
    assert written.parent == upload_dir
    assert written.name.endswith("_my_struct.cif")
    # And the bundle should be registered under a sane name.
    assert bundle.name == "my_struct"


def test_extension_is_forced_to_cif(backend: ViewerBackend, upload_dir: Path) -> None:
    bundle = backend.add_uploaded_file_bytes(_VALID_CIF, "no_extension")
    written = Path(bundle.cif_path).resolve()
    assert written.parent == upload_dir
    assert written.suffix == ".cif"
    assert written.name.endswith("_no_extension.cif")


def test_same_filename_different_bytes_do_not_overwrite(backend: ViewerBackend) -> None:
    first = backend.add_uploaded_file_bytes(_VALID_CIF, "same_name.cif")
    second = backend.add_uploaded_file_bytes(
        _VALID_CIF.replace(b"C1 C 0.0 0.0 0.0 1.0", b"N1 N 0.5 0.5 0.5 1.0"),
        "same_name.cif",
    )

    assert first.name == "same_name"
    assert second.name == "same_name_2"
    assert first.cif_path != second.cif_path
    assert Path(first.cif_path).read_bytes() != Path(second.cif_path).read_bytes()


def test_reupload_same_bytes_is_idempotent(backend: ViewerBackend) -> None:
    first = backend.add_uploaded_file_bytes(_VALID_CIF, "candidate.cif")
    second = backend.add_uploaded_file_bytes(_VALID_CIF, "candidate_again.cif")

    assert first.name == second.name
    assert backend.structure_names.count(first.name) == 1
    assert getattr(second, "_upload_existing", False) is True
