"""Unit-cell atom images and bond endpoints are independent of lattice wraps.

A 1.4 Å C–C contact is one periodic structure whether or not an endpoint
was stored one cell away. Display may add bond-context images, but those
images and the sticks between them have to match, and every stick has to
end on a drawn atom.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mat_viewer.loader import build_bundle_scene, load_structure_input
from mat_viewer.render.contracts import RenderSpec, ViewSpec
from mat_viewer.render.planning import prepare_render
from mat_viewer.render.plotly import build_figure
from tests.fixtures.periodic_bond_images import (
    BOND_LENGTH_A,
    CROSSING_OFFSETS_A,
    cartesian,
    committed_fixture,
    crossing_fractional,
    cubic_lattice,
    hydrogen_fractional,
    interior_fractional,
    translate_sites,
    triclinic_lattice,
    write_extxyz,
)

_ATOL = 1e-5


def _load(path: Path):
    return load_structure_input(path)


def _scene(structure, *, replicas: bool, hydrogen: bool = True, endpoints: bool = True):
    bundle = structure.frames[0].bundle
    return build_bundle_scene(
        bundle,
        display_mode="unit_cell",
        show_hydrogen=hydrogen,
        include_boundary_replicas=replicas,
        include_cross_boundary_bond_endpoints=endpoints,
    )


def _plan(structure, *, replicas: bool, hydrogen: bool = True, representation: str = "ball_stick"):
    return prepare_render(
        structure,
        view=ViewSpec(display="unit_cell", include_boundary_replicas=replicas),
        render=RenderSpec(
            representation=representation,
            show_hydrogen=hydrogen,
            show_cell=False,
            show_axes=False,
            show_labels=False,
            sphere_detail=(4, 6),
            cylinder_sides=6,
        ),
    )


def _match(points, expected, *, atol=_ATOL) -> None:
    pending = [np.asarray(point, dtype=float) for point in points]
    wanted = [np.asarray(point, dtype=float) for point in expected]
    assert len(pending) == len(wanted)
    used: set[int] = set()
    for point in pending:
        found = None
        for index, other in enumerate(wanted):
            if index in used:
                continue
            if np.allclose(point, other, rtol=0.0, atol=atol):
                found = index
                break
        assert found is not None, (point, wanted)
        used.add(found)


def _scene_centres(scene) -> list[np.ndarray]:
    return [np.asarray(atom["cart"], dtype=float) for atom in scene["draw_atoms"]]


def _scene_bonds(scene) -> list[tuple[np.ndarray, np.ndarray]]:
    return [
        (np.asarray(bond["start"], dtype=float), np.asarray(bond["end"], dtype=float))
        for bond in scene["bonds"]
    ]


def _assert_on_atoms(centres, bonds, *, atol=_ATOL) -> None:
    for start, end in bonds:
        for point in (start, end):
            assert any(np.allclose(point, centre, rtol=0.0, atol=atol) for centre in centres)


def _assert_length(bonds, distance: float) -> None:
    assert bonds
    for start, end in bonds:
        assert np.linalg.norm(end - start) == pytest.approx(distance, abs=1e-6)


def _plan_atoms(plan) -> list[np.ndarray]:
    return [
        np.asarray(primitive.metadata["_raster_center"], dtype=float)
        for primitive in plan.primitives
        if primitive.metadata.get("kind") == "atom"
    ]


def _plan_bond_points(plan) -> list[np.ndarray]:
    points = []
    for primitive in plan.primitives:
        if primitive.metadata.get("kind") != "bond":
            continue
        points.append(np.asarray(primitive.metadata["_raster_start"], dtype=float))
        points.append(np.asarray(primitive.metadata["_raster_end"], dtype=float))
    return points


def _assert_plan_and_plotly(structure, *, replicas: bool, centres, bonds, hydrogen: bool = True) -> None:
    plan = _plan(structure, replicas=replicas, hydrogen=hydrogen)
    plan_atoms = _plan_atoms(plan)
    plan_points = _plan_bond_points(plan)
    _match(plan_atoms, centres)
    _assert_on_atoms(plan_atoms, bonds)
    for start, end in bonds:
        assert any(np.allclose(start, point, rtol=0.0, atol=_ATOL) for point in plan_points)
        assert any(np.allclose(end, point, rtol=0.0, atol=_ATOL) for point in plan_points)
    for point in plan_points:
        on_atom = any(np.allclose(point, centre, rtol=0.0, atol=_ATOL) for centre in plan_atoms)
        on_midpoint = any(
            np.allclose(point, 0.5 * (start + end), rtol=0.0, atol=_ATOL)
            for start, end in bonds
        )
        assert on_atom or on_midpoint

    figure = build_figure(plan)
    bond_vertices = []
    atom_vertices: list[list[np.ndarray]] = []
    for trace in figure.data:
        role = (trace.meta or {}).get("mv_role")
        coords = np.column_stack(
            (
                np.asarray(trace.x, dtype=float),
                np.asarray(trace.y, dtype=float),
                np.asarray(trace.z, dtype=float),
            )
        )
        finite = coords[np.all(np.isfinite(coords), axis=1)]
        if role == "bond":
            bond_vertices.extend(finite)
        elif role == "atom":
            atom_vertices.append(finite)
    assert len(atom_vertices) == len(plan_atoms)
    rendered_centres = [vertices.mean(axis=0) for vertices in atom_vertices]
    _match(rendered_centres, plan_atoms)
    for point in plan_points:
        assert any(np.allclose(point, vertex, rtol=0.0, atol=_ATOL) for vertex in bond_vertices)


def _crossing_centres(axis: int, lattice: np.ndarray) -> list[np.ndarray]:
    points = []
    for offset in CROSSING_OFFSETS_A:
        frac = np.full(3, 0.5)
        frac[axis] = offset / 10.0
        points.append(frac @ lattice)
    return points


def _crossing_bonds(axis: int, lattice: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    pairs = ((-0.9, 0.5), (9.1, 10.5))
    bonds = []
    for left, right in pairs:
        start = np.full(3, 0.5)
        end = np.full(3, 0.5)
        start[axis] = left / 10.0
        end[axis] = right / 10.0
        bonds.append((start @ lattice, end @ lattice))
    return bonds


def _assert_display(structure, *, replicas: bool, centres, bonds, hydrogen: bool = True, distance: float = BOND_LENGTH_A) -> None:
    scene = _scene(structure, replicas=replicas, hydrogen=hydrogen)
    scene_centres = _scene_centres(scene)
    scene_bonds = _scene_bonds(scene)
    _match(scene_centres, centres)
    _match([point for bond in scene_bonds for point in bond], [point for bond in bonds for point in bond])
    _assert_on_atoms(scene_centres, scene_bonds)
    _assert_length(scene_bonds, distance)
    _assert_plan_and_plotly(
        structure,
        replicas=replicas,
        centres=centres,
        bonds=bonds,
        hydrogen=hydrogen,
    )


def _write(tmp_path: Path, name: str, fractional, lattice, species=None) -> Path:
    return write_extxyz(tmp_path / name, fractional, lattice, species)


@pytest.mark.parametrize("replicas", [False, True])
def test_committed_fixtures_share_atom_images_and_bond_endpoints(replicas: bool) -> None:
    unwrapped = _load(committed_fixture("synthetic_periodic_cc_unwrapped.extxyz"))
    wrapped = _load(committed_fixture("synthetic_periodic_cc_wrapped.extxyz"))
    lattice = cubic_lattice()
    centres = _crossing_centres(0, lattice)
    bonds = _crossing_bonds(0, lattice)

    for structure in (unwrapped, wrapped):
        bundle = structure.frames[0].bundle
        source_carts = [np.array(atom["cart"], dtype=float, copy=True) for atom in bundle.raw_atoms]
        source_shifts = [
            tuple(int(value) for value in record["right_image_shift"])
            for record in bundle.molcrys_analysis.bond_records
        ]
        _assert_display(structure, replicas=replicas, centres=centres, bonds=bonds)
        assert [tuple(int(value) for value in record["right_image_shift"]) for record in bundle.molcrys_analysis.bond_records] == source_shifts
        scene = _scene(structure, replicas=replicas)
        assert [tuple(int(value) for value in record["right_image_shift"]) for record in scene["_canonical_bond_records"]] == source_shifts
        for before, after in zip(source_carts, bundle.raw_atoms):
            np.testing.assert_allclose(after["cart"], before)

    unwrapped_shift = tuple(
        int(value)
        for value in unwrapped.frames[0].bundle.molcrys_analysis.bond_records[0]["right_image_shift"]
    )
    wrapped_shift = tuple(
        int(value)
        for value in wrapped.frames[0].bundle.molcrys_analysis.bond_records[0]["right_image_shift"]
    )
    assert unwrapped_shift == (0, 0, 0)
    assert wrapped_shift == (-1, 0, 0)
    assert len(unwrapped.frames[0].bundle.raw_atoms) == 2
    assert len(unwrapped.frames[0].bundle.molcrys_analysis.bond_records) == 1


def test_disabled_bond_context_omits_the_crossing_stick() -> None:
    structures = [
        _load(committed_fixture(name))
        for name in (
            "synthetic_periodic_cc_unwrapped.extxyz",
            "synthetic_periodic_cc_wrapped.extxyz",
        )
    ]
    homes = [
        np.array([0.5, 5.0, 5.0]),
        np.array([9.1, 5.0, 5.0]),
    ]
    seen = []
    for structure in structures:
        scene = _scene(structure, replicas=False, endpoints=False)
        centres = _scene_centres(scene)
        bonds = _scene_bonds(scene)
        _match(centres, homes)
        assert bonds == []
        assert scene["bonded_image_replica_count"] == 0
        seen.append(centres)
    _match(seen[0], seen[1])


@pytest.mark.parametrize("replicas", [False, True])
def test_ball_style_keeps_wrapped_sites_without_a_bond_halo(replicas: bool, tmp_path: Path) -> None:
    lattice = cubic_lattice()
    paths = [
        _write(tmp_path, f"{name}.extxyz", crossing_fractional(0, wrapped=wrapped), lattice)
        for name, wrapped in (("unwrapped", False), ("wrapped", True))
    ]
    if replicas:
        expected = _crossing_centres(0, lattice)
    else:
        expected = [
            np.array([0.5, 5.0, 5.0]),
            np.array([9.1, 5.0, 5.0]),
        ]
    rendered = []
    for path in paths:
        plan = _plan(_load(path), replicas=replicas, representation="ball")
        centres = _plan_atoms(plan)
        assert not any(primitive.metadata.get("kind") == "bond" for primitive in plan.primitives)
        _match(centres, expected)
        rendered.append(centres)
    _match(rendered[0], rendered[1])


@pytest.mark.parametrize("axis", [0, 1, 2])
@pytest.mark.parametrize("replicas", [False, True])
def test_each_cell_axis_matches_for_both_coordinate_representations(
    axis: int, replicas: bool, tmp_path: Path
) -> None:
    lattice = cubic_lattice()
    centres = _crossing_centres(axis, lattice)
    bonds = _crossing_bonds(axis, lattice)
    for wrapped in (False, True):
        path = _write(
            tmp_path,
            f"axis{axis}-{wrapped}-{replicas}.extxyz",
            crossing_fractional(axis, wrapped=wrapped),
            lattice,
        )
        _assert_display(_load(path), replicas=replicas, centres=centres, bonds=bonds)


@pytest.mark.parametrize(
    "shift, which",
    [
        ((1, 0, 0), (1,)),
        ((-1, 0, 0), (1,)),
        ((0, 1, 0), (0,)),
        ((0, 0, -1), (0, 1)),
        ((2, 0, 0), (1,)),
        ((1, -1, 1), (0, 1)),
        ((1, -1, 1), (1,)),
    ],
)
@pytest.mark.parametrize("replicas", [False, True])
def test_integer_translations_do_not_change_the_display(
    shift: tuple[int, int, int],
    which: tuple[int, ...],
    replicas: bool,
    tmp_path: Path,
) -> None:
    lattice = cubic_lattice()
    fractional = translate_sites(
        crossing_fractional(0, wrapped=True),
        shift,
        which=which,
    )
    path = _write(tmp_path, "translated.extxyz", fractional, lattice)
    _assert_display(
        _load(path),
        replicas=replicas,
        centres=_crossing_centres(0, lattice),
        bonds=_crossing_bonds(0, lattice),
    )


@pytest.mark.parametrize("replicas", [False, True])
def test_interior_molecule_is_one_image_after_a_lattice_translation(
    replicas: bool, tmp_path: Path
) -> None:
    lattice = cubic_lattice()
    centres = list(cartesian(interior_fractional(), lattice))
    bonds = [(centres[0], centres[1])]
    for name, fractional in (
        ("home", interior_fractional()),
        ("shifted", translate_sites(interior_fractional(), (0, -1, 2))),
    ):
        path = _write(tmp_path, f"{name}.extxyz", fractional, lattice)
        scene = _scene(_load(path), replicas=replicas)
        assert scene["bonded_image_replica_count"] == 0
        assert not any(atom.get("_is_boundary_replica") for atom in scene["draw_atoms"])
        _assert_display(_load(path), replicas=replicas, centres=centres, bonds=bonds)


@pytest.mark.parametrize("replicas", [False, True])
def test_triclinic_crossing_is_independent_of_the_stored_cell(
    replicas: bool, tmp_path: Path
) -> None:
    lattice = triclinic_lattice()
    centres = _crossing_centres(0, lattice)
    bonds = _crossing_bonds(0, lattice)
    for wrapped in (False, True):
        path = _write(
            tmp_path,
            f"triclinic-{wrapped}.extxyz",
            crossing_fractional(0, wrapped=wrapped),
            lattice,
        )
        _assert_display(_load(path), replicas=replicas, centres=centres, bonds=bonds)


@pytest.mark.parametrize("replicas", [False, True])
@pytest.mark.parametrize("hydrogen", [True, False])
def test_hydrogen_visibility_keeps_equivalent_inputs_aligned(
    replicas: bool, hydrogen: bool, tmp_path: Path
) -> None:
    lattice = cubic_lattice()
    species = ["C", "C", "H"]
    displays = []
    for wrapped in (False, True):
        path = _write(
            tmp_path,
            f"h-{wrapped}-{hydrogen}-{replicas}.extxyz",
            hydrogen_fractional(wrapped=wrapped),
            lattice,
            species,
        )
        structure = _load(path)
        scene = _scene(structure, replicas=replicas, hydrogen=hydrogen)
        centres = _scene_centres(scene)
        bonds = _scene_bonds(scene)
        elements = [atom["elem"] for atom in scene["draw_atoms"]]
        _assert_on_atoms(centres, bonds)
        if hydrogen:
            assert elements.count("H") == 2
            assert elements.count("C") == 4
            _assert_length(
                [
                    bond
                    for bond in bonds
                    if abs(np.linalg.norm(bond[1] - bond[0]) - BOND_LENGTH_A) < 1e-4
                ],
                BOND_LENGTH_A,
            )
            hydrogen_bonds = [
                bond
                for bond in bonds
                if abs(np.linalg.norm(bond[1] - bond[0]) - 1.1) < 1e-4
            ]
            assert len(hydrogen_bonds) == 2
            _assert_length(hydrogen_bonds, 1.1)
        else:
            assert "H" not in elements
            assert len(centres) == 4
            _match(centres, _crossing_centres(0, lattice))
            _assert_length(bonds, BOND_LENGTH_A)
        plan = _plan(structure, replicas=replicas, hydrogen=hydrogen)
        assert [primitive.metadata["element"] for primitive in plan.primitives if primitive.metadata.get("kind") == "atom"].count("H") == (2 if hydrogen else 0)
        _assert_plan_and_plotly(
            structure,
            replicas=replicas,
            centres=centres,
            bonds=bonds,
            hydrogen=hydrogen,
        )
        displays.append((centres, [point for bond in bonds for point in bond]))
    _match(displays[0][0], displays[1][0])
    _match(displays[0][1], displays[1][1])
