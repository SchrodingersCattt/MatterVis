from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import numpy as np
from molcrys_kit.utils.geometry import frac_to_cart

from ..structure.bonds import bonds_conflict
from ..structure.cif_parse import parse_asu
from ..structure.formula_unit import select_formula_unit
from ..structure.geometry import _nearest_pbc_cart, view_rotation
from ..style.disorder import disorder_alpha, is_minor
from ..style.palette import atom_r, elem_color, elem_color_light
from ..viewpoint import auto_view_dir
from ..legacy.plot_crystal import _compute_label_positions

def scene_ops():
    return SimpleNamespace(
        parse_asu=parse_asu,
        select_formula_unit=select_formula_unit,
        auto_view_dir=auto_view_dir,
        view_rotation=view_rotation,
        disorder_alpha=disorder_alpha,
        is_minor=is_minor,
        elem_color=elem_color,
        elem_color_light=elem_color_light,
        atom_r=atom_r,
        compute_label_positions=_compute_label_positions,
    )

def _bond_endpoints(ai, aj, cell, display_mode: str):
    start = np.array(ai["cart"], dtype=float)
    if ai.get("_strict_unit_cell") or aj.get("_strict_unit_cell"):
        # Strict unit-cell views wrap atom centres into the home cell.  The
        # source connectivity can still describe a bond whose nearest image
        # crosses a cell face (for example, source coordinates at -0.1 and
        # 0.6 fractional x).  Connecting the wrapped centres directly would
        # draw a spurious cell-spanning segment and the scene builder would
        # drop it at the long-render threshold.  Lift the endpoint to the
        # minimum-image position while keeping both displayed atoms in the
        # home cell.
        return start, np.array(_nearest_pbc_cart(start, aj["cart"], cell), dtype=float)
    if display_mode in ("formula_unit", "cluster") or (
        ai.get("_unwrapped") and aj.get("_unwrapped")
    ):
        end = np.array(aj["cart"], dtype=float)
    else:
        end = np.array(_nearest_pbc_cart(ai["cart"], aj["cart"], cell), dtype=float)
    return start, end

_SOURCE_IMAGE_TOL = 1e-5

