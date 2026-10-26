"""Cubic rocksalt ball view stays inside the conventional cell.

``--style ball`` draws no sticks. A unit-cell figure is the wrapped
crystallographic sites plus, when boundary replicas are on, the symmetric
face, edge, and corner images at 0 and 1. A one-sided coordination shell
at fractional ``-0.5`` is not part of that figure.
"""

from __future__ import annotations

import argparse
from itertools import permutations
from pathlib import Path

import numpy as np
from ase.build import bulk
from ase.io import write

from mat_viewer.cli import _display_mode
from mat_viewer.loader import build_bundle_scene
from mat_viewer.loader.structure_input import load_structure_input
from mat_viewer.render.contracts import RenderSpec, ViewSpec
from mat_viewer.render.planning import prepare_render

_FRAC_DECIMALS = 6


def _poscar(tmp_path: Path) -> Path:
    atoms = bulk("NaCl", "rocksalt", a=5.6402, cubic=True)
    path = tmp_path / "POSCAR_NaCl"
    write(path, atoms, format="vasp", direct=True, vasp5=True)
    return path


def _scene(path: Path, *, replicas: bool, cross_boundary: bool, mode: str = "unit_cell"):
    loaded = load_structure_input(path)
    return loaded, build_bundle_scene(
        loaded.frames[0].bundle,
        display_mode=mode,
        show_hydrogen=True,
        include_boundary_replicas=replicas,
        include_cross_boundary_bond_endpoints=cross_boundary,
    )


def _site_key(element: str, frac) -> tuple[str, tuple[float, float, float]]:
    rounded = np.round(np.asarray(frac, dtype=float), _FRAC_DECIMALS)
    rounded[np.abs(rounded) < 10 ** (-_FRAC_DECIMALS)] = 0.0
    return element, tuple(float(value) for value in rounded)


def _draw_keys(scene) -> list[tuple[str, tuple[float, float, float]]]:
    return [
        _site_key(str(atom["elem"]), atom["frac"])
        for atom in scene["draw_atoms"]
    ]


def _assert_inside_closed_cell(keys) -> None:
    for _element, frac in keys:
        assert all(-1e-8 <= component <= 1.0 + 1e-8 for component in frac)


def _boundary_images(frac: tuple[float, float, float]) -> list[tuple[float, float, float]]:
    """Symmetric 0/1 images of one conventional-cell special position."""
    axes: list[tuple[float, ...]] = []
    for value in frac:
        if abs(value) <= 1e-8 or abs(value - 1.0) <= 1e-8:
            axes.append((0.0, 1.0))
        else:
            axes.append((float(value),))
    return [
        (x, y, z)
        for x in axes[0]
        for y in axes[1]
        for z in axes[2]
    ]


def _expected_replica_keys() -> list[tuple[str, tuple[float, float, float]]]:
    sodium = (
        (0.0, 0.0, 0.0),
        (0.5, 0.5, 0.0),
        (0.5, 0.0, 0.5),
        (0.0, 0.5, 0.5),
    )
    chlorine = (
        (0.5, 0.0, 0.0),
        (0.0, 0.5, 0.0),
        (0.0, 0.0, 0.5),
        (0.5, 0.5, 0.5),
    )
    keys = []
    for element, sites in (("Na", sodium), ("Cl", chlorine)):
        for site in sites:
            keys.extend(
                _site_key(element, image) for image in _boundary_images(site)
            )
    return keys


def _assert_cubic_symmetry(keys) -> None:
    present = set(keys)
    for element, frac in keys:
        inverted = _site_key(element, tuple(1.0 - value for value in frac))
        assert inverted in present
        for order in permutations(range(3)):
            permuted = _site_key(element, tuple(frac[axis] for axis in order))
            assert permuted in present


def test_auto_view_of_periodic_poscar_is_unit_cell(tmp_path: Path) -> None:
    loaded, _scene_unused = _scene(
        _poscar(tmp_path),
        replicas=True,
        cross_boundary=False,
    )
    assert _display_mode(loaded, argparse.Namespace(view="auto")) == "unit_cell"


