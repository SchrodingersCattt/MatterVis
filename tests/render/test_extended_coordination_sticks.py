"""Ball-and-stick for extended crystals follows MolCrysKit role and regime.

Stick geometry is the neighbor-list edge (``BondRecord.role`` plus the
signed image), not an element whitelist and not a structure-specific mesh.
``get_connectivity_report()`` chooses the extended lattice presentation.
"""

from __future__ import annotations

from itertools import permutations
from pathlib import Path

import numpy as np
import pytest
from ase import Atoms
from ase.build import bulk
from ase.io import write

from mat_viewer.loader import build_bundle_scene
from mat_viewer.loader.structure_input import load_structure_input
from mat_viewer.render.contracts import RenderSpec, ViewSpec
from mat_viewer.render.planning import prepare_render

_FRAC_DECIMALS = 5


def _write(tmp_path: Path, name: str, atoms) -> Path:
    path = tmp_path / f"{name}.vasp"
    write(path, atoms, format="vasp", direct=True, vasp5=True)
    return path


def _scene(path: Path):
    loaded = load_structure_input(path)
    scene = build_bundle_scene(
        loaded.frames[0].bundle,
        display_mode="unit_cell",
        show_hydrogen=True,
        include_boundary_replicas=True,
    )
    return loaded, scene


def _frac(scene, cart) -> tuple[float, float, float]:
    matrix = np.asarray(scene["M"], dtype=float)
    fractional = np.asarray(cart, dtype=float) @ np.linalg.inv(matrix)
    rounded = np.round(fractional, _FRAC_DECIMALS)
    rounded[np.abs(rounded) < 10 ** (-_FRAC_DECIMALS)] = 0.0
    return tuple(float(value) for value in rounded)


def _segments(
    scene,
) -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
    segments = []
    for bond in scene["bonds"]:
        start = _frac(scene, bond["start"])
        end = _frac(scene, bond["end"])
        segments.append(tuple(sorted((start, end))))
    return segments


def _assert_inside(scene) -> None:
    for bond in scene["bonds"]:
        for endpoint in (bond["start"], bond["end"]):
            fractional = _frac(scene, endpoint)
            assert all(-1e-6 <= component <= 1.0 + 1e-6 for component in fractional)
    for atom in scene["draw_atoms"]:
        fractional = _frac(scene, atom["cart"])
        assert all(-1e-6 <= component <= 1.0 + 1e-6 for component in fractional)


def _assert_cubic_permutation(segments) -> None:
    present = set(segments)

    def permute(point, order):
        return tuple(point[axis] for axis in order)

    for start, end in segments:
        for order in permutations(range(3)):
            permuted = tuple(sorted((permute(start, order), permute(end, order))))
            assert permuted in present


def _assert_inversion(segments) -> None:
    present = set(segments)

    def invert(point):
        return tuple(
            float(np.round(1.0 - component, _FRAC_DECIMALS)) for component in point
        )

    for start, end in segments:
        inverted = tuple(sorted((invert(start), invert(end))))
        assert inverted in present


def _degrees(scene) -> list[int]:
    counts = [0] * len(scene["draw_atoms"])
    for bond in scene["bonds"]:
        counts[int(bond["i"])] += 1
        counts[int(bond["j"])] += 1
    return counts


def _assert_ball_stick_spheres_clear_cylinders(loaded) -> None:
    plan = prepare_render(
        loaded,
        view=ViewSpec(display="unit_cell", include_boundary_replicas=True),
        render=RenderSpec(
            representation="ball_stick",
            backend="cpu",
            bond_radius=0.15,
            show_cell=True,
            show_axes=False,
            show_labels=False,
            shading="flat",
        ),
    )
    atoms = [
        primitive
        for primitive in plan.primitives
        if primitive.metadata.get("kind") == "atom"
    ]
    bonds = [
        primitive
        for primitive in plan.primitives
        if primitive.metadata.get("kind") == "bond"
    ]
    assert atoms
    assert bonds
    atom_radii = [float(primitive.metadata["_raster_radius"]) for primitive in atoms]
    bond_radii = [float(primitive.metadata["_raster_radius"]) for primitive in bonds]
    assert min(atom_radii) > max(bond_radii) * 1.5


