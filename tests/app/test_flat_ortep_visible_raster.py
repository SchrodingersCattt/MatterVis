"""The flat ORTEP publication raster must remain visible.

An opaque scene background covers the paper image and leaves only the
caption. The raster itself can still contain the drawing.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mat_viewer.app import ViewerBackend


def _cif(name: str, a: float, b: float, c: float, sites: list[tuple]) -> str:
    rows = "\n".join(
        f"{label} {elem} {x:.4f} {y:.4f} {z:.4f} 1 {uiso:.4f}"
        for label, elem, x, y, z, uiso, _diag in sites
    )
    aniso = "\n".join(
        f"{label} {diag[0]:.4f} {diag[1]:.4f} {diag[2]:.4f} 0 0 0"
        for label, _elem, _x, _y, _z, _uiso, diag in sites
        if diag is not None
    )
    aniso_loop = ""
    if aniso:
        aniso_loop = (
            "loop_\n"
            "_atom_site_aniso_label\n"
            "_atom_site_aniso_U_11\n"
            "_atom_site_aniso_U_22\n"
            "_atom_site_aniso_U_33\n"
            "_atom_site_aniso_U_12\n"
            "_atom_site_aniso_U_13\n"
            "_atom_site_aniso_U_23\n"
            f"{aniso}\n"
        )
    return f"""data_{name}
_cell_length_a {a}
_cell_length_b {b}
_cell_length_c {c}
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
_atom_site_U_iso_or_equiv
{rows}
{aniso_loop}"""


def _backend(tmp_path: Path, structures: dict[str, str]) -> ViewerBackend:
    folder = tmp_path / "structures"
    folder.mkdir()
    catalog = {}
    for name, text in structures.items():
        path = folder / f"{name}.cif"
        path.write_text(text, encoding="utf-8")
        catalog[name] = {"cif_path": str(path), "title": name}
    local = tmp_path / ".local"
    local.mkdir()
    (local / "catalog.local.json").write_text(
        json.dumps({"structures": catalog}),
        encoding="utf-8",
    )
    return ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))


def _ink_fraction(source: str) -> float:
    raw = base64.b64decode(source.split(",", 1)[1])
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    pixels = np.asarray(image)
    white = np.all(pixels >= 250, axis=2)
    return float((~white).mean())


def _alpha(color: str) -> float:
    text = str(color).strip().lower().replace(" ", "")
    if text == "transparent":
        return 0.0
    if text.startswith("rgba(") and text.endswith(")"):
        parts = text[5:-1].split(",")
        if len(parts) == 4:
            return float(parts[3])
    if text.startswith("#") and len(text) == 9:
        return int(text[7:9], 16) / 255.0
    return 1.0


def _assert_visible_raster(payload: dict) -> None:
    scene = payload["layout"]["scene"]
    assert _alpha(scene.get("bgcolor")) == 0.0
    images = payload["layout"].get("images") or []
    assert images
    assert _ink_fraction(images[0]["source"]) > 0.01
    caption = " ".join(
        str(item.get("text") or "") for item in payload["layout"].get("annotations") or []
    )
    assert "2D ORTEP Publication Mode" in caption


def test_flat_ortep_raster_stays_visible_across_structures_and_controls(tmp_path: Path):
    structures = {
        "aniso_small": _cif(
            "aniso_small",
            10,
            10,
            10,
            [
                ("O1", "O", 0.50, 0.50, 0.50, 0.02, (0.01, 0.02, 0.03)),
                ("H1", "H", 0.62, 0.50, 0.50, 0.03, (0.04, 0.02, 0.02)),
                ("H2", "H", 0.46, 0.60, 0.50, 0.03, (0.02, 0.04, 0.02)),
            ],
        ),
        "iso_long": _cif(
            "iso_long",
            40,
            12,
            18,
            [
                ("C1", "C", 0.20, 0.40, 0.30, 0.05, None),
                ("C2", "C", 0.28, 0.42, 0.31, 0.04, None),
                ("N1", "N", 0.35, 0.55, 0.40, 0.06, None),
            ],
        ),
        "aniso_shifted": _cif(
            "aniso_shifted",
            6,
            22,
            9,
            [
                ("C1", "C", 0.10, 0.80, 0.15, 0.03, (0.02, 0.01, 0.04)),
                ("C2", "C", 0.25, 0.70, 0.20, 0.04, (0.03, 0.03, 0.02)),
                ("N1", "N", 0.18, 0.62, 0.28, 0.04, (0.02, 0.04, 0.02)),
            ],
        ),
    }
    backend = _backend(tmp_path, structures)
    try:
        for scene_id in backend.scene_store.order:
            backend.patch_state(
                {"material": "mesh", "style": "ball_stick"},
                scene_id=scene_id,
                broadcast=False,
            )
            mesh, _ = backend.figure_for_state(backend.get_state(scene_id))
            mesh_scene = mesh.to_plotly_json()["layout"]["scene"]
            assert _alpha(mesh_scene.get("bgcolor", "#FFFFFF")) == 1.0

            backend.patch_state(
                {"material": "flat", "style": "ball_stick"},
                scene_id=scene_id,
                broadcast=False,
            )
            flat_stick, _ = backend.figure_for_state(backend.get_state(scene_id))
            stick_payload = flat_stick.to_plotly_json()
            assert not (stick_payload["layout"].get("images") or [])

            for projection in ("perspective", "orthographic", "perspective"):
                backend.patch_state(
                    {"material": "flat", "style": "ortep", "projection": projection},
                    scene_id=scene_id,
                    broadcast=False,
                )
                first, _ = backend.figure_for_state(backend.get_state(scene_id))
                second, _ = backend.figure_for_state(backend.get_state(scene_id))
                _assert_visible_raster(first.to_plotly_json())
                _assert_visible_raster(second.to_plotly_json())

            backend.patch_state(
                {
                    "camera": {
                        "position": [4.0, -6.0, 9.0],
                        "focal_point": [1.0, 2.0, 0.5],
                        "up": [0.0, 0.0, 1.0],
                    }
                },
                scene_id=scene_id,
                broadcast=False,
            )
            moved, _ = backend.figure_for_state(backend.get_state(scene_id))
            _assert_visible_raster(moved.to_plotly_json())
            stored = backend.get_state(scene_id)["camera"]
            assert "position" in stored

            backend.patch_state(
                {"material": "mesh", "style": "ortep"},
                scene_id=scene_id,
                broadcast=False,
            )
            mesh_ortep, _ = backend.figure_for_state(backend.get_state(scene_id))
            mesh_payload = mesh_ortep.to_plotly_json()
            assert not (mesh_payload["layout"].get("images") or [])
            assert _alpha(mesh_payload["layout"]["scene"].get("bgcolor", "#FFFFFF")) == 1.0
    finally:
        backend.close()


def test_flat_ortep_without_displacement_parameters_still_raises(tmp_path: Path):
    text = """data_plain
_cell_length_a 8
_cell_length_b 8
_cell_length_c 8
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
C1 C 0.2 0.2 0.2 1
C2 C 0.4 0.3 0.2 1
"""
    backend = _backend(tmp_path, {"plain": text})
    try:
        scene_id = backend.scene_store.order[0]
        backend.patch_state(
            {"material": "flat", "style": "ortep"},
            scene_id=scene_id,
            broadcast=False,
        )
        with pytest.raises(ValueError, match="no ADP data"):
            backend.figure_for_state(backend.get_state(scene_id))
    finally:
        backend.close()
