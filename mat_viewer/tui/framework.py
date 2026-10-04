"""Dense framework overview rendering for the terminal compositor."""

from __future__ import annotations

import math
from collections import Counter
from typing import TYPE_CHECKING

import numpy as np

from .braille import BrailleCanvas
from .projection import Viewport

if TYPE_CHECKING:
    from .crystal_ir import CrystalIR
    from ..math.camera import Camera

FRAMEWORK_MIN_ATOMS = 64
FRAMEWORK_MIN_BOND_RATIO = 0.30


def structure_is_framework_like(crystal: "CrystalIR", *, show_minor: bool = True) -> bool:
    """Return whether a dense structure needs a bonded network overview.

    Molecule centroids are useful for a molecular crystal, but they erase the
    shape of a MOF or polymer when MolCrysKit reports one large connected
    fragment.  This deliberately uses only the manifested IR: molecule sizes
    and the already-perceived bond graph.  It is a presentation heuristic, not
    a chemical re-analysis.
    """
    visible = [
        atom for atom in crystal.atoms if show_minor or not atom.is_minor
    ]
    if len(visible) < FRAMEWORK_MIN_ATOMS:
        return False
    sizes = Counter(
        atom.molecule_index for atom in visible if atom.molecule_index >= 0
    )
    if not sizes:
        # Non-CIF adapters may not carry molecule assignments.  Fall back to
        # connected components of the already supplied bond graph so a large
        # polymer still receives a useful network overview.
        visible_indices = {atom.index for atom in visible}
        adjacency: dict[int, set[int]] = {index: set() for index in visible_indices}
        for bond in crystal.bonds:
            if bond.i in visible_indices and bond.j in visible_indices:
                adjacency[bond.i].add(bond.j)
                adjacency[bond.j].add(bond.i)
        remaining = set(visible_indices)
        while remaining:
            root = remaining.pop()
            stack = [root]
            component_size = 1
            while stack:
                current = stack.pop()
                for neighbour in adjacency[current]:
                    if neighbour in remaining:
                        remaining.remove(neighbour)
                        component_size += 1
                        stack.append(neighbour)
            sizes[component_size] += 1
        if sizes:
            largest = max(sizes)
        else:
            return False
    else:
        largest = max(sizes.values())
    if largest < FRAMEWORK_MIN_ATOMS:
        return False
    # A large connected fragment is the characteristic case for a framework.
    # The lower share bound also catches repeated framework fragments while
    # avoiding the many small molecules in an ionic molecular crystal.
    share = largest / max(len(visible), 1)
    if share < 0.25:
        return False
    visible_indices = {atom.index for atom in visible}
    bond_count = sum(
        bond.i in visible_indices and bond.j in visible_indices
        for bond in crystal.bonds
    )
    return bond_count >= max(12, int(largest * FRAMEWORK_MIN_BOND_RATIO))


def framework_skeleton_stats(
    crystal: "CrystalIR", *, show_minor: bool = False
) -> dict[str, int]:
    """Return bounded counts used by the title and the network legend."""
    visible = [
        atom for atom in crystal.atoms if show_minor or not atom.is_minor
    ]
    heavy = [atom for atom in visible if atom.element.upper() != "H"]
    visible_indices = {atom.index for atom in visible}
    edges = sum(
        bond.i in visible_indices and bond.j in visible_indices
        for bond in crystal.bonds
    )
    partial = sum(atom.occupancy < 0.99 for atom in visible)
    return {
        "visible_atoms": len(visible),
        "heavy_atoms": len(heavy),
        "bonds": edges,
        "partial_atoms": partial,
    }

