from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from ase import Atoms
from ase.io import write

from _atom_property_fixtures import write_lammps_sidecar_trajectory
from mat_viewer.cli import main


def test_inspect_properties_reports_bounded_array_metadata(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    atoms = Atoms("CO", positions=[[0, 0, 0], [1.2, 0, 0]])
    atoms.arrays["velocity"] = np.asarray([[1.0, 0, 0], [0, 2.0, 0]])
    path = tmp_path / "velocity.extxyz"
    write(path, atoms, format="extxyz")

    main(["inspect", str(path), "--properties", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "mattervis.atom-property-catalog/v1"
    assert payload["properties"] == [
        {
            "field": "array:velocity",
            "source": "array",
            "name": "velocity",
            "dtype": "float64",
            "shape_tail": [3],
            "components": [],
            "unit": None,
        }
    ]


def test_lammps_property_discovery_reads_only_the_index_header(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "frame.dump"
    path.write_text(
        "ITEM: TIMESTEP\n0\nITEM: NUMBER OF ATOMS\n2\n"
        "ITEM: BOX BOUNDS pp pp pp\n0 5\n0 5\n0 5\n"
        "ITEM: ATOMS id type x y z q vx vy vz\n"
        "1 1 0 0 0 -0.2 1 0 0\n2 1 1 0 0 0.2 0 2 0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        np,
        "loadtxt",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("inspect must not parse atom rows")
        ),
    )

    main(["inspect", str(path), "--properties", "--json"])

    fields = {
        item["field"] for item in json.loads(capsys.readouterr().out)["properties"]
    }
    assert {"column:q", "column:vx", "column:vy", "column:vz"} <= fields


def test_render_check_validates_and_serializes_property_spec(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source.xyz"
    source.write_text("1\nprobe\nC 0 0 0\n", encoding="utf-8")

    main(
        [
            "render",
            str(source),
            "-o",
            str(tmp_path / "out.png"),
            "--check",
            "--json",
            "--color-by",
            "array:charge",
            "--color-reduction",
            "component",
            "--color-component",
            "0",
            "--color-range",
            "-1",
            "1",
            "--color-center",
            "0",
            "--colormap",
            "coolwarm",
            "--color-unit",
            "e",
        ]
    )

    spec = json.loads(capsys.readouterr().out)["atom_property_color"]
    assert spec["component"] == 0
    assert spec["value_range"] == [-1.0, 1.0]
    assert spec["center"] == 0.0
    assert spec["unit"] == "e"


def test_render_check_rejects_unknown_colormap(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source.xyz"
    source.write_text("1\nprobe\nC 0 0 0\n", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        main(
            [
                "render",
                str(source),
                "-o",
                str(tmp_path / "out.png"),
                "--check",
                "--color-by",
                "charge",
                "--colormap",
                "definitely-not-a-colormap",
            ]
        )
    assert error.value.code == 2
    assert "unknown matplotlib colormap" in capsys.readouterr().err


def test_lammps_sidecar_property_discovery(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source, manifest = write_lammps_sidecar_trajectory(tmp_path)

    main(
        [
            "inspect",
            str(source),
            "--properties",
            "--property-data",
            str(manifest),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    velocity = next(
        item
        for item in payload["properties"]
        if item["field"] == "sidecar:velocity"
    )
    assert velocity == {
        "field": "sidecar:velocity",
        "source": "sidecar",
        "name": "velocity",
        "dtype": "float32",
        "shape_tail": [3],
        "components": ["x", "y", "z"],
        "unit": "angstrom/ps",
    }
    assert payload["manifest_hash"]


def test_lammps_sidecar_property_general_render(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source, manifest = write_lammps_sidecar_trajectory(tmp_path)
    output = tmp_path / "velocity.png"

    main(
        [
            "render",
            str(source),
            "-o",
            str(output),
            "--backend",
            "cpu",
            "--renderer",
            "general",
            "--frame",
            "1",
            "--style",
            "ball",
            "--width",
            "160",
            "--height",
            "120",
            "--property-data",
            str(manifest),
            "--color-by",
            "sidecar:velocity",
            "--color-reduction",
            "magnitude",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    metadata = payload["result"]["metadata"]["atom_property_color"]
    assert output.is_file() and output.stat().st_size > 0
    assert payload["source"]["selected_frames"] == [1]
    assert metadata["fields"] == ["sidecar:velocity"]
    assert metadata["reduction"] == "magnitude"
    assert metadata["range"] == [3.0, 4.0]
    assert metadata["unit"] == "angstrom/ps"
    assert metadata["finite_count"] == 2
    assert metadata["missing_count"] == 0
    assert metadata["manifest_hash"]