def _periodic_component_ranks(
    atom_count: int,
    canonical_bond_records: list[dict[str, Any]],
) -> list[int]:
    """Return the translational rank (0..3) of every quotient-graph component.

    A finite molecule may cross a unit-cell face but still has a consistent
    integer image assignment and therefore rank zero. An infinite chain,
    layer, or framework contains one or more non-zero translation cycles.
    Detecting those cycles avoids treating a periodic network as a finite
    fragment and copying the whole cell into the positive octant.
    """
    adjacency: dict[int, list[tuple[int, tuple[int, int, int]]]] = {}
    for record in canonical_bond_records:
        try:
            left = int(record["left"])
            right = int(record["right"])
            relation = tuple(int(value) for value in record["right_image_shift"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            len(relation) != 3
            or not 0 <= left < atom_count
            or not 0 <= right < atom_count
        ):
            continue
        adjacency.setdefault(left, []).append((right, relation))
        adjacency.setdefault(right, []).append(
            (left, tuple(-value for value in relation))
        )

    ranks = [0] * atom_count
    visited: set[int] = set()
    for root in range(atom_count):
        if root in visited:
            continue
        potentials: dict[int, tuple[int, int, int]] = {root: (0, 0, 0)}
        component: list[int] = []
        cycle_vectors: list[tuple[int, int, int]] = []
        queue = [root]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            current_potential = potentials[current]
            for neighbour, edge_shift in adjacency.get(current, ()):
                proposed = tuple(
                    current_potential[axis] + edge_shift[axis] for axis in range(3)
                )
                known = potentials.get(neighbour)
                if known is None:
                    potentials[neighbour] = proposed
                    queue.append(neighbour)
                    continue
                cycle = tuple(proposed[axis] - known[axis] for axis in range(3))
                if cycle != (0, 0, 0):
                    cycle_vectors.append(cycle)
        rank = (
            int(np.linalg.matrix_rank(np.asarray(cycle_vectors, dtype=float)))
            if cycle_vectors
            else 0
        )
        for source_index in component:
            ranks[source_index] = rank
    return ranks

def _manifest_strict_bonded_images(
    draw_atoms: list[dict[str, Any]],
    source_atoms: list[dict[str, Any]],
    M: Any,
    canonical_bond_records: list[dict[str, Any]],
) -> int:
    """Add complete periodic images required by strict-cell bonds.

    A strict-cell image is introduced because one bond crosses a cell face.
    The image must represent the whole connected chemical fragment: adding
    only the endpoint that crosses the face leaves, for example, a translated
    ClO4 centre with just one of its four O atoms.
    """
    if not draw_atoms or not any(atom.get("_strict_unit_cell") for atom in draw_atoms):
        return 0

    instances: dict[tuple[int, tuple[int, int, int]], int] = {}
    home_by_source: dict[int, dict[str, Any]] = {}
    for draw_index, atom in enumerate(draw_atoms):
        identity = source_image_identity(atom, source_atoms, draw_index)
        if identity is None:
            continue
        instances.setdefault(identity, draw_index)
        if identity[1] == (0, 0, 0):
            home_by_source.setdefault(identity[0], atom)

    M_arr = np.asarray(M, dtype=float)
    adjacency: dict[int, list[tuple[int, tuple[int, int, int]]]] = {}
    for record in canonical_bond_records:
        try:
            left = int(record["left"])
            right = int(record["right"])
            relation = tuple(int(value) for value in record["right_image_shift"])
        except (KeyError, TypeError, ValueError):
            continue
        if len(relation) != 3:
            continue
        adjacency.setdefault(left, []).append((right, relation))
        adjacency.setdefault(right, []).append(
            (left, tuple(-value for value in relation))
        )

    additions: list[dict[str, Any]] = []

    def _add_image(source: int, shift: tuple[int, int, int]) -> None:
        target_key = (source, shift)
        source_atom = home_by_source.get(source)
        if source_atom is None or target_key in instances:
            return
        copied = dict(source_atom)
        wrapped_frac = np.asarray(source_atom.get("frac"), dtype=float)
        target_frac = wrapped_frac + np.asarray(shift, dtype=float)
        copied["frac"] = target_frac
        copied["cart"] = frac_to_cart(target_frac, M_arr)
        copied["_wrapped_frac"] = wrapped_frac.copy()
        copied["_image_shift"] = shift
        copied["_strict_unit_cell"] = True
        copied["_is_bonded_image_replica"] = True
        copied.pop("_is_boundary_replica", None)
        copied.pop("_is_fragment_boundary_replica", None)
        instances[target_key] = len(draw_atoms) + len(additions)
        additions.append(copied)

    # Seed image instances from canonical records whose endpoint crosses a
    # face. The checks retain the existing safety guard for each seed.
    for record in canonical_bond_records:
        try:
            left = int(record["left"])
            right = int(record["right"])
            relation = tuple(int(value) for value in record["right_image_shift"])
        except (KeyError, TypeError, ValueError):
            continue
        if len(relation) != 3 or relation == (0, 0, 0):
            continue
        for source, target, shift in (
            (left, right, relation),
            (right, left, tuple(-value for value in relation)),
        ):
            source_atom = home_by_source.get(source)
            target_atom = home_by_source.get(target)
            target_key = (target, shift)
            if source_atom is None or target_atom is None or target_key in instances:
                continue
            candidate = dict(target_atom)
            candidate_frac = np.asarray(
                target_atom.get("frac"), dtype=float
            ) + np.asarray(shift, dtype=float)
            candidate["frac"] = candidate_frac
            candidate["cart"] = frac_to_cart(candidate_frac, M_arr)
            if bonds_conflict(source_atom, candidate):
                continue
            if (
                float(
                    np.linalg.norm(
                        np.asarray(candidate["cart"]) - np.asarray(source_atom["cart"])
                    )
                )
                > 3.5
            ):
                continue
            _add_image(target, shift)

    # Complete every seeded image over its canonical connected component.
    # The signed record relation maps current@q to neighbour@(q + edge).
    for (seed_source, seed_shift), _draw_index in list(instances.items()):
        if seed_shift == (0, 0, 0):
            continue
        potentials: dict[int, tuple[int, int, int]] = {seed_source: (0, 0, 0)}
        queue = [seed_source]
        while queue:
            current = queue.pop(0)
            current_potential = potentials[current]
            for neighbour, edge_shift in adjacency.get(current, ()):
                proposed = tuple(
                    current_potential[axis] + edge_shift[axis] for axis in range(3)
                )
                known = potentials.get(neighbour)
                if known is None:
                    potentials[neighbour] = proposed
                    queue.append(neighbour)
                elif known != proposed:
                    # Keep the first shortest-path potential for malformed
                    # record sets containing an inconsistent cycle.
                    continue
        for source, relative_shift in potentials.items():
            absolute_shift = tuple(
                seed_shift[axis] + relative_shift[axis] for axis in range(3)
            )
            _add_image(source, absolute_shift)

    draw_atoms.extend(additions)
    return len(additions)

def _manifest_spanning_bond_context(
    draw_atoms: list[dict[str, Any]],
    source_atoms: list[dict[str, Any]],
    M: Any,
    canonical_bond_records: list[dict[str, Any]],
    ring_records: list[dict[str, Any]],
) -> int:
    """Materialise exact neighbouring context for periodic frameworks.

    A cell-spanning component is infinite, so whole-component replication is
    undefined and per-site face replication produces disconnected fragments.
    Signed MCK bond records identify the exact adjacent-cell endpoints. Those
    direct neighbours are the finite context; only an aromatic ring containing
    an endpoint is completed in the same image. This keeps
    boundary coordination centres and linkers visually symmetric without
    attempting to replicate an infinite framework component.
    """
    if not canonical_bond_records or not any(
        atom.get("_cell_spanning_component") for atom in draw_atoms
    ):
        return 0

    instances: set[tuple[int, tuple[int, int, int]]] = set()
    home_by_source: dict[int, dict[str, Any]] = {}
    for draw_index, atom in enumerate(draw_atoms):
        identity = source_image_identity(atom, source_atoms, draw_index)
        if identity is None:
            continue
        instances.add(identity)
        if identity[1] == (0, 0, 0) and atom.get("_cell_spanning_component"):
            home_by_source.setdefault(identity[0], atom)

    adjacency: dict[int, list[tuple[int, tuple[int, int, int]]]] = {}
    for record in canonical_bond_records:
        try:
            left = int(record["left"])
            right = int(record["right"])
            relation = tuple(int(value) for value in record["right_image_shift"])
        except (KeyError, TypeError, ValueError):
            continue
        if len(relation) != 3:
            continue
        adjacency.setdefault(left, []).append((right, relation))
        adjacency.setdefault(right, []).append(
            (left, tuple(-value for value in relation))
        )
    for neighbours in adjacency.values():
        neighbours.sort(key=lambda item: (item[0], item[1]))

    rings_by_source: dict[int, list[set[int]]] = {}
    for ring in ring_records:
        if not bool(ring.get("is_aromatic")) or int(ring.get("size", 0)) > 8:
            continue
        members = {
            int(value)
            for value in ring.get("cycle_atom_indices", ring.get("atom_indices", ()))
        }
        members.intersection_update(home_by_source)
        if not members:
            continue
        for source in members:
            rings_by_source.setdefault(source, []).append(members)

    M_arr = np.asarray(M, dtype=float)
    additions: list[dict[str, Any]] = []

    def add_context(seed: int, shift: tuple[int, int, int]) -> None:
        placements: dict[int, tuple[int, int, int]] = {seed: (0, 0, 0)}
        ring_anchors = list(placements)
        for anchor in ring_anchors:
            for ring in rings_by_source.get(anchor, ()):
                queue = [anchor]
                visited = {anchor}
                while queue:
                    current = queue.pop(0)
                    current_shift = placements[current]
                    for neighbour, edge_shift in adjacency.get(current, ()):
                        if neighbour not in ring or neighbour in visited:
                            continue
                        visited.add(neighbour)
                        proposed = tuple(
                            current_shift[axis] + edge_shift[axis] for axis in range(3)
                        )
                        placements.setdefault(neighbour, proposed)
                        queue.append(neighbour)
        for source in sorted(placements):
            relative = placements[source]
            absolute = tuple(shift[axis] + relative[axis] for axis in range(3))
            key = (source, absolute)
            home = home_by_source.get(source)
            if home is None or key in instances:
                continue
            base_frac = np.asarray(
                home.get("_wrapped_frac", source_atoms[source].get("frac")),
                dtype=float,
            )
            if base_frac.shape != (3,) or not np.all(np.isfinite(base_frac)):
                continue
            shift_arr = np.asarray(absolute, dtype=float)
            copied = dict(home)
            copied["frac"] = base_frac + shift_arr
            copied["cart"] = frac_to_cart(copied["frac"], M_arr)
            copied["_wrapped_frac"] = base_frac.copy()
            copied["_image_shift"] = absolute
            copied["_is_boundary_replica"] = True
            copied["_is_framework_context_replica"] = True
            copied.pop("_is_fragment_boundary_replica", None)
            instances.add(key)
            additions.append(copied)

    for record in canonical_bond_records:
        try:
            left = int(record["left"])
            right = int(record["right"])
            relation = tuple(int(value) for value in record["right_image_shift"])
        except (KeyError, TypeError, ValueError):
            continue
        if len(relation) != 3 or relation == (0, 0, 0):
            continue
        if left not in home_by_source or right not in home_by_source:
            continue
        add_context(right, relation)
        add_context(left, tuple(-value for value in relation))

    draw_atoms.extend(additions)
    return len(additions)

def source_image_identity(
    atom: dict[str, Any],
    source_atoms: list[dict[str, Any]],
    fallback_index: int,
) -> tuple[int, tuple[int, int, int]] | None:
    """Return a manifested atom's absolute raw-source image identity.

    ``_image_shift`` is intentionally not used here: boundary replication
    stores a shift relative to MCK's unwrapped display home image.  The stable
    identity is the integer offset between the displayed fractional coordinate
    and the source atom's crystallographically wrapped coordinate.
    """
    try:
        source_index = int(atom.get("_source_index", fallback_index))
    except (TypeError, ValueError):
        return None
    if not 0 <= source_index < len(source_atoms):
        return None
    display_frac = np.asarray(atom.get("frac"), dtype=float)
    wrapped_frac = np.asarray(
        atom.get("_wrapped_frac", source_atoms[source_index].get("frac")),
        dtype=float,
    )
    if display_frac.shape != (3,) or wrapped_frac.shape != (3,):
        return None
    delta = display_frac - wrapped_frac
    image = np.rint(delta).astype(int)
    if not np.allclose(delta, image, rtol=0.0, atol=_SOURCE_IMAGE_TOL):
        return None
    return source_index, tuple(int(value) for value in image)

def _canonical_display_bond_pairs(
    draw_atoms: list[dict[str, Any]],
    source_atoms: list[dict[str, Any]],
    canonical_bond_records: list[dict[str, Any]],
) -> tuple[list[tuple[int, int]], dict[str, int]]:
    """Lift PBC-aware canonical records to matching display atom instances.

    For a canonical record ``(left, right, S)`` and a visible instance
    ``left@q``, the sole valid target is ``right@(q + S)``.  The lookup is
    linear in manifested atoms plus valid edge copies; it never re-perceives
    bonds or forms a Cartesian product of replica sets.
    """
    instances: dict[tuple[int, tuple[int, int, int]], int] = {}
    fragment_instances: dict[
        tuple[int, tuple[int, int, int]],
        dict[int, int],
    ] = {}
    fragment_keys_by_source: dict[
        int,
        list[tuple[int, tuple[int, int, int]]],
    ] = {}
    shifts_by_source: dict[int, list[tuple[tuple[int, int, int], int]]] = {}
    collision_keys: set[tuple[int, tuple[int, int, int]]] = set()
    unresolved_instances = 0
    for draw_index, atom in enumerate(draw_atoms):
        identity = source_image_identity(atom, source_atoms, draw_index)
        if identity is None:
            unresolved_instances += 1
            continue
        existing = instances.get(identity)
        if existing is not None:
            collision_keys.add(identity)
            continue
        instances[identity] = draw_index
        shifts_by_source.setdefault(identity[0], []).append((identity[1], draw_index))
        molecule_index = atom.get("_source_molecule_index")
        if molecule_index is not None and not atom.get("_cell_spanning_component"):
            try:
                relative_shift = tuple(
                    int(value) for value in atom.get("_image_shift", (0, 0, 0))
                )
                fragment_key = (int(molecule_index), relative_shift)
                members = fragment_instances.setdefault(fragment_key, {})
                if identity[0] not in members:
                    fragment_keys_by_source.setdefault(identity[0], []).append(
                        fragment_key
                    )
                members[identity[0]] = draw_index
            except (TypeError, ValueError):
                pass

    pairs: set[tuple[int, int]] = set()
    missing_targets = 0
    instance_lookups = 0
    max_copies_per_record = 0
    for record in canonical_bond_records:
        try:
            left = int(record["left"])
            right = int(record["right"])
            relation = tuple(int(value) for value in record["right_image_shift"])
        except (KeyError, TypeError, ValueError):
            continue
        if len(relation) != 3:
            continue
        emitted_for_record: set[tuple[int, int]] = set()
        # Relative fragment shifts only apply to same-cell records; cross-cell
        # records use absolute source/image lookup to avoid home-home extras.
        for fragment_key in sorted(fragment_keys_by_source.get(left, ())):
            # A non-zero canonical shift does not describe the home fragment;
            # its copied boundary fragments still need the internal bond.
            if relation != (0, 0, 0) and fragment_key[1] == (0, 0, 0):
                continue
            members = fragment_instances[fragment_key]
            if left in members and right in members:
                pair = (members[left], members[right])
            else:
                continue
            instance_lookups += 1
            pair_key = tuple(sorted(pair))
            pairs.add(pair_key)
            emitted_for_record.add(pair_key)
        directions = (
            (left, right, relation),
            (right, left, tuple(-value for value in relation)),
        )
        for source, target, shift in directions:
            for image, source_draw_index in shifts_by_source.get(source, ()):
                source_key = (source, image)
                if source_key in collision_keys:
                    continue
                target_image = tuple(image[axis] + shift[axis] for axis in range(3))
                target_key = (target, target_image)
                instance_lookups += 1
                target_draw_index = instances.get(target_key)
                if target_draw_index is None or target_key in collision_keys:
                    missing_targets += 1
                    continue
                pair_key = tuple(sorted((source_draw_index, target_draw_index)))
                pairs.add(pair_key)
                emitted_for_record.add(pair_key)
            max_copies_per_record = max(max_copies_per_record, len(emitted_for_record))

    return sorted(pairs), {
        "display_instances": len(instances),
        "instance_collisions": len(collision_keys),
        "unresolved_instances": unresolved_instances,
        "instance_lookups": instance_lookups,
        "missing_target_instances": missing_targets,
        "max_copies_per_record": max_copies_per_record,
    }

def _prune_unconnected_spanning_replicas(
    draw_atoms: list[dict[str, Any]],
    source_atoms: list[dict[str, Any]],
    canonical_bond_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not canonical_bond_records:
        return draw_atoms
    pairs, _stats = _canonical_display_bond_pairs(
        draw_atoms,
        source_atoms,
        canonical_bond_records,
    )
    connected = {index for pair in pairs for index in pair}
    bonded_sources = {
        int(record[endpoint])
        for record in canonical_bond_records
        for endpoint in ("left", "right")
    }
    return [
        atom
        for index, atom in enumerate(draw_atoms)
        if not (
            atom.get("_cell_spanning_component")
            and atom.get("_is_boundary_replica")
            and int(atom.get("_source_index", -1)) in bonded_sources
            and index not in connected
        )
    ]
