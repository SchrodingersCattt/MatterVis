"""Readable molecule silhouettes with opaque depth surfaces and short IDs.

Every visible molecule gets a compact ID and a depth band, including singleton
ions. Formula annotations are placed only in free cells. Hulls are presentation
silhouettes at the mean molecular depth, not atom-level molecular surfaces.
"""

from __future__ import annotations

import numpy as np

from ._hull2d import convex_hull_2d
from .depth import depth_band, depth_marker
from .glyph_frame import _LABEL_OFFSETS, _draw_cell, _draw_ghosts, _element_rgb, _Grid
from .text import ascii7_text, terminal_text


def render_molecule_frame(
    crystal,
    camera,
    points,
    depths,
    viewport,
    *,
    width,
    height,
    mono,
    show_cell,
    show_minor,
    show_boundary,
    selected_display_index,
    charset,
    show_depth=True,
    slice_distance=None,
    ghost_points=None,
    show_bonds=True,
) -> str:
    grid = _Grid(width, height, mono=mono)
    grid.show_depth = show_depth
    groups: dict[int, list[int]] = {}
    for i, atom in enumerate(crystal.atoms):
        if i >= len(points) or (atom.is_minor and not show_minor):
            continue
        if atom.is_boundary_replica and not show_boundary:
            continue
        if slice_distance is not None and slice_distance[i] > 1:
            continue
        key = atom.molecule_index if atom.molecule_index >= 0 else -i - 1
        groups.setdefault(key, []).append(i)
    formulas = {
        i: name for name, indices in crystal.species_map.items() for i in indices
    }
    from .compositor import resolve_molecule_detail

    detail = resolve_molecule_detail(
        molecule_count=len(groups), width=width, height=height
    )
    records = []
    for group, indices in groups.items():
        xy = np.asarray([points[i] for i in indices])
        d = float(np.mean([depths[i] for i in indices]))
        centre = xy.mean(axis=0)
        polygon = (
            [viewport.to_grid(*xy[i]) for i in convex_hull_2d(xy.tolist())]
            if detail == "hull" or selected_display_index in indices
            else []
        )
        row, col = viewport.to_grid(*centre)
        adjacent = slice_distance is not None and all(
            slice_distance[i] == 1 for i in indices
        )
        records.append((d, group, indices, polygon, row, col, adjacent))
    records.sort(reverse=True, key=lambda item: (item[0], item[1]))
    grid.depth_range = (
        min((r[0] for r in records), default=0),
        max((r[0] for r in records), default=0),
    )
    if show_cell and crystal.lattice is not None:
        _draw_cell(grid, crystal, camera, viewport)
    # Front silhouettes erase back outlines, leaving their visible portions.
    for d, group, indices, polygon, row, col, adjacent in reversed(records):
        rgb = _element_rgb(crystal.atoms[indices[0]].element)
        _fill_surface(grid, polygon, d, group)
        if len(polygon) >= 2:
            for p0, p1 in zip(polygon, [*polygon[1:], polygon[0]]):
                grid.stroke(
                    *p0,
                    *p1,
                    d,
                    d,
                    text=None,
                    rgb=rgb,
                    kind="bond",
                    owners=(group, group),
                    style="dim" if adjacent else "normal",
                )
    grid.resolve_strokes()
    placed = []
    for d, group, indices, polygon, row, col, adjacent in records:
        if not grid.in_bounds(row, col):
            continue
        band = depth_band(d, *grid.depth_range)
        if not show_depth:
            band = 1
        atom = crystal.atoms[indices[0]]
        token = f"M{group}" if group >= 0 and len(indices) > 1 else atom.element
        if selected_display_index in indices:
            selected = crystal.atoms[selected_display_index]
            formatter = ascii7_text if charset == "ascii7" else terminal_text
            token = f"[{formatter(selected.display_label)}]"
            if len(token) > grid.width:
                token = f"[{selected.element}]"
        token += depth_marker(band, charset) if show_depth else ""
        if adjacent:
            token = f"({token})"
        r, c, token = grid.fit_text(row, col, token)
        if grid.place(
            r,
            c,
            token,
            d,
            rgb=_element_rgb(atom.element),
            kind="atom",
            anchor=group,
            style="dim"
            if adjacent
            else "bold"
            if slice_distance is not None or band == 0
            else "dim"
            if band == 2
            else "normal",
        ):
            placed.append((group, row, col))
    for group, row, col in placed:
        formula = formulas.get(group, "")
        base, sep, discriminator = formula.rpartition("_")
        if sep and discriminator.isdigit():
            formula = base
        formatter = ascii7_text if charset == "ascii7" else terminal_text
        formula = formatter(formula)
        if not formula or len(formula) > 16:
            continue
        for dr, dc in _LABEL_OFFSETS:
            if grid.place_in_gaps(
                row + dr, col + dc - len(formula) // 2, formula, anchor=group
            ):
                break
    if ghost_points is not None:
        centres = [
            np.mean(ghost_points[indices], axis=0) for indices in groups.values()
        ]
        _draw_ghosts(grid, centres, viewport, charset)
    return grid.to_string()


def render_network_frame(crystal, camera, points, depths, viewport, **options) -> str:
    """Keep the existing overview skeleton, with the shared readable rasterizer."""
    from dataclasses import replace

    from .crystal_ir import BondIR
    from .framework import _framework_representatives
    from .glyph_frame import render_glyph_frame

    representatives, mapping = _framework_representatives(
        crystal,
        points,
        depths,
        viewport,
        width=options["width"],
        height=options["height"],
        show_minor=options["show_minor"],
        selected_display_index=options["selected_display_index"],
    )
    pairs = {
        tuple(sorted((mapping[b.i], mapping[b.j])))
        for b in crystal.bonds
        if b.i in mapping and b.j in mapping and mapping[b.i] != mapping[b.j]
    }
    skeleton = replace(crystal, bonds=[BondIR(i, j) for i, j in sorted(pairs)])
    return render_glyph_frame(
        skeleton,
        camera,
        points,
        depths,
        viewport,
        **options,
        visible_indices=representatives,
        show_hydrogen=False,
        label_mode="label",
    )


def _fill_surface(grid, polygon, depth: float, owner: int) -> None:
    """Scan-convert the projected hull into a depth-only opaque silhouette."""
    if len(polygon) < 3:
        return
    for row in range(
        max(0, min(p[0] for p in polygon)),
        min(grid.height, max(p[0] for p in polygon) + 1),
    ):
        intersections = []
        for (r0, c0), (r1, c1) in zip(polygon, [*polygon[1:], polygon[0]]):
            if min(r0, r1) <= row < max(r0, r1):
                intersections.append(c0 + (row - r0) * (c1 - c0) / (r1 - r0))
        intersections.sort()
        for start, end in zip(intersections[::2], intersections[1::2]):
            for col in range(
                max(0, int(np.ceil(start))), min(grid.width, int(np.floor(end)) + 1)
            ):
                if grid.kind[row][col] == "cell" or grid.depth[row][col] <= depth:
                    grid.chars[row][col] = " "
                    grid.depth[row][col] = depth
                    grid.kind[row][col] = "surface"
                    grid.anchor[row][col] = owner
