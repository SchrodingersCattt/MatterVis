"""Representation slice: ``vdw`` radii and ``flat_illustration``.

The default ``ball_stick`` PNG hash is the CPU render of the water scene
below on main ``23470b2baf98664360284ef843fb9c68c49fce26``, before this
slice. A change to that hash means the default look moved.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from mat_viewer.cli import main
from mat_viewer.config import atom_radius, covalent_radius, reload_config, vdw_radius
from mat_viewer.config.colors import ATOM_RADIUS
from mat_viewer.presets import DEFAULT_STYLE
from mat_viewer.render.cli import _STYLES
from mat_viewer.render.contracts import CameraSpec, LinePrimitive, RenderSpec, TriangleMeshPrimitive
from mat_viewer.render.cpu.raster import render_png, render_rgba
from mat_viewer.render.planning import prepare_render
from mat_viewer.render.representations import REPRESENTATIONS
from mat_viewer.renderer import validate_style_schema

# Byte contract for default ball-and-stick water. See the module docstring.
_DEFAULT_BALL_STICK_PNG_SHA256 = (
    "affb411dadc5f5e8d9fb14b89774ac42ad9eeb6ad1cd5aba49e92d58fbf29b00"
)


def _water() -> dict:
    return {
        "atoms": [
            {
                "symbol": "O",
                "label": "O1",
                "cart": [0.0, 0.0, 0.0],
                "atom_radius": atom_radius("O"),
            },
            {
                "symbol": "H",
                "label": "H1",
                "cart": [0.96, 0.0, 0.0],
                "atom_radius": atom_radius("H"),
            },
            {
                "symbol": "H",
                "label": "H2",
                "cart": [-0.24, 0.93, 0.0],
                "atom_radius": atom_radius("H"),
            },
        ],
        "bonds": [
            {
                "i": 0,
                "j": 1,
                "start": [0.0, 0.0, 0.0],
                "end": [0.96, 0.0, 0.0],
                "vector_A": [0.96, 0.0, 0.0],
            },
            {
                "i": 0,
                "j": 2,
                "start": [0.0, 0.0, 0.0],
                "end": [-0.24, 0.93, 0.0],
                "vector_A": [-0.24, 0.93, 0.0],
            },
        ],
    }


def _methane() -> dict:
    directions = np.array(
        [
            [1.0, 1.0, 1.0],
            [1.0, -1.0, -1.0],
            [-1.0, 1.0, -1.0],
            [-1.0, -1.0, 1.0],
        ],
        dtype=float,
    )
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    atoms = [
        {
            "symbol": "C",
            "label": "C1",
            "cart": [0.0, 0.0, 0.0],
            "atom_radius": atom_radius("C"),
        }
    ]
    bonds = []
    for index, direction in enumerate(directions):
        cart = (direction * 1.09).tolist()
        atoms.append(
            {
                "symbol": "H",
                "label": f"H{index + 1}",
                "cart": cart,
                "atom_radius": atom_radius("H"),
            }
        )
        bonds.append(
            {
                "i": 0,
                "j": index + 1,
                "start": [0.0, 0.0, 0.0],
                "end": cart,
                "vector_A": cart,
            }
        )
    return {"atoms": atoms, "bonds": bonds}


def _render(scene: dict, representation: str, **extra):
    payload = {
        "representation": representation,
        "show_cell": False,
        "show_hydrogen": True,
        "width": 96,
        "height": 96,
        "scale": 1,
        "sphere_detail": (6, 8),
        "cylinder_sides": 6,
    }
    payload.update(extra)
    plan = prepare_render(scene, camera=CameraSpec(), render=payload)
    image = render_png(plan)
    rgba = render_rgba(plan, scale=1)
    return plan, image, rgba


def _element_radius(plan, element: str) -> float:
    radii = []
    for primitive in plan.primitives:
        if primitive.metadata.get("kind") != "atom":
            continue
        if primitive.metadata.get("element") != element:
            continue
        center = np.mean(primitive.vertices, axis=0)
        radii.append(float(np.max(np.linalg.norm(primitive.vertices - center, axis=1))))
    assert radii, f"no {element} sphere in the plan"
    return radii[0]


def _ink(rgba: np.ndarray) -> tuple[int, int]:
    mask = np.any(rgba[:, :, :3] < 250, axis=2)
    count = int(mask.sum())
    if count == 0:
        return 0, 0
    rows, cols = np.nonzero(mask)
    span = int(max(cols.max() - cols.min(), rows.max() - rows.min()))
    return count, span


def test_style_ids_match_the_render_contract():
    assert set(_STYLES) == REPRESENTATIONS
    assert RenderSpec().representation == "ball_stick"
    assert RenderSpec().shading == "smooth"
    assert "vdw" in _STYLES
    assert "flat_illustration" in _STYLES
    assert ATOM_RADIUS["C"] == 0.18


def test_vdw_radius_is_the_molcrys_bondi_table_not_a_local_whitelist():
    from molcrys_kit.constants import get_vdw_radius

    source = Path(vdw_radius.__code__.co_filename).read_text(encoding="utf-8")
    assert "molcrys_kit.constants" in source
    assert "Bondi" in source
    elements = Path(__file__).resolve().parents[2] / "mat_viewer" / "config" / "elements.py"
    assert "1.70" not in elements.read_text(encoding="utf-8")

    for symbol in ("C", "H", "N", "O", "Cl", "Fe"):
        tabulated = float(get_vdw_radius(symbol))
        assert vdw_radius(symbol) == pytest.approx(tabulated)
        assert vdw_radius(symbol) != pytest.approx(atom_radius(symbol))
        assert vdw_radius(symbol) != pytest.approx(covalent_radius(symbol))

    assert vdw_radius("C") == pytest.approx(1.70)
    assert vdw_radius("C") > 1.0
    with pytest.raises(ValueError, match="van der Waals table"):
        vdw_radius("D")


def test_vdw_radius_overrides_do_not_implement_the_representation():
    reload_config(overrides={"mck_overrides": {"vdw_radius_overrides": {"C": 0.18}}})
    try:
        assert vdw_radius("C") == pytest.approx(1.70)
    finally:
        reload_config("__missing_config__.toml")


def test_vdw_is_recorded_as_vdw_and_uses_tabulated_radii():
    scene = _methane()
    scene["atoms"][0]["atom_radius"] = 9.0
    plan, image, rgba = _render(scene, "vdw")
    assert plan.metadata["representation"] == "vdw"
    assert plan.metadata["shading"] == "smooth"
    assert _element_radius(plan, "C") == pytest.approx(1.70)
    assert _element_radius(plan, "H") == pytest.approx(1.20)
    assert all(primitive.metadata.get("kind") != "bond" for primitive in plan.primitives)
    assert image.output_sha256

    space_plan, space_image, space_rgba = _render(_methane(), "space_filling")
    ball_plan, ball_image, ball_rgba = _render(_methane(), "ball")
    assert space_plan.metadata["representation"] == "space_filling"
    assert ball_plan.metadata["representation"] == "ball"
    assert _element_radius(space_plan, "C") == pytest.approx(max(0.18, 0.2))
    assert _element_radius(ball_plan, "C") == pytest.approx(max(0.18, 0.2))
    assert _element_radius(space_plan, "C") < 0.5
    assert image.output_sha256 != space_image.output_sha256
    assert image.output_sha256 != ball_image.output_sha256

    vdw_count, vdw_span = _ink(rgba)
    space_count, space_span = _ink(space_rgba)
    ball_count, ball_span = _ink(ball_rgba)
    assert vdw_count > space_count * 3
    assert vdw_count > ball_count * 3
    assert vdw_span > space_span
    assert vdw_span > ball_span


def test_flat_illustration_is_not_the_flat_material_or_shading_flag():
    spec = RenderSpec(representation="flat_illustration")
    assert spec.representation == "flat_illustration"
    assert spec.shading == "smooth"

    style = validate_style_schema({**DEFAULT_STYLE, "style": "flat_illustration"})
    assert style["style"] == "flat_illustration"
    assert style["material"] == "mesh"
    assert style["fast_rendering"] is False

    plan, image, _rgba = _render(_methane(), "flat_illustration")
    assert plan.metadata["representation"] == "flat_illustration"
    assert plan.metadata["shading"] == "smooth"
    atoms = [
        primitive
        for primitive in plan.primitives
        if isinstance(primitive, TriangleMeshPrimitive)
        and primitive.metadata.get("kind") == "atom"
    ]
    assert atoms
    assert all(primitive.vertex_normals is not None for primitive in atoms)
    assert all(primitive.metadata.get("_raster_ambient") == 1.0 for primitive in atoms)
    assert all(primitive.metadata.get("_raster_diffuse") == 0.0 for primitive in atoms)
    assert any(
        isinstance(primitive, LinePrimitive) and primitive.metadata.get("kind") == "bond"
        for primitive in plan.primitives
    )

    shaded, shaded_image, _shaded_rgba = _render(_methane(), "ball_stick", shading="flat")
    assert shaded.metadata["shading"] == "flat"
    shaded_atoms = [
        primitive
        for primitive in shaded.primitives
        if isinstance(primitive, TriangleMeshPrimitive)
        and primitive.metadata.get("kind") == "atom"
    ]
    assert shaded_atoms
    assert all(primitive.vertex_normals is None for primitive in shaded_atoms)
    assert image.output_sha256 != shaded_image.output_sha256

    material_flat, material_image, _material_rgba = _render(
        _methane(),
        "ball_stick",
        material="flat",
    )
    assert material_flat.metadata["shading"] == "flat"
    assert material_flat.metadata["representation"] == "ball_stick"
    assert image.output_sha256 != material_image.output_sha256

    stick = validate_style_schema(
        {**DEFAULT_STYLE, "style": "ball_stick", "material": "flat", "shading": "smooth"}
    )
    assert stick["style"] == "ball_stick"
    assert stick["material"] == "flat"


def test_default_ball_stick_png_matches_main():
    explicit = RenderSpec(
        show_cell=False,
        show_hydrogen=True,
        width=96,
        height=96,
        scale=1,
        sphere_detail=(6, 8),
        cylinder_sides=6,
    )
    omitted = RenderSpec(
        representation="ball_stick",
        show_cell=False,
        show_hydrogen=True,
        width=96,
        height=96,
        scale=1,
        sphere_detail=(6, 8),
        cylinder_sides=6,
    )
    assert explicit.representation == "ball_stick"
    first = render_png(prepare_render(_water(), render=explicit))
    second = render_png(prepare_render(_water(), render=omitted))
    assert first.output_sha256 == _DEFAULT_BALL_STICK_PNG_SHA256
    assert second.output_sha256 == _DEFAULT_BALL_STICK_PNG_SHA256

    from mat_viewer.render.radius import atom_visual_radius

    assert atom_visual_radius(0.18, element="C", representation="ball_stick") == pytest.approx(
        0.18 * 0.46
    )
    assert atom_visual_radius(0.18, element="C", representation="ball") == pytest.approx(0.18)
    assert atom_visual_radius(
        0.18, element="C", representation="space_filling"
    ) == pytest.approx(0.18)
    assert atom_visual_radius(1.70, element="C", representation="vdw") == pytest.approx(1.70)


def test_unknown_element_is_not_given_a_display_radius(tmp_path: Path):
    scene = {
        "atoms": [
            {
                "symbol": "D",
                "label": "D1",
                "cart": [0.0, 0.0, 0.0],
                "atom_radius": 0.18,
            }
        ],
        "bonds": [],
    }
    with pytest.raises(ValueError, match="van der Waals table") as caught:
        prepare_render(scene, render={"representation": "vdw", "show_cell": False})
    message = str(caught.value)
    assert "0.18" not in message
    assert "0.77" not in message


def test_cli_check_records_new_styles_without_rewriting_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    output = tmp_path / "figure.png"
    source = tmp_path / "methane.cif"
    main(
        [
            "render",
            str(source),
            "-o",
            str(output),
            "--backend",
            "cpu",
            "--style",
            "vdw",
            "--check",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["render"]["representation"] == "vdw"
    assert payload["render"]["shading"] == "smooth"
    assert not output.exists()

    main(
        [
            "render",
            str(source),
            "-o",
            str(output),
            "--backend",
            "cpu",
            "--style",
            "flat_illustration",
            "--check",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["render"]["representation"] == "flat_illustration"
    assert payload["render"]["shading"] == "smooth"

    main(
        [
            "render",
            str(source),
            "-o",
            str(output),
            "--backend",
            "cpu",
            "--check",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["render"]["representation"] == "ball_stick"


def test_flat_illustration_traces_are_not_billboards():
    from mat_viewer.renderer import build_figure

    scene = {
        "draw_atoms": [
            {
                "label": "C1",
                "elem": "C",
                "cart": [0.0, 0.0, 0.0],
                "atom_radius": 0.18,
                "color": "#555555",
                "color_light": "#888888",
                "is_minor": False,
            },
            {
                "label": "H1",
                "elem": "H",
                "cart": [1.1, 0.0, 0.0],
                "atom_radius": 0.16,
                "color": "#DDDDDD",
                "color_light": "#EEEEEE",
                "is_minor": False,
            },
        ],
        "bonds": [
            {
                "i": 0,
                "j": 1,
                "start": [0.0, 0.0, 0.0],
                "end": [1.1, 0.0, 0.0],
                "color_i": "#555555",
                "color_j": "#DDDDDD",
                "is_minor": False,
            }
        ],
        "view_x": [1.0, 0.0, 0.0],
        "view_y": [0.0, 1.0, 0.0],
        "view_z": [0.0, 0.0, 1.0],
    }
    common = {
        "style": "flat_illustration",
        "disorder": "none",
        "atom_scale": 1.0,
        "bond_radius": 0.1,
        "show_axes": False,
        "show_labels": False,
        "show_unit_cell": False,
        "topology_enabled": False,
    }
    for material in ("mesh", "flat"):
        figure = build_figure(scene, {**common, "material": material})
        atom_meshes = [
            trace
            for trace in figure.data
            if trace.type == "mesh3d" and (trace.meta or {}).get("mv_role") == "atom"
        ]
        assert atom_meshes
        assert all(trace.flatshading is False for trace in atom_meshes)
        assert all(float(trace.lighting.diffuse) == 0.0 for trace in atom_meshes)
        assert not any(
            trace.type == "scatter3d"
            and "markers" in str(getattr(trace, "mode", ""))
            and (trace.meta or {}).get("mv_role") == "atom"
            for trace in figure.data
        )

    billboard = build_figure(
        scene,
        {**common, "style": "ball_stick", "material": "flat"},
    )
    assert any(
        trace.type == "scatter3d"
        and "markers" in str(getattr(trace, "mode", ""))
        and (trace.meta or {}).get("mv_role") == "atom"
        for trace in billboard.data
    )


def test_vdw_radius_is_tabulated_once_and_scaled_once(monkeypatch) -> None:
    """Traces and both viewport fitters share one van der Waals radius."""
    from molcrys_kit.constants import get_vdw_radius

    from mat_viewer import config
    from mat_viewer.render.radius import display_atom_radii, display_sphere_radius
    from mat_viewer.render.traces_atoms import _style_atom_radius
    from mat_viewer.render.viewport import _display_atom_radii as fitted_radii
    from mat_viewer.render.viewport_uniform import (
        _display_atom_radii as uniform_radii,
    )

    symbol = "Cs"
    tabulated = float(get_vdw_radius(symbol))
    scale = 1.25
    atom = {
        "elem": symbol,
        "symbol": symbol,
        "cart": [0.0, 0.0, 0.0],
        "atom_radius": 9.0,
    }
    style = {"style": "vdw", "atom_scale": scale, "bond_radius": 0.15}
    calls: list[str] = []
    real = config.vdw_radius

    def counting(name: str) -> float:
        calls.append(str(name))
        return real(name)

    monkeypatch.setattr(config, "vdw_radius", counting)
    drawn = display_sphere_radius(atom, style)
    assert calls == [symbol]
    assert drawn == pytest.approx(tabulated * scale)
    assert drawn != pytest.approx(9.0 * scale)
    calls.clear()
    assert _style_atom_radius(atom, style) == pytest.approx(drawn)
    assert calls == [symbol]
    calls.clear()
    fitted = display_atom_radii([atom], style)
    assert fitted[0] == pytest.approx(drawn)
    assert fitted_radii([atom], style)[0] == pytest.approx(drawn)
    assert uniform_radii([atom], style)[0] == pytest.approx(drawn)
    assert len(calls) == 3


def test_coordination_fit_is_the_same_in_both_viewports() -> None:
    from mat_viewer.render.viewport import _display_atom_radii as fitted_radii
    from mat_viewer.render.viewport_uniform import (
        _display_atom_radii as uniform_radii,
    )

    style = {"style": "ball_stick", "atom_scale": 1.0, "bond_radius": 0.2}
    site = {
        "elem": "C",
        "cart": [0.0, 0.0, 0.0],
        "atom_radius": 0.05,
        "_coordination_site": True,
    }
    plain = {**site, "_coordination_site": False}
    assert fitted_radii([site], style)[0] == pytest.approx(0.4)
    assert uniform_radii([site], style)[0] == pytest.approx(0.4)
    assert fitted_radii([plain], style)[0] == pytest.approx(0.05)
    assert uniform_radii([plain], style)[0] == pytest.approx(0.05)


def test_large_vdw_element_frames_orthographic_and_perspective_views() -> None:
    from argparse import Namespace
    from types import SimpleNamespace

    from mat_viewer.cli import _camera_spec
    from mat_viewer.config import vdw_radius
    from mat_viewer.render.radius import union_representation_bounds

    symbol = "Cs"
    tabulated = float(vdw_radius(symbol))
    atom = {
        "elem": symbol,
        "cart": [0.0, 0.0, 0.0],
        "atom_radius": 0.18,
        "_render_visible": True,
    }
    low, high = union_representation_bounds(
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0],
        [atom],
        representation="vdw",
        atom_scale=1.0,
    )
    assert low[0] == pytest.approx(-tabulated)
    assert high[0] == pytest.approx(tabulated)
    compact_low, compact_high = union_representation_bounds(
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0],
        [atom],
        representation="ball_stick",
        atom_scale=1.0,
    )
    assert float(high[0] - low[0]) > float(compact_high[0] - compact_low[0])

    bundle = SimpleNamespace(
        M=np.eye(3),
        scene={
            "bounds": {
                "center": [0.0, 0.0, 0.0],
                "mins": [0.0, 0.0, 0.0],
                "maxs": [0.0, 0.0, 0.0],
            },
            "draw_atoms": [atom],
        },
    )
    structure = SimpleNamespace(frames=(SimpleNamespace(bundle=bundle),))

    def camera(style: str, projection: str):
        return _camera_spec(
            structure,
            Namespace(
                output="figure.png",
                width=900,
                height=720,
                show_hydrogen=True,
                show_unit_cell=False,
                camera_axis=None,
                view_direction=(0.0, 0.0, 1.0),
                camera_position=None,
                camera_up=(0.0, 1.0, 0.0),
                camera_distance=1.8,
                projection=projection,
                style=style,
                atom_scale=1.0,
                bond_radius=0.15,
            ),
            display="cluster",
        )

    ortho_vdw = camera("vdw", "orthographic")
    ortho_stick = camera("ball_stick", "orthographic")
    perspective_vdw = camera("vdw", "perspective")
    perspective_stick = camera("ball_stick", "perspective")
    assert ortho_vdw.projection == "orthographic"
    assert perspective_vdw.projection == "perspective"
    assert ortho_vdw.ortho_scale > ortho_stick.ortho_scale
    assert ortho_vdw.ortho_scale > tabulated
    vdw_distance = float(
        np.linalg.norm(
            np.asarray(perspective_vdw.position) - np.asarray(perspective_vdw.target)
        )
    )
    stick_distance = float(
        np.linalg.norm(
            np.asarray(perspective_stick.position)
            - np.asarray(perspective_stick.target)
        )
    )
    assert vdw_distance > stick_distance
    assert vdw_distance > tabulated
