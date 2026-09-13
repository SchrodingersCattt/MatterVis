"""Strict alignment and safety tests for atom-property sidecars."""

from __future__ import annotations

from dataclasses import dataclass, replace as dataclass_replace
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pytest

from _atom_property_fixtures import write_lammps_sidecar_trajectory
from mat_viewer.loader.property_sidecar import (
    align_sidecar_property,
    load_atom_property_manifest,
    strict_alignment_order,
)
from mat_viewer.properties import (
    AtomPropertyColorSpec,
    reduce_frame_batch_property,
    resolve_frame_batch_property_context,
    resolve_source_property_context,
)
from mat_viewer.render.batch_pipeline import load_frame_batches
from mat_viewer.loader.structure_input import load_structure_input


@dataclass
class _Frame:
    index: int
    info: dict
    atom_arrays: dict


def _manifest(tmp_path: Path, *, atom_key: str = "id", source_sha: str | None = None):
    np.save(tmp_path / "timesteps.npy", np.asarray([10, 20]))
    np.save(tmp_path / "atom_ids.npy", np.asarray([[2, 1], [2, 1]]))
    np.save(
        tmp_path / "charge.npy",
        np.asarray([[20.0, 10.0], [40.0, 30.0]], dtype=np.float32),
    )
    payload = {
        "schema": "mattervis.atom-properties/v1",
        "source": {"sha256": source_sha},
        "frames": {"key": "timestep", "ids": "timesteps.npy"},
        "atoms": {"key": atom_key, "ids": "atom_ids.npy"},
        "properties": {
            "charge": {
                "values": "charge.npy",
                "unit": "e",
                "components": [],
            }
        },
    }
    (tmp_path / "properties.json").write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path / "properties.json"


def test_id_alignment_reorders_each_selected_frame(tmp_path: Path) -> None:
    source = tmp_path / "run.dump"
    source.write_text("source", encoding="utf-8")
    manifest = load_atom_property_manifest(_manifest(tmp_path))
    frames = (
        _Frame(3, {"timestep": 20}, {"id": np.asarray([1, 2])}),
        _Frame(1, {"timestep": 10}, {"id": np.asarray([1, 2])}),
    )
    aligned = align_sidecar_property(manifest, "charge", frames, source_path=source)
    assert [item.frame_index for item in aligned] == [3, 1]
    assert aligned[0].values.tolist() == [30.0, 40.0]
    assert aligned[1].values.tolist() == [10.0, 20.0]


@pytest.mark.parametrize(
    ("source", "sidecar", "message"),
    [
        ([1, 1], [1, 2], "source id values contain duplicate"),
        ([1, 2], [1, 1], "sidecar id values contain duplicate"),
        ([1, 2], [1, 3], "set differs"),
        ([1, 2], [1, 2, 3], "set differs"),
    ],
)
def test_strict_id_failures(source, sidecar, message) -> None:
    with pytest.raises(ValueError, match=message):
        strict_alignment_order(source, sidecar, key_name="id")


def test_frame_id_mismatch_fails(tmp_path: Path) -> None:
    source = tmp_path / "run.dump"
    source.write_text("source", encoding="utf-8")
    manifest = load_atom_property_manifest(_manifest(tmp_path))
    frame = _Frame(0, {"timestep": 99}, {"id": np.asarray([1, 2])})
    with pytest.raises(ValueError, match="absent from sidecar"):
        align_sidecar_property(manifest, "charge", [frame], source_path=source)


def test_row_alignment_requires_and_checks_source_hash(tmp_path: Path) -> None:
    source = tmp_path / "structure.xyz"
    source.write_bytes(b"two atoms")
    values = np.asarray([1.0, 2.0], dtype=np.float32)
    np.save(tmp_path / "charge.npy", values)
    payload = {
        "schema": "mattervis.atom-properties/v1",
        "source": {"sha256": sha256(b"different").hexdigest()},
        "atoms": {"key": "row"},
        "properties": {"charge": {"values": "charge.npy"}},
    }
    manifest_path = tmp_path / "row.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    manifest = load_atom_property_manifest(manifest_path)
    frame = _Frame(0, {}, {"x": np.zeros(2)})
    with pytest.raises(ValueError, match="SHA-256"):
        align_sidecar_property(manifest, "charge", [frame], source_path=source)


def test_object_npy_is_rejected_without_pickle(tmp_path: Path) -> None:
    np.save(tmp_path / "bad.npy", np.asarray([{"unsafe": True}], dtype=object))
    np.save(tmp_path / "ids.npy", np.asarray([1]))
    payload = {
        "schema": "mattervis.atom-properties/v1",
        "source": {"sha256": None},
        "atoms": {"key": "id", "ids": "ids.npy"},
        "properties": {"bad": {"values": "bad.npy"}},
    }
    manifest_path = tmp_path / "bad.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe or invalid NPY"):
        load_atom_property_manifest(manifest_path)


def test_lammps_trajectory_sidecar_aligns_frames_ids_and_velocity(
    tmp_path: Path,
) -> None:
    source, manifest_path = write_lammps_sidecar_trajectory(tmp_path)
    frames = load_frame_batches(
        source,
        input_format="lammps-dump",
        type_map=None,
        frame_indices=(0, 1),
        repeat=(1, 1, 1),
    )
    manifest = load_atom_property_manifest(manifest_path)

    context = resolve_frame_batch_property_context(
        frames,
        AtomPropertyColorSpec(fields=("sidecar:velocity",)),
        input_path=str(source),
        embedded_source="column",
        manifest=manifest,
    )

    assert [frame.timestep for frame in frames] == [10, 20]
    assert [frame.atom_ids.tolist() for frame in frames] == [[2, 1], [1, 2]]
    assert context.frames[0].values.tolist() == pytest.approx([2.0, 1.0])
    assert context.frames[1].values.tolist() == pytest.approx([3.0, 4.0])
    assert context.frames[0].reduction == "magnitude"
    assert context.frames[0].unit == "angstrom/ps"
    assert context.scale.value_range == (1.0, 4.0)
    assert context.manifest_hash == manifest.manifest_hash

    worker_frame = reduce_frame_batch_property(
        frames[1],
        AtomPropertyColorSpec(fields=("sidecar:velocity",)),
        input_path=str(source),
        embedded_source="column",
        manifest=manifest,
        sidecar_data={
            "properties": {"velocity": manifest.open_property("velocity")},
            "frame_ids": manifest.frame_ids(),
            "atom_ids": manifest.atom_ids(),
        },
    )
    assert worker_frame.values.tolist() == pytest.approx([3.0, 4.0])

    structure = load_structure_input(
        source,
        input_format="lammps-dump",
        frame_indices=(0, 1),
    )
    structure = dataclass_replace(structure, property_manifest=manifest)
    general_context = resolve_source_property_context(
        structure,
        AtomPropertyColorSpec(fields=("sidecar:velocity",)),
    )
    assert structure.frames[0].info["timestep"] == 10
    assert structure.frames[0].atom_arrays["id"].tolist() == [1, 2]
    assert general_context.frames[0].values.tolist() == pytest.approx([1.0, 2.0])
    assert general_context.frames[1].values.tolist() == pytest.approx([3.0, 4.0])