def _framework_representatives(
    crystal: "CrystalIR",
    pts_2d: np.ndarray,
    depth: np.ndarray,
    viewport: "Viewport",
    *,
    width: int,
    height: int,
    show_minor: bool,
    selected_display_index: int | None,
) -> tuple[list[int], dict[int, int]]:
    """Select a stable screen-space skeleton and map source atoms to nodes."""
    visible = [
        index
        for index, atom in enumerate(crystal.atoms)
        if (show_minor or not atom.is_minor) and index < len(pts_2d)
    ]
    heavy = [index for index in visible if crystal.atoms[index].element.upper() != "H"]
    if not heavy:
        heavy = visible
    degree = Counter()
    for bond in crystal.bonds:
        if bond.i in visible and bond.j in visible:
            degree[bond.i] += 1
            degree[bond.j] += 1

    # Keep the overview sparse enough that edges remain separable.  The
    # terminal still exposes the exact graph through selection/focus actions.
    target = max(12, min(len(heavy), int(width * height * 0.05), 180))
    bin_size = max(1, int(math.ceil(math.sqrt(len(heavy) / max(target, 1)))))
    by_bin: dict[tuple[int, int], int] = {}
    for index in heavy:
        row, col = viewport.to_grid(float(pts_2d[index][0]), float(pts_2d[index][1]))
        key = (row // bin_size, col // bin_size)
        previous = by_bin.get(key)
        if previous is None:
            by_bin[key] = index
            continue
        # Prefer a coordination centre, then the front-most atom, then the
        # lower source index.  All tie breaks are deterministic.
        candidate_key = (degree[index], float(depth[index]), -index)
        previous_key = (degree[previous], float(depth[previous]), -previous)
        if candidate_key > previous_key:
            by_bin[key] = index

    representatives = list(by_bin.values())
    # A selected atom and its first bonded shell always remain exact, even if
    # the overview has merged their screen-space bins.
    forced: list[int] = []
    if selected_display_index is not None and selected_display_index in visible:
        forced.append(selected_display_index)
        for bond in crystal.bonds:
            if bond.i == selected_display_index:
                forced.append(bond.j)
            elif bond.j == selected_display_index:
                forced.append(bond.i)
    for index in forced:
        if index in visible and index not in representatives:
            representatives.append(index)

    # Map every visible atom to its coarse node.  Choosing the nearest node in
    # projected space preserves long framework edges after downsampling.
    rep_xy = np.asarray([pts_2d[index] for index in representatives], dtype=float)
    mapping: dict[int, int] = {}
    for index in visible:
        if index in representatives:
            mapping[index] = index
            continue
        distances = np.sum((rep_xy - pts_2d[index]) ** 2, axis=1)
        mapping[index] = representatives[int(np.argmin(distances))]
    return representatives, mapping


def _render_canvas_with_labels(
    canvas: "BrailleCanvas",
    *,
    width: int,
    height: int,
    mono: bool,
    charset: str,
    labels: list[tuple[int, int, str, int, bool]],
) -> str:
    """Render a canvas and a small set of non-overlapping textual labels."""
    from . import compositor as _c
    colored_rows = canvas.render_colored(charset=charset)
    occupied: set[tuple[int, int]] = set()
    placed: dict[int, list[tuple[int, str, int, bool]]] = {}
    for row, col, text, color, selected in sorted(
        labels, key=lambda item: (not item[4], item[0], item[1])
    ):
        text = text[:width]
        if not text or not 0 <= row < height:
            continue
        start = max(0, min(width - len(text), col - len(text) // 2))
        cells = {(row, start + offset) for offset in range(len(text))}
        if cells & occupied:
            continue
        occupied.update(cells)
        placed.setdefault(row, []).append((start, text, color, selected))

    output_lines: list[str] = []
    for row_index in range(height):
        row_data = colored_rows[row_index] if row_index < len(colored_rows) else []
        row_labels = sorted(placed.get(row_index, []), key=lambda item: item[0])
        if not row_labels:
            if mono:
                output_lines.append("".join(ch for ch, _ in row_data).rstrip())
            else:
                output_lines.append(_c._build_colored_braille_line(row_data))
            continue
        parts: list[str] = []
        col = 0
        label_index = 0
        braille_run: list[tuple[str, int]] = []

        def flush() -> None:
            nonlocal braille_run
            if braille_run:
                parts.append(
                    "".join(ch for ch, _ in braille_run)
                    if mono
                    else _c._color_run_to_ansi(braille_run)
                )
                braille_run = []

        while col < width:
            if label_index < len(row_labels) and row_labels[label_index][0] == col:
                flush()
                _, text, color, selected = row_labels[label_index]
                if mono:
                    parts.append(text)
                else:
                    prefix = "\033[1;7;" if selected else "\033[1;"
                    parts.append(f"{prefix}38;5;{color}m{text}\033[0m")
                col += len(text)
                label_index += 1
            else:
                braille_run.append(row_data[col] if col < len(row_data) else (" ", 0))
                col += 1
        flush()
        output_lines.append("".join(parts).rstrip())
    while output_lines and not output_lines[-1]:
        output_lines.pop()
    return "\n".join(output_lines)


def compose_framework_frame(
    crystal: "CrystalIR",
    camera: "Camera",
    pts_2d: np.ndarray,
    depth: np.ndarray,
    viewport: "Viewport",
    canvas: "BrailleCanvas",
    width: int,
    height: int,
    mono: bool,
    show_bonds: bool,
    show_minor: bool,
    selected_display_index: int | None,
    charset: str,
) -> str:
    """Render a dense framework as a bounded bonded skeleton.

    The graph is downsampled in screen space instead of replacing the network
    with one centroid.  A selected atom plus its bonded neighbours is kept at
    full resolution so the same view supports navigation and local inspection.
    """
    from . import compositor as _c

    representatives, mapping = _framework_representatives(
        crystal,
        pts_2d,
        depth,
        viewport,
        width=width,
        height=height,
        show_minor=show_minor,
        selected_display_index=selected_display_index,
    )
    visible = {
        index
        for index, atom in enumerate(crystal.atoms)
        if show_minor or not atom.is_minor
    }
    depth_min = float(depth.min()) if len(depth) else 0.0
    depth_max = float(depth.max()) if len(depth) else 1.0
    if show_bonds:
        # Draw one edge per representative pair.  Minor/partial bonds remain
        # dashed, preserving uncertainty without flooding the terminal.
        edge_seen: set[tuple[int, int]] = set()
        for bond in crystal.bonds:
            if bond.i not in mapping or bond.j not in mapping:
                continue
            ri, rj = mapping[bond.i], mapping[bond.j]
            if ri == rj:
                continue
            pair = tuple(sorted((ri, rj)))
            if pair in edge_seen:
                continue
            edge_seen.add(pair)
            p0 = viewport.to_px(float(pts_2d[ri][0]), float(pts_2d[ri][1]))
            p1 = viewport.to_px(float(pts_2d[rj][0]), float(pts_2d[rj][1]))
            tier = max(
                _c._depth_tier(float(depth[ri]), depth_min, depth_max),
                _c._depth_tier(float(depth[rj]), depth_min, depth_max),
            )
            color = _c._BACK_TIER_COLOR if tier == _c._TIER_BACK else _c.BOND_COLOR
            partial = (
                crystal.atoms[bond.i].is_minor
                or crystal.atoms[bond.j].is_minor
                or crystal.atoms[bond.i].occupancy < 0.99
                or crystal.atoms[bond.j].occupancy < 0.99
            )
            if partial:
                canvas.draw_dashed_line(*p0, *p1, dash=2, gap=2, color=color)
            else:
                canvas.draw_line(*p0, *p1, color=color)

    # Draw nodes after edges so coordination centres remain visible.
    for index in sorted(representatives, key=lambda value: float(depth[value])):
        atom = crystal.atoms[index]
        tier = _c._depth_tier(float(depth[index]), depth_min, depth_max)
        color = _c._tier_color(
            _c.element_ansi_color(atom.element, default=_c.DEFAULT_COLOR), tier
        )
        radius = _c._atom_radius(atom.element, tier, detail_scale=0.55)
        px = viewport.to_px(float(pts_2d[index][0]), float(pts_2d[index][1]))
        if atom.occupancy < 0.99 or atom.is_minor:
            _c._draw_dashed_circle(canvas, px[0], px[1], radius, color=color)
        else:
            _c._draw_circle(canvas, px[0], px[1], radius, color=color)

    # The overview is intentionally Braille-first: automatic element labels
    # turn a visual network into a metadata table.  Exact labels appear only
    # after selection/focus, where they identify the local shell being read.
    labels: list[tuple[int, int, str, int, bool]] = []
    forced = {selected_display_index} if selected_display_index is not None else set()
    if selected_display_index is not None:
        for bond in crystal.bonds:
            if bond.i == selected_display_index:
                forced.add(bond.j)
            elif bond.j == selected_display_index:
                forced.add(bond.i)
    label_indices = list(dict.fromkeys(forced))
    for index in label_indices:
        if index not in visible or index >= len(pts_2d):
            continue
        atom = crystal.atoms[index]
        row, col = viewport.to_grid(float(pts_2d[index][0]), float(pts_2d[index][1]))
        if not viewport.in_bounds_grid(row, col):
            continue
        selected = index == selected_display_index
        label = _c._atom_label_text(atom, "element" if not selected else "label", charset)
        if atom.occupancy < 0.99 and not label.endswith("*"):
            label += "*"
        if selected:
            label = f"[{label}]"
        labels.append(
            (
                row,
                col,
                label,
                _c.element_ansi_color(atom.element, default=_c.DEFAULT_COLOR),
                selected,
            )
        )
    return _render_canvas_with_labels(
        canvas,
        width=width,
        height=height,
        mono=mono,
        charset=charset,
        labels=labels,
    )

__all__ = ["framework_skeleton_stats", "structure_is_framework_like", "compose_framework_frame"]
