"""Native file-lock and read-only cleanup for content-addressed uploads.

The POSIX tests call the real chmod and unlink path. The Windows tests
open the published CIF with the Win32 sharing modes the upload retry
claims to handle. A mocked ``PermissionError`` does not exercise those
modes.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

import pytest

from mat_viewer.app import ViewerBackend
from mat_viewer.extensions.structures import StructureWriter
from mat_viewer.presets import default_preset_path

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


def test_remove_candidate_deletes_a_real_readonly_file(tmp_path: Path) -> None:
    path = tmp_path / "candidate.cif"
    path.write_bytes(b"data_synthetic\n")
    path.chmod(0o400)
    assert not os.access(path, os.W_OK)
    StructureWriter._remove_candidate(path)
    assert not path.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows rejects unlinking a read-only file")
def test_windows_readonly_unlink_needs_candidate_cleanup(tmp_path: Path) -> None:
    path = tmp_path / "candidate.cif"
    path.write_bytes(b"data_synthetic\n")
    path.chmod(stat.S_IREAD)
    with pytest.raises(PermissionError):
        path.unlink()
    StructureWriter._remove_candidate(path)
    assert not path.exists()


def _win32_file():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    return create_file, close_handle


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing modes")
def test_exclusive_reader_makes_upload_retry_fail_closed(backend: ViewerBackend) -> None:
    bundle = backend.add_uploaded_file_bytes(_VALID_CIF, "locked.cif")
    path = Path(bundle.cif_path)
    before = path.read_bytes()
    create_file, close_handle = _win32_file()
    generic_read = 0x80000000
    open_existing = 3
    invalid = wintypes_invalid()
    handle = create_file(str(path), generic_read, 0, None, open_existing, 0, None)
    assert handle != invalid
    try:
        with pytest.raises(RuntimeError, match="could not be verified"):
            ViewerBackend._reuse_published_upload(
                str(path), before, _sha256(before)
            )
    finally:
        close_handle(handle)
    assert path.read_bytes() == before


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing modes")
def test_shared_read_without_delete_keeps_the_published_inode(
    backend: ViewerBackend,
) -> None:
    bundle = backend.add_uploaded_file_bytes(_VALID_CIF, "shared.cif")
    path = Path(bundle.cif_path)
    before = path.read_bytes()
    identity = path.stat().st_ino
    create_file, close_handle = _win32_file()
    generic_read = 0x80000000
    file_share_read = 0x1
    open_existing = 3
    handle = create_file(
        str(path), generic_read, file_share_read, None, open_existing, 0, None
    )
    assert handle != wintypes_invalid()
    temporary = ""
    try:
        ViewerBackend._reuse_published_upload(str(path), before, _sha256(before))
        descriptor, temporary = tempfile.mkstemp(
            prefix=".replace-", suffix=".tmp", dir=str(path.parent)
        )
        os.close(descriptor)
        Path(temporary).write_bytes(before)
        with pytest.raises(PermissionError):
            os.replace(temporary, path)
        again = backend.add_uploaded_file_bytes(_VALID_CIF, "shared.cif")
        assert Path(again.cif_path) == path
        assert path.stat().st_ino == identity
        assert path.read_bytes() == before
    finally:
        close_handle(handle)
        try:
            os.unlink(temporary)
        except OSError:
            pass


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def wintypes_invalid():
    import ctypes

    return ctypes.c_void_p(-1).value
