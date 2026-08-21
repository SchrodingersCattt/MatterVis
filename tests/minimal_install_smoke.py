"""Run the browser-free MatterVis path in an installation without extras."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


OPTIONAL_IMPORTS = (
    "dash",
    "plotly",
    "kaleido",
    "textual",
    "skimage",
    "imageio",
)

CIF = """data_mattervis_minimal
_cell_length_a 8.0
_cell_length_b 8.0
_cell_length_c 8.0
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_space_group_name_H-M_alt 'P 1'
_space_group_IT_number 1
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
C1 C 0.45 0.50 0.50 1.0
O1 O 0.60 0.50 0.50 1.0
"""


def _assert_optional_modules_absent() -> None:
    imported = [name for name in OPTIONAL_IMPORTS if name in sys.modules]
    if imported:
        raise AssertionError(f"minimal import loaded optional modules: {imported}")


def _render(source: Path, output: Path) -> dict:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "mat_viewer.cli",
            "render",
            str(source),
            "-o",
            str(output),
            "--backend",
            "cpu",
            "--view",
            "unit_cell",
            "--no-cell",
            "--width",
            "96",
            "--height",
            "96",
            "--scale",
            "1",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    if payload["backend"] != "cpu":
        raise AssertionError(payload)
    return payload


def main() -> None:
    import mat_viewer
    import mat_viewer.app
    import mat_viewer.cube
    import mat_viewer.cube.core
    import mat_viewer.ortep
    import mat_viewer.render
    import mat_viewer.render.plotly
    import mat_viewer.renderer

    if importlib.util.find_spec("crystal_viewer") is not None:
        raise AssertionError(
            "wheel must not contain the removed crystal_viewer package"
        )
    if not callable(mat_viewer.render):
        raise AssertionError(
            "mat_viewer.render must remain callable after subpackage import"
        )
    _assert_optional_modules_absent()

    with TemporaryDirectory(prefix="mattervis-minimal-") as directory:
        root = Path(directory)
        source = root / "minimal.cif"
        source.write_text(CIF, encoding="utf-8")
        outputs = {
            "png": root / "minimal.png",
            "pdf": root / "minimal.pdf",
            "svg": root / "minimal.svg",
        }
        for output in outputs.values():
            _render(source, output)
        if not outputs["png"].read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise AssertionError("CPU PNG signature is invalid")
        if not outputs["pdf"].read_bytes().startswith(b"%PDF"):
            raise AssertionError("CPU PDF signature is invalid")
        svg = outputs["svg"].read_text(encoding="utf-8").lower()
        if "<svg" not in svg or "<image" in svg:
            raise AssertionError("CPU SVG must be true vector output")

    _assert_optional_modules_absent()
    print("minimal MatterVis CPU PNG/PDF/SVG smoke test passed")


if __name__ == "__main__":
    main()
