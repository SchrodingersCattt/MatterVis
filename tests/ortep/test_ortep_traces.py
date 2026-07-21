from __future__ import annotations

import numpy as np

from crystal_viewer.loader import build_empty_bundle
from crystal_viewer.ortep import (
    DEFAULT_HYDROGEN_ORTEP_UISO,
    DEFAULT_MAX_ORTEP_UISO,
    DEFAULT_ORTEP_UISO,
    MAX_ORTEP_UISO_BY_ELEMENT,
    _atom_u,
    ortep_atom_mesh_traces,
    ortep_octant_shade_traces,
)


def test_ortep_traces_include_mesh_and_optional_axes():
    scene = build_empty_bundle().scene
    scene["draw_atoms"] = [
        {
            "label": "C1",
            "elem": "C",
            "cart": [0.0, 0.0, 0.0],
            "color": "#555555",
            "is_minor": False,
            "U": np.eye(3) * 0.04,
            "uiso": 0.04,
        }
    ]
    style = {"ortep_probability": 0.5}
    assert ortep_atom_mesh_traces(scene, style)
    assert ortep_octant_shade_traces(scene, {**style, "ortep_octant_shading": True})


# === DO NOT REMOVE WITHOUT READING THIS COMMENT ===============================
#
# These tests guard the visual Uiso clamp on ORTEP ellipsoids. Without
# the clamp, CIFs that encode disorder by inflating Uiso (Materials
# Studio exports, legacy SHELX, parts of the DAP-4 / NH4-perchlorate
# family) render disordered NH4 hydrogens as huge white spheres that
# swallow the rest of the scene.
#
# This regression has been fixed and re-broken at least twice by
# unrelated rewrites of ortep._atom_u. If you are deleting either
# DEFAULT_HYDROGEN_ORTEP_UISO, MAX_ORTEP_UISO_BY_ELEMENT, the
# _clamp_u_for_visualisation helper, or these tests, please re-read
# the H-cap discussion in the PR that introduced them and either
# replace the clamp with an equivalent guard or document why it is
# safe to drop. The "this site is disordered" cue belongs on the
# disorder rendering axis (outline rings / opacity), NOT on
# ellipsoid size.
# ==============================================================================


def test_ortep_fallback_uiso_shrinks_hydrogen():
    _, h_uiso = _atom_u({"elem": "H"})
    _, c_uiso = _atom_u({"elem": "C"})

    # Default fallback (no Uiso provided) gives a sensibly small
    # hydrogen and a larger heavy-atom default.
    assert h_uiso == DEFAULT_HYDROGEN_ORTEP_UISO
    assert h_uiso < c_uiso

    # Hydrogen sees a per-element ceiling that is tighter than the
    # generic heavy-atom default, so passing the heavy-atom default
    # to a hydrogen must be clipped.
    _, explicit_h_uiso = _atom_u({"elem": "H", "uiso": DEFAULT_ORTEP_UISO})
    assert explicit_h_uiso == MAX_ORTEP_UISO_BY_ELEMENT["H"]


def test_ortep_caps_disorder_inflated_uiso():
    """Some CIFs encode disorder by inflating Uiso instead of writing
    proper PART/disorder records. The renderer must clamp those values
    so a single H8/H21-style atom doesn't dominate the scene as a giant
    white blob, and the clamped H must end up *visually identical* in
    size to a "well-behaved" ordered H in the same scene -- otherwise
    the user reads the inflated-Uiso atom as still abnormally large.
    """

    # NH4 H atoms in DAP-4-style CIFs ship with Uiso = 0.20-0.25.
    _, clamped_h = _atom_u({"elem": "H", "uiso": 0.25})
    _, ordered_h = _atom_u({"elem": "H", "uiso": 0.025})
    _, clamped_heavy = _atom_u({"elem": "C", "uiso": 0.50})

    assert clamped_h == MAX_ORTEP_UISO_BY_ELEMENT["H"]
    assert clamped_heavy == DEFAULT_MAX_ORTEP_UISO

    # The whole point of the cap: a disordered NH4 H must not render
    # any larger than an ordinary C-H hydrogen.
    assert clamped_h <= ordered_h or abs(clamped_h - ordered_h) < 1e-9

    # A small explicit Uiso (typical for refined ordered H) passes
    # through unchanged.
    _, tiny_h = _atom_u({"elem": "H", "uiso": 0.017})
    assert tiny_h == 0.017


def test_ortep_caps_anisotropic_u_eigenvalues():
    """Anisotropic U bloat (worst eigenvalue ≫ cap) is rescaled rather
    than truncated component-by-component, so the ellipsoid keeps its
    shape (orientation + axial ratios) while its overall size stops
    swallowing neighbouring atoms.
    """

    inflated = np.diag([0.5, 0.05, 0.02])
    U_render, _ = _atom_u({"elem": "H", "U": inflated, "uiso": 0.5})
    eigs = np.linalg.eigvalsh(U_render)
    cap = MAX_ORTEP_UISO_BY_ELEMENT["H"]
    assert eigs.max() <= cap + 1e-9
    # Ratios preserved within numerical tolerance.
    assert np.isclose(eigs.max() / eigs.min(), 25.0, rtol=1e-6)
