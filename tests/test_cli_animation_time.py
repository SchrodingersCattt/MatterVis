from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


def _write_two_frame_dump(path: Path) -> None:
    path.write_text(
        """ITEM: TIMESTEP
100
ITEM: NUMBER OF ATOMS
2
ITEM: BOX BOUNDS pp pp pp
0 5
0 5
0 5
ITEM: ATOMS id type x y z
1 1 1.0 1.0 1.0
2 2 2.1 1.0 1.0
ITEM: TIMESTEP
160
ITEM: NUMBER OF ATOMS
2
ITEM: BOX BOUNDS pp pp pp
0 5
0 5
0 5
ITEM: ATOMS id type x y z
1 1 1.2 1.0 1.0
2 2 2.3 1.0 1.0
""",
        encoding="utf-8",
    )


def test_cli_animation_labels_real_lammps_timesteps(tmp_path: Path) -> None:
    imageio = pytest.importorskip("imageio.v2")
    pillow = pytest.importorskip("PIL.Image")
    source = tmp_path / "run.dump"
    output = tmp_path / "time.gif"
    _write_two_frame_dump(source)

    executable = shutil.which("mat-vis")
    assert executable is not None, "the installed mat-vis console script is required"
    repository = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(repository) + (
        os.pathsep + existing_pythonpath if existing_pythonpath else ""
    )
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
            "96",
            "--height",
            "80",
            "--scale",
            "1",
            "--fps",
            "2",
            "--display-time",
            "ps",
            "--time-step",
            "0.5",
            "--time-step-unit",
            "fs",
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
    timing = payload["result"]["metadata"]["simulation_time"]
    assert timing["source"] == "frame_info.timestep"
    assert timing["simulation_steps"] == [100.0, 160.0]
    assert timing["values"] == pytest.approx([0.05, 0.08])
    assert timing["labels"] == ["t = 0.05 ps", "t = 0.08 ps"]
    assert payload["result"]["metadata"]["fps"] == 2.0
    assert len(imageio.mimread(output)) == 2

    with pillow.open(output) as animation:
        durations = []
        for frame_index in range(animation.n_frames):
            animation.seek(frame_index)
            durations.append(animation.info.get("duration"))
    assert durations == [500, 500]
