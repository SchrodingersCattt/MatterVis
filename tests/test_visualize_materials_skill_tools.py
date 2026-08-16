from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "visualize-materials" / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SCRIPTS))
    return module


verified = _load("render_verified")


def test_png_analysis_rejects_nonempty_all_white_file(tmp_path: Path) -> None:
    blank = tmp_path / "blank.png"
    Image.new("RGB", (400, 300), "white").save(blank)

    stats = verified.analyze_png(blank)

    assert stats["blank"] is True
    assert stats["foreground_pixels"] == 0
    assert stats["foreground_bbox"] is None


def test_png_analysis_records_foreground_geometry(tmp_path: Path) -> None:
    output = tmp_path / "content.png"
    image = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(image).rectangle((40, 30, 359, 269), fill="#3366CC")
    image.save(output)

    stats = verified.analyze_png(output)

    assert stats["blank"] is False
    assert stats["foreground_bbox"] == [40, 30, 360, 270]
    assert stats["foreground_fraction"] == pytest.approx(0.64)


def test_png_analysis_uses_declared_nonwhite_background(tmp_path: Path) -> None:
    output = tmp_path / "colored-background.png"
    image = Image.new("RGB", (200, 100), "#203040")
    ImageDraw.Draw(image).rectangle((20, 10, 179, 89), fill="#CC3333")
    image.save(output)

    stats = verified.analyze_png(output, background=(32, 48, 64))

    assert stats["blank"] is False
    assert stats["foreground_bbox"] == [20, 10, 180, 90]


def test_backend_classification_uses_captured_fallback() -> None:
    parsed = verified._parse_command(
        [
            "mat-vis",
            "render",
            "input.cif",
            "-o",
            "output.png",
            "--style",
            "ball_stick",
            "--material",
            "mesh",
        ]
    )

    effective, reason, warnings = verified._classify_backend(
        parsed,
        "",
        "Plotly/Kaleido static export is unavailable (missing browser).\n"
        "Falling back to Matplotlib flat ORTEP output; "
        "the visual style differs from the requested Plotly rendering.\n",
    )

    assert effective["backend"] == "matplotlib-flat-ortep"
    assert effective["evidence"] == "captured CLI fallback message"
    assert reason and "missing browser" in reason
    assert warnings == ["export"]


def _fake_mat_vis(path: Path) -> None:
    path.write_text(
        f"""#!{sys.executable}
import sys
from PIL import Image, ImageDraw

args = sys.argv[1:]
output = args[args.index("-o") + 1]
image = Image.new("RGB", (200, 100), "white")
if "blank" not in output:
    ImageDraw.Draw(image).rectangle((20, 10, 179, 89), fill="#CC3333")
image.save(output)
print("Wrote png :", output)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


@pytest.mark.parametrize(
    ("stem", "expected_code", "expected_status", "expected_blank"),
    [
        ("valid", 0, "ok", False),
        ("blank", 4, "failed", True),
    ],
)
def test_verified_wrapper_writes_evidence_and_rejects_blank(
    tmp_path: Path,
    stem: str,
    expected_code: int,
    expected_status: str,
    expected_blank: bool,
) -> None:
    executable = tmp_path / "mat-vis"
    _fake_mat_vis(executable)
    input_path = tmp_path / "input.cif"
    input_path.write_text("data_test\n", encoding="utf-8")
    output = tmp_path / f"{stem}.png"
    manifest = tmp_path / f"{stem}.manifest.json"

    run = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "render_verified.py"),
            "--manifest",
            str(manifest),
            "--min-bbox-coverage",
            "0.70",
            "--crop-padding",
            "5",
            "--",
            str(executable),
            "render",
            str(input_path),
            "-o",
            str(output),
            "--style",
            "ball_stick",
            "--material",
            "mesh",
            "--width",
            "200",
            "--height",
            "100",
            "--scale",
            "1",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    evidence = json.loads(manifest.read_text(encoding="utf-8"))
    assert run.returncode == expected_code
    assert evidence["status"] == expected_status
    assert evidence["output"]["blank"] is expected_blank
    assert evidence["effective"]["backend"] == "plotly-kaleido"
    if expected_blank:
        assert evidence["output"]["crop"]["applied"] is False
    else:
        assert evidence["output"]["crop"] == {
            "applied": True,
            "background": [255, 255, 255],
            "crop_box": [15, 5, 185, 95],
            "cropped_dimensions": [170, 90],
            "original_dimensions": [200, 100],
            "padding": 5,
            "rescaled": False,
        }
        assert evidence["output"]["width"] == 170
        assert evidence["output"]["height"] == 90
    assert Path(evidence["logs"]["stdout"]).is_file()
    assert Path(evidence["logs"]["stderr"]).is_file()


def test_installer_keeps_venv_optional() -> None:
    run = subprocess.run(
        ["bash", str(SCRIPTS / "install_runtime.sh"), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Usage: install_runtime.sh [options]" in run.stdout
    assert "--venv ABSOLUTE_PATH" in run.stdout
    assert "--venv ABSOLUTE_PATH [options]" not in run.stdout
