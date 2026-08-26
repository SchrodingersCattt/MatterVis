from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_cli_animation_time import _write_two_frame_dump


def _environment() -> tuple[str, Path, dict[str, str]]:
    executable = shutil.which("mat-vis")
    assert executable is not None, "the installed mat-vis console script is required"
    repository = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(repository) + (
        os.pathsep + existing_pythonpath if existing_pythonpath else ""
    )
    return executable, repository, environment


def test_cli_animation_labels_generic_frame_fields(tmp_path: Path) -> None:
    imageio = pytest.importorskip("imageio.v2")
    source = tmp_path / "neb.dump"
    output = tmp_path / "neb.gif"
    table = tmp_path / "neb.csv"
    _write_two_frame_dump(source)
    table.write_text("rotation_deg\n12\n47\n", encoding="utf-8")
    executable, repository, environment = _environment()

    completed = subprocess.run(
        [
            executable,
            "render",
            str(source),
            "-o",
            str(output),
            "--input-format",
            "lammps-dump",
            "--type-map",
            "C",
            "N",
            "--frame-range",
            "0:2",
            "--no-cell",
            "--width",
            "192",
            "--height",
            "120",
            "--scale",
            "1",
            "--fps",
            "2",
            "--frame-field",
            "image=index,role=progress",
            "--frame-field",
            "lambda=linear:0:0.5,role=progress",
            "--frame-field",
            f"angle=table:{table}:rotation_deg,role=observable,unit=deg",
            "--frame-label",
            "image={image}  lambda={lambda:.1f}  rotation={angle:.0f} deg",
            "--json",
        ],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    payload = json.loads(completed.stdout)
    annotation = payload["result"]["metadata"]["frame_annotation"]
    assert annotation["displayed"] is True
    assert annotation["labels"] == [
        "image=0  lambda=0.0  rotation=12 deg",
        "image=1  lambda=0.5  rotation=47 deg",
    ]
    fields = {field["name"]: field for field in annotation["fields"]}
    assert fields["image"]["role"] == "progress"
    assert fields["lambda"]["values"] == pytest.approx([0.0, 0.5])
    assert fields["angle"]["unit"] == "deg"
    assert fields["angle"]["provenance"]["sha256"]
    assert payload["result"]["metadata"]["simulation_time"] == {"displayed": False}
    assert len(imageio.mimread(output)) == 2


def test_cli_rejects_incomplete_or_conflicting_frame_annotations(
    tmp_path: Path,
) -> None:
    source = tmp_path / "neb.dump"
    output = tmp_path / "neb.gif"
    _write_two_frame_dump(source)
    executable, repository, environment = _environment()
    base = [
        executable,
        "render",
        str(source),
        "-o",
        str(output),
        "--input-format",
        "lammps-dump",
        "--type-map",
        "C",
        "N",
        "--frame-range",
        "0:2",
        "--json",
    ]

    incomplete = subprocess.run(
        [*base, "--frame-field", "image=index"],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert incomplete.returncode != 0
    assert "must be used together" in incomplete.stderr

    conflicting = subprocess.run(
        [
            *base,
            "--display-time",
            "ps",
            "--time-step",
            "0.001",
            "--frame-field",
            "image=index",
            "--frame-label",
            "image={image}",
        ],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert conflicting.returncode != 0
    assert "cannot be combined" in conflicting.stderr
