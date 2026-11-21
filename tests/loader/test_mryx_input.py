"""Shared Mercury IO contracts using unrelated, hand-constructed geometry."""

from __future__ import annotations

import base64
import gzip
import math
from pathlib import Path

import numpy as np
import pytest

from mat_viewer.loader import (
    build_loaded_crystal,
    count_structure_frames,
    iter_atomistic_frames,
    load_atomistic_input,
    load_structure_input,
    load_uploaded_cif,
)
from mat_viewer.loader.mryx_adapter import read_mryx_frames


# pytest.warns re-emits unmatched warnings from this test module, so the
# repository's ASE-module-only filter cannot match this known upstream warning.
# Keep the exact warning filter scoped here; MRYX UserWarning assertions remain.
pytestmark = pytest.mark.filterwarnings(
    r"ignore:Setting the shape on a NumPy array has been deprecated in NumPy 2\.5\.:DeprecationWarning"
)


def synthetic_mryx():
    # Independent artificial geometry: isolated helium in rectangular cells.
    def structure(i):
        return f"""<item><value type="GraphicsCrystal">
        <value type="ConcreteCrystalStructure"><cell><value>
        <a><value>{6 + i}</value></a><b><value>8</value></b><c><value>10</value></c>
        <alpha><value>{math.pi / 2}</value></alpha>
        <beta><value>{math.pi / 2}</value></beta>
        <gamma><value>{math.pi / 2}</value></gamma>
        </value></cell><molecule><value type="Molecule3d"><atoms><item>
        <serial_number>301</serial_number><value type="Atom3d"><label>He47</label>
        <element><code>8100</code></element><site><value>
        <fractional><vector type="Container"><item>0.2</item><item>0.4</item><item>0.6</item></vector></fractional>
        <uncertain_value><value>0.23</value></uncertain_value>
        </value></site></value></item></atoms></value></molecule>
        </value></value></item>"""

    xml = (
        "<All><graphics_crystal>"
        + structure(0)
        + structure(1)
        + "</graphics_crystal></All>"
    )
    return gzip.compress(xml.encode(), mtime=0)


@pytest.fixture
def mryx_path(tmp_path):
    path = tmp_path / "synthetic.mryx"
    path.write_bytes(synthetic_mryx())
    return path


@pytest.mark.parametrize("input_format", [None, "mryx", "mercury", "auto"])
def test_shared_input_frame_selection(mryx_path, input_format):
    with pytest.warns(UserWarning, match="MRYX conservative import"):
        count = count_structure_frames(mryx_path, input_format=input_format)
        atomistic = load_atomistic_input(
            mryx_path, input_format=input_format, frame_indices=[-1, 0]
        )
        frames = list(
            iter_atomistic_frames(
                mryx_path, input_format=input_format, frame_indices=[1]
            )
        )
    assert count == 2
    assert atomistic.input_format == "mryx"
    assert atomistic.total_frames == 2
    assert [f.index for f in atomistic.frames] == [1, 0]
    assert [f.index for f, _ in frames] == [1]
    np.testing.assert_allclose(atomistic.frames[0].atoms.cell.lengths(), [7, 8, 10])
    assert atomistic.frames[0].atom_arrays["label"].tolist() == ["He47"]


def test_render_bundle_geometry_and_provenance(mryx_path):
    with pytest.warns(UserWarning, match="MRYX"):
        loaded = load_structure_input(mryx_path, frame_indices=[1])
    bundle = loaded.frames[0].bundle
    assert loaded.total_frames == 2
    assert loaded.frames[0].index == 1
    assert bundle.raw_atoms[0]["label"] == "He47"
    np.testing.assert_allclose(bundle.raw_atoms[0]["cart"], [1.4, 3.2, 6])
    np.testing.assert_allclose(bundle.M, np.diag([7, 8, 10]))
    assert bundle.frame_info["mryx"]["structure_index"] == 1
    assert bundle.scene["source_info"]["mryx"]["occupancy_semantics"] == "unspecified"
    assert "occupancy" not in bundle.atom_arrays


def test_single_bundle_and_legacy_upload(mryx_path, tmp_path):
    contents = (
        "data:application/octet-stream;base64,"
        + base64.b64encode(mryx_path.read_bytes()).decode()
    )
    with pytest.warns(UserWarning, match="MRYX"):
        bundle = build_loaded_crystal(
            name="custom-name",
            title="custom title",
            cif_path=str(mryx_path),
            source="upload",
        )
        uploaded = load_uploaded_cif(
            contents=contents,
            filename="synthetic.mryx",
            existing_names=[],
            upload_dir=str(tmp_path / "uploads"),
        )
    assert bundle.name == "custom-name"
    assert bundle.title == "custom title"
    assert bundle.scene["source_structure_count"] == 2
    assert bundle.frame_info["mryx"]["structure_index"] == 0
    assert bundle.scene["input_diagnostics"]
    assert uploaded.frame_info["mryx"]["structure_index"] == 0