def test_ball_unit_cell_has_symmetric_boundary_images_only(tmp_path: Path) -> None:
    """ASE cubic NaCl ``--style ball --view unit_cell``.

    Boundary replicas default on. Centres stay in ``[0, 1]``, including the
    intentional images that sit on a face, edge, or corner. Inversion through
    the cell centre and cubic axis permutation both preserve the figure.
    """
    path = _poscar(tmp_path)
    _loaded, scene = _scene(path, replicas=True, cross_boundary=False)
    keys = _draw_keys(scene)

    assert scene["display_mode"] == "unit_cell"
    assert scene["connectivity_regime"] == "extended"
    assert scene["is_inorganic_solid"] is True
    assert scene["bonds"]
    assert {bond["role"] for bond in scene["bonds"]} == {"coordination"}
    assert scene["framework_context_replica_count"] == 0
    assert scene["bonded_image_replica_count"] == 0
    assert not any(
        atom.get("_is_framework_context_replica") or atom.get("_is_bonded_image_replica")
        for atom in scene["draw_atoms"]
    )
    for atom in scene["draw_atoms"]:
        shift = tuple(int(value) for value in atom.get("_image_shift", (0, 0, 0)))
        assert shift in {(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)}

    _assert_inside_closed_cell(keys)
    _assert_cubic_symmetry(keys)
    assert sorted(keys) == sorted(_expected_replica_keys())
    assert len(keys) == 27

    loaded = load_structure_input(path)
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
    matrix = np.asarray(loaded.frames[0].bundle.M, dtype=float)
    centres = [
        np.asarray(primitive.metadata["_raster_center"], dtype=float)
        for primitive in plan.primitives
        if primitive.metadata.get("kind") == "atom"
    ]
    assert len(centres) == len(keys)
    fracs = np.asarray(centres, dtype=float) @ np.linalg.inv(matrix)
    assert np.all(fracs >= -1e-6)
    assert np.all(fracs <= 1.0 + 1e-6)
    rendered = [
        _site_key(str(primitive.metadata["element"]), frac)
        for primitive, frac in zip(
            (
                primitive
                for primitive in plan.primitives
                if primitive.metadata.get("kind") == "atom"
            ),
            fracs,
        )
    ]
    assert sorted(rendered) == sorted(keys)
    assert not any(
        primitive.metadata.get("kind") == "bond" for primitive in plan.primitives
    )


def test_ball_unit_cell_without_replicas_stays_on_wrapped_sites(tmp_path: Path) -> None:
    """``--no-boundary-replicas`` must not grow the bonded-image halo."""
    path = _poscar(tmp_path)
    expected = [
        _site_key(element, frac)
        for element, sites in (
            (
                "Na",
                (
                    (0.0, 0.0, 0.0),
                    (0.5, 0.5, 0.0),
                    (0.5, 0.0, 0.5),
                    (0.0, 0.5, 0.5),
                ),
            ),
            (
                "Cl",
                (
                    (0.5, 0.0, 0.0),
                    (0.0, 0.5, 0.0),
                    (0.0, 0.0, 0.5),
                    (0.5, 0.5, 0.5),
                ),
            ),
        )
        for frac in sites
    ]
    for cross_boundary in (False, True):
        _loaded, scene = _scene(
            path,
            replicas=False,
            cross_boundary=cross_boundary,
        )
        keys = _draw_keys(scene)
        assert len(scene["draw_atoms"]) == 8
        assert scene["bonded_image_replica_count"] == 0
        _assert_inside_closed_cell(keys)
        matrix = np.asarray(scene["M"], dtype=float)
        inverse = np.linalg.inv(matrix)
        for bond in scene["bonds"]:
            for endpoint in (bond["start"], bond["end"]):
                frac = np.asarray(endpoint, dtype=float) @ inverse
                assert np.all(frac >= -1e-6)
                assert np.all(frac <= 1.0 + 1e-6)
        assert sorted(keys) == sorted(expected)


def test_ionic_formula_unit_centres_stay_in_the_home_cell(tmp_path: Path) -> None:
    _loaded, scene = _scene(
        _poscar(tmp_path),
        replicas=True,
        cross_boundary=False,
        mode="formula_unit",
    )
    keys = _draw_keys(scene)
    assert keys
    _assert_inside_closed_cell(keys)
    assert all(
        not atom.get("_is_boundary_replica")
        and not atom.get("_is_framework_context_replica")
        for atom in scene["draw_atoms"]
    )
