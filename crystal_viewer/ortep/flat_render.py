"""Matplotlib 2D vector ORTEP publication renderer.

Produces classic ORTEP-III style figures: sharp vector ellipses with
clip-path bond truncation, octant hatching, dashed disorder outlines,
and atom labels — all in pure 2D (no 3D engine).

Usage::

    from crystal_viewer.ortep.flat_render import render_ortep_flat
    fig = render_ortep_flat(scene, style)
    fig.savefig("output.pdf", bbox_inches="tight")
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse, Circle
from matplotlib.lines import Line2D
from matplotlib.collections import LineCollection

from .core import (
    _as_u_matrix,
    _atom_u,
    _probability_scale,
    ellipsoid_principal_axes,
)


# ── Projection helpers ──────────────────────────────────────────────────────

def _project_2d(point_3d, view_x, view_y):
    """Project a 3D point onto the 2D screen plane."""
    p = np.asarray(point_3d, dtype=float)
    return float(np.dot(p, view_x)), float(np.dot(p, view_y))


def _ellipse_params_2d(atom, view_x, view_y, probability: float):
    """Compute 2D ellipse parameters for an atom.

    Returns (cx, cy, width, height, angle_deg) for matplotlib Ellipse.
    """
    center = np.asarray(atom["cart"], dtype=float)
    cx, cy = _project_2d(center, view_x, view_y)

    U, uiso = _atom_u(atom)
    mat = _as_u_matrix(U, uiso=uiso)

    # Project the 3×3 ADP onto the 2D screen plane
    P = np.array([view_x, view_y], dtype=float)
    U2 = P @ mat @ P.T
    U2 = (U2 + U2.T) / 2.0

    eigvals, eigvecs = np.linalg.eigh(U2)
    eigvals = np.clip(eigvals, 0.0, None)

    scale = _probability_scale(probability, dimensions=2)
    a = scale * math.sqrt(float(eigvals[0]))
    b = scale * math.sqrt(float(eigvals[1]))

    # Angle of the first eigenvector (smaller eigenvalue) from x-axis
    angle_deg = math.degrees(math.atan2(eigvecs[1, 1], eigvecs[0, 1]))

    # Matplotlib Ellipse uses (width, height) where width is along the
    # ellipse's own rotated x-axis.  eigvals[1] >= eigvals[0] from eigh.
    return cx, cy, 2.0 * b, 2.0 * a, angle_deg


def _depth(atom, view_z):
    """Depth for painter's algorithm (larger = further from camera)."""
    return -float(np.dot(atom["cart"], view_z))


# ── Hatching ────────────────────────────────────────────────────────────────

def _hatch_lines_for_ellipse(cx, cy, w, h, angle_deg, n_lines=5):
    """Generate parallel hatch line segments clipped to one quadrant.

    Returns list of ((x0,y0), (x1,y1)) segments.
    """
    # We hatch the upper-right quadrant in the ellipse's local frame,
    # then rotate to world.
    angle_rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
    a, b = w / 2.0, h / 2.0  # semi-axes

    segments = []
    for k in range(1, n_lines + 1):
        # Horizontal lines in local frame at y = b * k / (n_lines+1)
        y_local = b * k / (n_lines + 1)
        # x range on the ellipse at this y: x² / a² + y² / b² = 1
        # → x = ±a * sqrt(1 - (y/b)²)
        r2 = 1.0 - (y_local / b) ** 2
        if r2 <= 0:
            continue
        x_range = a * math.sqrt(r2)
        # Only the right half (positive x): 0 to x_range
        x0_local, y0_local = 0.0, y_local
        x1_local, y1_local = x_range, y_local

        # Rotate to world
        x0 = cx + x0_local * cos_a - y0_local * sin_a
        y0 = cy + x0_local * sin_a + y0_local * cos_a
        x1 = cx + x1_local * cos_a - y1_local * sin_a
        y1 = cy + x1_local * sin_a + y1_local * cos_a
        segments.append(((x0, y0), (x1, y1)))
    return segments


# ── Main renderer ───────────────────────────────────────────────────────────