def test_display_scene_keeps_import_diagnostics(mryx_path):
    from mat_viewer.loader.core import build_bundle_scene

    with pytest.warns(UserWarning, match="MRYX"):
        bundle = build_loaded_crystal(
            name="synthetic",
            cif_path=str(mryx_path),
            source="upload",
        )
    scene = build_bundle_scene(bundle, display_mode="formula_unit", show_hydrogen=True)
    assert scene["source_structure_count"] == 2
    assert scene["input_format"] == "mryx"
    assert scene["input_diagnostics"]
    assert scene["draw_atoms"][0]["label"] == "He47"


def test_overlong_synthetic_label_is_rejected(tmp_path):
    xml = synthetic_mryx_xml().replace("He47", "He" + ("X" * 130))
    path = tmp_path / "long-label.mryx"
    path.write_bytes(gzip.compress(xml.encode(), mtime=0))
    with pytest.raises(ValueError, match="label exceeds"):
        read_mryx_frames(path)


def synthetic_mryx_xml():
    return gzip.decompress(synthetic_mryx()).decode()


def test_iteration_parses_the_whole_document(monkeypatch, mryx_path):
    import molcrys_kit.io as io

    calls = []
    original = io.read_mryx_atoms

    def wrapped(path, index=0):
        calls.append(index)
        return original(path, index=index)

    monkeypatch.setattr(io, "read_mryx_atoms", wrapped)
    with pytest.warns(UserWarning, match="MRYX"):
        frames = list(iter_atomistic_frames(mryx_path, frame_indices=[1]))
    assert calls == [":"]
    assert [frame.index for frame, _fmt in frames] == [1]


def test_missing_upstream_is_actionable(monkeypatch, mryx_path):
    import molcrys_kit.io as io

    # The shared pin exports readers lazily. Removing the name from the
    # table, and from the module dict if a previous call cached it, is what
    # an older checkout without read_mryx_atoms looks like.
    exports = {
        name: target
        for name, target in io._EXPORTS.items()
        if name != "read_mryx_atoms"
    }
    monkeypatch.setattr(io, "_EXPORTS", exports)
    if "read_mryx_atoms" in io.__dict__:
        monkeypatch.delattr(io, "read_mryx_atoms")
    with pytest.raises(ValueError, match="declared MolCrysKit dependency"):
        read_mryx_frames(mryx_path)


def test_malformed_and_out_of_range(mryx_path, tmp_path):
    with pytest.warns(UserWarning, match="MRYX"):
        with pytest.raises(ValueError, match="out of range"):
            load_structure_input(mryx_path, frame_indices=[2])
    path = tmp_path / "bad.mryx"
    path.write_bytes(b"not XML")
    with pytest.raises(ValueError, match="MRYX"):
        load_structure_input(path)


def test_native_upload_suffix_and_path_safety(mryx_path, tmp_path):
    from mat_viewer.app import ViewerBackend
    from mat_viewer.presets import default_preset_path

    backend = ViewerBackend(preset_path=default_preset_path(), root_dir=str(tmp_path))
    backend._upload_sync_mode = True
    with pytest.warns(UserWarning, match="MRYX"):
        bundle = backend.add_uploaded_file_bytes(
            mryx_path.read_bytes(), "../../synthetic.mryx"
        )
    assert Path(bundle.cif_path).suffix == ".mryx"
    assert ".." not in Path(bundle.cif_path).name
    assert bundle.frame_info["mryx"]["structure_index"] == 0
    assert len(bundle.raw_atoms) == 1


def test_animation_time_keeps_structure_indices(mryx_path):
    from dataclasses import replace

    from mat_viewer.render.animation_time import (
        AnimationTimeSpec,
        resolve_animation_times,
    )
    from mat_viewer.render.batch_pipeline import load_frame_batches

    with pytest.warns(UserWarning, match="MRYX"):
        frames = load_frame_batches(
            mryx_path,
            input_format="mryx",
            type_map=None,
            frame_indices=[0, 1],
            repeat=(1, 1, 1),
        )
    assert [frame.source_index for frame in frames] == [0, 1]
    assert [frame.info["mryx"]["structure_index"] for frame in frames] == [0, 1]
    assert all("time_fs" not in frame.info for frame in frames)
    assert all("time_ps" not in frame.info for frame in frames)
    assert all("time_ns" not in frame.info for frame in frames)
    assert all("timestep" not in frame.info for frame in frames)
    with pytest.raises(ValueError, match="document indices"):
        resolve_animation_times(
            frames,
            AnimationTimeSpec(
                display_unit="ps", time_step=0.5, dump_frequency=1
            ),
        )
    explicit = tuple(
        replace(frame, info={**frame.info, "time_ps": float(frame.source_index)})
        for frame in frames
    )
    series = resolve_animation_times(
        explicit, AnimationTimeSpec(display_unit="ps")
    )
    assert series.source == "frame_info.time_ps"
    assert series.labels == ("t = 0 ps", "t = 1 ps")


def test_cli_render_common_pipeline(mryx_path, tmp_path):
    from mat_viewer.cli import main

    output = tmp_path / "synthetic.png"
    with pytest.warns(UserWarning, match="MRYX"):
        result = main(
            [
                "render",
                str(mryx_path),
                "--frame",
                "1",
                "-o",
                str(output),
                "--backend",
                "cpu",
            ]
        )
    assert result is None
    assert output.read_bytes().startswith(b"\x89PNG")
