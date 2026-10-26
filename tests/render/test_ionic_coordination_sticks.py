"""Rock-salt ball-and-stick must show a complete octahedral coordination net."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pytest
from ase.build import bulk
from ase.io import write

from mat_viewer.agent import prepare_render
from mat_viewer.cli import (
    _build_render_parser,
    _display_mode,
    _resolve_omitted_render_style,
)
from mat_viewer.loader import build_bundle_scene
from mat_viewer.loader.structure_input import load_structure_input
from mat_viewer.structure.bonds import prefers_lattice_ball_default

_LATTICE = 5.6402
_HALF = _LATTICE / 2.0


def _write_rocksalt(path: Path) -> None:
    structure = bulk("NaCl", "rocksalt", a=_LATTICE, cubic=True)
    write(path, structure, format="vasp", direct=True, vasp5=True)


def _loaded(path: Path):
    return load_structure_input(path)


def _home_atoms(scene: dict) -> list[tuple[int, dict]]:
    homes = []
    for index, atom in enumerate(scene["draw_atoms"]):
        if atom.get("_is_framework_context_replica"):
            continue
        if atom.get("_is_boundary_replica") or atom.get("_is_bonded_image_replica"):
            continue
        homes.append((index, atom))
    return homes


def _neighbor_vectors(scene: dict, index: int) -> list[np.ndarray]:
    vectors = []
    for bond in scene["bonds"]:
        if bond["i"] == index:
            vectors.append(np.asarray(bond["end"] - bond["start"], dtype=float))
        elif bond["j"] == index:
            vectors.append(np.asarray(bond["start"] - bond["end"], dtype=float))
    return vectors


def test_rocksalt_ball_stick_has_octahedral_coordination(tmp_path: Path) -> None:
    poscar = tmp_path / "POSCAR_NaCl"
    _write_rocksalt(poscar)
    bundle = _loaded(poscar).frames[0].bundle
    scene = build_bundle_scene(
        bundle,
        display_mode="unit_cell",
        show_hydrogen=True,
        preset={},
    )

    assert scene["is_ionic_solid"] is True
    assert scene["suppressed_ionic_bond_count"] == 0
    homes = _home_atoms(scene)
    assert len(homes) == 8
    assert sorted(atom["elem"] for _, atom in homes) == ["Cl", "Cl", "Cl", "Cl", "Na", "Na", "Na", "Na"]

    axis_counts = [0, 0, 0]
    for bond in scene["bonds"]:
        vector = np.asarray(bond["end"] - bond["start"], dtype=float)
        assert float(np.linalg.norm(vector)) == pytest.approx(_HALF, abs=1e-3)
        dominant = int(np.argmax(np.abs(vector)))
        off_axis = [axis for axis in range(3) if axis != dominant]
        assert np.all(np.abs(vector[off_axis]) < 1e-3)
        axis_counts[dominant] += 1
    assert axis_counts[0] == axis_counts[1] == axis_counts[2] > 0

    octahedron = []
    for sign in (-1.0, 1.0):
        for axis in range(3):
            vector = np.zeros(3)
            vector[axis] = sign * _HALF
            octahedron.append(vector)
    for index, _atom in homes:
        vectors = _neighbor_vectors(scene, index)
        assert len(vectors) == 6
        for expected in octahedron:
            assert any(np.allclose(vector, expected, atol=1e-3) for vector in vectors)
        image_partners = 0
        for bond in scene["bonds"]:
            if index not in (bond["i"], bond["j"]):
                continue
            other = bond["j"] if bond["i"] == index else bond["i"]
            partner = scene["draw_atoms"][other]
            if partner.get("_is_framework_context_replica"):
                image_partners += 1
        assert image_partners == 3


def test_ionic_ball_stick_spheres_are_larger_than_sticks(tmp_path: Path) -> None:
    poscar = tmp_path / "POSCAR_NaCl"
    _write_rocksalt(poscar)
    structure = _loaded(poscar)
    plan = prepare_render(
        structure,
        view={"display": "unit_cell", "include_boundary_replicas": True},
        render_spec={
            "representation": "ball_stick",
            "backend": "cpu",
            "show_cell": False,
            "show_axes": False,
            "width": 32,
            "height": 32,
            "scale": 1,
            "sphere_detail": (4, 6),
            "cylinder_sides": 4,
        },
    )
    atom_radii = []
    bond_radii = []
    for primitive in plan.primitives:
        metadata = primitive.metadata or {}
        radius = metadata.get("_raster_radius")
        if metadata.get("kind") == "atom" and radius is not None:
            atom_radii.append(float(radius))
        if metadata.get("kind") == "bond" and radius is not None:
            bond_radii.append(float(radius))
    assert atom_radii
    assert bond_radii
    assert min(atom_radii) > max(bond_radii)


def test_ionic_ball_style_does_not_stroke_coordination_contacts(tmp_path: Path) -> None:
    poscar = tmp_path / "POSCAR_NaCl"
    _write_rocksalt(poscar)
    plan = prepare_render(
        _loaded(poscar),
        view={"display": "unit_cell"},
        render_spec={
            "representation": "ball",
            "backend": "cpu",
            "show_cell": False,
            "show_axes": False,
            "width": 32,
            "height": 32,
            "scale": 1,
            "sphere_detail": (4, 6),
        },
    )
    assert plan.primitives
    assert all((primitive.metadata or {}).get("kind") != "bond" for primitive in plan.primitives)


def test_omitted_style_uses_ball_and_unit_cell_for_rocksalt(tmp_path: Path) -> None:
    poscar = tmp_path / "POSCAR_NaCl"
    _write_rocksalt(poscar)
    args = argparse.Namespace(
        input=str(poscar),
        input_format=None,
        type_map=None,
        frame=0,
        bond_scale=None,
        bond_threshold_specs=(),
        json_output=True,
        view="auto",
        show_unit_cell=None,
    )
    structure = _loaded(poscar)
    assert _resolve_omitted_render_style(args) == "ball"
    assert _display_mode(structure, args) == "unit_cell"


def test_explicit_ball_stick_stays_available_and_molecules_keep_their_default() -> None:
    parser = argparse.ArgumentParser()
    render = _build_render_parser(parser.add_subparsers())
    omitted = render.parse_args(["nacl.poscar", "-o", "out.png"])
    explicit = render.parse_args(
        ["nacl.poscar", "-o", "out.png", "--style", "ball_stick"]
    )
    assert omitted.style is None
    assert explicit.style == "ball_stick"
    assert prefers_lattice_ball_default(
        {"is_ionic_solid": False, "is_inorganic_solid": False},
        atoms=[{"elem": "C"}, {"elem": "O"}, {"elem": "H"}],
    ) is False
    assert prefers_lattice_ball_default(atoms=[{"elem": "Na"}, {"elem": "Cl"}]) is True