def render_ortep_flat(scene: dict, style: dict | None = None) -> plt.Figure:
    """Render a 2D publication-quality ORTEP figure.

    Parameters
    ----------
    scene : dict
        MatterVis scene dict with draw_atoms, bonds, view_x, view_y etc.
    style : dict, optional
        Style overrides.  Relevant keys:
        - ortep_probability (float, default 0.5)
        - show_hydrogen (bool, default True)
        - show_labels (bool, default True)
        - bond_radius (float, default 0.12) — controls line width
        - figsize ((w, h), default (7, 6))
        - dpi (int, default 150)

    Returns
    -------
    matplotlib.figure.Figure
    """
    if style is None:
        style = {}

    probability = float(style.get("ortep_probability", 0.5))
    show_h = bool(style.get("show_hydrogen", True))
    show_labels = bool(style.get("show_labels", True))
    bond_lw = max(1.0, 12.0 * float(style.get("bond_radius", 0.12)))
    h_radius_override = style.get("ortep_hydrogen_radius")
    n_hatch_lines = int(style.get("ortep_octant_hatch_lines", 5))
    figsize = style.get("figsize", (7, 6))
    dpi = int(style.get("dpi", 150))

    # View basis — recompute from view_direction if present so that
    # callers who override scene["view_direction"] get consistent results.
    view_dir = scene.get("view_direction")
    up = scene.get("up")
    if view_dir is not None:
        from ..math.rotation import view_rotation
        R = view_rotation(view_dir, up)
        view_x = R[0]
        view_y = R[1]
        view_z = R[2]
    else:
        view_x = np.asarray(scene.get("view_x", [1.0, 0.0, 0.0]), dtype=float)
        view_y = np.asarray(scene.get("view_y", [0.0, 1.0, 0.0]), dtype=float)
        view_z = np.cross(view_x, view_y)
        nrm = np.linalg.norm(view_z)
        if nrm > 1e-9:
            view_z = view_z / nrm
        else:
            view_z = np.array([0.0, 0.0, 1.0])

    atoms = scene.get("draw_atoms", [])
    bonds = scene.get("bonds", [])

    # Filter atoms
    draw_atoms = []
    for atom in atoms:
        elem = str(atom.get("elem", "C"))
        if not show_h and elem in ("H", "D"):
            continue
        draw_atoms.append(atom)

    # Sort by depth (painter's algorithm: far first)
    draw_atoms.sort(key=lambda a: _depth(a, view_z))

    # Precompute 2D ellipse params for all atoms
    atom_ellipses: dict[int, tuple] = {}  # original index → (cx, cy, w, h, angle)
    atom_2d: dict[int, tuple] = {}  # original index → (cx, cy)
    for atom in draw_atoms:
        idx = id(atom)
        elem = str(atom.get("elem", "C"))
        if elem in ("H", "D") and h_radius_override:
            cx, cy = _project_2d(atom["cart"], view_x, view_y)
            r = float(h_radius_override)
            atom_ellipses[idx] = (cx, cy, 2 * r, 2 * r, 0.0)
        else:
            params = _ellipse_params_2d(atom, view_x, view_y, probability)
            atom_ellipses[idx] = params
        cx, cy = atom_ellipses[idx][0], atom_ellipses[idx][1]
        atom_2d[idx] = (cx, cy)

    # Create figure
    fig, ax = plt.subplots(1, 1, figsize=figsize, dpi=dpi)
    ax.set_aspect("equal")
    ax.axis("off")

    # ── Layer 1: Bonds (bottom) ─────────────────────────────────────────
    for bond in bonds:
        i = int(bond.get("i", -1))
        j = int(bond.get("j", -1))
        if i < 0 or j < 0 or i >= len(atoms) or j >= len(atoms):
            continue
        ai, aj = atoms[i], atoms[j]
        if not show_h and str(ai.get("elem", "C")) in ("H", "D"):
            continue
        if not show_h and str(aj.get("elem", "C")) in ("H", "D"):
            continue

        start_2d = _project_2d(bond["start"], view_x, view_y)
        end_2d = _project_2d(bond["end"], view_x, view_y)

        line = Line2D(
            [start_2d[0], end_2d[0]],
            [start_2d[1], end_2d[1]],
            linewidth=bond_lw,
            color="black",
            solid_capstyle="round",
            zorder=1,
        )
        ax.add_line(line)

    # ── Layer 2: Atom fills (white disks to clip bonds) ─────────────────
    for atom in draw_atoms:
        idx = id(atom)
        cx, cy, w, h, angle = atom_ellipses[idx]
        occ = float(atom.get("occ", 1.0))
        is_minor = bool(atom.get("is_minor", False))
        is_disordered = occ < 0.999 or is_minor

        if is_disordered:
            # Disorder: no fill (open ellipse)
            continue

        elem = str(atom.get("elem", "C"))
        if elem in ("H", "D") and h_radius_override:
            patch = Circle(
                (cx, cy), radius=float(h_radius_override),
                facecolor="white", edgecolor="none", zorder=2,
            )
        else:
            patch = Ellipse(
                (cx, cy), w, h, angle=angle,
                facecolor="white", edgecolor="none", zorder=2,
            )
        ax.add_patch(patch)

    # ── Layer 3: Hatching (ordered non-H atoms only) ────────────────────
    for atom in draw_atoms:
        idx = id(atom)
        cx, cy, w, h, angle = atom_ellipses[idx]
        occ = float(atom.get("occ", 1.0))
        is_minor = bool(atom.get("is_minor", False))
        is_disordered = occ < 0.999 or is_minor
        elem = str(atom.get("elem", "C"))

        if is_disordered or elem in ("H", "D"):
            continue

        hatch_segs = _hatch_lines_for_ellipse(cx, cy, w, h, angle, n_lines=n_hatch_lines)
        if hatch_segs:
            lc = LineCollection(
                hatch_segs,
                linewidths=0.7,
                colors="black",
                zorder=3,
            )
            ax.add_collection(lc)

    # ── Layer 4: Atom outlines (top) ────────────────────────────────────
    for atom in draw_atoms:
        idx = id(atom)
        cx, cy, w, h, angle = atom_ellipses[idx]
        occ = float(atom.get("occ", 1.0))
        is_minor = bool(atom.get("is_minor", False))
        is_disordered = occ < 0.999 or is_minor
        elem = str(atom.get("elem", "C"))

        linestyle = "--" if is_disordered else "-"
        linewidth = 1.2

        if elem in ("H", "D") and h_radius_override:
            patch = Circle(
                (cx, cy), radius=float(h_radius_override),
                facecolor="none", edgecolor="black",
                linewidth=linewidth, linestyle=linestyle, zorder=4,
            )
        else:
            patch = Ellipse(
                (cx, cy), w, h, angle=angle,
                facecolor="none", edgecolor="black",
                linewidth=linewidth, linestyle=linestyle, zorder=4,
            )
        ax.add_patch(patch)

    # ── Layer 5: Labels ─────────────────────────────────────────────────
    if show_labels:
        for atom in draw_atoms:
            idx = id(atom)
            cx, cy, w, h, angle = atom_ellipses[idx]
            label = atom.get("label", "")
            if not label:
                continue
            elem = str(atom.get("elem", "C"))
            if elem in ("H", "D"):
                continue
            # Offset label to upper-right of ellipse
            offset_x = max(w, h) * 0.55
            offset_y = max(w, h) * 0.35
            ax.text(
                cx + offset_x, cy + offset_y, label,
                fontsize=7, ha="left", va="bottom",
                color="black", zorder=5,
            )

    # Auto-range with generous margin based on largest ellipse
    ax.autoscale_view()
    all_semi = [max(w, h) / 2.0 for (_, _, w, h, _) in atom_ellipses.values()] if atom_ellipses else [1.0]
    margin = max(all_semi) + 0.5
    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    ax.set_xlim(xlim[0] - margin, xlim[1] + margin)
    ax.set_ylim(ylim[0] - margin, ylim[1] + margin)

    plt.tight_layout()
    return fig