def _assert_ball_draws_no_sticks(loaded) -> None:
    plan = prepare_render(
        loaded,
        view=ViewSpec(display="unit_cell", include_boundary_replicas=True),
        render=RenderSpec(
            representation="ball",
            backend="cpu",
            show_cell=True,
            show_axes=False,
            show_labels=False,
            shading="flat",
        ),
    )
    assert not any(
        primitive.metadata.get("kind") == "bond" for primitive in plan.primitives
    )
    assert any(
        primitive.metadata.get("kind") == "atom" for primitive in plan.primitives
    )


@pytest.mark.parametrize(
    ("builder", "body_degree", "centrosymmetric", "delta_shape"),
    [
        (
            lambda: bulk("NaCl", "rocksalt", a=5.6402, cubic=True),
            6,
            True,
            (0.5, 0.0, 0.0),
        ),
        (
            lambda: bulk("CsCl", "cesiumchloride", a=4.123, cubic=True),
            8,
            True,
            (0.5, 0.5, 0.5),
        ),
        (
            lambda: bulk("ZnS", "zincblende", a=5.4093, cubic=True),
            4,
            False,
            None,
        ),
    ],
)
def test_extended_ball_stick_draws_first_neighbor_coordination(
    tmp_path: Path,
    builder,
    body_degree: int,
    centrosymmetric: bool,
    delta_shape,
) -> None:
    loaded, scene = _scene(_write(tmp_path, "crystal", builder()))
    segments = _segments(scene)
    lengths = {
        round(
            float(
                np.linalg.norm(
                    np.asarray(bond["end"], dtype=float)
                    - np.asarray(bond["start"], dtype=float)
                )
            ),
            4,
        )
        for bond in scene["bonds"]
    }

    assert scene["connectivity_regime"] == "extended"
    assert scene["connectivity_rank"] == 3
    assert scene["bonds"]
    assert {bond["role"] for bond in scene["bonds"]} == {"coordination"}
    assert len(lengths) == 1
    assert scene["bonded_image_replica_count"] == 0
    assert scene["framework_context_replica_count"] == 0
    _assert_inside(scene)
    _assert_cubic_permutation(segments)
    if centrosymmetric:
        _assert_inversion(segments)
    assert max(_degrees(scene)) == body_degree

    if delta_shape is not None:
        expected = tuple(sorted(abs(component) for component in delta_shape))
        for start, end in segments:
            delta = tuple(
                sorted(
                    abs(round(end[axis] - start[axis], _FRAC_DECIMALS))
                    for axis in range(3)
                )
            )
            assert delta == expected

    _assert_ball_stick_spheres_clear_cylinders(loaded)
    _assert_ball_draws_no_sticks(loaded)


def test_mattervis_has_no_ionic_stick_whitelist() -> None:
    import mat_viewer.structure.bonds as bonds

    assert not hasattr(bonds, "ionic_coordinate_source_pairs")
    assert not hasattr(bonds, "_IONIC_HALIDES")
    assert not hasattr(bonds, "_IONIC_CATIONS")


def test_molecular_covalent_ball_stick_is_unchanged(tmp_path: Path) -> None:
    atoms = Atoms(
        "COH",
        positions=[[0.0, 0.0, 0.0], [1.21, 0.0, 0.0], [-0.55, 0.95, 0.0]],
        cell=np.eye(3) * 12.0,
        pbc=True,
    )
    loaded, scene = _scene(_write(tmp_path, "formaldehyde", atoms))
    assert scene["connectivity_regime"] == "molecular"
    assert scene["connectivity_rank"] == 0
    assert scene["bonds"]
    assert {bond["role"] for bond in scene["bonds"]} == {"covalent"}
    assert not any(atom.get("_coordination_site") for atom in scene["draw_atoms"])

    plan = prepare_render(
        loaded,
        view=ViewSpec(display="unit_cell", include_boundary_replicas=True),
        render=RenderSpec(
            representation="ball_stick",
            backend="cpu",
            show_hydrogen=True,
            show_cell=False,
            show_axes=False,
            show_labels=False,
            shading="flat",
        ),
    )
    assert any(
        primitive.metadata.get("kind") == "bond" for primitive in plan.primitives
    )
