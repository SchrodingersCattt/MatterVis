from __future__ import annotations

from dataclasses import dataclass
import builtins
import json
from pathlib import Path

import numpy as np
import pytest

from mat_viewer.cube.cpu import cube_isosurface_meshes, ensure_cube_isosurfaces
from mat_viewer.cube.io import CubeAtom, CubeData
from mat_viewer.cli import main


def _cube(tmp_path: Path) -> CubeData:
    coordinates = np.indices((7, 7, 7), dtype=float)
    radius = np.sqrt(sum((coordinates[axis] - 3.0) ** 2 for axis in range(3)))
    values = 2.0 - radius
    return CubeData(
        title="test",
        comment="",
        atoms=[],
        origin=np.zeros(3),
        axes=np.eye(3) * 0.2,
        values=values,
        path=tmp_path / "test.cube",
    )


def test_cube_extra_produces_backend_neutral_positive_and_negative_meshes(
    tmp_path: Path,
) -> None:
    meshes = cube_isosurface_meshes(_cube(tmp_path), isovalue=0.5)

    assert {mesh["id"] for mesh in meshes} == {"cube:positive", "cube:negative"}
    for mesh in meshes:
        assert mesh["vertices"].ndim == 2
        assert mesh["vertices"].shape[1] == 3
        assert mesh["triangles"].ndim == 2
        assert mesh["triangles"].shape[1] == 3
        assert mesh["normals"].shape == mesh["vertices"].shape


def test_cube_meshes_are_attached_to_the_loaded_scene(tmp_path: Path) -> None:
    @dataclass
    class Bundle:
        cube_data: CubeData
        scene: dict
        scene_cache: dict

    @dataclass
    class Frame:
        bundle: Bundle

    @dataclass
    class Structure:
        frames: tuple[Frame, ...]
        input_format: str = "cube"

    scene: dict = {}
    bundle = Bundle(_cube(tmp_path), scene, {("unit_cell", False): scene})
    structure = Structure((Frame(bundle),))

    assert ensure_cube_isosurfaces(structure) is structure
    assert scene["isosurfaces"]
    assert bundle.cube_data.surface_meshes is scene["isosurfaces"]


def _write_cube(path: Path) -> None:
    coordinates = np.indices((7, 7, 7), dtype=float)
    radius = np.sqrt(sum((coordinates[axis] - 3.0) ** 2 for axis in range(3)))
    values = (2.0 - radius).ravel()
    value_lines = [
        " ".join(f"{value:.8e}" for value in values[index : index + 6])
        for index in range(0, len(values), 6)
    ]
    path.write_text(
        "\n".join(
            [
                "MatterVis cube integration",
                "positive and negative radial field",
                "1 0.0 0.0 0.0",
                "7 0.4 0.0 0.0",
                "7 0.0 0.4 0.0",
                "7 0.0 0.0 0.4",
                "6 0.0 1.2 1.2 1.2",
                *value_lines,
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_cube_cli_preflight_and_cpu_render_are_plotly_free(
    tmp_path: Path, capsys
) -> None:
    pytest.importorskip("skimage")
    source = tmp_path / "orbital.cube"
    output = tmp_path / "orbital.png"
    _write_cube(source)

    import mat_viewer.capabilities as capability_module

    if capability_module.molcryskit_contract_missing():
        pytest.skip("requires the exact MolCrysKit structure-contract commit")

    main(
        [
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
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert payload["backend"] == "cpu"
    assert payload["source"]["input_format"] == "cube"
    assert payload["install"] == 'python -m pip install "matter-vis[cube]"'


def test_explicit_legacy_cube_trace_fails_when_cube_extra_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    from mat_viewer.render.traces_isosurface import isosurface_overlay_traces

    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name.startswith("skimage"):
            raise ImportError("test blocked scikit-image")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    with pytest.raises(ImportError, match=r"matter-vis\[cube\]"):
        isosurface_overlay_traces(
            {"cube_data": _cube(tmp_path)},
            {"isosurface_enabled": True},
        )


def test_public_cube_facade_excludes_bond_reinference_builders() -> None:
    import mat_viewer.cube as cube_module

    removed = {"bond_traces", "build_orbital_figure", "build_orbital_panel_figure"}
    assert removed.isdisjoint(cube_module.__all__)
    for name in removed:
        assert not hasattr(cube_module, name)


def test_cube_structure_bridge_preserves_mck_boundary_bond(tmp_path: Path) -> None:
    from mat_viewer.loader.cube_adapter import build_loaded_crystal_from_cube

    cube = CubeData(
        title="periodic bond",
        comment="canonical MCK bond contract",
        atoms=[
            CubeAtom(6, 0.0, np.asarray([9.8, 5.0, 5.0])),
            CubeAtom(6, 0.0, np.asarray([0.2, 5.0, 5.0])),
        ],
        origin=np.zeros(3),
        axes=np.eye(3),
        values=np.zeros((10, 10, 10)),
        path=tmp_path / "periodic.cube",
    )

    bundle = build_loaded_crystal_from_cube(cube)

    assert bundle.molcrys_analysis.bond_pairs == [(0, 1)]
    assert bundle.molcrys_analysis.bond_records[0]["right_image_shift"] == [1, 0, 0]
    assert bundle.scene["bonds"][0]["end"][0] == pytest.approx(10.2)
