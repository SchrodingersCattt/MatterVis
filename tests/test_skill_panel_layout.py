from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visualize-materials" / "scripts" / "check_panel_layout.py"


def _module():
    spec = importlib.util.spec_from_file_location("check_panel_layout", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_equal_boundaries_cover_full_width() -> None:
    module = _module()
    assert module.equal_boundaries(100, 3) == [0, 33, 66, 100]


def test_measure_panel_reports_bbox_occupancy_and_pads() -> None:
    module = _module()
    rgb = np.full((100, 120, 3), 255, dtype=np.uint8)
    rgb[20:80, 20:100] = 50
    result = module.measure_panel(
        rgb,
        0,
        120,
        background=np.array([255, 255, 255], dtype=np.uint8),
        tolerance=10,
        min_occupancy=0.30,
        max_occupancy=0.95,
        min_pad=20,
    )
    assert result["ink_bbox_local_px"] == [20, 20, 100, 80]
    assert result["safety_pad_px"] == {"left": 20, "top": 20, "right": 20, "bottom": 20}
    assert result["bbox_occupancy_fraction"] == 0.4
    assert result["pass"]


def test_checker_script_exists_and_decodes_png(tmp_path: Path) -> None:
    assert SCRIPT.is_file()
    png = tmp_path / "panel.png"
    image = np.full((80, 160, 3), 255, dtype=np.uint8)
    image[20:60, 20:70] = 0
    image[20:60, 90:140] = 0
    Image.fromarray(image).save(png)
    module = _module()
    decoded = np.asarray(Image.open(png).convert("RGB"))
    first = module.measure_panel(
        decoded,
        0,
        80,
        background=np.array([255, 255, 255], dtype=np.uint8),
        tolerance=10,
        min_occupancy=0.20,
        max_occupancy=0.95,
        min_pad=10,
    )
    assert first["ink_present"]
    assert first["pass"]
