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

_VALID_EXTXYZ = b'''2
Lattice="8.0 0.0 0.0 0.0 8.0 0.0 0.0 0.0 8.0" Properties=species:S:1:pos:R:3:molecule_index:I:1 pbc="T T T"
C 0.0 0.0 0.0 0
O 1.2 0.0 0.0 0
'''


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


def test_extxyz_extension_is_preserved(backend: ViewerBackend, upload_dir: Path) -> None:
    bundle = backend.add_uploaded_file_bytes(_VALID_EXTXYZ, "sample.extxyz")
    written = Path(bundle.cif_path).resolve()
    assert written.parent == upload_dir
    assert written.suffix == ".extxyz"
    assert bundle.source_format == "extxyz"


def test_unknown_extension_is_rejected(backend: ViewerBackend) -> None:
    with pytest.raises(ValueError, match="unsupported structure file extension"):
        backend.add_uploaded_file_bytes(_VALID_CIF, "sample.txt")


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


def test_reupload_existing_creates_scene_when_none_points_at_structure(
    backend: ViewerBackend,
) -> None:
    """Bug: the upload manifest persists across restarts. After a restart
    the structure is back in ``structure_names`` but no scene references
    it, so the legacy short-circuit returned the existing bundle without
    creating or switching to a scene. The browser's ``native_upload.js``
    then sat on ``"Updating scene..."`` until its 30 s timeout because
    ``state.structure`` never changed. Re-upload must produce a visible
    scene tab pointing at the existing structure."""
    backend.add_uploaded_file_bytes(_VALID_CIF, "shown.cif")
    structure = "shown"

    # Simulate "structure restored from manifest, scene long gone".
    scene_ids = [
        sid
        for sid, sc in backend.scene_store.scenes.items()
        if sc.structure_name == structure
    ]
    for sid in scene_ids:
        backend.scene_store.remove(sid, save=False)
    assert structure in backend.structure_names
    assert not any(
        sc.structure_name == structure for sc in backend.scene_store.scenes.values()
    )

    initial_version = backend.version
    bundle = backend.add_uploaded_file_bytes(_VALID_CIF, "shown.cif")

    assert getattr(bundle, "_upload_existing", False) is True
    matching = [
        sc
        for sc in backend.scene_store.scenes.values()
        if sc.structure_name == structure
    ]
    assert len(matching) == 1
    assert backend.active_scene_id() == matching[0].id
    assert backend.get_state()["structure"] == structure
    assert backend.version > initial_version


def test_reupload_existing_switches_active_scene_when_scene_already_exists(
    backend: ViewerBackend,
) -> None:
    """When a scene already references the re-uploaded structure, the
    server must switch the active scene to it rather than silently
    accepting the upload. Otherwise the browser's upload watcher sees no
    ``state.structure`` change and the new tab never lights up."""
    backend.add_uploaded_file_bytes(_VALID_CIF, "stable.cif")
    structure = "stable"
    original_scene_id = next(
        sid
        for sid, sc in backend.scene_store.scenes.items()
        if sc.structure_name == structure
    )

    # Drop a second, unrelated scene in front so the upload path has to
    # *switch* the active scene, not just confirm it.
    other_bytes = _VALID_CIF.replace(b"C1 C 0.0 0.0 0.0 1.0", b"O1 O 0.1 0.1 0.1 1.0")
    other = backend.add_uploaded_file_bytes(other_bytes, "decoy.cif")
    other_scene_id = next(
        sid
        for sid, sc in backend.scene_store.scenes.items()
        if sc.structure_name == other.name
    )
    backend.set_active_scene(other_scene_id, broadcast=False)
    assert backend.active_scene_id() == other_scene_id

    initial_version = backend.version
    bundle = backend.add_uploaded_file_bytes(_VALID_CIF, "stable.cif")

    assert getattr(bundle, "_upload_existing", False) is True
    matching = [
        sc
        for sc in backend.scene_store.scenes.values()
        if sc.structure_name == structure
    ]
    # No duplicate scene was created for the existing structure.
    assert len(matching) == 1
    assert matching[0].id == original_scene_id
    assert backend.active_scene_id() == original_scene_id
    assert backend.get_state()["structure"] == structure
    assert backend.version > initial_version
